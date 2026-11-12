from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..models import SeerAssistedQuery, SeerRun, SeerServiceMap
from ..services.llm import ProviderError, get_provider
from ..services.usage import check_quota
from .assisted import (
    assisted_query_state,
    compare_cohorts,
    create_assisted_query,
    execute_assisted_query,
    match_delegated_agent,
    store_coding_agent_states,
    translate_query,
    update_coding_agent_state,
)
from .assisted import (
    export_indexes as export_index_data,
)
from .auth import SeerContext, authenticate
from .codereview import execute_code_review
from .engine import (
    create_pr_for_run,
    create_run_record,
    execute_chat_run,
    execute_feature_run,
    fixability,
    summarize_issue,
)
from .repos import RepoDefinition
from .rpc import SentryRPCClient
from .state import append_block, block, wire_state
from .supergroups import cluster_lightweight, get_supergroup, get_supergroups_by_group_ids
from .wire import (
    AssistedQueryStartRequest,
    AssistedQueryStateRequest,
    ChatRequest,
    CodingAgentStateUpdateRequest,
    CompareCohortRequest,
    DelegatedAgentMatchRequest,
    ExportIndexesRequest,
    FeatureRunRequest,
    FixabilityRequest,
    GenericPayload,
    LlmGenerateRequest,
    OneShotRequest,
    PrStateRequest,
    ReposRequest,
    RunsByIdsRequest,
    ServiceMapUpdateRequest,
    StateRequest,
    StoreCodingAgentStatesRequest,
    SummarizeIssueRequest,
    SupergroupByGroupIdsRequest,
    SupergroupClusterRequest,
    SupergroupGetRequest,
    TranslateAgenticRequest,
    UpdateRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/v1", tags=["seer"])
router_v0 = APIRouter(tags=["seer"])
_background_tasks: set[asyncio.Task[Any]] = set()


def _org_id(auth: SeerContext, fallback: Any = None) -> int:
    value = auth.org_id if auth.org_id is not None else fallback
    try:
        value = int(value)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="organization_id is required") from None
    if value <= 0:
        raise HTTPException(status_code=400, detail="organization_id is required")
    return value


async def _schedule(job_name: str, *args: Any) -> None:
    settings = get_settings()
    if settings.runner == "arq":
        from arq import create_pool
        from arq.connections import RedisSettings

        pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
        try:
            await pool.enqueue_job(job_name, *args)
        finally:
            await pool.close()
        return
    fn = {
        "execute_feature_job": execute_feature_run,
        "execute_chat_job": execute_chat_run,
        "execute_pr_job": create_pr_for_run,
        "execute_assisted_job": execute_assisted_query,
        "execute_code_review_job": execute_code_review,
    }[job_name]
    task = asyncio.create_task(asyncio.to_thread(fn, *args))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)


