from __future__ import annotations

import pytest

from app.infrastructure.agent.session_lock import RedisSessionLock


class FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.expirations: dict[str, int] = {}

    async def set(
        self,
        name: str,
        value: str,
        *,
        ex: int,
        nx: bool,
    ) -> bool:
        assert ex == 30
        assert nx is True
        if name in self.values:
            return False
        self.values[name] = value
        return True

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def delete(self, key: str) -> int:
        if key in self.values:
            del self.values[key]
            self.expirations.pop(key, None)
            return 1
        return 0

    async def expire(self, key: str, seconds: int) -> bool:
        if key not in self.values:
            return False
        self.expirations[key] = seconds
        return True


@pytest.mark.asyncio
async def test_redis_session_lock_acquires_once_and_releases_owner_token() -> None:
    redis = FakeRedis()
    lock = RedisSessionLock(redis, ttl_seconds=30)  # type: ignore[arg-type]

    first = await lock.acquire(session_id="session-1", owner="run-1")
    blocked = await lock.acquire(session_id="session-1", owner="run-2")

    assert first is not None
    assert blocked is None
    assert list(redis.values) == ["investment:agent:session:session-1:lock"]

    await lock.release(first)

    assert redis.values == {}


@pytest.mark.asyncio
async def test_redis_session_lock_does_not_release_another_owner() -> None:
    redis = FakeRedis()
    lock = RedisSessionLock(redis, ttl_seconds=30)  # type: ignore[arg-type]

    first = await lock.acquire(session_id="session-1", owner="run-1")
    assert first is not None
    redis.values["investment:agent:session:session-1:lock"] = "run-2:other-token"

    await lock.release(first)

    assert redis.values == {"investment:agent:session:session-1:lock": "run-2:other-token"}


@pytest.mark.asyncio
async def test_redis_session_lock_renews_only_owner_token() -> None:
    redis = FakeRedis()
    lock = RedisSessionLock(redis, ttl_seconds=30)  # type: ignore[arg-type]

    first = await lock.acquire(session_id="session-1", owner="run-1")
    assert first is not None

    renewed = await lock.renew(first)
    assert renewed is True
    assert redis.expirations == {"investment:agent:session:session-1:lock": 30}

    redis.values["investment:agent:session:session-1:lock"] = "run-2:other-token"

    assert await lock.renew(first) is False
