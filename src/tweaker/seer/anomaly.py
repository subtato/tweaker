from __future__ import annotations

import statistics
from typing import Any


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
    median = statistics.median(values)
    deviations = [abs(value - median) for value in values]
    mad = statistics.median(deviations) or 1.0
    robust_sigma = 1.4826 * mad
    lower = median - 3 * robust_sigma
    upper = median + 3 * robust_sigma
    for point in points:
        value = point.get("value")
        if not isinstance(value, (int, float)):
            point.setdefault("anomaly", {"anomaly_type": "no_data", "anomaly_score": 0.0})
            continue
        z = abs(float(value) - median) / robust_sigma
        if value > upper:
            kind = "anomaly_higher_confidence"
        elif value < lower:
            kind = "anomaly_lower_confidence"
        else:
            kind = "none"
        point["anomaly"] = {
            "anomaly_type": kind,
            "anomaly_score": round(min(1.0, z / 4), 4),
        }
        point.setdefault("yhat_lower", round(lower, 6))
        point.setdefault("yhat_upper", round(upper, 6))
    return {"success": True, "timeseries": points}


def store(payload: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "message": "Stored anomaly detection data."}


def delete(payload: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "message": "Deleted anomaly detection data."}


def alert_data(payload: dict[str, Any]) -> dict[str, Any]:
    return {"success": True, "data": [], "timeseries": []}
