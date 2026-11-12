from __future__ import annotations

import logging
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from ..services.redaction import compact_event, error_summary, in_app_frames
from .repos import RepoDefinition, RepoManager, repo_from_preference
from .rpc import SentryRPCClient, SentryRPCError

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class IssueContext:
    org_id: int
    project_id: int
    group_id: int
    issue: dict[str, Any]
    event: dict[str, Any]
    compact_event: dict[str, Any]
    committers: dict[str, Any] = field(default_factory=dict)
    repos: list[RepoDefinition] = field(default_factory=list)
    code_text: str = ""
    code_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def short_id(self) -> str:
        return str(self.issue.get("shortId") or self.issue.get("short_id") or self.group_id)

    @property
    def title(self) -> str:
        return str(self.issue.get("title") or "Unknown error")

    @property
    def suspect_commits(self) -> list[dict[str, Any]]:
        commits = self.committers.get("suspect_commits") or []
        stack = self.committers.get("stack_commits") or []
        merged: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in [*commits, *stack]:
            if not isinstance(item, dict):
                continue
            key = str(item.get("id") or item.get("commitId") or item.get("message") or "")[:80]
            if key and key in seen:
                continue
            if key:
                seen.add(key)
            merged.append(item)
        return merged[:10]

    def issue_proxy(self) -> SimpleNamespace:
        return SimpleNamespace(
            short_id=self.short_id,
            title=self.title,
            culprit=str(self.issue.get("culprit") or ""),
            level=str(self.issue.get("level") or "error"),
            status=str(self.issue.get("status") or "unresolved"),
            platform=str(self.issue.get("platform") or ""),
            event_count=int(self.issue.get("count") or 0),
            first_seen=self.issue.get("firstSeen"),
            last_seen=self.issue.get("lastSeen"),
            permalink=str(self.issue.get("permalink") or ""),
            issue_metadata=self.issue.get("metadata") or {},
            repo_id=self.repos[0].full_name if self.repos else None,
        )

    def repo_proxy(self) -> SimpleNamespace | None:
        if not self.repos:
            return None
        repo = self.repos[0]
        return SimpleNamespace(
            name=repo.full_name,
            url=repo.provider_raw or f"{repo.owner}/{repo.name}",
            default_branch=repo.branch_name or "main",
        )

    @property
    def error_text(self) -> str:
        return error_summary(self.compact_event)

    @property
    def frames(self) -> list[dict[str, Any]]:
        return in_app_frames(self.compact_event)


def load_issue_context(
    rpc: SentryRPCClient,
    *,
    org_id: int,
    group_id: int,
    project_id: int | None = None,
    issue_id: str | int | None = None,
    repo_pins: dict[str, Any] | None = None,
    include_code: bool = True,
    branch_override: str | None = None,
) -> IssueContext:
    details = rpc.get_issue_details(org_id, issue_id or group_id) or {}
    issue = details.get("issue") if isinstance(details.get("issue"), dict) else {}
    resolved_project_id = int(details.get("project_id") or project_id or 0)
    latest = rpc.get_latest_issue_event(org_id, group_id)
    events = latest.get("events") if isinstance(latest.get("events"), list) else []
    event = events[0] if events and isinstance(events[0], dict) else {}
    compact = compact_event(event, max_chars=60_000) if event else {}

    committers: dict[str, Any] = {}
    try:
        committers = rpc.get_issue_committers(org_id, issue_id or group_id)
    except SentryRPCError as exc:
        logger.info("Issue committers unavailable: %s", exc)

    repos: list[RepoDefinition] = []
    if include_code:
        try:
            preference = rpc.get_project_preferences(org_id, resolved_project_id)
            repos = repo_from_preference(preference)
        except SentryRPCError as exc:
            logger.info("Project preferences unavailable: %s", exc)

    if repo_pins:
        for repo in repos:
            pin = repo_pins.get(repo.full_name)
            if not isinstance(pin, dict):
                continue
            repo.base_commit_sha = pin.get("sha") or pin.get("base_sha") or repo.base_commit_sha
            repo.branch_name = pin.get("branch") or pin.get("base_branch") or repo.branch_name
    if branch_override:
        for repo in repos:
            repo.branch_name = branch_override

    ctx = IssueContext(
        org_id=org_id,
        project_id=resolved_project_id,
        group_id=group_id,
        issue=issue,
        event=event,
        compact_event=compact,
        committers=committers,
        repos=repos,
    )
    if repos and compact:
        manager = RepoManager(viewer_context=rpc.viewer_context)
        code_context, used = manager.context_for_issue(repos, compact, ctx.suspect_commits)
        ctx.code_text = code_context.render()
        ctx.code_files = code_context.files
        ctx.notes = code_context.notes
        if used:
            ctx.repos = used
    return ctx
