from __future__ import annotations

import statistics
from typing import Any

from sqlalchemy import select

from ..db import session_scope
from ..models import SeerAnomalyData


def _points(payload: dict[str, Any]) -> list[dict[str, Any]]:
    context = payload.get("context")
    if isinstance(context, list):
        return [item for item in context if isinstance(item, dict)]
    if isinstance(context, dict):
        if isinstance(context.get("history"), list) and isinstance(context.get("current"), list):
            return [
                item
                for item in [*context["history"], *context["current"]]
                if isinstance(item, dict)
            ]
        current = context.get("cur_window")
        if isinstance(current, dict):
            return [current]
    timeseries = payload.get("timeseries")
    if isinstance(timeseries, list):
        return [item for item in timeseries if isinstance(item, dict)]
    return []


def detect(payload: dict[str, Any]) -> dict[str, Any]:
    points = _points(payload)
    values = [
        float(point["value"])
        for point in points
        if isinstance(point.get("value"), (int, float))
    ]
    if len(values) < 3:
        for point in points:
            point.setdefault("anomaly", {"anomaly_type": "no_data", "anomaly_score": 0.0})
        return {"success": True, "message": "Not enough history for anomaly detection.", "timeseries": points}
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    sensitivity = str(config.get("sensitivity") or "medium").lower()
    z_limit = {"low": 4.0, "medium": 3.0, "high": 2.0}.get(sensitivity, 3.0)
    raw_direction = config.get("direction")
    direction = str(raw_direction if raw_direction is not None else "both").lower()
    if direction in {"0", "1", "2"}:
        direction = {"0": "up", "1": "down", "2": "both"}[direction]
    allow_higher = direction in {"both", "above", "up", "higher", "high"}
    allow_lower = direction in {"both", "below", "down", "lower", "low"}
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations) or 1.0
    robust_sigma = 1.4826 * mad
    lower = median - z_limit * robust_sigma
    upper = median + z_limit * robust_sigma
    for point in points:
        value = point.get("value")
        if not isinstance(value, (int, float)):
            point.setdefault("anomaly", {"anomaly_type": "no_data", "anomaly_score": 0.0})
            continue
        z = abs(float(value) - median) / robust_sigma
        if allow_higher and value > upper:
            kind = "anomaly_higher_confidence"
        elif allow_lower and value < lower:
            kind = "anomaly_lower_confidence"
        else:
            kind = "none"
        point["anomaly"] = {
            "anomaly_type": kind,
            "anomaly_score": round(min(1.0, z / (z_limit + 1)), 4),
        }
        point.setdefault("yhat_lower", round(lower, 6))
        point.setdefault("yhat_upper", round(upper, 6))
    return {"success": True, "timeseries": points}


def _alert_key(alert: dict[str, Any]) -> str | None:
    source_id = alert.get("source_id")
    if source_id is not None:
        return f"source:{source_id}"
    alert_id = alert.get("id")
    if alert_id is not None:
        return f"alert:{alert_id}"
    return None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _alert_identity(alert: dict[str, Any]) -> tuple[str | None, int | None, int | None]:
    key = _alert_key(alert)
    source_id = _int_or_none(alert.get("source_id"))
    alert_id = _int_or_none(alert.get("id"))
    return key, source_id, source_id if source_id is not None else alert_id


def store(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist training data for a dynamic-alert detector.

    Mirrors Seer's ``StoreDataRequest``: ``organization_id``, ``project_id``,
    ``alert``, ``config``, and ``timeseries``.
    """
    org_id = _int_or_none(payload.get("organization_id"))
    alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else {}
    key, source_id, external_id = _alert_identity(alert)
    if org_id is None:
        return {"success": False, "message": "organization_id is required"}
    if key is None:
        # Older clients can call store without a detector identity. Accept the
        # request so their retry loop does not spin, but there is nothing to key
        # the training data on.
        return {"success": True, "message": "No alert specified; nothing to store."}
    project_id = _int_or_none(payload.get("project_id"))
    config = payload.get("config") if isinstance(payload.get("config"), dict) else {}
    timeseries = payload.get("timeseries")
    if not isinstance(timeseries, list):
        timeseries = []
    cur_window = alert.get("cur_window") if isinstance(alert.get("cur_window"), dict) else {}
    with session_scope() as session:
        item = session.scalar(
            select(SeerAnomalyData).where(
                SeerAnomalyData.org_id == org_id,
                SeerAnomalyData.alert_key == key,
            )
        )
        if item is None:
            item = SeerAnomalyData(
                org_id=org_id,
                project_id=project_id,
                alert_key=key,
                source_id=source_id,
                external_alert_id=external_id,
                config=config,
                timeseries=timeseries,
                cur_window=cur_window,
            )
            session.add(item)
        else:
            item.project_id = project_id if project_id is not None else item.project_id
            item.source_id = source_id
            item.external_alert_id = external_id
            item.config = config
            item.timeseries = timeseries
            item.cur_window = cur_window
        session.commit()
    return {"success": True, "message": "Stored anomaly detection data."}


def delete(payload: dict[str, Any]) -> dict[str, Any]:
    org_id = _int_or_none(payload.get("organization_id"))
    alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else {}
    key, _source_id, _external_id = _alert_identity(alert)
    if org_id is None:
        return {"success": False, "message": "organization_id is required"}
    if key is None:
        return {"success": True, "message": "No alert specified; nothing to delete."}
    with session_scope() as session:
        item = session.scalar(
            select(SeerAnomalyData).where(
                SeerAnomalyData.org_id == org_id,
                SeerAnomalyData.alert_key == key,
            )
        )
        if item is not None:
            session.delete(item)
            session.commit()
    return {"success": True, "message": "Deleted anomaly detection data."}


def alert_data(payload: dict[str, Any]) -> dict[str, Any]:
    """Return threshold data for a detector over ``[start, end]``.

    Mirrors Seer's ``SeerDetectorDataResponse``:
    ``{success, message, data: [{external_alert_id, timestamp, value,
    yhat_lower, yhat_upper}]}``.
    """
    alert = payload.get("alert") if isinstance(payload.get("alert"), dict) else {}
    key, _source_id, external_id = _alert_identity(alert)
    org_id = _int_or_none(payload.get("organization_id"))
    if key is None:
        return {"success": True, "message": None, "data": []}
    start = _float_or_none(payload.get("start"))
    end = _float_or_none(payload.get("end"))
    with session_scope() as session:
        query = select(SeerAnomalyData).where(SeerAnomalyData.alert_key == key)
        if org_id is not None:
            query = query.where(SeerAnomalyData.org_id == org_id)
        item = session.scalar(query)
        if item is None:
            return {"success": True, "message": None, "data": []}
        points = [point for point in (item.timeseries or []) if isinstance(point, dict)]
        row_external_id = external_id if external_id is not None else item.external_alert_id
        data = []
        for point in points:
            timestamp = _float_or_none(point.get("timestamp"))
            if timestamp is None:
                continue
            if start is not None and timestamp < start:
                continue
            if end is not None and timestamp > end:
                continue
            data.append(
                {
                    "external_alert_id": row_external_id,
                    "timestamp": timestamp,
                    "value": point.get("value"),
                    "yhat_lower": point.get("yhat_lower"),
                    "yhat_upper": point.get("yhat_upper"),
                }
            )
        return {"success": True, "message": None, "data": data}


def _float_or_none(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
