from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select

from ..db import session_scope
from ..models import OrgQuota, SeerRun, UsageRecord


def _month_start() -> datetime:
    now = datetime.now(UTC)
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def record_usage(
    *,
    org_id: str,
    run_id: int | None,
    kind: str,
    provider: str,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cost_usd: float,
) -> None:
    if input_tokens <= 0 and output_tokens <= 0 and cost_usd <= 0:
        return
    with session_scope() as session:
        session.add(
            UsageRecord(
                org_id=str(org_id),
                run_id=run_id,
                kind=kind,
                provider=provider,
                model=model,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd,
            )
        )
        session.commit()


def usage_summary(org_id: str | None, *, since: datetime | None = None) -> dict[str, Any]:
    since = since or _month_start()
    usage_filters = [UsageRecord.created_at >= since]
    run_filters = [SeerRun.created_at >= since]
    if org_id:
        usage_filters.append(UsageRecord.org_id == str(org_id))
        run_filters.append(SeerRun.org_id == _org_int(org_id))
    with session_scope() as session:
        totals = session.execute(
            select(
                func.coalesce(func.sum(UsageRecord.input_tokens), 0),
                func.coalesce(func.sum(UsageRecord.output_tokens), 0),
                func.coalesce(func.sum(UsageRecord.cost_usd), 0.0),
                func.count(UsageRecord.id),
            ).where(*usage_filters)
        ).one()
        runs = int(session.scalar(select(func.count(SeerRun.id)).where(*run_filters)) or 0)
        by_kind = session.execute(
            select(UsageRecord.kind, func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens))
            .where(*usage_filters)
            .group_by(UsageRecord.kind)
        ).all()
        quota = session.get(OrgQuota, str(org_id)) if org_id else None
        return {
            "org_id": str(org_id) if org_id else None,
            "since": since.isoformat(),
            "input_tokens": int(totals[0] or 0),
            "output_tokens": int(totals[1] or 0),
            "total_tokens": int((totals[0] or 0) + (totals[1] or 0)),
            "cost_usd": round(float(totals[2] or 0.0), 6),
            "model_calls": int(totals[3] or 0),
            "runs": runs,
            "by_kind": {str(kind): int(tokens or 0) for kind, tokens in by_kind},
            "quota": _quota_dict(quota),
        }


def check_quota(org_id: str) -> tuple[bool, str | None]:
    with session_scope() as session:
        quota = session.get(OrgQuota, str(org_id))
        if quota is None:
            return True, None
        since = quota.period_start or _month_start()
        tokens = int(
            session.scalar(
                select(
                    func.coalesce(
                        func.sum(UsageRecord.input_tokens + UsageRecord.output_tokens), 0
                    )
                ).where(UsageRecord.org_id == str(org_id), UsageRecord.created_at >= since)
            )
            or 0
        )
        cost = float(
            session.scalar(
                select(func.coalesce(func.sum(UsageRecord.cost_usd), 0.0)).where(
                    UsageRecord.org_id == str(org_id), UsageRecord.created_at >= since
                )
            )
            or 0.0
        )
        runs = int(
            session.scalar(
                select(func.count(SeerRun.id)).where(
                    SeerRun.org_id == _org_int(org_id), SeerRun.created_at >= since
                )
            )
            or 0
        )
        if quota.monthly_token_limit is not None and tokens >= quota.monthly_token_limit:
            return False, "Monthly token budget exhausted."
        if quota.monthly_cost_limit is not None and cost >= quota.monthly_cost_limit:
            return False, "Monthly cost budget exhausted."
        if quota.monthly_run_limit is not None and runs >= quota.monthly_run_limit:
            return False, "Monthly run budget exhausted."
    return True, None


def set_quota(
    org_id: str,
    *,
    monthly_token_limit: int | None = None,
    monthly_cost_limit: float | None = None,
    monthly_run_limit: int | None = None,
) -> dict[str, Any]:
    with session_scope() as session:
        quota = session.get(OrgQuota, str(org_id))
        if quota is None:
            quota = OrgQuota(org_id=str(org_id))
            session.add(quota)
        quota.monthly_token_limit = monthly_token_limit
        quota.monthly_cost_limit = monthly_cost_limit
        quota.monthly_run_limit = monthly_run_limit
        quota.period_start = _month_start()
        session.commit()
        return _quota_dict(quota)


def _quota_dict(quota: OrgQuota | None) -> dict[str, Any] | None:
    if quota is None:
        return None
    return {
        "monthly_token_limit": quota.monthly_token_limit,
        "monthly_cost_limit": quota.monthly_cost_limit,
        "monthly_run_limit": quota.monthly_run_limit,
        "period_start": quota.period_start.isoformat() if quota.period_start else None,
    }


def _org_int(org_id: str) -> int:
    try:
        return int(org_id)
    except (TypeError, ValueError):
        return -1