def _load_run(run_id: int, auth: SeerContext) -> SeerRun:
    with session_scope() as session:
        run = session.get(SeerRun, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        if auth.org_id is not None and run.org_id != auth.org_id:
            raise HTTPException(status_code=404, detail="Run not found")
        session.expunge(run)
        return run


@router.get("/models")
def models(auth: SeerContext = Depends(authenticate)) -> dict[str, Any]:
    provider = get_provider()
    return {"models": [provider.model] if provider.model else []}


@router.post("/automation/agent/feature/run")
async def feature_run(
    payload: FeatureRunRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    data = payload.payload or {}
    org_id = _org_id(auth, data.get("organization_id"))
    allowed, reason = check_quota(str(org_id))
    if not allowed:
        raise HTTPException(status_code=402, detail=reason or "Usage budget exhausted")
    group_id = _as_int(data.get("group_id"))
    project_id = _as_int(data.get("project_id"))
    short_id = str(data.get("short_id") or group_id or "issue")
    title = str(data.get("title") or short_id)
    run = create_run_record(
        org_id=org_id,
        org_slug=str(data.get("organization_slug") or ""),
        project_id=project_id,
        group_id=group_id,
        feature_id=payload.feature_id,
        kind="feature",
        title=f"Autofix — {title}",
        payload={
            **data,
            "on_completion_hook": data.get("on_completion_hook"),
            "referrer": payload.referrer,
            "stopping_point": data.get("stopping_point"),
        },
        viewer_context=auth.viewer_header,
        sentry_run_id=payload.ref,
        external_idempotency_key=payload.external_idempotency_key or payload.ref,
        referrer=payload.referrer,
        user_id=auth.user_id,
    )
    await _schedule("execute_feature_job", run.id)
    return {"run_id": run.id, "sentry_run_id": run.sentry_run_id}


@router.post("/automation/explorer/chat")
async def explorer_chat(
    payload: ChatRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    org_id = _org_id(auth, payload.organization_id)
    allowed, reason = check_quota(str(org_id))
    if not allowed:
        raise HTTPException(status_code=402, detail=reason or "Usage budget exhausted")
    data = payload.model_dump()
    if payload.run_id is not None:
        run = _load_run(payload.run_id, auth)
        with session_scope() as session:
            existing = session.get(SeerRun, run.id)
            if existing is None:
                raise HTTPException(status_code=404, detail="Run not found")
            existing.payload = {**(existing.payload or {}), **data}
            existing.sentry_run_id = payload.ref or existing.sentry_run_id
            existing.status = "processing"
            existing.failure_reason = None
            session.commit()
        run_id = run.id
    else:
        group_id = _as_int(payload.metadata.get("group_id"))
        run = create_run_record(
            org_id=org_id,
            org_slug=str((payload.user_org_context or {}).get("org_slug") or ""),
            project_id=payload.project_id,
            group_id=group_id,
            feature_id=None,
            kind="explorer",
            title=payload.query[:120] or "Seer Agent",
            payload=data,
            viewer_context=auth.viewer_header,
            sentry_run_id=payload.ref,
            external_idempotency_key=payload.external_idempotency_key or payload.ref,
            referrer=str(payload.metadata.get("referrer") or "seer.explorer"),
            user_id=auth.user_id,
        )
        run_id = run.id
    await _schedule("execute_chat_job", run_id)
    return {"run_id": run_id, "sentry_run_id": payload.ref}


@router.post("/automation/explorer/state")
def explorer_state(
    payload: StateRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    return wire_state(_load_run(payload.run_id, auth))


@router.post("/automation/explorer/runs/by-ids")
def runs_by_ids(
    payload: RunsByIdsRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    data: dict[str, dict[str, Any]] = {}
    for run_id in payload.run_ids:
        try:
            run = _load_run(run_id, auth)
        except HTTPException:
            continue
        data[str(run_id)] = {"status": run.status, "run_id": run.id}
    return {"data": data}


@router.post("/automation/explorer/update", status_code=status.HTTP_202_ACCEPTED)
async def explorer_update(
    payload: UpdateRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    run = _load_run(payload.run_id, auth)
    payload_type = payload.payload.get("type")
    if payload_type == "interrupt":
        with session_scope() as session:
            current = session.get(SeerRun, payload.run_id)
            if current is not None:
                current.status = "completed"
                current.failure_reason = None
                append_block(
                    current,
                    block(step="chat", content="Run interrupted by the user."),
                )
                session.commit()
        return {"status": "accepted"}
    if payload_type == "user_input_response":
        with session_scope() as session:
            current = session.get(SeerRun, payload.run_id)
            if current is not None:
                append_block(
                    current,
                    block(
                        step="chat",
                        content=json.dumps(payload.payload.get("response_data") or {}, default=str),
                        role="user",
                    ),
                )
                current.status = "completed"
                session.commit()
        return {"status": "accepted"}
    if payload_type == "select_solution":
        with session_scope() as session:
            current = session.get(SeerRun, payload.run_id)
            if current is not None:
                current.run_metadata = {
                    **(current.run_metadata or {}),
                    "selected_solution": payload.payload,
                }
                append_block(
                    current,
                    block(
                        step="solution",
                        content="Solution selected. Ready to generate code changes.",
                        metadata={"referrer": run.referrer},
                    ),
                )
                session.commit()
        return {"status": "accepted"}
    if payload_type in {"create_pr", "create_branch"}:
        pr_payload = dict(payload.payload)
        if payload_type == "create_branch":
            pr_payload["open_pr"] = False
        # Mark the PR state before returning so Sentry's poll sees "creating"
        # rather than an empty map (which would look like "nothing to wait for").
        with session_scope() as session:
            current = session.get(SeerRun, payload.run_id)
            if current is not None:
                states = dict(current.repo_pr_states or {})
                wanted = payload.payload.get("repo_name")
                for repo in (current.run_metadata or {}).get("repos") or []:
                    if not isinstance(repo, dict):
                        continue
                    name = f"{repo.get('owner', '')}/{repo.get('name', '')}"
                    if wanted and name != wanted:
                        continue
                    states[name] = {
                        "repo_name": name,
                        "provider": "gitlab" if "gitlab" in str(repo.get("provider", "")) else "github",
                        "branch_name": None,
                        "pr_number": None,
                        "pr_url": None,
                        "pr_id": None,
                        "commit_sha": None,
                        "pr_creation_status": "creating",
                        "pr_creation_error": None,
                        "title": None,
                        "description": None,
                        "integration_id": repo.get("integration_id"),
                    }
                current.repo_pr_states = states
                session.commit()
        await _schedule("execute_pr_job", run.id, pr_payload)
        return {"status": "accepted"}
    return {"status": "accepted"}


@router.post("/automation/explorer/repos")
def explorer_repos(
    payload: ReposRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    run = _load_run(payload.run_id, auth)
    if run.project_id is None:
        return {"repos": []}
    settings = get_settings()
    with SentryRPCClient(viewer_context=run.viewer_context) as rpc:
        preference = rpc.get_project_preferences(run.org_id, run.project_id)
    repos = [
        RepoDefinition.from_wire(item)
        for item in preference.get("repositories") or []
        if isinstance(item, dict)
    ]
    return {
        "repos": [
            {
                "default_branch": repo.branch_name or "main",
                "external_id": repo.external_id,
                "has_read_access": True,
                "has_write_access": bool(
                    settings.gitlab_token if repo.is_gitlab else settings.github_token
                ),
                "name": repo.name,
                "owner": repo.owner,
                "provider": repo.provider,
                "repo_name": repo.full_name,
                "integration_id": _as_int(repo.integration_id),
            }
            for repo in repos
        ]
    }


@router.post("/automation/explorer/state/pr")
def state_pr(
    payload: PrStateRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    org_id = _org_id(auth, payload.organization_id)
    with session_scope() as session:
        runs = list(
            session.scalars(
                select(SeerRun)
                .where(SeerRun.org_id == org_id)
                .order_by(SeerRun.updated_at.desc())
                .limit(200)
            )
        )
        for run in runs:
            for state in (run.repo_pr_states or {}).values():
                if not isinstance(state, dict):
                    continue
                if str(state.get("pr_id") or "") == str(payload.pr_id) or str(
                    state.get("pr_url") or ""
                ) == str(payload.pr_id):
                    return wire_state(run)
    return {"session": None}


@router.post("/automation/summarize/issue")
async def summarize(
    payload: SummarizeIssueRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    org_id = _org_id(auth, payload.organization_id)
    return await asyncio.to_thread(
        summarize_issue,
        org_id=org_id,
        group_id=payload.group_id,
        project_id=payload.project_id,
        viewer=auth.viewer_header,
    )


@router.post("/automation/summarize/fixability")
async def fixability_score(
    payload: FixabilityRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    org_id = _org_id(auth, payload.organization_id)
    return fixability(
        org_id=org_id,
        group_id=payload.group_id,
        project_id=payload.project_id,
        viewer=auth.viewer_header,
        summary=payload.summary,
    )


@router.post("/llm/generate")
def llm_generate(
    payload: LlmGenerateRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    provider = get_provider()
    system = payload.system_prompt or "You are a concise, accurate assistant."
    try:
        text, usage = provider.complete_text(
            system=system,
            prompt=payload.prompt,
            max_tokens=payload.max_tokens,
        )
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {
        "content": text,
        "response": text,
        "usage": {
            "prompt_tokens": usage.input_tokens,
            "completion_tokens": usage.output_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
            "dollar_cost": usage.cost_usd,
            "model": provider.model,
        },
    }


@router.post("/automation/oneshot/run")
def oneshot(payload: OneShotRequest, auth: SeerContext = Depends(authenticate)) -> dict[str, Any]:
    from .oneshots import run_oneshot

    return run_oneshot(
        payload.oneshot_id,
        payload.payload,
        org_id=auth.org_id,
        viewer=auth.viewer_header,
    )


@router.post("/automation/explorer/index", status_code=status.HTTP_202_ACCEPTED)
def index_project(auth: SeerContext = Depends(authenticate)) -> dict[str, Any]:
    return {"status": "accepted"}


@router.post("/automation/explorer/index/org-repo-knowledge", status_code=status.HTTP_202_ACCEPTED)
def index_repo_knowledge(auth: SeerContext = Depends(authenticate)) -> dict[str, Any]:
    return {"status": "accepted"}


@router.post("/automation/explorer/index/org-project-knowledge", status_code=status.HTTP_202_ACCEPTED)
def index_project_knowledge(auth: SeerContext = Depends(authenticate)) -> dict[str, Any]:
    return {"status": "accepted"}


@router.post("/automation/explorer/index/sentry-knowledge", status_code=status.HTTP_202_ACCEPTED)
def index_sentry_knowledge(auth: SeerContext = Depends(authenticate)) -> dict[str, Any]:
    return {"status": "accepted"}


@router.post("/automation/explorer/export-indexes")
def export_indexes(
    payload: ExportIndexesRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    return export_index_data(payload.org_id)


def _as_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

@router_v0.post("/v0/issues/supergroups/cluster-lightweight")
def supergroup_cluster(
    payload: SupergroupClusterRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    return cluster_lightweight(payload.model_dump())


@router_v0.post("/v0/issues/supergroups/get")
def supergroup_get(
    payload: SupergroupGetRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    data = get_supergroup(payload.organization_id, payload.supergroup_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Supergroup not found")
    return data


@router_v0.post("/v0/issues/supergroups/get-by-group-ids")
def supergroup_by_group_ids(
    payload: SupergroupByGroupIdsRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    return get_supergroups_by_group_ids(payload.organization_id, payload.group_ids)


@router_v0.get("/v0/issues/similar-issues/grouping-record/delete/{project_id}")
def delete_grouping_records(project_id: int, auth: SeerContext = Depends(authenticate)) -> dict[str, Any]:
    from .similarity import delete_grouping_records as delete_records

    return {"deleted": delete_records(project_id), "project_id": project_id}


@router.post("/explorer/service-map/update", status_code=status.HTTP_202_ACCEPTED)
def service_map_update(
    payload: ServiceMapUpdateRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    with session_scope() as session:
        item = session.scalar(
            select(SeerServiceMap).where(SeerServiceMap.org_id == payload.organization_id)
        )
        if item is None:
            item = SeerServiceMap(
                org_id=payload.organization_id,
                nodes=payload.nodes,
                edges=payload.edges,
            )
            session.add(item)
        else:
            item.nodes = payload.nodes
            item.edges = payload.edges
        session.commit()
    return {"status": "accepted", "nodes": len(payload.nodes), "edges": len(payload.edges)}


@router.post("/assisted-query/start")
async def assisted_query_start(
    payload: AssistedQueryStartRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    org_id = _org_id(auth, payload.org_id)
    allowed, reason = check_quota(str(org_id))
    if not allowed:
        raise HTTPException(status_code=402, detail=reason or "Usage budget exhausted")
    item = create_assisted_query(
        org_id=org_id,
        org_slug=payload.org_slug,
        project_ids=payload.project_ids,
        natural_language_query=payload.natural_language_query,
        strategy=payload.strategy,
        options=payload.options,
        viewer_context=auth.viewer_header,
    )
    await _schedule("execute_assisted_job", item.id)
    return {"run_id": item.id}


@router.post("/assisted-query/state")
def assisted_query_state_route(
    payload: AssistedQueryStateRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    org_id = _org_id(auth, payload.organization_id)
    with session_scope() as session:
        item = session.get(SeerAssistedQuery, payload.run_id)
        if item is None or item.org_id != org_id:
            raise HTTPException(status_code=404, detail="Run not found")
        return assisted_query_state(item)


@router.post("/assisted-query/translate-agentic")
def assisted_query_translate(
    payload: TranslateAgenticRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    return translate_query(payload.natural_language_query, payload.strategy)


@router.post("/workflows/compare/cohort")
def compare_cohort_route(
    payload: CompareCohortRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    return compare_cohorts(payload.model_dump())


@router.post("/automation/coding-agent/state/update")
def coding_agent_state_update(
    payload: CodingAgentStateUpdateRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    ok = update_coding_agent_state(
        payload.agent_id,
        payload.updates.model_dump(exclude_none=True),
    )
    if not ok:
        raise HTTPException(status_code=404, detail="Coding agent not found")
    return {"status": "updated", "agent_id": payload.agent_id}


@router.post("/automation/coding-agent/state/set")
def coding_agent_state_set(
    payload: StoreCodingAgentStatesRequest, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    if not store_coding_agent_states(payload.run_id, payload.coding_agent_states):
        raise HTTPException(status_code=404, detail="Run not found")
    return {"status": "updated", "run_id": payload.run_id}


@router.post("/pr-metrics/delegated-agent-match")
def delegated_agent_match(
    payload: DelegatedAgentMatchRequest, auth: SeerContext = Depends(authenticate)
) -> Any:
    match = match_delegated_agent(payload.model_dump())
    if match is None:
        return Response(status_code=status.HTTP_202_ACCEPTED)
    return match


def _code_review_org(payload: GenericPayload, auth: SeerContext) -> int:
    data = payload.model_dump()
    inner = data.get("data") if isinstance(data.get("data"), dict) else {}
    bug = inner.get("bug_prediction_specific_information") if isinstance(inner, dict) else {}
    repo = inner.get("repo") if isinstance(inner, dict) else {}
    for value in (
        (bug or {}).get("organization_id"),
        (repo or {}).get("organization_id"),
        auth.org_id,
    ):
        try:
            if value is not None:
                return int(value)
        except (TypeError, ValueError):
            continue
    raise HTTPException(status_code=400, detail="organization_id is required")


@router.post("/code_review", status_code=status.HTTP_202_ACCEPTED)
@router.post("/code_review/check/rerun", status_code=status.HTTP_202_ACCEPTED)
@router.post("/code_review/review-request", status_code=status.HTTP_202_ACCEPTED)
async def code_review_accept(
    payload: GenericPayload, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    from .codereview import create_code_review

    org_id = _code_review_org(payload, auth)
    review = create_code_review(payload.model_dump(), org_id)
    await _schedule("execute_code_review_job", review.id)
    return {"status": "accepted", "review_id": review.id}


@router.post("/code_review/pr-closed")
def code_review_pr_closed(
    payload: GenericPayload, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    from .codereview import close_code_review

    close_code_review(payload.model_dump(), _code_review_org(payload, auth))
    return {"status": "ok"}


@router.post("/offboarding/repository")
def offboard_repository(
    payload: GenericPayload, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    return {"status": "ok"}


@router.post("/anomaly-detection/detect")
def anomaly_detect(
    payload: GenericPayload, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    from .anomaly import detect

    return detect(payload.model_dump())


@router.post("/anomaly-detection/store")
def anomaly_store(
    payload: GenericPayload, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    from .anomaly import store

    return store(payload.model_dump())


@router.post("/anomaly-detection/delete-alert-data")
def anomaly_delete(
    payload: GenericPayload, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    from .anomaly import delete

    return delete(payload.model_dump())


@router.post("/anomaly-detection/alert-data")
def anomaly_alert_data(
    payload: GenericPayload, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    from .anomaly import alert_data

    return alert_data(payload.model_dump())


@router_v0.post("/v0/issues/similar-issues")
def similar_issues_route(
    payload: GenericPayload, auth: SeerContext = Depends(authenticate)
) -> dict[str, Any]:
    from .similarity import similar_issues

    return similar_issues(payload.model_dump())
