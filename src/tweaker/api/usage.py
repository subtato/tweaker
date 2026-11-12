from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..schemas import QuotaUpdate
from ..services.usage import set_quota, usage_summary

router = APIRouter(prefix="/usage", tags=["usage"], dependencies=[Depends(require_auth)])


@router.get("")
def usage(org_id: str | None = Query(default=None), db: Session = Depends(get_db)) -> dict[str, object]:
    return usage_summary(org_id)


@router.put("/quota/{org_id}")
def update_quota(org_id: str, payload: QuotaUpdate) -> dict[str, object]:
    return set_quota(
        org_id,
        monthly_token_limit=payload.monthly_token_limit,
        monthly_cost_limit=payload.monthly_cost_limit,
        monthly_run_limit=payload.monthly_run_limit,
    )
