from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import AuditEvent, Issue, Organization, Repository
from .redaction import compact_event
from .sentry import SentryClient, client_for

logger = logging.getLogger(__name__)


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _project_slug(payload: dict[str, Any]) -> str:
    project = payload.get("project")
    if isinstance(project, dict):
        return str(project.get("slug") or project.get("name") or "")
    return str(payload.get("projectSlug") or "")


def upsert_issue(
    session: Session,
    org: Organization,
    payload: dict[str, Any],
    *,
    event: dict[str, Any] | None = None,
    commit: bool = True,
) -> Issue:
    sentry_id = str(payload.get("id") or payload.get("issueId") or "")
    if not sentry_id:
        raise ValueError("Sentry issue payload has no id")
    issue = session.scalar(
        select(Issue).where(Issue.org_id == org.id, Issue.sentry_id == sentry_id)
    )
    if issue is None:
        issue = Issue(org_id=org.id, sentry_id=sentry_id)
        session.add(issue)

    issue.project_slug = _project_slug(payload) or issue.project_slug
    issue.short_id = str(payload.get("shortId") or payload.get("short_id") or issue.short_id or "")
    issue.title = str(payload.get("title") or issue.title or "")
    issue.culprit = str(payload.get("culprit") or issue.culprit or "")
    issue.level = str(payload.get("level") or issue.level or "error")
    issue.status = str(payload.get("status") or issue.status or "unresolved")
    issue.platform = str(payload.get("platform") or issue.platform or "")
    issue.first_seen = parse_datetime(payload.get("firstSeen")) or issue.first_seen
    issue.last_seen = parse_datetime(payload.get("lastSeen")) or issue.last_seen
    issue.event_count = int(payload.get("count") or issue.event_count or 0)
    issue.user_count = int(payload.get("userCount") or issue.user_count or 0)
    issue.permalink = str(payload.get("permalink") or issue.permalink or "")
    issue.issue_metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else issue.issue_metadata
    issue.raw = payload
    if event:
        issue.latest_event = compact_event(event)
    if issue.repo_id is None:
        issue.repo_id = select_repo_for_issue(session, org, issue)
    if commit:
        session.commit()
    return issue


def refresh_issue(session: Session, org: Organization, issue: Issue) -> Issue:
    """Refresh one issue and its latest event from Sentry."""
    with client_for(org) as client:
        payload = client.get_issue(issue.sentry_id)
        event = client.get_latest_event(org.sentry_org_slug, issue.sentry_id)
        commits = client.get_issue_commits(issue.sentry_id)
    issue = upsert_issue(session, org, payload, event=event, commit=False)
    if commits:
        issue.issue_metadata = {**(issue.issue_metadata or {}), "suspect_commits": commits[:10]}
    session.commit()
    return issue


def sync_issues(
    session: Session,
    org: Organization,
    *,
    project_slug: str | None = None,
    query: str = "is:unresolved",
    limit: int = 25,
    with_events: bool = False,
) -> list[Issue]:
    with client_for(org) as client:
        payloads = client.list_issues(
            org.sentry_org_slug,
            project_slug=project_slug,
            query=query,
            limit=limit,
        )
        issues: list[Issue] = []
        for payload in payloads:
            event: dict[str, Any] = {}
            if with_events and payload.get("id"):
                try:
                    event = client.get_latest_event(org.sentry_org_slug, str(payload["id"]))
                except Exception as exc:  # keep syncing other issues
                    logger.warning("Could not fetch event for %s: %s", payload.get("id"), exc)
            issues.append(upsert_issue(session, org, payload, event=event, commit=False))
        session.commit()
        return issues


def select_repo_for_issue(session: Session, org: Organization, issue: Issue) -> str | None:
    repos = list(
        session.scalars(
            select(Repository).where(Repository.org_id == org.id, Repository.enabled.is_(True))
        )
    )
    for repo in repos:
        if issue.project_slug and issue.project_slug in (repo.project_slugs or []):
            return repo.id
    if org.default_repo_id:
        return org.default_repo_id
    return repos[0].id if len(repos) == 1 else None


def get_repo(session: Session, org: Organization, repo_id: str | None) -> Repository | None:
    if not repo_id:
        return None
    repo = session.get(Repository, repo_id)
    if repo is None or repo.org_id != org.id:
        return None
    return repo


def audit(
    session: Session,
    org_id: str | None,
    action: str,
    *,
    actor: str = "system",
    target_type: str = "",
    target_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> None:
    session.add(
        AuditEvent(
            org_id=org_id,
            actor=actor,
            action=action,
            target_type=target_type,
            target_id=target_id,
            event_metadata=metadata or {},
        )
    )


def sentry_event_for_issue(client: SentryClient, org: Organization, issue: Issue) -> dict[str, Any]:
    return client.get_latest_event(org.sentry_org_slug, issue.sentry_id)
