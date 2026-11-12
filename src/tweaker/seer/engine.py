from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from sqlalchemy import select

from ..config import get_settings
from ..db import session_scope
from ..metrics import counter, observe
from ..models import SeerRun
from ..services.classifier import heuristic_fixability
from ..services.llm import get_provider
from ..services.llm.prompts import (
    ASK_SYSTEM,
    INVESTIGATION_SYSTEM,
    build_ask_prompt,
    build_investigation_prompt,
)
from ..services.llm.schema import AskAnswer, Diagnosis
from ..services.patch import check_applies, strip_code_fences, validate_patch
from ..services.usage import record_usage
from . import lifecycle
from .agent_loop import run_agent
from .context import IssueContext, load_issue_context
from .diff import unified_diff_to_patches
from .publish import create_pull_request
from .repos import RepoDefinition, RepoManager
from .rpc import SentryRPCClient
from .state import append_block, artifact, block, iso_now, set_status

logger = logging.getLogger(__name__)


def _rpc(run: SeerRun) -> SentryRPCClient:
    settings = get_settings()
    return SentryRPCClient(
        base_url=settings.sentry_url,
        shared_secret=settings.seer_rpc_shared_secret,
        viewer_context=run.viewer_context,
    )


def _run_metrics(kind: str, status: str, started: float) -> None:
    counter("tweaker_seer_runs_total", kind=kind, status=status)
    observe("tweaker_seer_run_duration_seconds", time.monotonic() - started, kind=kind)


def _record_usage(run: SeerRun, usage: Any) -> None:
    current = run.usage or {"usages": []}
    usages = list(current.get("usages") or [])
    usages.append(
        {
            "completion_tokens": usage.output_tokens,
            "prompt_tokens": usage.input_tokens,
            "total_tokens": usage.input_tokens + usage.output_tokens,
            "prompt_cache_read_tokens": 0,
            "prompt_cache_write_tokens": 0,
            "thinking_tokens": 0,
            "dollar_cost": usage.cost_usd,
            "model": getattr(usage, "model", ""),
        }
    )
    run.usage = {"usages": usages}
    counter(
        "tweaker_llm_tokens_total",
        usage.input_tokens + usage.output_tokens,
        model=getattr(run, "model", "") or "unknown",
    )
    try:
        record_usage(
            org_id=str(run.org_id),
            run_id=run.id,
            kind=run.kind,
            provider=getattr(run, "provider", ""),
            model=getattr(run, "model", ""),
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_usd=usage.cost_usd,
        )
    except Exception:
        logger.warning("Could not record usage for run %s", run.id, exc_info=True)


def _diagnose(
    ctx: IssueContext,
    *,
    mode: str = "investigate",
    max_tokens: int = 12_000,
    extra_context: str = "",
) -> tuple[Diagnosis, Any]:
    provider = get_provider()
    prompt = build_investigation_prompt(
        issue=ctx.issue_proxy(),
        event=ctx.compact_event,
        code_context=ctx.code_text or "(No repository context is available.)",
        suspect_commits=ctx.suspect_commits,
        repo=ctx.repo_proxy(),
        mode=mode,
    )
    if len(ctx.repos) > 1:
        prompt += (
            "\n\n<repositories>\n"
            + "\n".join(
                f"- {repo.full_name} ({repo.provider}, branch {repo.branch_name or 'default'})"
                for repo in ctx.repos
            )
            + "\n</repositories>\nUse these exact owner/name labels in `patches`."
        )
    if extra_context:
        prompt += (
            "\n\n<additional_context>\n"
            + extra_context[:20_000]
            + "\n</additional_context>\n"
            "Use this user or review context to improve the diagnosis. Keep the patch minimal."
        )
    result = provider.complete_structured(
        system=INVESTIGATION_SYSTEM,
        prompt=prompt,
        schema=Diagnosis,
        max_tokens=max_tokens,
    )
    return result.value, result.usage


def _root_cause_artifact(diagnosis: Diagnosis) -> dict[str, Any]:
    return artifact(
        "root_cause",
        {
            "one_line_description": diagnosis.summary,
            "five_whys": diagnosis.five_whys or [diagnosis.root_cause],
            "reproduction_steps": diagnosis.reproduction_steps or diagnosis.tests,
            "cause": diagnosis.root_cause,
        },
        "Root cause analysis generated from Sentry telemetry and repository context.",
    )


def _solution_artifact(diagnosis: Diagnosis) -> dict[str, Any]:
    steps = [
        {"title": f"Step {index}", "description": item}
        for index, item in enumerate(diagnosis.fix_plan or [], start=1)
    ]
    if not steps:
        steps = [{"title": "Implement fix", "description": diagnosis.root_cause}]
    return artifact(
        "solution",
        {"one_line_summary": diagnosis.summary, "steps": steps},
        "Remediation plan derived from the root cause.",
    )


def _repo_for_name(repos: list[RepoDefinition], name: str) -> RepoDefinition | None:
    normalized = name.strip().rstrip("/")
    for repo in repos:
        if normalized in {repo.full_name, f"{repo.owner}/{repo.name}"}:
            return repo
    return None


def _patch_pairs(
    diagnosis: Diagnosis, repos: list[RepoDefinition]
) -> list[tuple[RepoDefinition, str]]:
    pairs: list[tuple[RepoDefinition, str]] = []
    if diagnosis.patches:
        for item in diagnosis.patches:
            repo = _repo_for_name(repos, item.repo)
            patch = strip_code_fences(item.patch or "")
            if repo is not None and patch.strip():
                pairs.append((repo, patch))
        return pairs
    patch = strip_code_fences(diagnosis.patch or "")
    if patch.strip() and repos:
        pairs.append((repos[0], patch))
    return pairs


