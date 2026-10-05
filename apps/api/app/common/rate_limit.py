"""Lightweight Redis-based rate limiting."""

from __future__ import annotations

import logging
import time

from fastapi import HTTPException, Request, status

from app.common.config import get_settings

logger = logging.getLogger(__name__)


class RateLimiter:
    """A sliding window rate limiter using Redis sorted sets.

    Protects endpoints by keeping counts within the last N seconds.
    Does not run if Redis is unavailable or in TEST environment to avoid flakiness.
    """

    def __init__(self, requests: int, window: int) -> None:
        self.requests = requests
        self.window = window

    async def __call__(self, request: Request) -> None:
        settings = get_settings()
        if settings.app_env.value == "testing":
            return

        from app.common.dependencies import get_redis

        try:
            redis_client = get_redis()
        except RuntimeError:
            return  # Redis disabled/not initialized

        ip = "127.0.0.1"
        if request.client and request.client.host:
            ip = request.client.host

        now = time.time()
        window_start = now - self.window
        key = f"rate_limit:{ip}:{request.url.path}"

        try:
            async with redis_client.pipeline(transaction=True) as pipe:
                pipe.zremrangebyscore(key, 0, window_start)
                pipe.zadd(key, {str(now): now})
                pipe.zcard(key)
                pipe.expire(key, self.window)
                res = await pipe.execute()

            count = res[2]
            if count > self.requests:
                logger.warning(
                    f"Rate limit exceeded for {ip} on {request.url.path} ({count}/{self.requests})"
                )
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="Too Many Requests"
                )
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Rate limiting failed, passing request: {e}")
            # Fail open if Redis is down
            pass
