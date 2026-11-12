from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import session_scope
from ..models import Issue, Organization, Repository, Run, RunStep, utcnow
from .classifier import assess_fixability, heuristic_fixability
from .codebase import CodebaseContext, RepositoryWorkspace
from .ingest import audit, get_repo, refresh_issue
from .llm import ProviderError, get_provider
from .llm.prompts import INVESTIGATION_SYSTEM, build_investigation_prompt
from .llm.schema import Diagnosis
from .patch import check_applies, strip_code_fences, validate_patch
from .publishing import PublishError, publish_pull_request
from .redaction import compact_event, error_summary, in_app_frames
from .sentry import SentryError

logger = logging.getLogger(__name__)


class RunCancelled(RuntimeError):
    pass


def create_run(
    session: Session,
    *,
    org: Organization,
    issue: Issue | None,
    kind: str = "investigate",
    trigger: str = "manual",
    repo_id: str | None = None,
    publish: bool = False,
    actor: str = "user",
) -> Run:
    provider = get_provider(org)
    run = Run(
        org_id=org.id,
        issue_id=issue.id if issue else None,
        repo_id=repo_id or (issue.repo_id if issue else None),
        kind=kind,
        status="queued",
        trigger=trigger,
        provider=provider.name,
        model=provider.model,
        run_metadata={"publish": publish, "actor": actor},
    )
    session.add(run)
    session.flush()
    session.add(
        RunStep(
            run_id=run.id,
            seq=1,
            kind="queue",
            title="Queued investigation",
            status="succeeded",
            body="Tweaker accepted the run and is gathering context.",
        )
    )
    audit(
        session,
        org.id,
        "run.created",
        actor=actor,
        target_type="run",
        target_id=run.id,
        metadata={"issue_id": issue.id if issue else None, "kind": kind, "trigger": trigger},
    )
    session.commit()
    return run


def run_investigation(run_id: str) -> None:
    """Execute an investigation. Synchronous by design: it is invoked from a
    worker thread so blocking HTTP and git calls never stall the API loop."""
    settings = get_settings()
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            logger.error("Run %s disappeared before execution", run_id)
            return
        if run.status == "cancelled":
            return
        org = session.get(Organization, run.org_id)
        issue = session.get(Issue, run.issue_id) if run.issue_id else None
        if org is None or issue is None:
            _fail_run(session, run, "The issue or organization no longer exists.")
            return
        repo = get_repo(session, org, run.repo_id)
        run.status = "running"
        run.started_at = utcnow()
        session.commit()

    try:
        _execute(run_id, org_id=org.id, issue_id=issue.id, repo_id=repo.id if repo else None, settings=settings)
    except RunCancelled:
        _mark_cancelled(run_id)
    except Exception as exc:
        logger.exception("Investigation %s failed", run_id)
        _fail_run_by_id(run_id, _safe_error(exc))
        return
    _finalise(run_id)