def _patches(diagnosis: Diagnosis, repos: list[RepoDefinition]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for repo, patch in _patch_pairs(diagnosis, repos):
        result.extend(unified_diff_to_patches(patch, repo.full_name))
    return result


def _validate(diagnosis: Diagnosis, ctx: IssueContext, repos: list[RepoDefinition]) -> str | None:
    pairs = _patch_pairs(diagnosis, repos)
    if diagnosis.patches and len(pairs) < len(
        [item for item in diagnosis.patches if (item.patch or "").strip()]
    ):
        return "The model returned a patch for an unknown repository."
    for repo, patch in pairs:
        structural = validate_patch(patch)
        if not structural.ok:
            return f"{repo.full_name}: {structural.message}"
        try:
            manager = RepoManager()
            workspace = manager.ensure(repo)
            checked = check_applies(patch, workspace)
            if not checked.ok:
                return f"{repo.full_name}: {checked.message}"
        except Exception as exc:
            logger.warning("Could not validate Seer patch for %s: %s", repo.full_name, exc)
            return f"{repo.full_name}: could not check patch against repository: {exc}"
    return None


def _hook_payload(run: SeerRun) -> dict[str, Any]:
    payload = run.payload or {}
    hook = payload.get("on_completion_hook")
    if isinstance(hook, dict):
        return hook
    return {}


def _call_hook(run: SeerRun) -> None:
    hook = _hook_payload(run)
    module_path = hook.get("module_path")
    if not module_path:
        return
    try:
        with _rpc(run) as rpc:
            rpc.call_on_completion_hook(
                module_path=str(module_path),
                organization_id=run.org_id,
                run_id=run.id,
            )
    except Exception as exc:
        logger.warning("Seer completion hook failed for run %s: %s", run.id, exc)
        run.run_metadata = {**(run.run_metadata or {}), "hook_error": str(exc)[:1000]}


def _context_for_run(
    run: SeerRun,
    branch_override: str | None = None,
    branch_overrides: dict[str, str] | None = None,
) -> IssueContext:
    if run.group_id is None:
        raise RuntimeError("This run has no Sentry issue")
    payload = run.payload or {}
    step_args = payload.get("step_args") if isinstance(payload.get("step_args"), dict) else {}
    repo_pins = payload.get("repo_pins") or step_args.get("repo_pins") or {}
    with _rpc(run) as rpc:
        return load_issue_context(
            rpc,
            org_id=run.org_id,
            group_id=run.group_id,
            project_id=run.project_id,
            issue_id=payload.get("group_id") or run.group_id,
            repo_pins=repo_pins if isinstance(repo_pins, dict) else None,
            branch_override=branch_override,
            branch_overrides=branch_overrides,
        )


def execute_feature_run(run_id: int) -> None:
    started = time.monotonic()
    with session_scope() as session:
        run = session.get(SeerRun, run_id)
        if run is None:
            return
        set_status(run, "processing")
        session.commit()
    try:
        with session_scope() as session:
            run = session.get(SeerRun, run_id)
            if run is None:
                return
            if run.feature_id == "autofix_single_run":
                _execute_single_run(run, session)
            else:
                _execute_step_run(run, session)
            if run.status != "error":
                set_status(run, "completed")
            _run_metrics(run.kind, run.status, started)
            session.commit()
        with session_scope() as session:
            run = session.get(SeerRun, run_id)
            if run is not None:
                _call_hook(run)
                lifecycle.deliver_feature_result(
                    run,
                    status="error" if run.status == "error" else "completed",
                    error=(run.run_metadata or {}).get("error"),
                )
                session.commit()
    except Exception as exc:
        logger.exception("Seer feature run %s failed", run_id)
        _run_metrics("feature", "error", started)
        with session_scope() as session:
            run = session.get(SeerRun, run_id)
            if run is not None:
                set_status(run, "error", "run_failed")
                append_block(
                    run,
                    block(
                        step=str((run.payload or {}).get("step") or "root_cause"),
                        content=f"Tweaker could not complete this run: {exc}",
                    ),
                )
                run.run_metadata = {**(run.run_metadata or {}), "error": str(exc)[:4000]}
                session.commit()
        try:
            with session_scope() as session:
                run = session.get(SeerRun, run_id)
                if run is not None:
                    lifecycle.emit_webhook(
                        run, lifecycle.ERRORED, {"error": str(exc)[:1000]}
                    )
                    _call_hook(run)
                    lifecycle.deliver_feature_result(
                        run, status="error", error=str(exc)[:4000]
                    )
                    session.commit()
        except Exception:
            logger.exception("Failure hook failed for Seer run %s", run_id)


def _execute_step_run(run: SeerRun, session: Any) -> None:
    payload = run.payload or {}
    step = str(payload.get("step") or "root_cause")
    branch_override = None
    branch_overrides: dict[str, str] = {}
    if step == "pr_iteration":
        states = run.repo_pr_states or {}
        for repo_name, state in states.items():
            if isinstance(state, dict) and state.get("branch_name"):
                branch_overrides[str(repo_name)] = str(state["branch_name"])
        if not branch_overrides:
            first_state = next((item for item in states.values() if isinstance(item, dict)), None)
            if first_state:
                branch_override = first_state.get("branch_name")
    lifecycle.emit_step_started(run, step)
    ctx = _context_for_run(
        run,
        branch_override=branch_override,
        branch_overrides=branch_overrides,
    )
    feedback = _feedback_text(payload) if step == "pr_iteration" else ""
    user_context = str(payload.get("user_context") or "")
    extra_context = "\n\n".join(part for part in (user_context, feedback) if part)
    diagnosis, usage = _diagnose(
        ctx,
        mode="autofix" if step in {"code_changes", "pr_iteration"} else "investigate",
        extra_context=extra_context,
    )
    session.commit()
    _record_usage(run, usage)
    run.run_metadata = {
        **(run.run_metadata or {}),
        "group_id": run.group_id,
        "repos": [asdict(repo) for repo in ctx.repos],
        "issue": {
            "short_id": ctx.short_id,
            "title": ctx.title,
            "permalink": ctx.issue.get("permalink"),
        },
        "code_files": ctx.code_files,
        "referrer": payload.get("referrer") or run.referrer,
        "stopping_point": payload.get("stopping_point"),
        "summary": diagnosis.summary,
        "root_cause": diagnosis.root_cause,
        "confidence": diagnosis.confidence,
    }

    if step == "root_cause":
        root_cause = _root_cause_artifact(diagnosis)
        append_block(
            run,
            block(
                step="root_cause",
                content=diagnosis.summary,
                artifacts=[root_cause],
                metadata={"referrer": run.referrer},
            ),
        )
        lifecycle.emit_step_completed(run, step, {"root_cause": root_cause["data"]})
        run.status = "completed"
    elif step == "solution":
        solution = _solution_artifact(diagnosis)
        append_block(
            run,
            block(
                step="solution",
                content=diagnosis.summary,
                artifacts=[solution],
                metadata={"referrer": run.referrer},
            ),
        )
        lifecycle.emit_step_completed(run, step, {"solution": solution["data"]})
        run.status = "completed"
    elif step in {"code_changes", "pr_iteration"}:
        patch_error = _validate(diagnosis, ctx, ctx.repos)
        if patch_error:
            run.run_metadata = {**(run.run_metadata or {}), "patch_error": patch_error}
            append_block(
                run,
                block(
                    step=step,
                    content=f"The proposed patch could not be applied: {patch_error}",
                ),
            )
            lifecycle.emit_webhook(run, lifecycle.ERRORED, {"error": patch_error})
            run.status = "error"
            run.failure_reason = "patch_validation_failed"
            return
        patches = _patches(diagnosis, ctx.repos)
        if not patches:
            append_block(
                run,
                block(
                    step=step,
                    content="No safe code change was produced for this issue.",
                ),
            )
            run.status = "completed"
            return
        append_block(
            run,
            block(
                step=step,
                content=diagnosis.summary,
                file_patches=patches,
                metadata={"referrer": run.referrer},
            ),
        )
        if step == "pr_iteration":
            lifecycle.emit_step_completed(
                run,
                step,
                {
                    "pull_requests": lifecycle.pr_entries_from_states(
                        run.repo_pr_states or {}
                    ),
                    "code_changes": patches,
                    "iteration_index": max(0, _iteration_index(run) - 1),
                },
            )
        else:
            lifecycle.emit_step_completed(run, step, {"code_changes": patches})
        run.status = "completed"
    else:
        append_block(
            run,
            block(step=step, content=f"Unsupported autofix step: {step}"),
        )
        run.status = "error"
        run.failure_reason = "unsupported_step"


def _execute_single_run(run: SeerRun, session: Any) -> None:
    payload = run.payload or {}
    stopping_point = str(
        payload.get("stopping_point")
        or ("open_pr" if payload.get("pr_internal_settings") else "code_changes")
    )
    ctx = _context_for_run(run)
    lifecycle.emit_step_started(run, "root_cause")
    diagnosis, usage = _diagnose(
        ctx,
        mode="autofix",
        extra_context=str(payload.get("user_context") or ""),
    )
    session.commit()
    _record_usage(run, usage)
    run.run_metadata = {
        **(run.run_metadata or {}),
        "group_id": run.group_id,
        "repos": [asdict(repo) for repo in ctx.repos],
        "issue": {
            "short_id": ctx.short_id,
            "title": ctx.title,
            "permalink": ctx.issue.get("permalink"),
        },
        "stopping_point": stopping_point,
        "referrer": run.referrer,
        "summary": diagnosis.summary,
        "root_cause": diagnosis.root_cause,
        "confidence": diagnosis.confidence,
    }
    root_cause = artifact(
        "single_run_root_cause",
        {
            "headline": diagnosis.summary,
            "cause": diagnosis.root_cause,
            "five_whys": diagnosis.five_whys or [diagnosis.root_cause],
            "reproduction_steps": diagnosis.reproduction_steps or diagnosis.tests,
        },
        "Single-run root cause generated from Sentry telemetry and repository context.",
    )
    append_block(
        run,
        block(
            step="root_cause",
            content=diagnosis.summary,
            artifacts=[root_cause],
            metadata={"referrer": run.referrer},
        ),
    )
    lifecycle.emit_step_completed(run, "root_cause", {"root_cause": root_cause["data"]})
    if stopping_point == "root_cause":
        run.status = "completed"
        return
    lifecycle.emit_step_started(run, "solution")
    solution = _solution_artifact(diagnosis)
    append_block(
        run,
        block(
            step="solution",
            content=diagnosis.summary,
            artifacts=[solution],
            metadata={"referrer": run.referrer},
        ),
    )
    lifecycle.emit_step_completed(run, "solution", {"solution": solution["data"]})
    if stopping_point == "solution":
        run.status = "completed"
        return

    lifecycle.emit_step_started(run, "code_changes")
    patch_error = _validate(diagnosis, ctx, ctx.repos)
    if patch_error:
        run.run_metadata = {**(run.run_metadata or {}), "patch_error": patch_error}
        append_block(run, block(step="code_changes", content=f"Patch validation failed: {patch_error}"))
        lifecycle.emit_webhook(run, lifecycle.ERRORED, {"error": patch_error})
        run.status = "error"
        run.failure_reason = "patch_validation_failed"
        return
    patches = _patches(diagnosis, ctx.repos)
    if patches:
        append_block(
            run,
            block(
                step="code_changes",
                content=diagnosis.summary,
                file_patches=patches,
                metadata={"referrer": run.referrer},
            ),
        )
    lifecycle.emit_step_completed(run, "code_changes", {"code_changes": patches})
    pairs = _patch_pairs(diagnosis, ctx.repos)
    if stopping_point != "open_pr" or not pairs:
        run.status = "completed"
        return

    pr_settings = (
        payload.get("pr_internal_settings")
        if isinstance(payload.get("pr_internal_settings"), dict)
        else {}
    )
    states = dict(run.repo_pr_states or {})
    draft = not bool(pr_settings.get("ready_for_review", True))
    failures = 0
    created: list[dict[str, Any]] = []
    for repo, patch in pairs:
        token = RepoManager().token_for(repo)
        try:
            result = create_pull_request(
                run=run,
                issue=ctx,
                repo=repo,
                patch=patch,
                token=token,
                body=pr_settings.get("description_suffix"),
                draft=draft,
            )
            states[repo.full_name] = _pr_state(repo, result, "completed", None)
            created.append(lifecycle.pull_request_entry(repo, states[repo.full_name]))
            _record_pr_commit_sha(run, repo.full_name, result.get("commit_sha"))
        except Exception as exc:
            logger.warning("Seer single-run PR failed for %s: %s", repo.full_name, exc)
            states[repo.full_name] = _pr_state(repo, {}, "error", str(exc))
            failures += 1
    run.repo_pr_states = states
    if created:
        lifecycle.notify_pr_created(run, created)
        lifecycle.emit_pr_created(run, created, draft=draft)
    if failures == len(pairs):
        run.status = "error"
        run.failure_reason = "pr_creation_failed"
        return
    run.status = "completed"


def _pr_state(
    repo: RepoDefinition,
    result: dict[str, Any],
    status: str,
    error: str | None,
) -> dict[str, Any]:
    pr_url = result.get("pr_url") or None
    return {
        "repo_name": repo.full_name,
        "provider": "gitlab" if repo.is_gitlab else "github",
        "repo_external_id": repo.external_id,
        "branch_name": result.get("branch_name"),
        "pr_number": _pr_number(pr_url),
        "pr_url": pr_url,
        "pr_id": str(_pr_number(pr_url)) if _pr_number(pr_url) else None,
        "commit_sha": result.get("commit_sha"),
        "pr_creation_status": status,
        "pr_creation_error": error,
        "title": None,
        "description": None,
        "integration_id": repo.integration_id,
    }


def _iteration_index(run: SeerRun) -> int:
    return sum(
        1
        for item in (run.blocks or [])
        if ((item.get("message") or {}).get("metadata") or {}).get("step")
        == "pr_iteration"
    )


def _record_pr_commit_sha(run: SeerRun, repo_name: str, sha: str | None) -> None:
    if not sha:
        return
    blocks = [dict(item) for item in (run.blocks or [])]
    for item in reversed(blocks):
        patches = item.get("merged_file_patches") or []
        if any(patch.get("repo_name") == repo_name for patch in patches):
            item["pr_commit_shas"] = {
                **(item.get("pr_commit_shas") or {}),
                repo_name: sha,
            }
            run.blocks = blocks
            return


def _pr_number(url: str | None) -> int | None:
    if not url:
        return None
    try:
        path = urlparse(url).path.rstrip("/")
        return int(path.rsplit("/", 1)[-1])
    except (ValueError, IndexError):
        return None


def _execute_agent_chat(run_id: int) -> None:
    started = time.monotonic()
    with session_scope() as session:
        run = session.get(SeerRun, run_id)
        if run is None:
            return
        payload = dict(run.payload or {})
        metadata = dict(run.run_metadata or {})
        pending = metadata.pop("pending_user_input", None)
        if isinstance(pending, dict):
            payload["pending_user_input"] = pending
            run.run_metadata = metadata
        question = str(payload.get("query") or "")
        append_block(run, block(step="chat", content=question, role="user"))
        session.commit()
        session.expunge(run)
    try:
        result = run_agent(run, question, payload)
        with session_scope() as session:
            current = session.get(SeerRun, run_id)
            if current is None:
                return
            for item in result.blocks:
                append_block(current, item)
            _record_usage(current, result.usage)
            if result.status == "awaiting_user_input":
                current.run_metadata = {
                    **(current.run_metadata or {}),
                    "agent_tool_calls": result.tool_calls,
                    "agent_metadata": result.metadata,
                    "pending_user_input": result.pending_user_input,
                }
                set_status(current, "awaiting_user_input")
                _run_metrics(current.kind, "awaiting_user_input", started)
                session.commit()
                return
            append_block(
                current,
                block(
                    step="chat",
                    content=result.answer or "I could not produce an answer from the available evidence.",
                    metadata={"referrer": current.referrer},
                ),
            )
            current.run_metadata = {
                **(current.run_metadata or {}),
                "agent_tool_calls": result.tool_calls,
                "agent_metadata": result.metadata,
                "pending_user_input": None,
            }
            set_status(current, "completed")
            _run_metrics(current.kind, "completed", started)
            session.commit()
        with session_scope() as session:
            current = session.get(SeerRun, run_id)
            if current is not None:
                _call_hook(current)
                session.commit()
    except Exception as exc:
        logger.exception("Seer agent run %s failed", run_id)
        _run_metrics("explorer", "error", started)
        with session_scope() as session:
            current = session.get(SeerRun, run_id)
            if current is not None:
                set_status(current, "error", "run_failed")
                append_block(
                    current,
                    block(step="chat", content=f"Tweaker could not answer: {exc}"),
                )
                current.run_metadata = {**(current.run_metadata or {}), "error": str(exc)[:4000]}
                session.commit()


def execute_chat_run(run_id: int) -> None:
    started = time.monotonic()
    with session_scope() as session:
        run = session.get(SeerRun, run_id)
        if run is None:
            return
        set_status(run, "processing")
        session.commit()
    if get_settings().agent_tool_loop and get_provider().name != "demo":
        _execute_agent_chat(run_id)
        return
    try:
        with session_scope() as session:
            run = session.get(SeerRun, run_id)
            if run is None:
                return
            payload = run.payload or {}
            question = str(payload.get("query") or "")
            append_block(
                run,
                block(step="chat", content=question, role="user"),
            )
            group_id = _group_id_from_payload(payload)
            context_parts: list[str] = []
            issue = None
            tool_calls: list[dict[str, Any]] = []
            tool_results: list[dict[str, Any]] = []
            searched_issues: list[dict[str, Any]] = []

            with _rpc(run) as rpc:
                if group_id:
                    try:
                        ctx = load_issue_context(
                            rpc,
                            org_id=run.org_id,
                            group_id=group_id,
                            project_id=run.project_id,
                        )
                        context_parts.append(_chat_context(ctx))
                        issue = ctx.issue_proxy()
                        tool_calls.append(
                            {
                                "id": "get_issue_details",
                                "function": "get_issue_details",
                                "args": json.dumps({"issue_id": group_id}),
                            }
                        )
                        tool_results.append(
                            {
                                "tool_call_id": "get_issue_details",
                                "tool_call_function": "get_issue_details",
                                "content": json.dumps(
                                    {
                                        "short_id": ctx.short_id,
                                        "title": ctx.title,
                                        "culprit": ctx.issue.get("culprit"),
                                        "event_count": ctx.issue.get("count"),
                                    },
                                    default=str,
                                )[:4000],
                            }
                        )
                        try:
                            ownership = rpc.get_issue_ownership(run.org_id, group_id)
                            if ownership:
                                context_parts.append(
                                    "Ownership:\n" + json.dumps(ownership, default=str)[:6000]
                                )
                                tool_calls.append(
                                    {
                                        "id": "get_issue_ownership",
                                        "function": "get_issue_ownership",
                                        "args": json.dumps({"issue_id": group_id}),
                                    }
                                )
                                tool_results.append(
                                    {
                                        "tool_call_id": "get_issue_ownership",
                                        "tool_call_function": "get_issue_ownership",
                                        "content": json.dumps(ownership, default=str)[:4000],
                                    }
                                )
                        except Exception as exc:
                            logger.info("Ownership context failed for run %s: %s", run.id, exc)
                    except Exception as exc:
                        logger.warning("Chat context failed for run %s: %s", run.id, exc)

                trace_id = _trace_id_from_text(question)
                if trace_id is None and any(
                    word in question.lower() for word in ("trace", "latency", "slow", "span")
                ):
                    contexts = (
                        ctx.compact_event.get("contexts")
                        if group_id and isinstance(ctx.compact_event, dict)
                        else {}
                    )
                    trace_ctx = contexts.get("trace") if isinstance(contexts, dict) else {}
                    if isinstance(trace_ctx, dict):
                        trace_id = trace_ctx.get("trace_id")
                if trace_id:
                    try:
                        waterfall = rpc.get_trace_waterfall(run.org_id, str(trace_id))
                        if waterfall:
                            context_parts.append(
                                "Trace waterfall:\n"
                                + json.dumps(waterfall, default=str)[:20_000]
                            )
                            tool_calls.append(
                                {
                                    "id": "get_trace_waterfall",
                                    "function": "get_trace_waterfall",
                                    "args": json.dumps({"trace_id": str(trace_id)}),
                                }
                            )
                            tool_results.append(
                                {
                                    "tool_call_id": "get_trace_waterfall",
                                    "tool_call_function": "get_trace_waterfall",
                                    "content": json.dumps(waterfall, default=str)[:6000],
                                }
                            )
                    except Exception as exc:
                        logger.info("Trace waterfall failed for run %s: %s", run.id, exc)

                _extra_agent_tools(
                    rpc,
                    run,
                    question,
                    ctx if group_id else None,
                    context_parts,
                    tool_calls,
                    tool_results,
                )

                wants_search = not group_id or any(
                    word in question.lower()
                    for word in ("similar", "related", "other issue", "which issue", "on-call", "fix first", "highest")
                )
                if wants_search:
                    try:
                        import re

                        short_match = re.search(r"\b[A-Z][A-Z0-9]+-\w+\b", question)
                        if short_match:
                            query = short_match.group(0)
                        elif "is:" in question:
                            query = question
                        else:
                            query = "is:unresolved"
                        project_ids = [run.project_id] if run.project_id else []
                        searched_issues = rpc.execute_issues_query(
                            org_id=run.org_id,
                            project_ids=project_ids,
                            query=query,
                            limit=8,
                        )
                        if searched_issues:
                            context_parts.append(_search_context(searched_issues))
                            tool_calls.append(
                                {
                                    "id": "search_issues",
                                    "function": "search_issues",
                                    "args": json.dumps({"query": query, "limit": 8}),
                                }
                            )
                            tool_results.append(
                                {
                                    "tool_call_id": "search_issues",
                                    "tool_call_function": "search_issues",
                                    "content": json.dumps(searched_issues[:8], default=str)[:6000],
                                }
                            )
                    except Exception as exc:
                        logger.info("Issue search failed for run %s: %s", run.id, exc)

                custom_tools = payload.get("custom_tools") or []
                if isinstance(custom_tools, list) and custom_tools:
                    selected = _select_custom_tool(question, custom_tools)
                    if selected is not None:
                        tool, args = selected
                        try:
                            result = rpc.call_custom_tool(
                                org_id=run.org_id,
                                module_path=str(tool.get("module_path") or ""),
                                params=args,
                            )
                            context_parts.append(
                                f"Custom tool {tool.get('name')} result:\n"
                                + json.dumps(result, default=str)[:10_000]
                            )
                            tool_calls.append(
                                {
                                    "id": f"custom:{tool.get('name')}",
                                    "function": str(tool.get("name") or "custom_tool"),
                                    "args": json.dumps(args, default=str),
                                }
                            )
                            tool_results.append(
                                {
                                    "tool_call_id": f"custom:{tool.get('name')}",
                                    "tool_call_function": str(tool.get("name") or "custom_tool"),
                                    "content": json.dumps(result, default=str)[:6000],
                                }
                            )
                        except Exception as exc:
                            logger.info("Custom tool %s failed: %s", tool.get("name"), exc)

            if payload.get("monitoring_providers"):
                context_parts.append(
                    "Connected monitoring providers:\n"
                    + json.dumps(payload.get("monitoring_providers"), default=str)[:8000]
                )
            context_text = "\n\n".join(context_parts)
            if not context_text:
                context_text = json.dumps(payload.get("user_org_context") or {}, indent=2)[:20_000]
            if tool_results:
                append_block(
                    run,
                    block(
                        step="chat",
                        content="",
                        role="tool_use",
                        tool_calls=tool_calls,
                        tool_results=tool_results,
                    ),
                )
            run.run_metadata = {
                **(run.run_metadata or {}),
                "searched_issues": [
                    {
                        "short_id": item.get("shortId") or item.get("short_id"),
                        "title": item.get("title"),
                        "permalink": item.get("permalink"),
                    }
                    for item in searched_issues[:8]
                ],
            }
            provider = get_provider()
            answer = provider.complete_structured(
                system=ASK_SYSTEM,
                prompt=build_ask_prompt(question, context_text, issue=issue),
                schema=AskAnswer,
                max_tokens=3000,
            )
            session.commit()
            _record_usage(run, answer.usage)
            artifacts: list[dict[str, Any]] = []
            artifact_key = payload.get("artifact_key")
            if artifact_key:
                artifact_data = _generate_artifact(
                    question,
                    context_text,
                    payload.get("artifact_schema"),
                )
                if artifact_data is not None:
                    artifacts.append(
                        artifact(
                            str(artifact_key),
                            artifact_data,
                            "Structured artifact generated by the Seer Agent.",
                        )
                    )
            append_block(
                run,
                block(
                    step="chat",
                    content=answer.value.answer,
                    artifacts=artifacts,
                    metadata={"referrer": run.referrer},
                ),
            )
            set_status(run, "completed")
            _run_metrics(run.kind, "completed", started)
            session.commit()
        with session_scope() as session:
            run = session.get(SeerRun, run_id)
            if run is not None:
                _call_hook(run)
                session.commit()
    except Exception as exc:
        logger.exception("Seer chat run %s failed", run_id)
        _run_metrics("explorer", "error", started)
        with session_scope() as session:
            run = session.get(SeerRun, run_id)
            if run is not None:
                set_status(run, "error", "run_failed")
                append_block(run, block(step="chat", content=f"Tweaker could not answer: {exc}"))
                session.commit()


def _chat_context(ctx: IssueContext) -> str:
    lines = [
        f"Issue: {ctx.short_id} — {ctx.title}",
        f"Culprit: {ctx.issue.get('culprit')}",
        f"Status: {ctx.issue.get('status')}",
        f"Level: {ctx.issue.get('level')}",
        f"Events: {ctx.issue.get('count')}",
        f"Latest error: {ctx.error_text}",
    ]
    tags = ctx.compact_event.get("tags") if isinstance(ctx.compact_event, dict) else {}
    if isinstance(tags, dict) and tags:
        lines.append(
            "Tags: "
            + ", ".join(f"{key}={value}" for key, value in list(tags.items())[:20])
        )
    if ctx.code_files:
        lines.append(f"Relevant files: {', '.join(ctx.code_files[:12])}")
    if ctx.code_text:
        lines.append("Source context:\n" + ctx.code_text[:30_000])
    if ctx.suspect_commits:
        lines.append(
            "Suspect commits:\n"
            + "\n".join(
                f"- {str(item.get('id') or '')[:10]} {str(item.get('message') or '').splitlines()[0][:200]}"
                for item in ctx.suspect_commits[:8]
            )
        )
    return "\n".join(lines)


def _search_context(items: list[dict[str, Any]]) -> str:
    lines = ["Matching Sentry issues:"]
    for item in items[:8]:
        project = item.get("project") if isinstance(item.get("project"), dict) else {}
        short = item.get("shortId") or item.get("short_id") or item.get("id")
        lines.append(
            "### {short} — {title}\n"
            "- project: {project}\n"
            "- events: {count}\n"
            "- last seen: {last}\n"
            "- culprit: {culprit}\n"
            "- url: {url}".format(
                short=short,
                title=str(item.get("title") or "")[:300],
                project=project.get("slug") or item.get("projectSlug") or "",
                count=item.get("count") or item.get("event_count") or 0,
                last=item.get("lastSeen") or item.get("last_seen") or "",
                culprit=item.get("culprit") or "",
                url=item.get("permalink") or "",
            )
        )
    return "\n\n".join(lines)


def _extra_agent_tools(
    rpc: SentryRPCClient,
    run: SeerRun,
    question: str,
    ctx: IssueContext | None,
    context_parts: list[str],
    tool_calls: list[dict[str, Any]],
    tool_results: list[dict[str, Any]],
) -> None:
    import re

    lower = question.lower()
    trace_id = _trace_id_from_text(question)
    project_id = run.project_id or (ctx.project_id if ctx else None)

    def add(name: str, args: dict[str, Any], result: Any) -> None:
        context_parts.append(f"{name} result:\n{json.dumps(result, default=str)[:12_000]}")
        tool_calls.append(
            {"id": name, "function": name, "args": json.dumps(args, default=str)}
        )
        tool_results.append(
            {
                "tool_call_id": name,
                "tool_call_function": name,
                "content": json.dumps(result, default=str)[:6000],
            }
        )

    try:
        if "replay" in lower:
            match = re.search(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", lower)
            if match:
                replay_id = match.group(0)
                data = rpc.get_replay_metadata(run.org_id, replay_id)
                if data:
                    add("get_replay_metadata", {"replay_id": replay_id}, data)
                if project_id:
                    logs = rpc.get_replay_summary_logs(project_id, replay_id)
                    if logs:
                        add(
                            "get_replay_summary_logs",
                            {"project_id": project_id, "replay_id": replay_id},
                            logs,
                        )
        if "profile" in lower or "flamegraph" in lower:
            match = re.search(r"\b[0-9a-f]{8,32}\b", lower)
            if match:
                profile_id = match.group(0)
                data = rpc.get_profile_flamegraph(
                    run.org_id,
                    profile_id,
                    trace_id=trace_id,
                )
                if data:
                    add("get_profile_flamegraph", {"profile_id": profile_id}, data)
        if trace_id and "log" in lower:
            data = rpc.get_log_attributes_for_trace(run.org_id, trace_id)
            if data:
                add("get_log_attributes_for_trace", {"trace_id": trace_id}, data)
        if "metric" in lower:
            if trace_id:
                data = rpc.get_metric_attributes_for_trace(run.org_id, trace_id)
                if data:
                    add("get_metric_attributes_for_trace", {"trace_id": trace_id}, data)
            else:
                data = rpc.get_metric_metadata(
                    run.org_id,
                    [project_id] if project_id else [],
                    [word for word in re.findall(r"[a-zA-Z_.:-]{4,}", question)][:5],
                )
                if data:
                    add("get_metric_metadata", {"project_ids": [project_id]}, data)
        if ctx and "baseline" in lower:
            tags = list((ctx.compact_event.get("tags") or {}).keys())[:10]
            data = rpc.get_baseline_tag_distribution(
                run.org_id,
                ctx.project_id,
                ctx.group_id,
                tags,
            )
            if data:
                add("get_baseline_tag_distribution", {"tag_keys": tags}, data)
        if ctx and ("assign" in lower or "owner" in lower):
            data = rpc.get_group_assignees(run.org_id, [ctx.group_id])
            if data:
                add("get_group_assignees", {"group_ids": [ctx.group_id]}, data)
        if ctx and "dsn" in lower:
            project = ctx.issue.get("project") if isinstance(ctx.issue.get("project"), dict) else {}
            slug = project.get("slug")
            if slug:
                data = rpc.get_dsn(run.org_id, str(slug))
                if data:
                    add("get_dsn", {"project_slug": slug}, data)
    except Exception as exc:
        logger.info("Extra agent tools failed for run %s: %s", run.id, exc)


def _generate_artifact(
    question: str,
    context: str,
    schema: dict[str, Any] | None,
) -> dict[str, Any] | None:
    if not isinstance(schema, dict):
        return None
    prompt = (
        "Return JSON matching this schema. Use only the supplied context. "
        "Do not include markdown fences.\n\n"
        f"Schema: {json.dumps(schema)[:12_000]}\n\n"
        f"<untrusted_context>\n{context[:30_000]}\n</untrusted_context>\n\n"
        f"Question: {question}"
    )
    try:
        text, _ = get_provider().complete_text(
            system="You produce strict JSON artifacts from debugging context.",
            prompt=prompt,
            max_tokens=3000,
        )
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(text[start : end + 1])
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.info("Artifact generation failed: %s", exc)
        return None


def _select_custom_tool(
    question: str, tools: list[dict[str, Any]]
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    descriptions = []
    for tool in tools[:12]:
        descriptions.append(
            {
                "name": tool.get("name"),
                "description": tool.get("description"),
                "params": tool.get("param_schema"),
            }
        )
    prompt = (
        "Decide whether one of these tools should be called to answer the question. "
        "Return JSON only: {\"tool\": <name or null>, \"args\": {...}}. "
        "If no tool is needed, return {\"tool\": null, \"args\": {}}.\n\n"
        f"Tools: {json.dumps(descriptions, default=str)}\nQuestion: {question}"
    )
    try:
        text, _ = get_provider().complete_text(
            system="You select tools carefully and never invent arguments.",
            prompt=prompt,
            max_tokens=500,
        )
        start = text.find("{")
        end = text.rfind("}")
        if start < 0 or end <= start:
            return None
        data = json.loads(text[start : end + 1])
        name = data.get("tool")
        args = data.get("args") if isinstance(data.get("args"), dict) else {}
        for tool in tools:
            if tool.get("name") == name:
                return tool, args
    except Exception as exc:
        logger.info("Custom tool selection failed: %s", exc)
    return None


def _trace_id_from_text(text: str) -> str | None:
    import re

    match = re.search(r"\b[0-9a-f]{32}\b", text.lower())
    return match.group(0) if match else None


def _feedback_text(payload: dict[str, Any]) -> str:
    step_args = payload.get("step_args") if isinstance(payload.get("step_args"), dict) else {}
    raw = step_args.get("feedback")
    if not raw:
        return ""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return raw[:10_000]
    items = raw if isinstance(raw, list) else [raw]
    lines: list[str] = []
    for item in items:
        if isinstance(item, dict):
            text = item.get("text") or item.get("message") or item.get("body")
            source = item.get("source")
            if text:
                prefix = ""
                if isinstance(source, dict):
                    prefix = f"[{source.get('type') or 'feedback'}] "
                lines.append(prefix + str(text)[:4000])
        elif isinstance(item, str):
            lines.append(item[:4000])
    return "\n".join(lines)


def _group_id_from_payload(payload: dict[str, Any]) -> int | None:
    for key in ("group_id", "issue_id"):
        value = payload.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    for metadata in (payload.get("metadata") or {}, payload.get("query_metadata") or {}):
        for key in ("group_id", "issue_id"):
            value = metadata.get(key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
    page = payload.get("page_location") or {}
    for value in page.values() if isinstance(page, dict) else []:
        if isinstance(value, str) and value.isdigit():
            return int(value)
    context = str(payload.get("on_page_context") or "")
    import re

    match = re.search(r"/issues/(\d+)", context)
    if match:
        return int(match.group(1))
    return None


def summarize_issue(
    *,
    org_id: int,
    group_id: int,
    project_id: int | None,
    viewer: str | None,
    trace_tree: dict[str, Any] | None = None,
) -> dict[str, Any]:
    with SentryRPCClient(viewer_context=viewer) as rpc:
        ctx = load_issue_context(
            rpc,
            org_id=org_id,
            group_id=group_id,
            project_id=project_id,
            include_code=False,
        )
    extra = json.dumps(trace_tree, default=str)[:20_000] if trace_tree else ""
    diagnosis, _ = _diagnose(ctx, max_tokens=4_000, extra_context=extra)
    fixability = heuristic_fixability(ctx.issue_proxy(), ctx.compact_event, ctx.code_text)
    return {
        "group_id": str(group_id),
        "headline": diagnosis.summary,
        "whats_wrong": diagnosis.root_cause,
        "trace": ctx.error_text,
        "possible_cause": diagnosis.root_cause,
        "scores": {
            "possible_cause_confidence": diagnosis.confidence,
            "possible_cause_novelty": 0.5,
            "fixability_score": round(fixability.score / 100, 3),
        },
    }


def fixability(
    *,
    org_id: int,
    group_id: int,
    project_id: int | None,
    viewer: str | None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Fast fixability classifier.

    Sentry calls this with a ~600ms timeout, so it must not perform RPC or model
    calls. Use the summary Sentry already generated when available.
    """
    summary = summary or {}
    scores = summary.get("scores") if isinstance(summary.get("scores"), dict) else {}
    existing = scores.get("fixability_score") if scores else None
    if isinstance(existing, (int, float)):
        score = float(existing)
    elif summary.get("whats_wrong") or summary.get("possible_cause"):
        score = 0.68
    elif summary.get("trace"):
        score = 0.5
    else:
        score = 0.4
    return {
        "group_id": str(group_id),
        "headline": "Fast fixability estimate",
        "whats_wrong": summary.get("whats_wrong"),
        "trace": summary.get("trace"),
        "possible_cause": summary.get("possible_cause"),
        "scores": {
            "possible_cause_confidence": scores.get("possible_cause_confidence"),
            "possible_cause_novelty": scores.get("possible_cause_novelty"),
            "fixability_score": round(max(0.0, min(1.0, score)), 3),
        },
    }


def create_pr_for_run(run_id: int, payload: dict[str, Any]) -> None:
    with session_scope() as session:
        run = session.get(SeerRun, run_id)
        if run is None:
            return
        metadata = run.run_metadata or {}
        repos = metadata.get("repos") or []
        if not repos:
            run.status = "error"
            run.failure_reason = "no_repository"
            session.commit()
            return
        wanted = payload.get("repo_name")
        raw_repo = next(
            (item for item in repos if item.get("owner", "") + "/" + item.get("name", "") == wanted),
            repos[0],
        )
        repo = RepoDefinition.from_wire(raw_repo)
        patch = _latest_patch(run, repo.full_name)
        if not patch:
            run.status = "error"
            run.failure_reason = "no_code_changes"
            session.commit()
            return
        token = RepoManager().token_for(repo)
        existing_state = (run.repo_pr_states or {}).get(repo.full_name) or {}
        existing_pr_url = (
            existing_state.get("pr_url") if isinstance(existing_state, dict) else None
        )
        existing_branch = (
            existing_state.get("branch_name") if isinstance(existing_state, dict) else None
        )
        run.repo_pr_states = {
            **(run.repo_pr_states or {}),
            repo.full_name: _pr_state(repo, {}, "creating", None),
        }
        session.commit()
        issue = _issue_proxy_from_metadata(metadata)
        draft = not bool(payload.get("ready_for_review", True))
        try:
            result = create_pull_request(
                run=run,
                issue=issue,
                repo=repo,
                patch=patch,
                token=token,
                branch=existing_branch,
                base_branch=existing_branch or repo.branch_name,
                title=payload.get("title"),
                body=payload.get("pr_description_suffix"),
                draft=draft,
                existing_pr_url=existing_pr_url,
                open_pr=bool(payload.get("open_pr", True)),
            )
            state = _pr_state(repo, result, "completed", None)
            run.repo_pr_states = {
                **(run.repo_pr_states or {}),
                repo.full_name: state,
            }
            _record_pr_commit_sha(run, repo.full_name, result.get("commit_sha"))
            if state.get("pr_url") and not existing_pr_url:
                entries = [lifecycle.pull_request_entry(repo, state)]
                lifecycle.notify_pr_created(run, entries)
                lifecycle.emit_pr_created(run, entries, draft=draft)
        except Exception as exc:
            logger.warning("PR creation failed for Seer run %s: %s", run.id, exc)
            run.repo_pr_states = {
                **(run.repo_pr_states or {}),
                repo.full_name: _pr_state(repo, {}, "error", str(exc)[:2000]),
            }
            run.status = "error"
            run.failure_reason = "pr_creation_failed"
        session.commit()
        _call_hook(run)
        session.commit()


class _IssueProxy:
    def __init__(self, data: dict[str, Any]) -> None:
        self.short_id = str(data.get("short_id") or "")
        self.title = str(data.get("title") or "")
        self.permalink = str(data.get("permalink") or "")
        self.issue = {"permalink": self.permalink}


def _issue_proxy_from_metadata(metadata: dict[str, Any]) -> _IssueProxy:
    return _IssueProxy(metadata.get("issue") or {})


def _latest_patch(run: SeerRun, repo_name: str | None = None) -> str | None:
    for item in reversed(run.blocks or []):
        for patch in item.get("merged_file_patches") or []:
            if repo_name and patch.get("repo_name") != repo_name:
                continue
            diff = patch.get("diff")
            if diff:
                return str(diff)
    return None


def create_run_record(
    *,
    org_id: int,
    org_slug: str,
    project_id: int | None,
    group_id: int | None,
    feature_id: str | None,
    kind: str,
    title: str,
    payload: dict[str, Any],
    viewer_context: str | None,
    sentry_run_id: str | None,
    external_idempotency_key: str | None,
    referrer: str,
    user_id: int | None,
    run_uuid: str | None = None,
) -> SeerRun:
    from ..db import session_scope

    with session_scope() as session:
        existing = None
        if sentry_run_id:
            existing = session.scalar(
                select(SeerRun).where(SeerRun.sentry_run_id == sentry_run_id)
            )
        if existing is None and run_uuid:
            existing = session.scalar(select(SeerRun).where(SeerRun.uuid == run_uuid))
        if existing is None and external_idempotency_key:
            existing = session.scalar(
                select(SeerRun).where(SeerRun.external_idempotency_key == external_idempotency_key)
            )
        if existing is not None:
            existing.payload = payload
            existing.referrer = referrer
            existing.viewer_context = viewer_context or existing.viewer_context
            existing.sentry_run_id = sentry_run_id or existing.sentry_run_id
            existing.last_triggered_at = datetime.now(UTC)
            existing.status = "processing"
            existing.failure_reason = None
            session.commit()
            session.refresh(existing)
            return existing
        run = SeerRun(
            org_id=org_id,
            org_slug=org_slug,
            project_id=project_id,
            group_id=group_id,
            feature_id=feature_id,
            kind=kind,
            status="processing",
            title=title[:500],
            payload=payload,
            viewer_context=viewer_context,
            sentry_run_id=sentry_run_id,
            external_idempotency_key=external_idempotency_key,
            referrer=referrer,
            run_metadata={
                "group_id": group_id,
                "feature_id": feature_id,
                "referrer": referrer,
                "owner_user_id": user_id,
                "created_at": iso_now(),
            },
            blocks=[],
            repo_pr_states={},
            coding_agents={},
            queued_feedback=[],
            usage={"usages": []},
        )
        if run_uuid:
            run.uuid = run_uuid
        session.add(run)
        session.commit()
        session.refresh(run)
        return run
