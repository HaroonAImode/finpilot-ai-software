import asyncio
import time
from typing import Dict, Optional


class RateLimiter:
    """
    Token bucket rate limiter for Slack API calls.
    Respects per-method limits and Retry-After headers.
    """

    def __init__(self, config: Optional[Dict] = None):
        """
        Initialize rate limiter with configuration.

        config should be a dict with:
        - conversations_list: requests per minute (default: 50)
        - conversations_history: requests per minute (default: 20)
        - conversations_replies: requests per minute (default: 20)
        - files_list: requests per minute (default: 20)
        - files_info: requests per minute (default: 50)
        - users_info: requests per minute (default: 60)
        """
        self.config = config or {}
        self.default_rate = 20  # conservative default

        # Initialize token buckets per method
        self.buckets: Dict[str, dict] = {}
        self._init_buckets()

        # Track retry-after headers globally
        self.global_retry_until = 0.0
        self.lock = asyncio.Lock()

    def _init_buckets(self) -> None:
        """Initialize token buckets for each API method."""
        methods = [
            "conversations.list",
            "conversations.history",
            "conversations.replies",
            "files.list",
            "files.info",
            "users.info",
        ]

        current_time = time.time()
        for method in methods:
            # Get requests per minute from config, convert to tokens per second
            rate = self.config.get(method.replace(".", "_"), self.default_rate)
            tokens_per_second = rate / 60.0

            self.buckets[method] = {
                "tokens": rate,  # Start with full bucket
                "max_tokens": rate,
                "tokens_per_second": tokens_per_second,
                "last_refill": current_time,
                "retry_until": 0.0,
            }

    async def acquire(self, method: str, required_tokens: int = 1) -> None:
        """
        Acquire tokens for a method call.
        Blocks if insufficient tokens available.

        Args:
            method: Slack API method name (e.g., "conversations.history")
            required_tokens: Number of tokens needed (default: 1)
        """
        async with self.lock:
            # Check if we're under global rate limit
            current_time = time.time()
            if current_time < self.global_retry_until:
                sleep_time = self.global_retry_until - current_time
                await asyncio.sleep(sleep_time)
                current_time = time.time()

            # Initialize bucket if it doesn't exist
            if method not in self.buckets:
                self.buckets[method] = {
                    "tokens": self.default_rate,
                    "max_tokens": self.default_rate,
                    "tokens_per_second": self.default_rate / 60.0,
                    "last_refill": current_time,
                    "retry_until": 0.0,
                }

            bucket = self.buckets[method]

            # Refill tokens based on time passed
            time_passed = current_time - bucket["last_refill"]
            bucket["tokens"] = min(
                bucket["max_tokens"],
                bucket["tokens"] + time_passed * bucket["tokens_per_second"],
            )
            bucket["last_refill"] = current_time

            # Check if method-specific retry is in effect
            if current_time < bucket["retry_until"]:
                sleep_time = bucket["retry_until"] - current_time
                await asyncio.sleep(sleep_time)
                bucket["tokens"] = 0  # Reset tokens after retry
                bucket["last_refill"] = time.time()

            # Wait for sufficient tokens
            while bucket["tokens"] < required_tokens:
                wait_time = (required_tokens - bucket["tokens"]) / bucket["tokens_per_second"]
                await asyncio.sleep(wait_time)
                current_time = time.time()
                time_passed = current_time - bucket["last_refill"]
                bucket["tokens"] = min(
                    bucket["max_tokens"],
                    bucket["tokens"] + time_passed * bucket["tokens_per_second"],
                )
                bucket["last_refill"] = current_time

            # Consume tokens
            bucket["tokens"] -= required_tokens

    def set_retry_after(self, method: str, retry_after_seconds: float, is_global: bool = False) -> None:
        """
        Set retry-after delay for a method or globally.

        Args:
            method: API method name
            retry_after_seconds: Seconds to wait before retrying
            is_global: If True, apply to all methods globally
        """
        retry_until = time.time() + retry_after_seconds

        if is_global:
            self.global_retry_until = max(self.global_retry_until, retry_until)
        else:
            if method in self.buckets:
                self.buckets[method]["retry_until"] = max(
                    self.buckets[method]["retry_until"],
                    retry_until,
                )

    def get_stats(self) -> Dict:
        """Get current rate limiter statistics."""
        return {
            "buckets": {
                method: {
                    "tokens": round(bucket["tokens"], 2),
                    "max_tokens": bucket["max_tokens"],
                    "retry_until": bucket.get("retry_until", 0),
                }
                for method, bucket in self.buckets.items()
            },
            "global_retry_until": self.global_retry_until,
        }


# Singleton instance
_rate_limiter: Optional[RateLimiter] = None


def get_rate_limiter(config: Optional[Dict] = None) -> RateLimiter:
    """Get or create the singleton rate limiter."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(config)
    return _rate_limiter
