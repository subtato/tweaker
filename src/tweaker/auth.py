from __future__ import annotations

from fastapi import Header, HTTPException, status

from .config import get_settings
from .crypto import constant_time_equal


def _extract_token(authorization: str | None, x_api_key: str | None) -> str | None:
    if x_api_key:
        return x_api_key
    if authorization and authorization.lower().startswith("bearer "):
        return authorization[7:].strip()
    return None


def require_auth(
    authorization: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Service-level bearer authentication.

    Tweaker is designed to sit behind the same SSO/reverse proxy as Sentry.
    Setting ``TWEAKER_AUTH_TOKEN`` adds a shared service token for direct
    deployments; leaving it unset keeps local/demo installations frictionless.
    """
    expected = get_settings().auth_token
    if not expected:
        return
    provided = _extract_token(authorization, x_api_key)
    if not constant_time_equal(provided, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API token",
            headers={"WWW-Authenticate": "Bearer"},
        )
