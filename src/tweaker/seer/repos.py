from __future__ import annotations

import json
import logging
import os
import re
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from urllib.parse import urlparse

from ..config import get_settings
from ..services.codebase import CodebaseContext, RepositoryWorkspace
from ..services.redaction import compact_event, error_summary, in_app_frames

logger = logging.getLogger(__name__)


class RepoError(RuntimeError):
    pass


@dataclass(slots=True)
class RepoDefinition:
    provider: str
    owner: str
    name: str
    external_id: str = ""
    integration_id: str | None = None
    branch_name: str | None = None
    base_commit_sha: str | None = None
    provider_raw: str | None = None
    code_mappings: list[dict[str, str]] | None = None
    instructions: str | None = None
    repository_id: int | None = None

    @classmethod
    def from_wire(cls, data: dict[str, Any]) -> RepoDefinition:
        return cls(
            provider=str(data.get("provider") or ""),
            owner=str(data.get("owner") or ""),
            name=str(data.get("name") or ""),
            external_id=str(data.get("external_id") or ""),
            integration_id=(
                str(data.get("integration_id"))
                if data.get("integration_id") is not None
                else None
            ),
            branch_name=data.get("branch_name"),
            base_commit_sha=data.get("base_commit_sha"),
            provider_raw=data.get("provider_raw"),
            code_mappings=data.get("code_mappings") or [],
            instructions=data.get("instructions"),
            repository_id=data.get("repository_id"),
        )

    @property
    def full_name(self) -> str:
        return f"{self.owner}/{self.name}"

    @property
    def is_github(self) -> bool:
        return "github" in self.provider.lower()

    @property
    def is_gitlab(self) -> bool:
        return "gitlab" in self.provider.lower()


