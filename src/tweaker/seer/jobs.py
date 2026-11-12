from __future__ import annotations

import asyncio
from typing import Any

from .engine import create_pr_for_run, execute_chat_run, execute_feature_run


async def execute_feature_job(ctx: dict[str, Any], run_id: int) -> None:
    await asyncio.to_thread(execute_feature_run, run_id)


async def execute_chat_job(ctx: dict[str, Any], run_id: int) -> None:
    await asyncio.to_thread(execute_chat_run, run_id)


async def execute_pr_job(ctx: dict[str, Any], run_id: int, payload: dict[str, Any]) -> None:
    await asyncio.to_thread(create_pr_for_run, run_id, payload)


async def execute_assisted_job(ctx: dict[str, Any], run_id: int) -> None:
    from .assisted import execute_assisted_query

    await asyncio.to_thread(execute_assisted_query, run_id)


async def execute_code_review_job(ctx: dict[str, Any], review_id: int) -> None:
    from .codereview import execute_code_review

    await asyncio.to_thread(execute_code_review, review_id)
