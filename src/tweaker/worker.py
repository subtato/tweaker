from __future__ import annotations

import logging
from typing import ClassVar

from arq import cron
from arq.connections import RedisSettings

from .config import get_settings
from .db import init_db
from .logging import configure_logging
from .seer.jobs import (
    execute_assisted_job,
    execute_chat_job,
    execute_code_review_job,
    execute_feature_job,
    execute_pr_job,
)
from .services.tasks import poll_sentry_job, run_investigation_job

logger = logging.getLogger(__name__)


async def startup(ctx: dict) -> None:
    configure_logging()
    init_db()
    logger.info("Tweaker worker ready")


async def shutdown(ctx: dict) -> None:
    logger.info("Tweaker worker stopped")


def _poll_minutes(interval_seconds: int) -> set[int]:
    step = max(1, min(60, interval_seconds // 60))
    return set(range(0, 60, step))


settings = get_settings()

cron_jobs = [
    cron(
        poll_sentry_job,
        name="poll_sentry",
        minute=_poll_minutes(settings.poll_interval_seconds),
        run_at_startup=False,
    )
]


class WorkerSettings:
    functions: ClassVar[list] = [
        run_investigation_job,
        execute_feature_job,
        execute_chat_job,
        execute_pr_job,
        execute_assisted_job,
        execute_code_review_job,
    ]
    cron_jobs: ClassVar[list] = cron_jobs
    on_startup: ClassVar = startup
    on_shutdown: ClassVar = shutdown
    redis_settings: ClassVar[RedisSettings] = RedisSettings.from_dsn(settings.redis_url)
    max_jobs: ClassVar[int] = 2
    job_timeout: ClassVar[int] = settings.max_run_seconds
    max_tries: ClassVar[int] = 1
    keep_result: ClassVar[int] = 3600
    health_check_interval: ClassVar[int] = 30


def main() -> None:
    from arq.worker import run_worker

    run_worker(WorkerSettings)


if __name__ == "__main__":
    main()
