from __future__ import annotations

import logging
from typing import Any

from ..config import get_settings
from ..models import SeerRun
from .repos import RepoDefinition
from .rpc import SentryRPCClient

logger = logging.getLogger(__name__)

# Sentry records these as issue-stream activities and uses them to notify
# Sentry Apps. Names match ``SentryAppEventType`` in Sentry's open-source tree.
ROOT_CAUSE_STARTED = "root_cause_started"
ROOT_CAUSE_COMPLETED = "root_cause_completed"
SOLUTION_STARTED = "solution_started"
SOLUTION_COMPLETED = "solution_completed"
CODING_STARTED = "coding_started"
CODING_COMPLETED = "coding_completed"
PR_CREATED = "pr_created"
PR_READY_FOR_REVIEW = "pr_ready_for_review"
ITERATION_STARTED = "iteration_started"
ITERATION_COMPLETED = "iteration_completed"
ERRORED = "errored"
STOPPED = "stopped"

_STEP_EVENTS = {
    "root_cause": (ROOT_CAUSE_STARTED, ROOT_CAUSE_COMPLETED),
    "solution": (SOLUTION_STARTED, SOLUTION_COMPLETED),
    "code_changes": (CODING_STARTED, CODING_COMPLETED),
    "pr_iteration": (ITERATION_STARTED, ITERATION_COMPLETED),
}


def _client(run: SeerRun) -> SentryRPCClient:
    settings = get_settings()
    return SentryRPCClient(
        base_url=settings.sentry_url,
        shared_secret=settings.seer_rpc_shared_secret,
        viewer_context=run.viewer_context,
        # Lifecycle signals are advisory. Use a short timeout and no retries so
        # an unreachable Sentry cannot stall the actual run.
        timeout=min(settings.seer_request_timeout, 5.0),
        retries=1,
    )


def _enabled() -> bool:
    return get_settings().seer_webhooks_enabled


def step_started_event(step: str) -> str | None:
    pair = _STEP_EVENTS.get(step)
    return pair[0] if pair else None


def step_completed_event(step: str) -> str | None:
    pair = _STEP_EVENTS.get(step)
    return pair[1] if pair else None


def _base_payload(run: SeerRun) -> dict[str, Any]:
    payload: dict[str, Any] = {"run_id": run.id}
    if run.group_id is not None:
        payload["group_id"] = run.group_id
    if run.project_id is not None:
        payload["project_id"] = run.project_id
    if run.sentry_run_id:
        payload["sentry_run_id"] = run.sentry_run_id
    return payload


def emit_webhook(run: SeerRun, event_name: str, extra: dict[str, Any] | None = None) -> None:
    """Best-effort Seer -> Sentry lifecycle webhook.

    Sentry treats a missing webhook as a lost activity, not as a failed run, so
    transport or permission errors are logged and swallowed.
    """
    if not _enabled():
        return
    payload = {**_base_payload(run), **(extra or {})}
    try:
        with _client(run) as rpc:
            rpc.send_seer_webhook(
                event_name=event_name,
                organization_id=run.org_id,
                payload=payload,
            )
    except Exception as exc:
        logger.info("Seer webhook %s failed for run %s: %s", event_name, run.id, exc)


def emit_step_started(run: SeerRun, step: str) -> None:
    event = step_started_event(step)
    if event:
        emit_webhook(run, event)


def emit_step_completed(
    run: SeerRun, step: str, extra: dict[str, Any] | None = None
) -> None:
    event = step_completed_event(step)
    if event:
        emit_webhook(run, event, extra)


def pull_request_entry(repo: RepoDefinition, state: dict[str, Any]) -> dict[str, Any]:
    pr_number = state.get("pr_number")
    if pr_number is None:
        pr_number = _pr_number_from_url(state.get("pr_url"))
    return {
        "repo_name": repo.full_name,
        "provider": "gitlab" if repo.is_gitlab else "github",
        "repo_external_id": repo.external_id,
        "pull_request": {
            "pr_number": pr_number,
            "pr_url": state.get("pr_url"),
        },
    }


def pr_entries_from_states(states: dict[str, Any]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for repo_name, state in (states or {}).items():
        if not isinstance(state, dict):
            continue
        pr_number = state.get("pr_number")
        if pr_number is None:
            pr_number = _pr_number_from_url(state.get("pr_url"))
        entries.append(
            {
                "repo_name": repo_name,
                "provider": state.get("provider") or "github",
                "repo_external_id": state.get("repo_external_id"),
                "pull_request": {
                    "pr_number": pr_number,
                    "pr_url": state.get("pr_url"),
                },
            }
        )
    return entries


def notify_pr_created(
    run: SeerRun, entries: list[dict[str, Any]]
) -> None:
    """Notify Sentry about newly opened PRs (attribution + run linking)."""
    if not entries or not _enabled():
        return
    try:
        with _client(run) as rpc:
            rpc.notify_seer_pr_created(
                organization_id=run.org_id,
                run_id=run.id,
                pull_requests=entries,
                group_id=run.group_id,
            )
    except Exception as exc:
        logger.info("Seer PR notification failed for run %s: %s", run.id, exc)


def emit_pr_created(
    run: SeerRun, entries: list[dict[str, Any]], *, draft: bool = False
) -> None:
    if not entries:
        return
    emit_webhook(run, PR_CREATED, {"pull_requests": entries})
    if not draft:
        emit_webhook(run, PR_READY_FOR_REVIEW, {"pull_requests": entries})


def deliver_feature_result(
    run: SeerRun, *, status: str, error: str | None = None
) -> None:
    """Deliver a single-run feature result to Sentry.

    Sentry's ``deliver_feature_result`` RPC marks the matching ``SeerAgentRun``
    complete (or errored). Step-by-step autofix uses the completion hook
    instead, so only single runs are delivered here.
    """
    if (
        not _enabled()
        or run.feature_id != "autofix_single_run"
        or not run.sentry_run_id
    ):
        return
    result = {
        "run_id": run.id,
        "status": run.status,
        "artifacts": [
            artifact
            for item in (run.blocks or [])
            for artifact in (item.get("artifacts") or [])
        ],
        "repo_pr_states": run.repo_pr_states or {},
    }
    try:
        with _client(run) as rpc:
            rpc.deliver_feature_result(
                organization_id=run.org_id,
                feature_id=run.feature_id,
                run_uuid=run.sentry_run_id,
                status=status,
                result=result,
                error=error,
            )
    except Exception as exc:
        logger.info("Seer feature delivery failed for run %s: %s", run.id, exc)


def _pr_number_from_url(url: Any) -> int | None:
    if not url:
        return None
    try:
        from urllib.parse import urlparse

        path = urlparse(str(url)).path.rstrip("/")
        return int(path.rsplit("/", 1)[-1])
    except (ValueError, IndexError):
        return None
