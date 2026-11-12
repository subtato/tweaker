from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import SeerRun
from ..seer.state import summarize_state, wire_state

router = APIRouter(prefix="/seer-runs", tags=["seer"], dependencies=[Depends(require_auth)])


@router.get("")
def list_seer_runs(
    org_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    feature_id: str | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=25, ge=1, le=100),
    db: Session = Depends(get_db),
) -> dict[str, object]:
    query = select(SeerRun)
    count_query = select(func.count(SeerRun.id))
    filters = []
    if org_id is not None:
        filters.append(SeerRun.org_id == org_id)
    if status:
        filters.append(SeerRun.status == status)
    if feature_id:
        filters.append(SeerRun.feature_id == feature_id)
    if filters:
        query = query.where(*filters)
        count_query = count_query.where(*filters)
    total = int(db.scalar(count_query) or 0)
    runs = list(
        db.scalars(
            query.order_by(SeerRun.updated_at.desc())
            .offset((page - 1) * per_page)
            .limit(per_page)
        )
    )
    return {
        "items": [summarize_state(run) for run in runs],
        "total": total,
        "page": page,
        "per_page": per_page,
    }


@router.get("/{run_id}")
def get_seer_run(run_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    run = db.get(SeerRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Seer run not found")
    return {
        "summary": summarize_state(run),
        "state": wire_state(run),
        "payload": run.payload or {},
        "metadata": run.run_metadata or {},
    }


@router.get("/{run_id}/patch")
def get_seer_patch(run_id: int, db: Session = Depends(get_db)) -> dict[str, object]:
    run = db.get(SeerRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Seer run not found")
    patches = []
    for item in run.blocks or []:
        for patch in item.get("merged_file_patches") or []:
            if patch.get("diff"):
                patches.append(patch)
    return {"patches": patches}
