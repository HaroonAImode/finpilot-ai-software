"""Shared Slack Web API client used by non-discovery jobs."""

import asyncio

import httpx


class SlackAPIClient:
    def __init__(self, bot_token: str, rate_limiter, settings) -> None:
        self.bot_token = bot_token
        self.rate_limiter = rate_limiter
        self.base_url = "https://slack.com/api"

    async def call(self, method: str, params: dict | None = None) -> dict:
        await self.rate_limiter.acquire(method)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{self.base_url}/{method}",
                params=params or {},
                headers={"Authorization": f"Bearer {self.bot_token}"},
            )
        if response.status_code == 429:
            retry_after = int(response.headers.get("Retry-After", "1"))
            self.rate_limiter.set_retry_after(method, retry_after)
            await asyncio.sleep(retry_after)
            return await self.call(method, params)
        response.raise_for_status()
        payload = response.json()
        if not payload.get("ok"):
            raise ValueError(f"Slack API error: {payload.get('error', 'unknown_error')}")
        return payload
