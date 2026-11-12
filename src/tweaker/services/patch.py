from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .codebase import RepositoryWorkspace

_DIFF_HEADER = re.compile(r"^(diff --git a/(.+?) b/(.+?)|--- a/(.+?)\n\+\+\+ b/(.+?))$", re.MULTILINE)


class PatchError(ValueError):
    pass


@dataclass(slots=True)
class PatchCheck:
    ok: bool
    message: str
    files: list[str]


def strip_code_fences(text: str) -> str:
    text = text.strip()
    if not text.startswith("```"):
        # git apply expects a newline at end-of-file; text.strip() removed it.
        return text + "\n" if text else text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip() + "\n"


def changed_files(patch: str) -> list[str]:
    files: list[str] = []
    for match in re.finditer(r"^\+\+\+ b/(.+)$", patch, re.MULTILINE):
        path = match.group(1).strip()
        if path != "/dev/null" and path not in files:
            files.append(path)
    for match in re.finditer(r"^--- a/(.+)$", patch, re.MULTILINE):
        path = match.group(1).strip()
        if path != "/dev/null" and path not in files:
            files.append(path)
    for match in re.finditer(r"^diff --git a/(.+?) b/(.+)$", patch, re.MULTILINE):
        for path in match.groups():
            if path not in files:
                files.append(path)
    return files


def _unsafe_path(path: str) -> str | None:
    cleaned = path.strip().replace("\\", "/")
    if not cleaned:
        return "empty path"
    if cleaned.startswith("/") or re.match(r"^[A-Za-z]:/", cleaned):
        return "absolute path"
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if ".." in parts:
        return "path traversal"
    if ".git" in parts:
        return "git internals"
    if cleaned.startswith("~"):
        return "home directory path"
    return None


def validate_patch(patch: str, max_chars: int = 80_000) -> PatchCheck:
    patch = strip_code_fences(patch or "")
    if not patch.strip():
        return PatchCheck(False, "The model did not produce a patch.", [])
    if len(patch) > max_chars:
        return PatchCheck(False, f"Patch is larger than {max_chars} characters.", [])
    if "GIT binary patch" in patch or "Binary files" in patch:
        return PatchCheck(False, "Binary patches are not supported.", [])
    if not re.search(r"^(--- |diff --git )", patch, re.MULTILINE):
        return PatchCheck(False, "Response is not a unified diff.", [])
    if "deleted file mode" in patch or "new file mode 120000" in patch:
        return PatchCheck(False, "File deletion and symlink changes are not allowed.", [])
    files = changed_files(patch)
    if not files:
        return PatchCheck(False, "Patch does not modify any files.", [])
    if len(files) > 25:
        return PatchCheck(False, "Patch touches too many files (limit 25).", files)
    for path in files:
        problem = _unsafe_path(path)
        if problem:
            return PatchCheck(False, f"Unsafe patch path ({problem}): {path}", files)
    return PatchCheck(True, "Patch structure is valid.", files)


def check_applies(patch: str, workspace: RepositoryWorkspace) -> PatchCheck:
    check = validate_patch(patch)
    if not check.ok:
        return check
    if workspace.root is None:
        workspace.ensure()
    assert workspace.root is not None
    patch = strip_code_fences(patch)
    # git apply reads a file, not stdin through our helper; use a temp file in
    # the repository's parent to keep the worktree clean.
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False, encoding="utf-8") as fh:
        fh.write(patch)
        patch_path = Path(fh.name)
    try:
        proc = workspace._run_git(
            ["apply", "--check", "--whitespace=nowarn", str(patch_path)],
            cwd=workspace.root,
            check=False,
        )
    finally:
        patch_path.unlink(missing_ok=True)
    if proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "patch does not apply").strip()
        return PatchCheck(False, message[-1500:], check.files)
    return PatchCheck(True, "Patch applies cleanly.", check.files)
