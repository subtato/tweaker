from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..metrics import counter, observe
from ..models import SeerAssistedQuery, SeerRun
from ..services.llm import ProviderError, get_provider

logger = logging.getLogger(__name__)


def _fallback_query(natural_language_query: str, strategy: str) -> dict[str, Any]:
    query = natural_language_query.lower()
    translated = "is:unresolved"
    if "slow" in query or "latency" in query or "duration" in query:
        translated = "span.duration:>1s"
    elif "timeout" in query:
        translated = "message:timeout"
    elif "error" in query or "exception" in query:
        translated = "level:error"
    elif "release" in query:
        translated = "release:*"
    elif strategy.lower() == "issues":
        translated = "is:unresolved"
    return {
        "query": translated,
        "explanation": f"Translated the {strategy.lower()} question into a Sentry search query.",
        "confidence": 0.55,
        "strategy": strategy,
        "dataset": "spans" if strategy.lower() in {"traces", "logs", "metrics"} else "issues",
    }


def translate_query(natural_language_query: str, strategy: str = "Traces") -> dict[str, Any]:
    provider = get_provider()
    prompt = (
        "Translate this natural-language observability question into a Sentry search query. "
        "Return JSON with keys: query (string), explanation (string), confidence (0-1), "
        "dataset (string), strategy (string). Do not invent fields that do not exist.\n\n"
        f"Strategy: {strategy}\nQuestion: {natural_language_query}"
    )
    try:
        text, _ = provider.complete_text(
            system="You translate natural language into precise Sentry search queries.",
            prompt=prompt,
            max_tokens=500,
        )
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(text[start : end + 1])
            if isinstance(data, dict) and isinstance(data.get("query"), str):
                data.setdefault("explanation", "")
                data.setdefault("confidence", 0.5)
                data.setdefault("strategy", strategy)
                data.setdefault("dataset", "spans" if strategy.lower() == "traces" else "issues")
                return data
    except (ProviderError, ValueError, json.JSONDecodeError) as exc:
        logger.info("Assisted query translation failed, using fallback: %s", exc)
    return _fallback_query(natural_language_query, strategy)


def create_assisted_query(
    *,
    org_id: int,
    org_slug: str,
    project_ids: list[int],
    natural_language_query: str,
    strategy: str,
    options: dict[str, Any],
    viewer_context: str | None,
) -> SeerAssistedQuery:
    with session_scope() as session:
        item = SeerAssistedQuery(
            org_id=org_id,
            org_slug=org_slug,
            project_ids=project_ids,
            natural_language_query=natural_language_query,
            strategy=strategy,
            status="processing",
            current_step="translate",
            completed_steps=[],
            viewer_context=viewer_context,
            options=options,
        )
        session.add(item)
        session.commit()
        session.refresh(item)
        return item


def execute_assisted_query(run_id: int) -> None:
    started = time.monotonic()
    with session_scope() as session:
        item = session.get(SeerAssistedQuery, run_id)
        if item is None:
            return
        try:
            result = translate_query(item.natural_language_query, item.strategy)
            item.final_response = result
            item.status = "completed"
            item.current_step = None
            item.completed_steps = [
                {"key": "translate"},
                {"key": "validate"},
            ]
        except Exception as exc:
            item.status = "error"
            item.unsupported_reason = str(exc)[:2000]
            item.current_step = None
        counter("tweaker_seer_runs_total", kind="assisted_query", status=item.status)
        observe(
            "tweaker_seer_run_duration_seconds",
            time.monotonic() - started,
            kind="assisted_query",
        )
        session.commit()


def assisted_query_state(item: SeerAssistedQuery) -> dict[str, Any]:
    return {
        "session": {
            "run_id": item.id,
            "status": item.status,
            "current_step": {"key": item.current_step} if item.current_step else None,
            "completed_steps": item.completed_steps or [],
            "updated_at": item.updated_at.isoformat() if item.updated_at else "",
            "final_response": item.final_response,
            "unsupported_reason": item.unsupported_reason,
        }
    }


def compare_cohorts(payload: dict[str, Any]) -> dict[str, Any]:
    baseline = payload.get("baseline") or []
    outliers = payload.get("outliers") or []
    config = payload.get("config") or {}

    def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
        numeric: dict[str, list[float]] = {}
        for row in rows:
            for key, value in (row or {}).items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    numeric.setdefault(key, []).append(float(value))
        return {
            "count": len(rows),
            "metrics": {
                key: {
                    "mean": round(sum(values) / len(values), 6),
                    "min": min(values),
                    "max": max(values),
                }
                for key, values in numeric.items()
            },
        }

    return {
        "baseline": summarize(baseline),
        "outliers": summarize(outliers),
        "config": config,
        "meta": payload.get("meta") or {},
        "significant": bool(baseline and outliers),
    }


def update_coding_agent_state(agent_id: str, updates: dict[str, Any]) -> bool:
    with session_scope() as session:
        runs = list(session.scalars(select(SeerRun).order_by(SeerRun.updated_at.desc()).limit(200)))
        for run in runs:
            agents = dict(run.coding_agents or {})
            if agent_id not in agents:
                continue
            state = dict(agents[agent_id] or {})
            state.update({key: value for key, value in updates.items() if value is not None})
            agents[agent_id] = state
            run.coding_agents = agents
            session.commit()
            return True
    return False


def store_coding_agent_states(run_id: int, states: list[dict[str, Any]]) -> bool:
    with session_scope() as session:
        run = session.get(SeerRun, run_id)
        if run is None:
            return False
        mapped: dict[str, Any] = {}
        for state in states:
            if not isinstance(state, dict):
                continue
            key = str(state.get("id") or state.get("name") or f"agent_{len(mapped)}")
            mapped[key] = state
        run.coding_agents = mapped
        session.commit()
        return True


def match_delegated_agent(payload: dict[str, Any]) -> dict[str, Any] | None:
    org_id = int(payload.get("organization_id") or 0)
    head_branch = str(payload.get("head_branch") or "")
    pr_url = str(payload.get("pr_url") or "")
    with session_scope() as session:
        runs = list(
            session.scalars(
                select(SeerRun)
                .where(SeerRun.org_id == org_id)
                .order_by(SeerRun.updated_at.desc())
                .limit(500)
            )
        )
        for run in runs:
            for repo, state in (run.repo_pr_states or {}).items():
                if not isinstance(state, dict):
                    continue
                if head_branch and state.get("branch_name") == head_branch:
                    return {
                        "run_id": run.id,
                        "agent_id": f"seer-{run.id}",
                        "signal_type": "branch",
                        "match_path": repo,
                    }
                if pr_url and state.get("pr_url") == pr_url:
                    return {
                        "run_id": run.id,
                        "agent_id": f"seer-{run.id}",
                        "signal_type": "pr_url",
                        "match_path": repo,
                    }
    return None


def export_indexes(org_id: int) -> dict[str, Any]:
    return {"org_id": org_id, "version": 1, "tables": {}}


def extract_trace_id(text: str) -> str | None:
    match = re.search(r"\b[0-9a-f]{32}\b", text.lower())
    return match.group(0) if match else None
