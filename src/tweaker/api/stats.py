from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import Issue, Run, SeerRun
from ..schemas import StatsResponse

router = APIRouter(prefix="/stats", tags=["stats"], dependencies=[Depends(require_auth)])


@router.get("", response_model=StatsResponse)
def stats(org_id: str | None = Query(default=None), db: Session = Depends(get_db)) -> StatsResponse:
    issue_filter = [Issue.org_id == org_id] if org_id else []
    run_filter = [Run.org_id == org_id] if org_id else []
    open_issues = int(
        db.scalar(select(func.count(Issue.id)).where(*issue_filter, Issue.status == "unresolved")) or 0
    )
    investigated = int(
        db.scalar(
            select(func.count(func.distinct(Run.issue_id))).where(
                *run_filter, Run.status == "succeeded", Run.issue_id.is_not(None)
            )
        )
        or 0
    )
    autofixes = int(
        db.scalar(
            select(func.count(Run.id)).where(
                *run_filter, Run.kind == "autofix", Run.pr_url.is_not(None)
            )
        )
        or 0
    )
    scanned = int(
        db.scalar(
            select(func.count(Issue.id)).where(*issue_filter, Issue.fixability_score.is_not(None))
        )
        or 0
    )
    fixable = int(
        db.scalar(
            select(func.count(Issue.id)).where(
                *issue_filter, Issue.fixability_score.is_not(None), Issue.fixability_score >= 70
            )
        )
        or 0
    )
    since = datetime.now(UTC) - timedelta(days=7)
    runs_last_7d = int(
        db.scalar(select(func.count(Run.id)).where(*run_filter, Run.created_at >= since)) or 0
    )
    tokens_last_7d = int(
        db.scalar(
            select(func.coalesce(func.sum(Run.input_tokens + Run.output_tokens), 0)).where(
                *run_filter, Run.created_at >= since
            )
        )
        or 0
    )
    average_confidence = db.scalar(
        select(func.avg(Run.confidence)).where(*run_filter, Run.confidence.is_not(None))
    )
    by_level_rows = db.execute(
        select(Issue.level, func.count(Issue.id)).where(*issue_filter).group_by(Issue.level)
    ).all()
    seer_runs = int(db.scalar(select(func.count(SeerRun.id))) or 0)
    seer_completed = int(
        db.scalar(select(func.count(SeerRun.id)).where(SeerRun.status == "completed")) or 0
    )
    seer_failed = int(
        db.scalar(select(func.count(SeerRun.id)).where(SeerRun.status == "error")) or 0
    )
    seer_runs_last_7d = int(
        db.scalar(select(func.count(SeerRun.id)).where(SeerRun.created_at >= since)) or 0
    )
    return StatsResponse(
        open_issues=open_issues,
        investigated=investigated,
        autofixes=autofixes,
        fixability_rate=round((fixable / scanned) * 100, 1) if scanned else 0.0,
        runs_last_7d=runs_last_7d,
        tokens_last_7d=tokens_last_7d,
        average_confidence=round(float(average_confidence), 3) if average_confidence else None,
        by_level={str(level): int(count) for level, count in by_level_rows},
        seer_runs=seer_runs,
        seer_completed=seer_completed,
        seer_failed=seer_failed,
        seer_runs_last_7d=seer_runs_last_7d,
    )
