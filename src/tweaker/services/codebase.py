from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from ..config import get_settings
from ..crypto import decrypt_secret

logger = logging.getLogger(__name__)

TEXT_SUFFIXES = {
    ".py", ".pyi", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".go", ".rb",
    ".php", ".java", ".kt", ".kts", ".cs", ".rs", ".swift", ".scala", ".vue",
    ".svelte", ".html", ".css", ".scss", ".sql", ".graphql", ".gql", ".json",
    ".yaml", ".yml", ".toml", ".ini", ".cfg", ".md", ".txt", ".sh", ".ex",
    ".exs", ".dart", ".lua", ".r", ".R", ".m", ".mm", ".c", ".h", ".cc",
    ".cpp", ".hpp",
}
MAX_FILE_BYTES = 1_200_000
GIT_TIMEOUT = 120


class CodebaseError(RuntimeError):
    pass


@dataclass
class CodeSnippet:
    path: str
    start_line: int
    end_line: int
    content: str
    reason: str = ""

    def render(self) -> str:
        header = f"--- {self.path}:{self.start_line}-{self.end_line}"
        if self.reason:
            header += f" ({self.reason})"
        return f"{header}\n{self.content}"


@dataclass
class CodebaseContext:
    snippets: list[CodeSnippet] = field(default_factory=list)
    files: list[str] = field(default_factory=list)
    commit_context: str = ""
    notes: list[str] = field(default_factory=list)

    def render(self, max_chars: int = 60_000) -> str:
        parts: list[str] = []
        if self.commit_context:
            parts.append("## Suspect commits\n" + self.commit_context)
        for snippet in self.snippets:
            rendered = snippet.render()
            if sum(len(part) for part in parts) + len(rendered) > max_chars:
                break
            parts.append(rendered)
        if not parts:
            parts.append("(No source context was available for this issue.)")
        return "\n\n".join(parts)

    def as_metadata(self) -> dict[str, Any]:
        return {
            "files": self.files,
            "snippet_count": len(self.snippets),
            "notes": self.notes,
        }


