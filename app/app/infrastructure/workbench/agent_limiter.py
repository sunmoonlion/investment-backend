"""Atomic fixed-window rate limits for onboarding requests."""

import hashlib
import time

from redis.asyncio import Redis


def _key(identity: str, seconds: int, now: int) -> str:
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    bucket = now // seconds
    return f"investment:agent-onboarding:{seconds}:{digest}:{bucket}"


async def consume_window(
    redis: Redis, identity: str, *, seconds: int, limit: int
) -> bool:
    now = int(time.time())
    key = _key(identity, seconds, now)
    async with redis.pipeline(transaction=True) as pipe:
        pipe.incr(key)
        pipe.expire(key, seconds, nx=True)
        count, _ = await pipe.execute()
    return int(count) <= limit


async def allow_pairing_ip(redis: Redis, source_ip: str) -> bool:
    minute = await consume_window(redis, f"pair-ip:{source_ip}", seconds=60, limit=5)
    hour = await consume_window(redis, f"pair-ip:{source_ip}", seconds=3600, limit=30)
    return minute and hour


async def allow_user_lookup(redis: Redis, owner: str) -> bool:
    return await consume_window(redis, f"lookup-user:{owner}", seconds=60, limit=5)


async def allow_user_lookup_failure(redis: Redis, owner: str) -> bool:
    return await consume_window(redis, f"lookup-fail:{owner}", seconds=3600, limit=20)


async def allow_install_issue(redis: Redis, owner: str) -> bool:
    return await consume_window(redis, f"install-issue:{owner}", seconds=3600, limit=10)
