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

    async def eval(self, script, numkeys, key, token, *args):
        assert numkeys == 1
        if self.values.get(key) != token:
            return 0
        if args:
            return await self.expire(key, args[0])
        return await self.delete(key)

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

    assert redis.values == {
        "investment:agent:session:session-1:lock": "run-2:other-token"
    }


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


@pytest.mark.asyncio
async def test_real_redis_expiry_and_late_owner_cannot_change_replacement():
    import asyncio
    import os
    import uuid

    from redis.asyncio import Redis

    url = os.environ.get("AGENT_TEST_REDIS_URL")
    if not url:
        pytest.skip("set AGENT_TEST_REDIS_URL to test the Lua scripts against Redis")
    redis = Redis.from_url(url, decode_responses=True)
    session_id = "luna-test-" + uuid.uuid4().hex
    key = f"investment:agent:session:{session_id}:lock"
    try:
        lock = RedisSessionLock(redis, ttl_seconds=30)
        old = await lock.acquire(session_id=session_id, owner="old")
        assert old is not None
        await redis.pexpire(key, 1)
        await asyncio.sleep(0.02)
        new = await lock.acquire(session_id=session_id, owner="new")
        assert new is not None
        assert not await lock.renew(old)
        await lock.release(old)
        assert await lock.renew(new)
        await lock.release(new)
        assert await redis.get(key) is None
    finally:
        await redis.delete(key)
        await redis.aclose()
