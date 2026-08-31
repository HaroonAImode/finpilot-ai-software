"""Per-IP and per-user rate limiting (architecture report §20).

Fails open when Redis is unavailable, and logs it: a Redis outage taking the
whole platform down would be a worse failure than a temporarily unthrottled one.
"""
from __future__ import annotations

import logging

from redis import asyncio as aioredis

from app.core.config import Settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


class RateLimiter:
    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _client(self, settings: Settings) -> aioredis.Redis | None:
        if self._redis is None:
            try:
                self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            except Exception:
                logger.exception("Could not create the Redis client for rate limiting")
                return None
        return self._redis

    async def _over_limit(self, redis, key: str, limit: int) -> bool:
        try:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, WINDOW_SECONDS)
            return count > limit
        except Exception:
            logger.exception("Rate limiting unavailable — allowing the request")
            return False

    async def check(self, *, settings: Settings, client_ip: str, user_id: str | None) -> bool:
        """True when the caller is over a limit and should get a 429.

        An authenticated user gets the higher per-user allowance instead of the
        per-IP one, so a whole office behind one NAT address does not throttle
        itself the moment a few people are working.
        """
        redis = await self._client(settings)
        if redis is None:
            return False

        if user_id:
            return await self._over_limit(
                redis, f"ratelimit:user:{user_id}", settings.rate_limit_per_user
            )
        return await self._over_limit(
            redis, f"ratelimit:ip:{client_ip}", settings.rate_limit_per_ip
        )


rate_limiter = RateLimiter()
