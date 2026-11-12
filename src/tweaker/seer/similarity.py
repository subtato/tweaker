from __future__ import annotations

import re
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..models import SeerGroupingRecord


def _tokens(stacktrace: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[A-Za-z_][A-Za-z0-9_.:-]{2,}", stacktrace or "")
        if token.lower() not in {"the", "and", "for", "with", "error", "exception"}
    }


def _distance(left: str, right: str) -> float:
    a = _tokens(left)
    b = _tokens(right)
    if not a and not b:
        return 0.0
    if not a or not b:
        return 1.0
    return round(1.0 - (len(a & b) / len(a | b)), 6)


def similar_issues(payload: dict[str, Any]) -> dict[str, Any]:
    project_id = int(payload.get("project_id") or 0)
    stacktrace = str(payload.get("stacktrace") or "")
    exception_type = str(payload.get("exception_type") or "")
    request_hash = str(payload.get("hash") or "")
    k = int(payload.get("k") or 5)
    threshold = float(payload.get("threshold") or 0.15)
    read_only = bool(payload.get("read_only"))
    training_mode = bool(payload.get("training_mode"))
    model = "tweaker-similarity-v1"

    with session_scope() as session:
        rows = list(
            session.scalars(
                select(SeerGroupingRecord)
                .where(SeerGroupingRecord.project_id == project_id)
                .order_by(SeerGroupingRecord.updated_at.desc())
                .limit(500)
            )
        )
        responses = []
        for row in rows:
            if row.hash == request_hash:
                continue
            distance = _distance(stacktrace, row.stacktrace)
            if distance <= threshold:
                responses.append(
                    {
                        "parent_hash": row.hash,
                        "stacktrace_distance": distance,
                        "should_group": True,
                    }
                )
            if len(responses) >= k:
                break

        if not read_only and not training_mode:
            existing = session.scalar(
                select(SeerGroupingRecord).where(
                    SeerGroupingRecord.project_id == project_id,
                    SeerGroupingRecord.hash == request_hash,
                )
            )
            if existing is None:
                session.add(
                    SeerGroupingRecord(
                        project_id=project_id,
                        hash=request_hash,
                        stacktrace=stacktrace[:100_000],
                        exception_type=exception_type[:300],
                        model=model,
                    )
                )
            else:
                existing.stacktrace = stacktrace[:100_000]
                existing.exception_type = exception_type[:300]
            session.commit()

    return {"responses": [] if training_mode else responses, "model_used": model}


def delete_grouping_records(project_id: int) -> int:
    with session_scope() as session:
        rows = list(
            session.scalars(
                select(SeerGroupingRecord).where(SeerGroupingRecord.project_id == project_id)
            )
        )
        for row in rows:
            session.delete(row)
        session.commit()
        return len(rows)
