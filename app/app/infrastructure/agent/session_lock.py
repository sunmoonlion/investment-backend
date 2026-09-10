from __future__ import annotations

import secrets
from collections.abc import Awaitable
from typing import cast

from redis.asyncio import Redis

from app.application.agent.redis_keys import session_lock_key
from app.application.agent.session_lock import SessionLockToken


class RedisSessionLock:
    def __init__(self, redis: Redis, *, ttl_seconds: int):
        self.redis = redis
        self.ttl_seconds = ttl_seconds

    async def acquire(self, *, session_id: str, owner: str) -> SessionLockToken | None:
        token = SessionLockToken(
            session_id=session_id,
            owner=owner,
            value=secrets.token_urlsafe(24),
        )
        acquired = await self.redis.set(
            self._key(session_id),
            self._value(token),
            ex=self.ttl_seconds,
            nx=True,
        )
        return token if acquired else None

    async def release(self, token: SessionLockToken) -> None:
        await cast(
            Awaitable[int],
            self.redis.eval(
                "if redis.call('get', KEYS[1]) == ARGV[1] then "
                "return redis.call('del', KEYS[1]) else return 0 end",
                1,
                self._key(token.session_id),
                self._value(token),
            ),
        )

    async def renew(self, token: SessionLockToken) -> bool:
        return bool(
            await cast(
                Awaitable[int],
                self.redis.eval(
                    "if redis.call('get', KEYS[1]) == ARGV[1] then "
                    "return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end",
                    1,
                    self._key(token.session_id),
                    self._value(token),
                    self.ttl_seconds,
                ),
            )
        )

    def _key(self, session_id: str) -> str:
        return session_lock_key(session_id)

    def _value(self, token: SessionLockToken) -> str:
        return f"{token.owner}:{token.value}"
