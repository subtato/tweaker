from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..auth import require_auth
from ..db import get_db
from ..models import AuditEvent
from ..schemas import AuditEventOut

router = APIRouter(prefix="/audit", tags=["audit"], dependencies=[Depends(require_auth)])


@router.get("", response_model=list[AuditEventOut])
def list_audit(
    org_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
) -> list[AuditEventOut]:
    query = select(AuditEvent)
    if org_id:
        query = query.where(AuditEvent.org_id == org_id)
    events = list(db.scalars(query.order_by(AuditEvent.created_at.desc()).limit(limit)))
    return [AuditEventOut.model_validate(event) for event in events]
