"""把事件经 Redis 推给正在看的页面。"""

from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger(__name__)


class RedisPublisher:
    """Redis 发布；测试里可以传 None。"""

    def __init__(self, redis, prefix: str):
        self.redis = redis
        self.prefix = prefix

    def session_channel(self, session_id: str) -> str:
        return f"{self.prefix}:session:{session_id}:events"

    def commands_channel(self) -> str:
        return f"{self.prefix}:commands"

    async def publish(self, channel: str, payload: dict[str, Any]) -> None:
        if self.redis is None:
            return
        try:
            await self.redis.publish(
                channel, json.dumps(payload, ensure_ascii=False, default=str)
            )
        except Exception:  # noqa: BLE001
            log.warning("redis publish failed channel=%s", channel)
