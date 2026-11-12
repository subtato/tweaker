from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy import select, text

from ..auth import require_auth
from ..config import get_settings
from ..db import get_db
from ..metrics import render_prometheus
from ..models import Organization
from ..schemas import AppConfigOut, HealthOut
from ..services.llm import get_provider

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
def health(db=Depends(get_db)) -> HealthOut:
    settings = get_settings()
    database = "ok"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        database = "error"
    return HealthOut(
        status="ok" if database == "ok" else "degraded",
        version=settings.version,
        database=database,
        runner=settings.runner,
    )


@router.get("/config", response_model=AppConfigOut)
def app_config(db=Depends(get_db)) -> AppConfigOut:
    settings = get_settings()
    provider = get_provider()
    orgs = list(db.scalars(select(Organization)))
    sentry_configured = any(org.sentry_token_encrypted for org in orgs)
    return AppConfigOut(
        app_name=settings.app_name,
        version=settings.version,
        auth_required=bool(settings.auth_token),
        demo_mode=settings.demo_mode,
        ai_provider=provider.name,
        ai_model=provider.model,
        ai_enabled=provider.name != "none",
        publishing_enabled=settings.enable_pr_publishing,
        sentry_configured=sentry_configured,
        mcp_endpoint="python -m tweaker.mcp_server",
        seer_api_configured=bool(settings.seer_api_shared_secret),
        seer_rpc_configured=bool(settings.seer_rpc_shared_secret),
        sentry_url=settings.sentry_url,
    )


@router.get("/me", dependencies=[Depends(require_auth)])
def me() -> dict[str, object]:
    settings = get_settings()
    return {
        "authenticated": True,
        "version": settings.version,
        "environment": settings.environment,
    }


@router.get("/metrics", response_class=PlainTextResponse, dependencies=[Depends(require_auth)])
def metrics() -> str:
    return render_prometheus()
