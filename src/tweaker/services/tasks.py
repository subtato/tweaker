from __future__ import annotations

import asyncio
import logging
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import Issue, Organization, Run
from .classifier import heuristic_fixability
from .ingest import audit, sync_issues
from .investigation import create_run, run_investigation

logger = logging.getLogger(__name__)
_background_tasks: set[asyncio.Task[Any]] = set()


async def enqueue_run(run_id: str) -> None:
    settings = get_settings()
    if settings.runner == "arq":
        await _enqueue_arq(run_id)
        return
    # Inline mode is intended for local development and single-process demos.
    task = asyncio.create_task(asyncio.to_thread(run_investigation, run_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


async def _enqueue_arq(run_id: str) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    settings = get_settings()
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    try:
        await pool.enqueue_job("run_investigation_job", run_id)
    finally:
        await pool.close()


def enqueue_run_sync(run_id: str) -> None:
    """Enqueue from a non-async context (webhook thread, poller)."""
    settings = get_settings()
    if settings.runner == "arq":
        asyncio.run(_enqueue_arq(run_id))
    else:
        run_investigation(run_id)


async def run_investigation_job(ctx: dict[str, Any], run_id: str) -> None:
    await asyncio.to_thread(run_investigation, run_id)


async def poll_sentry_job(ctx: dict[str, Any]) -> None:
    await asyncio.to_thread(poll_all_organizations)


def poll_all_organizations() -> None:
    with session_scope() as session:
        orgs = list(session.scalars(select(Organization)))
    for org in orgs:
        if not org.sentry_token_encrypted:
            continue
        try:
            with session_scope() as session:
                org = session.get(Organization, org.id)
                if org is None:
                    continue
                issues = sync_issues(session, org, query="is:unresolved", limit=25)
                _maybe_auto_investigate(session, org, issues)
        except Exception as exc:
            logger.warning("Sentry poll failed for org %s: %s", org.slug, exc)


def _maybe_auto_investigate(session: Any, org: Organization, issues: list[Issue]) -> None:
    if not org.auto_investigate:
        return
    for issue in issues:
        if issue.fixability_score is None:
            assessment = heuristic_fixability(issue, issue.latest_event or {})
            issue.fixability_score = assessment.score
            issue.fixability_reason = assessment.reason
            issue.fixability_signals = assessment.signals
        score = issue.fixability_score or 0
        if score < org.fixability_threshold:
            continue
        existing = session.scalar(
            select(Run).where(
                Run.issue_id == issue.id,
                Run.status.in_(["queued", "running", "succeeded"]),
            )
        )
        if existing is not None:
            continue
        kind = "autofix" if org.auto_fix else "investigate"
        run = create_run(
            session,
            org=org,
            issue=issue,
            kind=kind,
            trigger="auto",
            publish=bool(org.auto_fix),
            actor="system",
        )
        audit(
            session,
            org.id,
            "issue.auto_investigate",
            target_type="issue",
            target_id=issue.id,
            metadata={"run_id": run.id, "score": score},
        )
        session.commit()
        enqueue_run_sync(run.id)
