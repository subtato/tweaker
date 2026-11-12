from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from ..models import SeerRun


def iso_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def artifact(key: str, data: dict[str, Any] | None, reason: str) -> dict[str, Any]:
    return {"key": key, "data": data, "reason": reason}


def block(
    *,
    step: str,
    content: str,
    artifacts: list[dict[str, Any]] | None = None,
    file_patches: list[dict[str, Any]] | None = None,
    metadata: dict[str, str] | None = None,
    todos: list[dict[str, Any]] | None = None,
    role: str = "assistant",
    tool_calls: list[dict[str, Any]] | None = None,
    tool_results: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {
        "role": role,
        "content": content,
        "metadata": {"step": step, **(metadata or {})},
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "id": uuid.uuid4().hex,
        "message": message,
        "timestamp": iso_now(),
        "loading": False,
        "artifacts": artifacts or [],
        "file_patches": file_patches or [],
        "merged_file_patches": file_patches or [],
        "todos": todos or [],
        "tool_results": tool_results or [],
    }


def append_block(run: SeerRun, item: dict[str, Any]) -> None:
    run.blocks = [*(run.blocks or []), item]


def set_status(run: SeerRun, status: str, failure_reason: str | None = None) -> None:
    run.status = status
    run.failure_reason = failure_reason
    run.last_triggered_at = datetime.now(UTC)


def wire_state(run: SeerRun) -> dict[str, Any]:
    updated = run.updated_at or datetime.now(UTC)
    if updated.tzinfo is None:
        updated = updated.replace(tzinfo=UTC)
    session = {
        "run_id": run.id,
        "blocks": run.blocks or [],
        "status": run.status,
        "failure_reason": run.failure_reason,
        "updated_at": updated.isoformat().replace("+00:00", "Z"),
        "owner_user_id": (run.run_metadata or {}).get("owner_user_id"),
        "pending_user_input": (run.run_metadata or {}).get("pending_user_input"),
        "repo_pr_states": run.repo_pr_states or {},
        "coding_agents": run.coding_agents or {},
        "queued_feedback": run.queued_feedback or [],
        "metadata": run.run_metadata or {},
        "usage": run.usage or {"usages": []},
    }
    return {
        "session": session,
        "feature_id": run.feature_id,
        "sentry_run_id": run.sentry_run_id,
    }


def summarize_state(run: SeerRun) -> dict[str, Any]:
    """Compact state used by the console UI."""
    return {
        "run_id": run.id,
        "feature_id": run.feature_id,
        "kind": run.kind,
        "status": run.status,
        "org_id": run.org_id,
        "group_id": run.group_id,
        "project_id": run.project_id,
        "title": run.title,
        "updated_at": run.updated_at,
        "blocks": len(run.blocks or []),
        "repo_pr_states": run.repo_pr_states or {},
    }
