from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import Issue, Organization, Run
from ..schemas import (
    InvestigateRequest,
    IssueDetail,
    IssueListResponse,
    IssueSummary,
    RunSummary,
    ScanRequest,
)
from ..services.classifier import should_auto_investigate
from ..services.ingest import audit, refresh_issue
from ..services.investigation import create_run, scan_issue
from ..services.sentry import SentryError
from ..services.tasks import enqueue_run

router = APIRouter(prefix="/issues", tags=["issues"], dependencies=[Depends(require_auth)])


def _latest_run(db: Session, issue_id: str) -> Run | None:
    return db.scalar(
        select(Run).where(Run.issue_id == issue_id).order_by(Run.created_at.desc()).limit(1)
    )


def _summary(issue: Issue, run: Run | None = None) -> IssueSummary:
    return IssueSummary(
        id=issue.id,
        org_id=issue.org_id,
        sentry_id=issue.sentry_id,
        project_slug=issue.project_slug,
        short_id=issue.short_id,
        title=issue.title,
        culprit=issue.culprit,
        level=issue.level,
        status=issue.status,
        platform=issue.platform,
        first_seen=issue.first_seen,
        last_seen=issue.last_seen,
        event_count=issue.event_count,
        user_count=issue.user_count,
        permalink=issue.permalink,
        fixability_score=issue.fixability_score,
        fixability_reason=issue.fixability_reason,
        fixability_signals=issue.fixability_signals or [],
        repo_id=issue.repo_id,
        latest_run_id=run.id if run else None,
        latest_run_status=run.status if run else None,
    )


@router.get("", response_model=IssueListResponse)
def list_issues(
    org_id: str | None = Query(default=None),
    status: str = Query(default="unresolved"),
    level: str | None = Query(default=None),
    search: str | None = Query(default=None),
    min_fixability: int | None = Query(default=None, ge=0, le=100),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> IssueListResponse:
    query = select(Issue)
    count_query = select(func.count(Issue.id))
    filters = []
    if org_id:
        filters.append(Issue.org_id == org_id)
    if status and status != "all":
        filters.append(Issue.status == status)
    if level:
        filters.append(Issue.level == level)
    if min_fixability is not None:
        filters.append(Issue.fixability_score >= min_fixability)
    if search:
        pattern = f"%{search}%"
        filters.append(
            or_(
                Issue.title.ilike(pattern),
                Issue.culprit.ilike(pattern),
                Issue.short_id.ilike(pattern),
            )
        )
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    total = int(db.scalar(count_query) or 0)
    issues = list(
        db.scalars(
            query.order_by(Issue.last_seen.desc().nullslast(), Issue.created_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    )
    return IssueListResponse(
        items=[_summary(issue, _latest_run(db, issue.id)) for issue in issues],
        total=total,
        page=page,
        per_page=per_page,
    )


@router.get("/{issue_id}", response_model=IssueDetail)
def get_issue(issue_id: str, db: Session = Depends(get_db)) -> IssueDetail:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    runs = list(
        db.scalars(
            select(Run).where(Run.issue_id == issue.id).order_by(Run.created_at.desc()).limit(25)
        )
    )
    base = _summary(issue, runs[0] if runs else None)
    return IssueDetail(
        **base.model_dump(),
        issue_metadata=issue.issue_metadata or {},
        latest_event=issue.latest_event or {},
        runs=[RunSummary.model_validate(run) for run in runs],
    )


@router.post("/{issue_id}/investigate", response_model=RunSummary, status_code=202)
async def investigate_issue(
    issue_id: str,
    payload: InvestigateRequest,
    db: Session = Depends(get_db),
) -> RunSummary:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    org = db.get(Organization, issue.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not issue.latest_event and not org.sentry_token_encrypted:
        raise HTTPException(
            status_code=400,
            detail="No event is cached. Configure the Sentry connection and refresh the issue first.",
        )
    repo_id = payload.repo_id or issue.repo_id
    kind = "autofix" if payload.mode == "autofix" else "investigate"
    publish = bool(payload.publish and payload.mode == "autofix")
    run = create_run(
        db,
        org=org,
        issue=issue,
        kind=kind,
        trigger="manual",
        repo_id=repo_id,
        publish=publish,
    )
    await enqueue_run(run.id)
    return RunSummary.model_validate(run)


@router.post("/{issue_id}/scan", response_model=IssueSummary)
def scan_issue_route(
    issue_id: str, payload: ScanRequest, db: Session = Depends(get_db)
) -> IssueSummary:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    org = db.get(Organization, issue.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    issue = scan_issue(db, org, issue, use_llm=not payload.force)
    audit(db, org.id, "issue.scanned", actor="user", target_type="issue", target_id=issue.id)
    db.commit()
    return _summary(issue, _latest_run(db, issue.id))


@router.post("/{issue_id}/refresh", response_model=IssueSummary)
def refresh_issue_route(issue_id: str, db: Session = Depends(get_db)) -> IssueSummary:
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    org = db.get(Organization, issue.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if not org.sentry_token_encrypted:
        raise HTTPException(status_code=400, detail="Sentry token is not configured")
    try:
        issue = refresh_issue(db, org, issue)
    except (SentryError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    audit(db, org.id, "issue.refreshed", actor="user", target_type="issue", target_id=issue.id)
    db.commit()
    return _summary(issue, _latest_run(db, issue.id))


@router.post("/{issue_id}/auto-investigate", response_model=RunSummary, status_code=202)
async def auto_investigate_issue(
    issue_id: str, db: Session = Depends(get_db)
) -> RunSummary:
    """Create the run an automatic trigger would create, using current policy."""
    issue = db.get(Issue, issue_id)
    if issue is None:
        raise HTTPException(status_code=404, detail="Issue not found")
    org = db.get(Organization, issue.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    if issue.fixability_score is None:
        issue = scan_issue(db, org, issue, use_llm=False)
    from ..services.classifier import Fixability

    fixability = Fixability(
        score=issue.fixability_score or 0,
        reason=issue.fixability_reason or "",
        signals=issue.fixability_signals or [],
    )
    if not should_auto_investigate(org, fixability):
        raise HTTPException(
            status_code=409,
            detail=(
                f"Issue score {fixability.score} is below the auto-investigate threshold "
                f"{org.fixability_threshold}, or auto-investigate is disabled."
            ),
        )
    kind = "autofix" if org.auto_fix else "investigate"
    run = create_run(
        db,
        org=org,
        issue=issue,
        kind=kind,
        trigger="auto",
        publish=bool(org.auto_fix),
        actor="system",
    )
    await enqueue_run(run.id)
    return RunSummary.model_validate(run)