class RepositoryWorkspace:
    """Safe, read-mostly access to a repository checkout."""

    def __init__(self, repo: Any) -> None:
        self.repo = repo
        self.settings = get_settings()
        self.root: Path | None = None

    # -- lifecycle ---------------------------------------------------------

    def ensure(self, refresh: bool = True) -> Path:
        local = (self.repo.local_path or "").strip()
        if local:
            if not self.settings.allow_local_repositories:
                raise CodebaseError("Local repositories are disabled")
            path = Path(local).expanduser().resolve()
            if not path.exists():
                raise CodebaseError(f"Local repository path does not exist: {path}")
            if not path.is_dir():
                raise CodebaseError(f"Local repository path is not a directory: {path}")
            self.root = path
            return path

        target = self.settings.data_dir / "repos" / self.repo.id
        self.root = target
        if (target / ".git").exists():
            if refresh:
                self._run_git(["fetch", "--depth", "1", "origin", self.repo.default_branch], check=False)
                self._run_git(["reset", "--hard", f"origin/{self.repo.default_branch}"], check=False)
            return target

        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        url = self._authenticated_url()
        self._run_git(
            ["clone", "--depth", "1", "--branch", self.repo.default_branch, url, str(target)],
            cwd=target.parent,
            check=True,
        )
        return target

    def _authenticated_url(self) -> str:
        url = self.repo.url
        token = decrypt_secret(getattr(self.repo, "access_token_encrypted", None))
        if not token or not url.startswith(("http://", "https://")):
            return url
        parsed = urlparse(url)
        host = parsed.netloc
        if self.repo.provider == "gitlab":
            auth = f"oauth2:{token}"
        elif self.repo.provider == "github":
            auth = f"x-access-token:{token}"
        else:
            auth = f"git:{token}"
        return parsed._replace(netloc=f"{auth}@{host}").geturl()

    def _run_git(
        self,
        args: list[str],
        cwd: Path | None = None,
        check: bool = True,
        timeout: int = GIT_TIMEOUT,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_LFS_SKIP_SMUDGE"] = "1"
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(cwd or self.root or Path.cwd()),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise CodebaseError(f"git {' '.join(args[:3])} timed out") from exc
        if check and result.returncode != 0:
            raise CodebaseError(_git_error(result))
        return result

    def _relative(self, path: Path) -> str:
        assert self.root is not None
        return path.resolve().relative_to(self.root.resolve()).as_posix()

    def _safe_path(self, relative: str) -> Path | None:
        assert self.root is not None
        cleaned = relative.replace("\\", "/").lstrip("/")
        if cleaned.startswith("../") or "/../" in cleaned or cleaned == "..":
            return None
        candidate = (self.root / cleaned).resolve()
        try:
            candidate.relative_to(self.root.resolve())
        except ValueError:
            return None
        return candidate

    # -- reads -------------------------------------------------------------

    def read_file(
        self,
        relative: str,
        around_line: int | None = None,
        context: int = 35,
        max_lines: int = 180,
    ) -> CodeSnippet | None:
        if self.root is None:
            self.ensure()
        path = self._safe_path(relative)
        if path is None or not path.is_file():
            return None
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name not in {"Dockerfile", "Makefile"}:
            return None
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                return None
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None
        if not lines:
            return None
        if around_line and around_line > 0:
            start = max(1, around_line - context)
            end = min(len(lines), around_line + context)
        else:
            start = 1
            end = min(len(lines), max_lines)
        if end - start > max_lines:
            end = start + max_lines
        numbered = "\n".join(f"{idx:>6} | {lines[idx - 1]}" for idx in range(start, end + 1))
        return CodeSnippet(self._relative(path), start, end, numbered)

    def find_candidates(self, filename: str) -> list[str]:
        assert self.root is not None
        cleaned = filename.replace("\\", "/").lstrip("/")
        candidates: list[str] = []
        direct = self._safe_path(cleaned)
        if direct and direct.is_file():
            candidates.append(self._relative(direct))
        # Strip common build prefixes and try suffix matches.
        variants = [cleaned]
        for prefix in ("app/", "src/", "lib/", "webpack://", "file://"):
            if cleaned.startswith(prefix):
                variants.append(cleaned[len(prefix):])
        basename = Path(cleaned).name
        if basename and basename not in variants:
            variants.append(basename)
        for variant in variants:
            path = self._safe_path(variant)
            if path and path.is_file() and self._relative(path) not in candidates:
                candidates.append(self._relative(path))
        if basename:
            for found in self.root.rglob(basename):
                if len(candidates) >= 5:
                    break
                if found.is_file() and ".git" not in found.parts:
                    try:
                        rel = self._relative(found)
                    except ValueError:
                        continue
                    if rel not in candidates:
                        candidates.append(rel)
        return candidates[:5]

    def search(self, query: str, max_results: int = 12) -> list[dict[str, Any]]:
        if self.root is None:
            self.ensure()
        assert self.root is not None
        query = query.strip()
        if not query or len(query) < 3:
            return []
        results: list[dict[str, Any]] = []
        if (self.root / ".git").exists():
            proc = self._run_git(
                ["grep", "-n", "-I", "--no-color", "-m", "3", "-e", query, "--"],
                check=False,
            )
            for line in proc.stdout.splitlines():
                parts = line.split(":", 3)
                if len(parts) < 4:
                    continue
                path, lineno, _col, content = parts
                results.append(
                    {
                        "path": path,
                        "line": _to_int(lineno),
                        "content": content.strip()[:500],
                    }
                )
                if len(results) >= max_results:
                    break
        if not results:
            pattern = re.compile(re.escape(query), re.IGNORECASE)
            for path in self._walk_text_files():
                try:
                    if path.stat().st_size > MAX_FILE_BYTES:
                        continue
                    for idx, line in enumerate(
                        path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
                    ):
                        if pattern.search(line):
                            results.append(
                                {
                                    "path": self._relative(path),
                                    "line": idx,
                                    "content": line.strip()[:500],
                                }
                            )
                            break
                except OSError:
                    continue
                if len(results) >= max_results:
                    break
        return results

    def _walk_text_files(self):
        assert self.root is not None
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [
                d for d in dirnames if d not in {".git", "node_modules", "vendor", "dist", "build", ".venv", "venv"}
            ]
            for name in filenames:
                path = Path(dirpath) / name
                if path.suffix.lower() in TEXT_SUFFIXES or name in {"Dockerfile", "Makefile"}:
                    yield path

    # -- context -----------------------------------------------------------

    def context_for_event(
        self,
        event: dict[str, Any],
        frames: list[dict[str, Any]],
        error_text: str,
        suspect_commits: list[dict[str, Any]] | None = None,
        max_chars: int | None = None,
    ) -> CodebaseContext:
        if self.root is None:
            self.ensure()
        settings = get_settings()
        budget = max_chars or settings.max_context_chars
        ctx = CodebaseContext()
        seen: set[tuple[str, int]] = set()

        for frame in frames:
            filename = frame.get("filename") or frame.get("absPath") or ""
            if not filename:
                continue
            line = _to_int(frame.get("lineNo")) or None
            candidates = self.find_candidates(str(filename))
            if not candidates:
                ctx.notes.append(f"No source file found for {filename}")
                continue
            snippet = self.read_file(candidates[0], around_line=line, context=32)
            if snippet:
                key = (snippet.path, snippet.start_line)
                if key not in seen:
                    seen.add(key)
                    snippet.reason = f"stack frame {frame.get('function') or '?'}"
                    ctx.snippets.append(snippet)
                    ctx.files.append(snippet.path)

        # Search for the most distinctive fragments of the error.
        tokens = _search_terms(error_text)
        for token in tokens[:5]:
            for match in self.search(token, max_results=3):
                snippet = self.read_file(match["path"], around_line=match["line"], context=18)
                if snippet:
                    key = (snippet.path, snippet.start_line)
                    if key not in seen:
                        seen.add(key)
                        snippet.reason = f"match for {token!r}"
                        ctx.snippets.append(snippet)
                        ctx.files.append(snippet.path)

        # Include definitions for functions that appear in in-app frames.
        for frame in frames[:6]:
            function = str(frame.get("function") or "").strip()
            if not function or function in {"<module>", "anonymous", "?"}:
                continue
            for match in self.search(function, max_results=2):
                if match["path"] in ctx.files:
                    continue
                snippet = self.read_file(match["path"], around_line=match["line"], context=14)
                if snippet:
                    snippet.reason = f"definition of {function}"
                    ctx.snippets.append(snippet)
                    ctx.files.append(snippet.path)

        if suspect_commits:
            lines = []
            for commit in suspect_commits[:8]:
                message = str(commit.get("message") or "").splitlines()[0][:200]
                lines.append(
                    f"- {str(commit.get('id') or '')[:10]} {message} "
                    f"({commit.get('authorName') or commit.get('author_name') or 'unknown'})"
                )
            ctx.commit_context = "\n".join(lines)

        # Trim to budget.
        total = 0
        trimmed: list[CodeSnippet] = []
        for snippet in ctx.snippets:
            size = len(snippet.render())
            if total + size > budget:
                break
            trimmed.append(snippet)
            total += size
        ctx.snippets = trimmed
        ctx.files = sorted(set(ctx.files))
        return ctx


def _git_error(result: subprocess.CompletedProcess[str]) -> str:
    message = (result.stderr or result.stdout or "").strip()
    # Never surface embedded credentials.
    message = re.sub(r"https://[^@\s]+@", "https://[REDACTED]@", message)
    return message[-1000:] or f"git exited with {result.returncode}"


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _search_terms(error_text: str) -> list[str]:
    """Pick distinctive searchable fragments from an exception message."""
    text = re.sub(r"\s+", " ", error_text or "").strip()
    if not text:
        return []
    terms: list[str] = []
    # Quoted strings often name the missing key/route/field.
    for match in re.findall(r"['\"`]([^'\"`]{4,80})['\"`]", text):
        if not match.startswith(("http://", "https://")):
            terms.append(match)
    # Error class names and long identifiers.
    for match in re.findall(r"\b[A-Z][A-Za-z0-9_]{4,}\b", text):
        if match not in terms:
            terms.append(match)
    # A whole message is often a useful literal.
    if 8 <= len(text) <= 100 and text not in terms:
        terms.append(text)
    return [term for term in terms if len(term) >= 4][:8]