def _execute(run_id: str, *, org_id: str, issue_id: str, repo_id: str | None, settings: Any) -> None:
    with session_scope() as session:
        run = session.get(Run, run_id)
        org = session.get(Organization, org_id)
        issue = session.get(Issue, issue_id)
        repo = session.get(Repository, repo_id) if repo_id else None
        if run is None or org is None or issue is None:
            raise RuntimeError("Run context disappeared")
        if run.status == "cancelled":
            raise RunCancelled()

        # 1. Refresh Sentry context -------------------------------------------------
        step = _add_step(session, run, "context", "Collecting Sentry context", "running")
        related_events: list[dict[str, Any]] = []
        suspect_commits: list[dict[str, Any]] = []
        if org.sentry_token_encrypted:
            try:
                issue = refresh_issue(session, org, issue)
                suspect_commits = list((issue.issue_metadata or {}).get("suspect_commits") or [])
                _step_done(
                    session,
                    step,
                    f"Loaded {issue.short_id or issue.sentry_id}, latest event, and "
                    f"{len(suspect_commits)} suspect commit(s).",
                )
            except SentryError as exc:
                _step_done(session, step, f"Using cached issue context; Sentry refresh failed: {exc}", "warning")
        else:
            _step_done(session, step, "Using cached issue context (no Sentry token configured).", "warning")
        event = compact_event(issue.latest_event or {}, max_chars=settings.max_event_chars)
        if not event:
            raise RuntimeError(
                "No event payload is cached for this issue. Configure the Sentry connection and retry."
            )
        frames = in_app_frames(event)
        session.commit()

        # 2. Fixability -------------------------------------------------------------
        step = _add_step(session, run, "scan", "Scoring fixability", "running")
        preliminary = heuristic_fixability(issue, event)
        run.fixability_score = preliminary.score
        issue.fixability_score = preliminary.score
        issue.fixability_reason = preliminary.reason
        issue.fixability_signals = preliminary.signals
        issue.scanned_at = utcnow()
        _step_done(
            session,
            step,
            f"Fixability {preliminary.score}/100 — {preliminary.reason}",
            data={"score": preliminary.score, "signals": preliminary.signals},
        )
        session.commit()

        # 3. Source context ---------------------------------------------------------
        step = _add_step(session, run, "code", "Locating relevant source", "running")
        code_context = CodebaseContext()
        code_text = "(No repository is connected for this issue.)"
        if repo is not None:
            try:
                workspace = RepositoryWorkspace(repo)
                workspace.ensure()
                code_context = workspace.context_for_event(
                    event=event,
                    frames=frames,
                    error_text=error_summary(event),
                    suspect_commits=suspect_commits,
                    max_chars=settings.max_context_chars,
                )
                code_text = code_context.render(settings.max_context_chars)
                _step_done(
                    session,
                    step,
                    f"Read {len(code_context.snippets)} source snippet(s) from "
                    f"{len(code_context.files)} file(s).",
                    data=code_context.as_metadata(),
                )
            except Exception as exc:
                logger.warning("Codebase context failed for run %s: %s", run_id, exc)
                _step_done(session, step, f"Could not read repository: {exc}", "warning")
        else:
            _step_done(session, step, "No repository mapped; analyzing telemetry only.", "warning")
        session.commit()

        # 4. LLM diagnosis ----------------------------------------------------------
        step = _add_step(session, run, "analysis", "Analyzing root cause", "running")
        provider = get_provider(org)
        run.provider = provider.name
        run.model = provider.model
        prompt = build_investigation_prompt(
            issue=issue,
            event=event,
            code_context=code_text,
            suspect_commits=suspect_commits,
            related_events=related_events,
            repo=repo,
            mode="autofix" if run.kind == "autofix" else "investigate",
        )
        try:
            result = provider.complete_structured(
                system=INVESTIGATION_SYSTEM,
                prompt=prompt,
                schema=Diagnosis,
                max_tokens=12_000,
            )
        except ProviderError as exc:
            _step_failed(session, step, f"Model request failed: {exc}")
            raise RuntimeError(f"Model request failed: {exc}") from exc
        diagnosis = result.value
        run.input_tokens += result.usage.input_tokens
        run.output_tokens += result.usage.output_tokens
        run.cost_usd += result.usage.cost_usd
        run.summary = diagnosis.summary
        run.root_cause = diagnosis.root_cause
        run.confidence = max(0.0, min(1.0, diagnosis.confidence))
        run.run_metadata = {
            **(run.run_metadata or {}),
            "evidence": [item.model_dump() for item in diagnosis.evidence],
            "fix_plan": diagnosis.fix_plan,
            "tests": diagnosis.tests,
            "risk": diagnosis.risk,
            "needs_human": diagnosis.needs_human,
            "files": code_context.files,
        }
        _step_done(
            session,
            step,
            diagnosis.summary,
            data={"confidence": diagnosis.confidence, "risk": diagnosis.risk},
        )
        session.commit()

        # 5. Patch validation -------------------------------------------------------
        patch = strip_code_fences(diagnosis.patch or "")
        step = _add_step(session, run, "patch", "Validating proposed fix", "running")
        if patch:
            structural = validate_patch(patch, settings.max_patch_chars)
            if not structural.ok:
                run.run_metadata = {**(run.run_metadata or {}), "patch_error": structural.message}
                _step_done(session, step, f"Patch rejected: {structural.message}", "warning")
                patch = ""
            elif repo is not None:
                try:
                    workspace = RepositoryWorkspace(repo)
                    workspace.ensure()
                    applies = check_applies(patch, workspace)
                    if applies.ok:
                        _step_done(
                            session,
                            step,
                            f"Patch applies cleanly to {', '.join(applies.files)}.",
                            data={"files": applies.files},
                        )
                    else:
                        run.run_metadata = {**(run.run_metadata or {}), "patch_error": applies.message}
                        _step_done(
                            session,
                            step,
                            f"Patch could not be applied: {applies.message}",
                            "warning",
                        )
                        patch = ""
                except Exception as exc:
                    run.run_metadata = {**(run.run_metadata or {}), "patch_error": str(exc)}
                    _step_done(session, step, f"Could not validate patch: {exc}", "warning")
                    patch = ""
            else:
                _step_done(session, step, "Patch is structurally valid; no repository available to test apply.", "warning")
        else:
            _step_done(session, step, "No code patch was proposed for this issue.", "warning")
        run.patch = patch or None
        session.commit()

        # 6. Publish ---------------------------------------------------------------
        should_publish = bool((run.run_metadata or {}).get("publish")) and bool(patch) and repo is not None
        if should_publish:
            step = _add_step(session, run, "publish", "Opening pull request", "running")
            try:
                published = publish_pull_request(run, issue, repo, patch)
                run.pr_url = published.pr_url
                run.branch = published.branch
                run.run_metadata = {
                    **(run.run_metadata or {}),
                    "commit_sha": published.commit_sha,
                    "publish_message": published.message,
                }
                _step_done(
                    session,
                    step,
                    published.message + (f": {published.pr_url}" if published.pr_url else ""),
                    data={"branch": published.branch, "pr_url": published.pr_url},
                )
            except (PublishError, Exception) as exc:
                logger.warning("Publishing failed for run %s: %s", run_id, exc)
                run.run_metadata = {**(run.run_metadata or {}), "publish_error": _safe_error(exc)}
                _step_done(session, step, f"Could not publish: {_safe_error(exc)}", "warning")
        elif (run.run_metadata or {}).get("publish") and patch and repo is None:
            step = _add_step(session, run, "publish", "Opening pull request", "skipped")
            _step_done(session, step, "Connect a repository before publishing a fix.", "warning")
        session.commit()


