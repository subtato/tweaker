from __future__ import annotations

import re
from typing import Any

from ..services.patch import strip_code_fences

_HUNK = re.compile(
    r"^@@ -(?P<source_start>\d+)(?:,(?P<source_length>\d+))? "
    r"\+(?P<target_start>\d+)(?:,(?P<target_length>\d+))? @@ ?(?P<section>.*)$"
)


def unified_diff_to_patches(patch: str, repo_name: str) -> list[dict[str, Any]]:
    """Convert a unified diff into the AgentFilePatch shape Seer sends to Sentry."""
    patch = strip_code_fences(patch)
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    hunk: dict[str, Any] | None = None
    source_line = 0
    target_line = 0
    diff_line = 0

    def finish_hunk() -> None:
        nonlocal hunk
        if current is not None and hunk is not None:
            current["patch"]["hunks"].append(hunk)
        hunk = None

    def finish_file() -> None:
        nonlocal current
        finish_hunk()
        if current is not None:
            files.append(current)
        current = None

    for raw in patch.splitlines():
        line = raw.rstrip("\r")
        if line.startswith("diff --git "):
            finish_file()
            match = re.match(r"diff --git a/(.+?) b/(.+)$", line)
            if not match:
                continue
            current = _new_file(match.group(2))
            continue
        if line.startswith("--- "):
            if current is None:
                current = _new_file(line[4:].strip().removeprefix("a/"))
            else:
                current["patch"]["source_file"] = line[4:].strip().removeprefix("a/")
            continue
        if line.startswith("+++ "):
            if current is None:
                current = _new_file(line[4:].strip().removeprefix("b/"))
            else:
                current["patch"]["target_file"] = line[4:].strip().removeprefix("b/")
                current["patch"]["path"] = current["patch"]["target_file"]
            continue
        if line.startswith("new file mode"):
            if current:
                current["patch"]["type"] = "A"
            continue
        if line.startswith("deleted file mode"):
            if current:
                current["patch"]["type"] = "D"
            continue
        match = _HUNK.match(line)
        if match and current is not None:
            finish_hunk()
            source_line = int(match.group("source_start"))
            target_line = int(match.group("target_start"))
            diff_line = 0
            hunk = {
                "source_start": source_line,
                "source_length": int(match.group("source_length") or 1),
                "target_start": target_line,
                "target_length": int(match.group("target_length") or 1),
                "section_header": match.group("section") or "",
                "lines": [],
            }
            continue
        if hunk is None:
            continue
        if line.startswith("\\ No newline"):
            continue
        kind = line[:1] if line else " "
        value = line[1:] if line else ""
        diff_line += 1
        if kind == "+":
            hunk["lines"].append(
                {
                    "diff_line_no": diff_line,
                    "source_line_no": None,
                    "target_line_no": target_line,
                    "line_type": "+",
                    "value": value,
                }
            )
            current["patch"]["added"] += 1
            target_line += 1
        elif kind == "-":
            hunk["lines"].append(
                {
                    "diff_line_no": diff_line,
                    "source_line_no": source_line,
                    "target_line_no": None,
                    "line_type": "-",
                    "value": value,
                }
            )
            current["patch"]["removed"] += 1
            source_line += 1
        else:
            hunk["lines"].append(
                {
                    "diff_line_no": diff_line,
                    "source_line_no": source_line,
                    "target_line_no": target_line,
                    "line_type": " ",
                    "value": value,
                }
            )
            source_line += 1
            target_line += 1

    finish_file()
    for item in files:
        item["diff"] = patch
        item["repo_name"] = repo_name
    return files


def _new_file(path: str) -> dict[str, Any]:
    path = path.strip().removeprefix("b/")
    return {
        "repo_name": "",
        "diff": "",
        "code_url": None,
        "patch": {
            "path": path,
            "type": "M",
            "added": 0,
            "removed": 0,
            "source_file": path,
            "target_file": path,
            "hunks": [],
        },
    }
