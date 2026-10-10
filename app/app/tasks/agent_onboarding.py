"""Scheduled expiry cleanup for browser-approved agent onboarding state."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.workbench.repository import WorkbenchRepository
from app.worker import celery_app


async def _cleanup() -> dict[str, int]:
    postgres = get_postgres()
    await postgres.init()
    try:
        async with postgres.session_factory() as session:
            repo = WorkbenchRepository(session)
            async with repo.transaction():
                return await repo.cleanup_agent_onboarding(now=datetime.now(UTC))
    finally:
        await postgres.shutdown()


@celery_app.task(name="app.tasks.agent_onboarding.cleanup")
def cleanup():
    return asyncio.run(_cleanup())
