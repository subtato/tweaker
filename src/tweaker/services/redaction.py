from __future__ import annotations

import re
from typing import Any

_SECRET_KEY = re.compile(
    r"(pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"authorization|cookie|session|credential|dsn)",
    re.IGNORECASE,
)
_SECRET_PATTERNS = [
    re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsntrys_[A-Za-z0-9_=+/.-]{20,}\b"),
    re.compile(r"\bsntryu_[A-Za-z0-9_=+/.-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bglpat-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"https://[0-9a-f]{32}@[^\s\"']+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.DOTALL),
]
_REDACTED = "[REDACTED]"


def redact_text(value: str) -> str:
    for pattern in _SECRET_PATTERNS:
        value = pattern.sub(_REDACTED, value)
    return value


def redact_value(value: Any, *, depth: int = 0, max_depth: int = 12) -> Any:
    """Recursively redact credentials from telemetry before it reaches an LLM."""
    if depth > max_depth:
        return "[TRUNCATED]"
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _SECRET_KEY.search(key_str) and not key_str.lower().endswith("_count"):
                result[key_str] = _REDACTED if item not in (None, "") else item
            else:
                result[key_str] = redact_value(item, depth=depth + 1, max_depth=max_depth)
        return result
    if isinstance(value, list):
        return [redact_value(item, depth=depth + 1, max_depth=max_depth) for item in value[:200]]
    if isinstance(value, str):
        return redact_text(value)[:20_000]
    return value


def _normalise_frame(frame: dict[str, Any]) -> dict[str, Any]:
    return {
        "filename": frame.get("filename") or frame.get("absPath") or "",
        "absPath": frame.get("absPath"),
        "module": frame.get("module"),
        "package": frame.get("package"),
        "function": frame.get("function"),
        "lineNo": frame.get("lineNo"),
        "colNo": frame.get("colNo"),
        "inApp": bool(frame.get("inApp", False)),
        "contextLine": frame.get("contextLine"),
        "preContext": (frame.get("preContext") or [])[-6:],
        "postContext": (frame.get("postContext") or [])[:6],
        "sourceLink": frame.get("sourceLink"),
        "errors": frame.get("errors"),
    }


def _normalise_exception(value: dict[str, Any]) -> dict[str, Any]:
    stacktrace = value.get("stacktrace") or {}
    frames = stacktrace.get("frames") or []
    return {
        "type": value.get("type") or "",
        "value": redact_text(str(value.get("value") or ""))[:8000],
        "mechanism": value.get("mechanism"),
        "module": value.get("module"),
        "stacktrace": {
            "frames": [_normalise_frame(frame) for frame in frames if isinstance(frame, dict)]
        },
    }


def compact_event(event: dict[str, Any], max_chars: int = 40_000) -> dict[str, Any]:
    """Turn a Sentry event into a stable, redacted, model-friendly shape.

    The function is idempotent: it accepts both raw Sentry event payloads and
    events that were already compacted when the issue was ingested.
    """
    if not event:
        return {}

    if isinstance(event.get("exceptions"), list) and "entries" not in event:
        import json

        compact = redact_value(event)
        if len(json.dumps(compact, ensure_ascii=False, default=str)) <= max_chars:
            return compact
        compact["extra"] = {"_truncated": True}
        compact["contexts"] = {"_truncated": True}
        compact["breadcrumbs"] = (compact.get("breadcrumbs") or [])[-15:]
        return compact

    tags_raw = event.get("tags") or []
    tags: dict[str, str] = {}
    if isinstance(tags_raw, list):
        for tag in tags_raw:
            if isinstance(tag, dict) and tag.get("key"):
                tags[str(tag["key"])] = str(tag.get("value", ""))[:1000]
    elif isinstance(tags_raw, dict):
        tags = {str(k): str(v)[:1000] for k, v in tags_raw.items()}

    exceptions: list[dict[str, Any]] = []
    entries = event.get("entries") or []
    if isinstance(entries, list):
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get("type") == "exception":
                values = (entry.get("data") or {}).get("values") or []
                exceptions.extend(
                    _normalise_exception(item) for item in values if isinstance(item, dict)
                )

    breadcrumbs_container = event.get("breadcrumbs") or {}
    if isinstance(breadcrumbs_container, dict):
        breadcrumbs_raw = breadcrumbs_container.get("values") or []
    elif isinstance(breadcrumbs_container, list):
        breadcrumbs_raw = breadcrumbs_container
    else:
        breadcrumbs_raw = []
    breadcrumbs = []
    for crumb in breadcrumbs_raw[-40:]:
        if not isinstance(crumb, dict):
            continue
        breadcrumbs.append(
            {
                "timestamp": crumb.get("timestamp"),
                "type": crumb.get("type"),
                "category": crumb.get("category"),
                "level": crumb.get("level"),
                "message": redact_text(str(crumb.get("message") or ""))[:1500],
                "data": redact_value(crumb.get("data") or {}),
            }
        )

    compact = {
        "id": event.get("id") or event.get("eventID"),
        "eventID": event.get("eventID") or event.get("id"),
        "dateCreated": event.get("dateCreated") or event.get("timestamp"),
        "title": redact_text(str(event.get("title") or event.get("message") or ""))[:2000],
        "message": redact_text(str(event.get("message") or ""))[:8000],
        "culprit": redact_text(str(event.get("culprit") or ""))[:2000],
        "platform": event.get("platform"),
        "release": (event.get("release") or {}).get("version")
        if isinstance(event.get("release"), dict)
        else event.get("release"),
        "environment": tags.get("environment"),
        "level": tags.get("level") or event.get("level"),
        "tags": tags,
        "contexts": redact_value(event.get("contexts") or {}),
        "sdk": event.get("sdk"),
        "user": redact_value(event.get("user") or {}),
        "breadcrumbs": breadcrumbs,
        "exceptions": exceptions,
        "request": redact_value(
            {
                "url": (event.get("request") or {}).get("url"),
                "method": (event.get("request") or {}).get("method"),
            }
        ),
        "extra": redact_value(event.get("extra") or {}),
    }

    # Keep a bounded representation. The most useful debugging fields are
    # ordered first, so truncation removes low-signal payloads.
    import json

    encoded = json.dumps(compact, ensure_ascii=False, default=str)
    if len(encoded) > max_chars:
        compact["extra"] = {"_truncated": True}
        compact["contexts"] = {"_truncated": True}
        compact["breadcrumbs"] = breadcrumbs[-15:]
        encoded = json.dumps(compact, ensure_ascii=False, default=str)
        if len(encoded) > max_chars:
            compact["exceptions"] = exceptions[:3]
            for exc in compact["exceptions"]:
                frames = exc["stacktrace"]["frames"]
                exc["stacktrace"]["frames"] = frames[-60:]
    return compact


def error_summary(event: dict[str, Any]) -> str:
    compact = compact_event(event, max_chars=20_000)
    exceptions = compact.get("exceptions") or []
    if exceptions:
        exc = exceptions[-1]
        return f"{exc.get('type') or 'Error'}: {exc.get('value') or ''}".strip(": ")
    return str(compact.get("title") or compact.get("message") or "Unknown error")


def in_app_frames(event: dict[str, Any]) -> list[dict[str, Any]]:
    compact = compact_event(event, max_chars=100_000)
    frames: list[dict[str, Any]] = []
    for exc in compact.get("exceptions") or []:
        frames.extend(exc.get("stacktrace", {}).get("frames", []))
    app_frames = [frame for frame in frames if frame.get("inApp")]
    return app_frames or frames
