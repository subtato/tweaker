from __future__ import annotations

import json
import logging
from typing import Any

from ..db import session_scope
from ..models import SeerRun
from ..services.llm import ProviderError, get_provider

logger = logging.getLogger(__name__)


def run_oneshot(
    oneshot_id: str,
    payload: dict[str, Any],
    *,
    org_id: int | None,
    viewer: str | None,
) -> dict[str, Any]:
    handler = {
        "chat_suggestions": _chat_suggestions,
        "issue_view_title": _issue_view_title,
        "agent_question": _agent_question,
        "inbound_filter_name": _inbound_filter_name,
        "autofix_issue_data_judge": _autofix_issue_data_judge,
    }.get(oneshot_id)
    if handler is None:
        return {"result": {}}
    try:
        return {"result": handler(payload, org_id=org_id, viewer=viewer)}
    except Exception:
        logger.exception("Seer one-shot %s failed", oneshot_id)
        return {"result": {}}


def _chat_suggestions(
    payload: dict[str, Any], *, org_id: int | None, viewer: str | None
) -> dict[str, Any]:
    route = str(payload.get("route") or "")
    page_context = str(payload.get("page_context") or "")
    actions_enabled = bool(payload.get("actions_enabled"))
    suggestions: list[dict[str, Any]] = []
    if "/issues/" in route or "issue" in page_context.lower():
        suggestions = [
            {"text": "Investigate this issue and explain the root cause", "kind": "question"},
            {"text": "What changed recently that could have caused this?", "kind": "question"},
            {"text": "Find similar unresolved issues", "kind": "question"},
            {"text": "Propose a minimal fix for this issue", "kind": "question"},
        ]
        if actions_enabled:
            suggestions.append(
                {"text": "Assign this issue to the right owner", "kind": "action", "action_type": "assign"}
            )
    elif "dashboard" in route or "dashboard" in page_context.lower():
        suggestions = [
            {"text": "Explain what this dashboard is showing", "kind": "question"},
            {"text": "Which widget has the biggest regression?", "kind": "question"},
            {"text": "Find anomalies in these metrics", "kind": "question"},
        ]
        if actions_enabled:
            suggestions.append(
                {"text": "Create an alert for this threshold", "kind": "action", "action_type": "create_alert"}
            )
    elif "trace" in route or "span" in page_context.lower():
        suggestions = [
            {"text": "Summarize this trace and identify the slowest span", "kind": "question"},
            {"text": "Which errors are connected to this trace?", "kind": "question"},
            {"text": "Find the root cause of the latency", "kind": "question"},
        ]
    else:
        suggestions = [
            {"text": "What should on-call look at first?", "kind": "question"},
            {"text": "Which unresolved issue is most fixable?", "kind": "question"},
            {"text": "Summarize the highest-volume error from the last 24 hours", "kind": "question"},
            {"text": "What changed in the last release that could cause new errors?", "kind": "question"},
        ]
    return {"suggestions": suggestions[:5]}


def _issue_view_title(
    payload: dict[str, Any], *, org_id: int | None, viewer: str | None
) -> dict[str, Any]:
    query = str(payload.get("query") or "").strip()
    if not query:
        return {"title": ""}
    provider = get_provider()
    prompt = (
        "Write a short, specific issue-search title (max 80 characters) for this query. "
        "Return only the title.\n\nQuery: " + query
    )
    try:
        text, _ = provider.complete_text(
            system="You write concise issue titles.",
            prompt=prompt,
            max_tokens=100,
        )
        title = text.strip().splitlines()[0][:120]
    except ProviderError:
        title = query[:80]
    return {"title": title}


def _agent_question(
    payload: dict[str, Any], *, org_id: int | None, viewer: str | None
) -> dict[str, Any]:
    run_id = payload.get("run_id")
    question = str(payload.get("question") or "")
    try:
        run_id_int = int(run_id)
    except (TypeError, ValueError):
        return {"answer": ""}
    with session_scope() as session:
        run = session.get(SeerRun, run_id_int)
        if run is None or (org_id is not None and run.org_id != org_id):
            return {"answer": ""}
        blocks = run.blocks or []
        run_meta = run.run_metadata or {}
    transcript = _transcript(blocks)
    if not transcript:
        return {"answer": ""}
    provider = get_provider()
    prompt = (
        "Answer the question about this Seer Autofix run using only the transcript. "
        "Be concise. If the transcript does not contain the answer, say so.\n\n"
        f"Run: {run.title}\nMetadata: {json.dumps(run_meta, default=str)[:4000]}\n\n"
        f"<untrusted_run_transcript>\n{transcript[:60_000]}\n</untrusted_run_transcript>\n\n"
        f"Question: {question}"
    )
    try:
        answer, _ = provider.complete_text(
            system="You summarize and explain debugging runs accurately.",
            prompt=prompt,
            max_tokens=800,
        )
    except ProviderError:
        answer = ""
    return {"answer": answer}


def _transcript(blocks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in blocks:
        message = item.get("message") or {}
        role = message.get("role") or "assistant"
        content = message.get("content")
        if content:
            lines.append(f"{role}: {content}")
        for result in item.get("tool_results") or []:
            if isinstance(result, dict):
                lines.append(
                    f"tool {result.get('tool_call_function')}: {str(result.get('content') or '')[:3000]}"
                )
        for artifact in item.get("artifacts") or []:
            if artifact.get("key") in {"root_cause", "single_run_root_cause", "solution"}:
                lines.append(f"artifact {artifact['key']}: {json.dumps(artifact.get('data'), default=str)[:4000]}")
        for patch in item.get("merged_file_patches") or []:
            lines.append(f"patch {patch.get('repo_name')}/{patch.get('patch', {}).get('path')}: {str(patch.get('diff'))[:4000]}")
    return "\n\n".join(lines)


def _inbound_filter_name(
    payload: dict[str, Any], *, org_id: int | None, viewer: str | None
) -> dict[str, Any]:
    data_type = str(payload.get("data_type") or "error")
    conditions = payload.get("conditions") or []
    bits: list[str] = []
    for condition in conditions[:6]:
        if isinstance(condition, dict):
            key = condition.get("key") or condition.get("name")
            value = condition.get("value")
            if key:
                bits.append(f"{key}={value}" if value is not None else str(key))
    name = f"Filter {data_type} by " + ", ".join(bits[:3]) if bits else f"{data_type.title()} filter"
    provider = get_provider()
    prompt = (
        "Name this Sentry inbound data filter in 3-8 words. Return only the name.\n"
        f"Data type: {data_type}\nConditions: {json.dumps(conditions, default=str)[:4000]}"
    )
    try:
        text, _ = provider.complete_text(
            system="You name data filters clearly and concisely.",
            prompt=prompt,
            max_tokens=60,
        )
        candidate = text.strip().splitlines()[0].strip()
        if candidate:
            name = candidate[:200]
    except ProviderError:
        pass
    return {"name": name}


def _autofix_issue_data_judge(
    payload: dict[str, Any], *, org_id: int | None, viewer: str | None
) -> dict[str, Any]:
    issue_data = payload.get("issue_data") or {}
    event = issue_data.get("event") if isinstance(issue_data, dict) else None
    has_stack = bool(
        isinstance(event, dict)
        and (event.get("entries") or event.get("exception") or event.get("stacktrace"))
    )
    return {
        "verdict": "fixable" if has_stack else "not_fixable",
        "confidence": "medium" if has_stack else "low",
        "reason": (
            "The supplied issue data includes a stack trace."
            if has_stack
            else "The supplied issue data has no usable stack trace."
        ),
        "model": get_provider().model,
    }
