from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import SeerCodeReview

router = APIRouter(prefix="/code-reviews", tags=["code-review"], dependencies=[Depends(require_auth)])


def _out(item: SeerCodeReview) -> dict[str, object]:
    return {
        "id": item.id,
        "org_id": item.org_id,
        "provider": item.provider,
        "repo": f"{item.owner}/{item.name}",
        "pr_id": item.pr_id,
        "status": item.status,
        "conclusion": item.conclusion,
        "summary": item.summary,
        "comments": item.comments or [],
        "comment_count": item.comment_count,
        "check_run_id": item.check_run_id,
        "check_run_url": item.check_run_url,
        "review_url": item.review_url,
        "error": item.error,
        "created_at": item.created_at,
        "finished_at": item.finished_at,
    }


@router.get("")
def list_reviews(
    org_id: int | None = Query(default=None),
    limit: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    query = select(SeerCodeReview).order_by(SeerCodeReview.created_at.desc()).limit(limit)
    if org_id is not None:
        query = query.where(SeerCodeReview.org_id == org_id)
    items = list(db.scalars(query))
    return {"items": [_out(item) for item in items]}


@router.get("/{review_id}")
def get_review(review_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    item = db.get(SeerCodeReview, review_id)
    if item is None:
        raise HTTPException(status_code=404, detail="Code review not found")
    return _out(item)
