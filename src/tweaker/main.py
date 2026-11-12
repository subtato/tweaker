from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import api_router
from .config import get_settings
from .db import init_db
from .logging import configure_logging
from .seer.endpoints import router as seer_router
from .seer.endpoints import router_v0 as seer_v0_router
from .services.llm import provider_status

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    configure_logging()
    settings = get_settings()
    init_db()
    status = provider_status()
    logger.info(
        "Tweaker %s starting (env=%s, runner=%s, provider=%s/%s)",
        settings.version,
        settings.environment,
        settings.runner,
        status["provider"],
        status["model"],
    )
    if settings.demo_mode:
        logger.info("Demo mode enabled: seeded sample organization and issues")
    yield
    logger.info("Tweaker shutting down")


settings = get_settings()
app = FastAPI(
    title="Tweaker",
    version=settings.version,
    description="Open-source AI debugging for self-hosted Sentry",
    docs_url="/api/docs" if settings.debug else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.debug else None,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "Content-Disposition"],
)
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > settings.max_request_bytes:
                return JSONResponse(
                    status_code=413,
                    content={"detail": "Request body too large"},
                )
        except ValueError:
            pass
    started = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)
    response.headers["X-Request-ID"] = request_id
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    if request.url.path.startswith("/api"):
        logger.debug(
            "%s %s -> %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
            },
        )
    return response


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "request_id": request.headers.get("X-Request-ID")},
    )


app.include_router(api_router)
app.include_router(seer_router)
app.include_router(seer_v0_router)


def _mount_frontend(application: FastAPI) -> None:
    static_dir = settings.static_dir
    if not static_dir.is_absolute():
        static_dir = Path.cwd() / static_dir
    static_dir = static_dir.resolve()
    assets = static_dir / "assets"
    if assets.is_dir():
        application.mount("/assets", StaticFiles(directory=assets), name="assets")

    @application.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        if full_path.startswith(("api/", "v1/")):
            return JSONResponse(status_code=404, content={"detail": "Not found"})
        candidate = (static_dir / full_path).resolve()
        if full_path and candidate.is_file() and static_dir in candidate.parents:
            return FileResponse(candidate)
        index = static_dir / "index.html"
        if index.exists():
            return FileResponse(index, headers={"Cache-Control": "no-cache"})
        return JSONResponse(
            status_code=200,
            content={
                "app": "Tweaker",
                "version": settings.version,
                "message": "Frontend build not found. Run `npm install && npm run build` in web/.",
            },
        )


_mount_frontend(app)
