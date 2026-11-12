from __future__ import annotations

import hashlib
import hmac
import logging
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, Request, status

from ..config import get_settings
from ..services.rate_limit import limiter

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SeerContext:
    org_id: int | None
    user_id: int | None
    viewer_context: Any | None
    viewer_header: str | None
    raw_body: bytes


def _parse_signature(value: str) -> str:
    value = value.strip()
    if value.lower().startswith("rpcsignature"):
        parts = value.split(" ", 1)
        if len(parts) == 2:
            value = parts[1].strip()
    if value.startswith("rpc0:"):
        value = value[5:]
    return value


def verify_signature(raw_body: bytes, header: str | None, secret: str) -> bool:
    if not header:
        return False
    provided = _parse_signature(header)
    expected = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, provided)


def decode_viewer(header: str | None, secret: str | None) -> Any | None:
    if not header or not secret:
        return None
    try:
        from .viewer import decode_viewer_context

        return decode_viewer_context(header, secret)
    except Exception:
        logger.warning("Invalid X-Viewer-Context JWT", exc_info=True)
        return None


async def authenticate(request: Request) -> SeerContext:
    settings = get_settings()
    raw = await request.body()
    secret = settings.seer_api_shared_secret
    header = request.headers.get("Authorization")

    if secret:
        if not verify_signature(raw, header, secret):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Seer signature")
    elif not (settings.seer_allow_unsigned or settings.demo_mode):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="SEER_API_SHARED_SECRET is not configured",
        )

    if settings.seer_rate_limit_per_minute:
        client = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.allow(
            f"seer:{client}",
            limit=settings.seer_rate_limit_per_minute,
            window_seconds=60,
        )
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )

    viewer_header = request.headers.get("X-Viewer-Context")
    viewer = decode_viewer(viewer_header, secret)
    if viewer_header and viewer is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid viewer context")

    org_id = getattr(viewer, "organization_id", None) if viewer else None
    user_id = None
    if viewer is not None:
        try:
            user_id = viewer.actor.user_id
        except Exception:
            user_id = None
    return SeerContext(
        org_id=int(org_id) if org_id is not None else None,
        user_id=int(user_id) if user_id is not None else None,
        viewer_context=viewer,
        viewer_header=viewer_header,
        raw_body=raw,
    )
