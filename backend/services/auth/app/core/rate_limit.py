"""Login rate limiting (architecture report §20).

Redis-backed so the limit holds across service replicas. If Redis is unavailable
the limiter **fails open** — a login outage for every user is a worse outcome
than a temporarily unthrottled login endpoint, and passwords are still Argon2
hashed. The failure is logged loudly so it does not pass unnoticed.
"""
from __future__ import annotations

import logging

from fastapi import Request
from redis import asyncio as aioredis

from shared.exceptions import RateLimitedError

from app.core.config import Settings

logger = logging.getLogger(__name__)

WINDOW_SECONDS = 60


class LoginRateLimiter:
    def __init__(self) -> None:
        self._redis: aioredis.Redis | None = None

    async def _client(self, settings: Settings) -> aioredis.Redis | None:
        if self._redis is None:
            try:
                self._redis = aioredis.from_url(settings.redis_url, decode_responses=True)
            except Exception:
                logger.exception("Could not create the Redis client for login rate limiting")
                return None
        return self._redis

    @staticmethod
    def _client_ip(request: Request) -> str:
        # X-Forwarded-For is client-controlled and only trustworthy behind a proxy
        # that overwrites it. Until the Gateway exists, prefer the socket address.
        if request.client and request.client.host:
            return request.client.host
        return "unknown"

    async def check(self, request: Request, settings: Settings) -> None:
        redis = await self._client(settings)
        if redis is None:
            return

        key = f"login-attempts:{self._client_ip(request)}"
        try:
            attempts = await redis.incr(key)
            if attempts == 1:
                await redis.expire(key, WINDOW_SECONDS)
        except Exception:
            logger.exception("Login rate limiting unavailable — allowing the request")
            return

        if attempts > settings.login_rate_limit_per_minute:
            raise RateLimitedError("Too many login attempts. Please wait a minute and try again.")

    async def reset(self, request: Request, settings: Settings) -> None:
        redis = await self._client(settings)
        if redis is None:
            return
        try:
            await redis.delete(f"login-attempts:{self._client_ip(request)}")
        except Exception:
            logger.debug("Could not reset the login rate limit counter", exc_info=True)


login_rate_limiter = LoginRateLimiter()
