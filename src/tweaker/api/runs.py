from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import Issue, Organization, Run, RunStep, utcnow
from ..schemas import PublishRequest, RunDetail, RunListResponse, RunStepOut, RunSummary
from ..services.ingest import audit, get_repo
from ..services.publishing import PublishError, publish_pull_request
from ..services.tasks import enqueue_run
from .issues import _summary as issue_summary

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_auth)])


@router.get("", response_model=RunListResponse)
def list_runs(
    org_id: str | None = Query(default=None),
    issue_id: str | None = Query(default=None),
    status: str | None = Query(default=None),
    kind: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> RunListResponse:
    query = select(Run)
    count_query = select(func.count(Run.id))
    filters = []
    if org_id:
        filters.append(Run.org_id == org_id)
    if issue_id:
        filters.append(Run.issue_id == issue_id)
    if status:
        filters.append(Run.status == status)
    if kind:
        filters.append(Run.kind == kind)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    total = int(db.scalar(count_query) or 0)
    runs = list(
        db.scalars(
            query.order_by(Run.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    )
    return RunListResponse(
        items=[RunSummary.model_validate(run) for run in runs],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{run_id}", response_model=RunDetail)
def get_run(run_id: str, db: Session = Depends(get_db)) -> RunDetail:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    steps = list(
        db.scalars(select(RunStep).where(RunStep.run_id == run.id).order_by(RunStep.seq))
    )
    issue = db.get(Issue, run.issue_id) if run.issue_id else None
    return RunDetail(
        **RunSummary.model_validate(run).model_dump(),
        patch=run.patch,
        run_metadata=run.run_metadata or {},
        steps=[RunStepOut.model_validate(step) for step in steps],
        issue=issue_summary(issue) if issue else None,
    )


@router.post("/{run_id}/cancel", response_model=RunSummary)
def cancel_run(run_id: str, db: Session = Depends(get_db)) -> RunSummary:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if run.status not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail=f"Run is already {run.status}")
    run.status = "cancelled"
    run.finished_at = utcnow()
    db.add(
        RunStep(
            run_id=run.id,
            seq=9999,
            kind="cancelled",
            title="Cancellation requested",
            status="skipped",
            body="The run was cancelled by a user. In-flight work may finish before the worker exits.",
        )
    )
    audit(db, run.org_id, "run.cancelled", actor="user", target_type="run", target_id=run.id)
    db.commit()
    db.refresh(run)
    return RunSummary.model_validate(run)


@router.get("/{run_id}/patch", response_class=PlainTextResponse)
def download_patch(run_id: str, db: Session = Depends(get_db)) -> PlainTextResponse:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.patch:
        raise HTTPException(status_code=404, detail="This run has no patch")
    filename = f"tweaker-{run.id[:8]}.patch"
    return PlainTextResponse(
        run.patch,
        media_type="text/x-patch",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/{run_id}/publish", response_model=RunSummary)
def publish_run(
    run_id: str, payload: PublishRequest, db: Session = Depends(get_db)
) -> RunSummary:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    if not run.patch:
        raise HTTPException(status_code=409, detail="This run has no patch to publish")
    if run.pr_url:
        raise HTTPException(status_code=409, detail="This run has already been published")
    issue = db.get(Issue, run.issue_id) if run.issue_id else None
    if issue is None:
        raise HTTPException(status_code=409, detail="Run is not attached to an issue")
    org = db.get(Organization, run.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    repo = get_repo(db, org, run.repo_id or issue.repo_id)
    if repo is None:
        raise HTTPException(status_code=409, detail="Connect a repository before publishing")
    try:
        result = publish_pull_request(
            run,
            issue,
            repo,
            run.patch,
            branch=payload.branch,
            title=payload.title,
            body=payload.body,
            draft=payload.draft,
        )
    except PublishError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    run.branch = result.branch
    run.pr_url = result.pr_url
    run.run_metadata = {
        **(run.run_metadata or {}),
        "commit_sha": result.commit_sha,
        "publish_message": result.message,
    }
    db.add(
        RunStep(
            run_id=run.id,
            seq=9998,
            kind="publish",
            title="Pull request opened",
            status="succeeded",
            body=f"{result.message}: {result.pr_url or result.branch}",
            data={"branch": result.branch, "pr_url": result.pr_url},
        )
    )
    audit(
        db,
        run.org_id,
        "run.published",
        actor="user",
        target_type="run",
        target_id=run.id,
        metadata={"pr_url": result.pr_url, "branch": result.branch},
    )
    db.commit()
    db.refresh(run)
    return RunSummary.model_validate(run)


@router.post("/{run_id}/retry", response_model=RunSummary, status_code=202)
async def retry_run(run_id: str, db: Session = Depends(get_db)) -> RunSummary:
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    issue = db.get(Issue, run.issue_id) if run.issue_id else None
    org = db.get(Organization, run.org_id)
    if issue is None or org is None:
        raise HTTPException(status_code=409, detail="Run context is no longer available")
    new_run = Run(
        org_id=run.org_id,
        issue_id=run.issue_id,
        repo_id=run.repo_id,
        kind=run.kind,
        status="queued",
        trigger="retry",
        provider=run.provider,
        model=run.model,
        run_metadata={**(run.run_metadata or {}), "retry_of": run.id},
    )
    db.add(new_run)
    db.flush()
    db.add(
        RunStep(
            run_id=new_run.id,
            seq=1,
            kind="queue",
            title="Queued retry",
            status="succeeded",
            body=f"Retrying investigation {run.id[:8]}.",
        )
    )
    audit(db, org.id, "run.retried", actor="user", target_type="run", target_id=new_run.id)
    db.commit()
    await enqueue_run(new_run.id)
    return RunSummary.model_validate(new_run)
