from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

import jwt


@dataclass(slots=True)
class ViewerActor:
    type: str
    actor_id: int | None = None
    delegated_user_id: int | None = None

    @property
    def user_id(self) -> int | None:
        if self.type == "user":
            return self.actor_id
        if self.type == "agent":
            return self.delegated_user_id
        return None


@dataclass(slots=True)
class ViewerContextView:
    organization_id: int | None
    actor: ViewerActor
    claims: dict[str, Any]


def _key_id(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()[:8]


def decode_viewer_context(token: str, secret: str) -> ViewerContextView:
    """Verify the HS256 X-Viewer-Context JWT Sentry sends to Seer.

    The wire format is owned by ``sentry_viewercontext``; this is a small,
    dependency-light reader for the fields Tweaker needs.
    """
    header = jwt.get_unverified_header(token)
    kid = header.get("kid")
    if kid and kid != _key_id(secret):
        raise jwt.InvalidTokenError("Viewer context key id does not match")
    claims = jwt.decode(
        token,
        secret,
        algorithms=["HS256"],
        issuer="sentry",
        options={"require": ["iat", "exp", "iss"]},
        leeway=5,
    )
    if claims.get("version") != 2:
        raise jwt.InvalidTokenError("Unsupported viewer context version")
    actor_data = claims.get("actor")
    if not isinstance(actor_data, dict):
        raise jwt.InvalidTokenError("Viewer context has no actor")
    actor = ViewerActor(
        type=str(actor_data.get("type") or "user"),
        actor_id=_int_or_none(actor_data.get("id")),
        delegated_user_id=_int_or_none(actor_data.get("delegated_user_id")),
    )
    return ViewerContextView(
        organization_id=_int_or_none(claims.get("organization_id")),
        actor=actor,
        claims=claims,
    )


def _int_or_none(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
