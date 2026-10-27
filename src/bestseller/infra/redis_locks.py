"""Atomic lease ownership mutations. Script errors propagate; never fall back."""

from __future__ import annotations

from redis.asyncio import Redis

_COMPARE_OWNER_DELETE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('DEL', KEYS[1])
end
return 0
"""

_COMPARE_OWNER_EXPIRE = """
if redis.call('GET', KEYS[1]) == ARGV[1] then
    return redis.call('EXPIRE', KEYS[1], ARGV[2])
end
return 0
"""


async def compare_owner_delete(redis: Redis, key: str, owner: str) -> bool:
    """Delete only while owner still matches, in one Redis operation."""
    return bool(await redis.eval(_COMPARE_OWNER_DELETE, 1, key, str(owner)))


async def compare_owner_expire(redis: Redis, key: str, owner: str, ttl_seconds: int) -> bool:
    """Renew only while owner still matches, in one Redis operation."""
    return bool(await redis.eval(_COMPARE_OWNER_EXPIRE, 1, key, str(owner), ttl_seconds))
