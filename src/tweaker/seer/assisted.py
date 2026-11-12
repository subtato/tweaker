from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..metrics import counter, observe
from ..models import SeerAssistedQuery, SeerRun
from ..services.llm import ProviderError, get_provider
from .rpc import SentryRPCClient, SentryRPCError

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
            translated = translate_query(item.natural_language_query, item.strategy)
            item.current_step = "execute"
            session.commit()
            execution = _execute_translation(item, translated)
            item.final_response = {**translated, **execution}
            item.status = "completed"
            item.current_step = None
            item.completed_steps = [
                {"key": "translate"},
                {"key": "execute"},
                {"key": "validate"},
            ]
        except Exception as exc:
            logger.info("Assisted query %s execution failed: %s", run_id, exc)
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


def _dataset_for(translated: dict[str, Any], strategy: str) -> str:
    dataset = str(translated.get("dataset") or "").lower()
    if dataset:
        return dataset
    value = (strategy or "").lower()
    if value in {"logs", "metrics", "traces", "spans"}:
        return "spans" if value == "traces" else value
    return "issues"


def _execute_translation(
    item: SeerAssistedQuery, translated: dict[str, Any]
) -> dict[str, Any]:
    """Run the translated query and attach compact results.

    Execution is best-effort: an unavailable Sentry RPC must not make the
    assisted-query run fail, because the translated query is still useful.
    """
    dataset = _dataset_for(translated, item.strategy)
    query = str(translated.get("query") or "")
    options = item.options if isinstance(item.options, dict) else {}
    stats_period = str(options.get("stats_period") or "14d")
    if get_settings().demo_mode:
        return {
            "executed": False,
            "dataset": dataset,
            "note": "Query execution is disabled in demo mode.",
        }
    try:
        with SentryRPCClient(viewer_context=item.viewer_context) as rpc:
            if dataset in {"issues", "search_issues"}:
                rows = rpc.execute_issues_query(
                    org_id=item.org_id,
                    project_ids=item.project_ids,
                    query=query or "is:unresolved",
                    limit=int(options.get("limit") or 25),
                )
                return {
                    "executed": True,
                    "dataset": "issues",
                    "result_count": len(rows),
                    "results": rows[:25],
                }
            response = rpc.execute_timeseries_query(
                org_id=item.org_id,
                dataset=dataset,
                y_axes=[str(options.get("y_axis") or "count()")],
                query=query,
                group_by=list(options.get("group_by") or []),
                project_ids=item.project_ids,
                stats_period=stats_period,
                interval=options.get("interval"),
            )
            series_count = len(response) if isinstance(response, list) else len(response or {})
            return {
                "executed": True,
                "dataset": dataset,
                "series_count": series_count,
                "results": response,
            }
    except (SentryRPCError, OSError, ValueError) as exc:
        return {
            "executed": False,
            "dataset": dataset,
            "execution_error": str(exc)[:1000],
        }


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
        metrics: dict[str, dict[str, Any]] = {}
        for key, values in numeric.items():
            count = len(values)
            mean = sum(values) / count
            variance = (
                sum((value - mean) ** 2 for value in values) / (count - 1)
                if count > 1
                else 0.0
            )
            metrics[key] = {
                "mean": round(mean, 6),
                "min": min(values),
                "max": max(values),
                "count": count,
                "stdev": round(variance**0.5, 6),
            }
        return {"count": len(rows), "metrics": metrics}

    base = summarize(baseline)
    out = summarize(outliers)
    comparisons: dict[str, dict[str, Any]] = {}
    significant = False
    for key in set(base["metrics"]) & set(out["metrics"]):
        base_metric = base["metrics"][key]
        out_metric = out["metrics"][key]
        delta = out_metric["mean"] - base_metric["mean"]
        base_n = max(base_metric["count"], 1)
        out_n = max(out_metric["count"], 1)
        standard_error = (
            (base_metric["stdev"] ** 2) / base_n
            + (out_metric["stdev"] ** 2) / out_n
        ) ** 0.5
        if standard_error > 0:
            z_score: float | None = delta / standard_error
            metric_significant = abs(z_score) >= 1.96
        else:
            z_score = None
            metric_significant = delta != 0
        relative = delta / base_metric["mean"] if base_metric["mean"] else None
        comparisons[key] = {
            "delta": round(delta, 6),
            "relative_change": round(relative, 6) if relative is not None else None,
            "z_score": round(z_score, 4) if z_score is not None else None,
            "significant": metric_significant,
        }
        significant = significant or metric_significant

    return {
        "baseline": base,
        "outliers": out,
        "comparison": comparisons,
        "config": config,
        "meta": payload.get("meta") or {},
        "significant": significant,
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
    pull_request_id = payload.get("pull_request_id")
    repo_payload = payload.get("repo") if isinstance(payload.get("repo"), dict) else {}
    repo_filter = ""
    if repo_payload.get("owner") and repo_payload.get("name"):
        repo_filter = f"{repo_payload['owner']}/{repo_payload['name']}"
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
                if repo_filter and repo != repo_filter:
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
                if pull_request_id is not None and str(pull_request_id) in {
                    str(state.get("pr_id")),
                    str(state.get("pr_number")),
                }:
                    return {
                        "run_id": run.id,
                        "agent_id": f"seer-{run.id}",
                        "signal_type": "pull_request_id",
                        "match_path": repo,
                    }
    return None


def export_indexes(org_id: int) -> dict[str, Any]:
    return {"org_id": org_id, "version": 1, "tables": {}}


def extract_trace_id(text: str) -> str | None:
    match = re.search(r"\b[0-9a-f]{32}\b", text.lower())
    return match.group(0) if match else None
