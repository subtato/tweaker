from __future__ import annotations

import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx

from ..config import get_settings
from ..crypto import decrypt_secret
from .codebase import RepositoryWorkspace
from .patch import check_applies, strip_code_fences

logger = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


@dataclass(slots=True)
class PublishResult:
    branch: str
    pr_url: str | None
    commit_sha: str | None
    message: str


def branch_name(run: Any, issue: Any) -> str:
    short = (getattr(issue, "short_id", None) or getattr(issue, "sentry_id", "issue")).lower()
    short = re.sub(r"[^a-z0-9-]+", "-", short).strip("-")[:40]
    return f"tweaker/{short}-{run.id[:8]}"


def _askpass_script(token: str, username: str) -> Path:
    handle, path = tempfile.mkstemp(prefix="tweaker-askpass-", suffix=".sh")
    with os.fdopen(handle, "w", encoding="utf-8") as fh:
        fh.write("#!/bin/sh\n")
        fh.write('case "$1" in\n')
        fh.write(f"  *sername*) printf '%s\\n' {username!r} ;;\n")
        fh.write(f"  *) printf '%s\\n' {token!r} ;;\n")
        fh.write("esac\n")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IXUSR)
    return path


def _git(
    args: list[str],
    cwd: Path,
    *,
    token: str | None = None,
    username: str = "x-access-token",
    timeout: int = 180,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_LFS_SKIP_SMUDGE"] = "1"
    askpass: Path | None = None
    if token:
        askpass = _askpass_script(token, username)
        env["GIT_ASKPASS"] = str(askpass)
        env["SSH_ASKPASS"] = str(askpass)
        env["GIT_ASKPASS_REQUIRE"] = "force"
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
            check=False,
        )
    finally:
        if askpass:
            askpass.unlink(missing_ok=True)


def _parse_repo(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    path = parsed.path.strip("/")
    if path.endswith(".git"):
        path = path[:-4]
    parts = [part for part in path.split("/") if part]
    if len(parts) < 2:
        raise PublishError("Repository URL must contain an owner and repository name")
    return parts[-2], parts[-1]


def publish_pull_request(
    run: Any,
    issue: Any,
    repo: Any,
    patch: str,
    *,
    branch: str | None = None,
    title: str | None = None,
    body: str | None = None,
    draft: bool = False,
    existing_pr_url: str | None = None,
    open_pr: bool = True,
) -> PublishResult:
    settings = get_settings()
    if not settings.enable_pr_publishing:
        raise PublishError("Pull-request publishing is disabled")
    if repo.provider not in {"github", "gitlab"}:
        raise PublishError(
            "This repository provider does not support automated pull requests. "
            "Download the patch and apply it manually."
        )
    token = decrypt_secret(repo.access_token_encrypted)
    if not token:
        raise PublishError("A repository access token is required to open a pull request")
    patch = strip_code_fences(patch or "")
    if not patch.strip():
        raise PublishError("There is no patch to publish")

    branch = branch or branch_name(run, issue)
    workspace = RepositoryWorkspace(repo)
    check = check_applies(patch, workspace)
    if not check.ok:
        raise PublishError(f"Patch no longer applies: {check.message}")

    target = settings.data_dir / "publish" / run.id
    if target.exists():
        shutil.rmtree(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    username = "oauth2" if repo.provider == "gitlab" else "x-access-token"
    clone = _git(
        ["clone", "--depth", "1", "--branch", repo.default_branch, repo.url, str(target)],
        cwd=target.parent,
        token=token,
        username=username,
    )
    if clone.returncode != 0:
        raise PublishError(_clean_git_error(clone))

    patch_file = target.parent / f"{run.id}.patch"
    patch_file.write_text(patch, encoding="utf-8")
    try:
        apply_result = _git(["apply", "--whitespace=nowarn", str(patch_file)], cwd=target)
        if apply_result.returncode != 0:
            raise PublishError(f"Could not apply patch: {_clean_git_error(apply_result)}")
        _git(["checkout", "-b", branch], cwd=target)
        _git(["add", "-A"], cwd=target)
        commit_title = title or f"Fix {issue.short_id or issue.title[:60]}"
        commit_body = body or (
            f"Automated fix proposed by Tweaker.\n\n"
            f"Root cause: {(run.root_cause or run.summary or '')[:2000]}"
        )
        _git(
            [
                "-c",
                "user.name=Tweaker",
                "-c",
                "user.email=tweaker@users.noreply.github.com",
                "commit",
                "-m",
                commit_title,
                "-m",
                commit_body,
            ],
            cwd=target,
        )
        push = _git(["push", "origin", f"HEAD:refs/heads/{branch}"], cwd=target, token=token, username=username)
        if push.returncode != 0:
            raise PublishError(f"Could not push branch: {_clean_git_error(push)}")
        commit_sha = _git(["rev-parse", "HEAD"], cwd=target).stdout.strip()
    finally:
        patch_file.unlink(missing_ok=True)

    if existing_pr_url:
        pr_url = existing_pr_url
    elif not open_pr:
        pr_url = None
    else:
        pr_url = _open_pull_request(
            repo=repo,
            token=token,
            branch=branch,
            title=title or f"Fix {issue.short_id or issue.title[:60]}",
            body=body
            or (
                f"## Tweaker fix\n\n"
                f"**Issue:** [{issue.short_id}]({issue.permalink}) — {issue.title}\n\n"
                f"**Root cause:** {run.root_cause or 'See investigation'}\n\n"
                f"**Confidence:** {round((run.confidence or 0) * 100)}%\n\n"
                f"_Generated by [Tweaker]({settings.public_url}). Review before merging._"
            ),
            draft=draft,
        )
    return PublishResult(
        branch=branch,
        pr_url=pr_url,
        commit_sha=commit_sha,
        message=(
            "Pull request updated"
            if existing_pr_url
            else ("Branch pushed" if not open_pr else "Pull request opened")
        ),
    )


def _open_pull_request(
    *, repo: Any, token: str, branch: str, title: str, body: str, draft: bool
) -> str | None:
    if repo.provider == "github":
        owner, name = _parse_repo(repo.url)
        host = urlparse(repo.url).hostname or "github.com"
        api_base = "https://api.github.com" if host in {"github.com", "www.github.com"} else f"https://{host}/api/v3"
        response = httpx.post(
            f"{api_base}/repos/{owner}/{name}/pulls",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            json={
                "title": title,
                "head": branch,
                "base": repo.default_branch,
                "body": body,
                "draft": draft,
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise PublishError(f"GitHub PR creation failed ({response.status_code}): {response.text[:500]}")
        return str(response.json().get("html_url") or "")

    if repo.provider == "gitlab":
        parsed = urlparse(repo.url)
        host = parsed.hostname or "gitlab.com"
        api_base = f"https://{host}/api/v4"
        project_path = quote("/".join(parsed.path.strip("/").removesuffix(".git").split("/")), safe="")
        response = httpx.post(
            f"{api_base}/projects/{project_path}/merge_requests",
            headers={"PRIVATE-TOKEN": token},
            json={
                "source_branch": branch,
                "target_branch": repo.default_branch,
                "title": title,
                "description": body,
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise PublishError(f"GitLab MR creation failed ({response.status_code}): {response.text[:500]}")
        return str(response.json().get("web_url") or "")

    return None


def _clean_git_error(result: subprocess.CompletedProcess[str]) -> str:
    message = (result.stderr or result.stdout or f"git exited {result.returncode}").strip()
    message = re.sub(r"https://[^@\s]+@", "https://[REDACTED]@", message)
    return message[-1500:]
