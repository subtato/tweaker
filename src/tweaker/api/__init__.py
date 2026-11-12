from __future__ import annotations

from fastapi import APIRouter

from . import (
    ask,
    audit,
    code_reviews,
    config,
    issues,
    orgs,
    repos,
    runs,
    seer_runs,
    stats,
    usage,
    webhooks,
)

api_router = APIRouter(prefix="/api")
api_router.include_router(config.router)
api_router.include_router(orgs.router)
api_router.include_router(repos.router)
api_router.include_router(issues.router)
api_router.include_router(runs.router)
api_router.include_router(ask.router)
api_router.include_router(stats.router)
api_router.include_router(usage.router)
api_router.include_router(seer_runs.router)
api_router.include_router(code_reviews.router)
api_router.include_router(audit.router)
api_router.include_router(webhooks.router)