def _safe_key(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", value)[:120]


def _askpass(token: str, username: str) -> Path:
    handle, path = tempfile.mkstemp(prefix="tweaker-askpass-", suffix=".sh")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n")
        fh.write('case "$1" in\n')
        fh.write(f"  *sername*) printf '%s\\n' {username!r} ;;\n")
        fh.write(f"  *) printf '%s\\n' {token!r} ;;\n")
        fh.write("esac\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


class RepoManager:
    def __init__(self, viewer_context: str | None = None) -> None:
        self.settings = get_settings()
        self.viewer_context = viewer_context
        try:
            self.repo_map = json.loads(self.settings.repo_map or "{}")
        except json.JSONDecodeError:
            self.repo_map = {}
        if not isinstance(self.repo_map, dict):
            self.repo_map = {}

    def remote_url(self, repo: RepoDefinition) -> str:
        if repo.provider_raw and repo.provider_raw.startswith(("http://", "https://")):
            base = repo.provider_raw.rstrip("/")
        elif repo.is_gitlab:
            base = self.settings.gitlab_base_url.rstrip("/")
        else:
            base = self.settings.github_base_url.rstrip("/")
        return f"{base}/{repo.full_name}.git"

    def token_for(self, repo: RepoDefinition) -> str | None:
        if repo.is_gitlab:
            return self.settings.gitlab_token
        return self.settings.github_token

    def local_override(self, repo: RepoDefinition) -> Path | None:
        for key in (repo.full_name, f"{repo.provider}:{repo.full_name}", repo.external_id):
            value = self.repo_map.get(key)
            if value:
                path = Path(str(value)).expanduser().resolve()
                if path.exists():
                    return path
        return None

    def ensure(self, repo: RepoDefinition) -> RepositoryWorkspace:
        local = self.local_override(repo)
        if local is not None:
            return self._workspace(repo, local, self.remote_url(repo))

        target = self.settings.seer_repos_dir / _safe_key(repo.full_name)
        remote = self.remote_url(repo)
        token = self.token_for(repo)
        if not (target / ".git").exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                import shutil

                shutil.rmtree(target)
            args = ["clone", "--filter=blob:none", "--no-checkout", remote, str(target)]
            self._git(args, cwd=target.parent, token=token, repo=repo)
        branch = repo.branch_name or "main"
        ref = f"origin/{branch}"
        if repo.base_commit_sha:
            pinned = self._git(
                ["fetch", "--depth", "1", "origin", repo.base_commit_sha],
                cwd=target,
                token=token,
                repo=repo,
                check=False,
            )
            ref = repo.base_commit_sha if pinned.returncode == 0 else f"origin/{branch}"
        # Try the requested branch, then the remote default branch.
        fetch = self._git(
            ["fetch", "--depth", "1", "origin", branch],
            cwd=target,
            token=token,
            repo=repo,
            check=False,
        )
        if fetch.returncode != 0:
            self._git(["fetch", "--depth", "1", "origin"], cwd=target, token=token, repo=repo)
            head = self._git(
                ["symbolic-ref", "refs/remotes/origin/HEAD"],
                cwd=target,
                check=False,
            ).stdout.strip()
            branch = head.rsplit("/", 1)[-1] if head else "main"
            if not repo.base_commit_sha:
                ref = f"origin/{branch}"
        self._git(["checkout", "-f", ref], cwd=target, token=token, repo=repo)
        return self._workspace(repo, target, remote)

    def _workspace(self, repo: RepoDefinition, path: Path, remote: str) -> RepositoryWorkspace:
        proxy = SimpleNamespace(
            id=_safe_key(repo.full_name),
            local_path=str(path),
            url=remote,
            default_branch=repo.branch_name or "main",
            provider=repo.provider,
            access_token_encrypted=None,
        )
        return RepositoryWorkspace(proxy)

    def _git(
        self,
        args: list[str],
        *,
        cwd: Path,
        token: str | None = None,
        repo: RepoDefinition | None = None,
        check: bool = True,
        timeout: int = 180,
    ) -> subprocess.CompletedProcess[str]:
        env = os.environ.copy()
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GIT_LFS_SKIP_SMUDGE"] = "1"
        askpass: Path | None = None
        if token:
            username = "oauth2" if repo and repo.is_gitlab else "x-access-token"
            askpass = _askpass(token, username)
            env["GIT_ASKPASS"] = str(askpass)
            env["SSH_ASKPASS"] = str(askpass)
            env["GIT_ASKPASS_REQUIRE"] = "force"
        try:
            result = subprocess.run(
                ["git", *args],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                timeout=timeout,
                env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise RepoError(f"git {args[0]} timed out") from exc
        finally:
            if askpass:
                askpass.unlink(missing_ok=True)
        if check and result.returncode != 0:
            message = re.sub(
                r"https://[^@\s]+@", "https://[REDACTED]@", (result.stderr or result.stdout)
            )
            raise RepoError(message[-1200:])
        return result

    def context_for_issue(
        self,
        repos: list[RepoDefinition],
        event: dict[str, Any],
        commits: list[dict[str, Any]] | None = None,
    ) -> tuple[CodebaseContext, list[RepoDefinition]]:
        compact = compact_event(event, max_chars=100_000)
        frames = in_app_frames(compact)
        used: list[RepoDefinition] = []
        combined = CodebaseContext()
        remaining = self.settings.max_context_chars
        for repo in repos:
            try:
                workspace = self.ensure(repo)
                mapped_frames = _apply_code_mappings(frames, repo)
                ctx = workspace.context_for_event(
                    event=compact,
                    frames=mapped_frames,
                    error_text=error_summary(compact),
                    suspect_commits=commits,
                    max_chars=remaining,
                )
            except Exception as exc:
                logger.warning("Repo %s could not be prepared: %s", repo.full_name, exc)
                combined.notes.append(f"{repo.full_name}: {exc}")
                continue
            if not ctx.snippets:
                continue
            used.append(repo)
            combined.snippets.extend(ctx.snippets)
            combined.files.extend(ctx.files)
            if ctx.commit_context:
                combined.commit_context = ctx.commit_context
            remaining = max(2000, remaining - len(ctx.render(remaining)))
            if remaining <= 2000:
                break
        combined.files = sorted(set(combined.files))
        return combined, used


def _apply_code_mappings(
    frames: list[dict[str, Any]], repo: RepoDefinition
) -> list[dict[str, Any]]:
    mappings = repo.code_mappings or []
    if not mappings:
        return frames
    mapped: list[dict[str, Any]] = []
    for frame in frames:
        frame = dict(frame)
        filename = str(frame.get("filename") or "")
        for mapping in mappings:
            stack_root = str(mapping.get("stack_root") or "")
            source_root = str(mapping.get("source_root") or "")
            if stack_root and filename.startswith(stack_root):
                filename = source_root.rstrip("/") + "/" + filename[len(stack_root):].lstrip("/")
                frame["filename"] = filename
                break
        mapped.append(frame)
    return mapped


def repo_from_preference(preference: dict[str, Any]) -> list[RepoDefinition]:
    repos = preference.get("repositories") or []
    return [RepoDefinition.from_wire(item) for item in repos if isinstance(item, dict)]


def guess_repo_full_name(value: str) -> str | None:
    value = value.strip()
    if re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", value):
        return value
    parsed = urlparse(value)
    if parsed.netloc:
        path = parsed.path.strip("/")
        if path.endswith(".git"):
            path = path[:-4]
        if path.count("/") >= 1:
            return "/".join(path.split("/")[-2:])
    return None
