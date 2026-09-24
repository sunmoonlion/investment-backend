"""第五个运行角色：工作台 runner。同一镜像，`python -m app.bootstrap.runner`。

长连接不适合 Celery，所以单独一个 asyncio 进程：保持到各沙箱 app-server 的 WebSocket，投影事件，执行命令。
第一期单实例；多实例时命令由 `for update skip locked` 分摊，但同一沙箱的连接会重复——多实例分片留到 D9。
"""

from __future__ import annotations

import asyncio
import logging
import signal

from app.application.workbench.runner import Publisher, Runner
from app.infrastructure.logging.logging import setup_logging
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from core.config import get_settings

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    setup_logging()
    if not settings.workbench_enabled:
        logger.error("WORKBENCH_ENABLED is false; runner refuses to start")
        raise SystemExit(2)
    await get_redis().init()
    await get_postgres().init()
    runner = Runner(
        get_postgres().session_factory,
        publisher=Publisher(get_redis().client, settings.workbench_redis_key_prefix),
        runner_id=settings.workbench_runner_id,
        environment_key=settings.workbench_environment_key,
        poll_seconds=settings.workbench_poll_seconds,
    )
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, runner.stop)
    logger.info("service_starting service=%s role=runner", settings.service_name)
    try:
        await runner.run_forever()
    finally:
        await get_postgres().shutdown()
        await get_redis().shutdown()


if __name__ == "__main__":
    asyncio.run(main())