def _finalise(run_id: str) -> None:
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None or run.status == "cancelled":
            return
        run.status = "succeeded"
        run.finished_at = utcnow()
        audit(
            session,
            run.org_id,
            "run.succeeded",
            target_type="run",
            target_id=run.id,
            metadata={"confidence": run.confidence, "has_patch": bool(run.patch)},
        )


def _fail_run(session: Session, run: Run, message: str) -> None:
    run.status = "failed"
    run.error = message
    run.finished_at = utcnow()
    session.add(
        RunStep(
            run_id=run.id,
            seq=_next_seq(session, run.id),
            kind="error",
            title="Investigation failed",
            status="failed",
            body=message,
        )
    )
    audit(session, run.org_id, "run.failed", target_type="run", target_id=run.id, metadata={"error": message})
    session.commit()


def _fail_run_by_id(run_id: str, message: str) -> None:
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            return
        if run.status == "cancelled":
            return
        _fail_run(session, run, message)


def _mark_cancelled(run_id: str) -> None:
    with session_scope() as session:
        run = session.get(Run, run_id)
        if run is None:
            return
        run.status = "cancelled"
        run.finished_at = utcnow()
        session.add(
            RunStep(
                run_id=run.id,
                seq=_next_seq(session, run.id),
                kind="cancelled",
                title="Investigation cancelled",
                status="skipped",
                body="The run was cancelled before completion.",
            )
        )


def _add_step(session: Session, run: Run, kind: str, title: str, status: str) -> RunStep:
    step = RunStep(
        run_id=run.id,
        seq=_next_seq(session, run.id),
        kind=kind,
        title=title,
        status=status,
    )
    session.add(step)
    session.flush()
    session.commit()
    return step


def _step_done(
    session: Session,
    step: RunStep,
    body: str,
    status: str = "succeeded",
    data: dict[str, Any] | None = None,
) -> None:
    step.body = body
    step.status = status
    step.data = data or {}
    session.commit()


def _step_failed(session: Session, step: RunStep, body: str) -> None:
    step.body = body
    step.status = "failed"
    session.commit()


def _next_seq(session: Session, run_id: str) -> int:
    from sqlalchemy import func

    current = session.scalar(select(func.max(RunStep.seq)).where(RunStep.run_id == run_id))
    return int(current or 0) + 1


def _safe_error(exc: Exception) -> str:
    if isinstance(exc, (SentryError, ProviderError, PublishError)):
        return str(exc)[:2000]
    return f"{type(exc).__name__}: {exc}"[:2000]


def scan_issue(session: Session, org: Organization, issue: Issue, *, use_llm: bool = True) -> Issue:
    """Lightweight scan that does not create an investigation run."""
    event = compact_event(issue.latest_event or {}, max_chars=20_000)
    repo = get_repo(session, org, issue.repo_id)
    code_text = ""
    if repo is not None:
        try:
            workspace = RepositoryWorkspace(repo)
            workspace.ensure()
            frames = in_app_frames(event)
            ctx = workspace.context_for_event(
                event=event,
                frames=frames,
                error_text=error_summary(event),
                max_chars=20_000,
            )
            code_text = ctx.render(20_000)
        except Exception as exc:
            logger.warning("Scan code context failed: %s", exc)
    assessment = assess_fixability(issue, event, code_text, use_llm=use_llm, org=org)
    issue.fixability_score = assessment.score
    issue.fixability_reason = assessment.reason
    issue.fixability_signals = assessment.signals
    issue.scanned_at = utcnow()
    session.commit()
    return issue
