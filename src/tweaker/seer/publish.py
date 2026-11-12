from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any

from ..crypto import encrypt_secret
from ..services.publishing import PublishError, publish_pull_request
from ..services.scm import token_for
from .repos import RepoDefinition, RepoManager

logger = logging.getLogger(__name__)


def create_pull_request(
    *,
    run: Any,
    issue: Any,
    repo: RepoDefinition,
    patch: str,
    token: str | None,
    branch: str | None = None,
    base_branch: str | None = None,
    title: str | None = None,
    body: str | None = None,
    draft: bool = False,
    existing_pr_url: str | None = None,
    open_pr: bool = True,
) -> dict[str, Any]:
    manager = RepoManager()
    provider = "gitlab" if repo.is_gitlab else "github"
    remote = manager.remote_url(repo)
    token = token or token_for(repo.provider, manager.settings, base_url=remote)
    if not token:
        raise PublishError(
            "No source-control token is configured. Set TWEAKER_GITHUB_TOKEN, "
            "TWEAKER_GITHUB_ENTERPRISE_TOKEN, TWEAKER_GITLAB_TOKEN, or "
            "TWEAKER_GITLAB_SELF_HOSTED_TOKEN."
        )
    repo_proxy = SimpleNamespace(
        id=f"seer-{run.id}-{repo.full_name.replace('/', '-')}",
        url=remote,
        provider=provider,
        default_branch=base_branch or repo.branch_name or "main",
        access_token_encrypted=encrypt_secret(token),
        local_path=None,
    )
    issue_proxy = SimpleNamespace(
        short_id=issue.short_id,
        title=issue.title,
        permalink=issue.issue.get("permalink") if isinstance(issue.issue, dict) else "",
    )
    metadata = getattr(run, "run_metadata", None) or {}
    run_proxy = SimpleNamespace(
        id=str(run.id),
        root_cause=metadata.get("root_cause") or metadata.get("summary") or "",
        summary=metadata.get("summary") or "",
        confidence=metadata.get("confidence"),
    )
    result = publish_pull_request(
        run_proxy,
        issue_proxy,
        repo_proxy,
        patch,
        branch=branch,
        title=title or f"Fix {issue.short_id}",
        body=body,
        draft=draft,
        existing_pr_url=existing_pr_url,
        open_pr=open_pr,
    )
    return {
        "branch_name": result.branch,
        "pr_url": result.pr_url,
        "commit_sha": result.commit_sha,
        "message": result.message,
    }
