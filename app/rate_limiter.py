"""
Token Bucket Rate Limiter — Redis-backed, async, atomic.

Algorithm:
  Each client gets a "bucket" with up to `capacity` tokens.
  Tokens refill at `refill_rate` tokens/second.
  Each request consumes 1 token.
  If the bucket is empty → reject with 429.

Atomicity:
  A Lua script runs the check-and-update as a single Redis command,
  eliminating TOCTOU race conditions under concurrent load.
"""

import time
import logging
from dataclasses import dataclass
from typing import Optional

import redis.asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lua script — atomic token bucket on Redis
# ---------------------------------------------------------------------------
LUA_TOKEN_BUCKET = """
local key          = KEYS[1]
local capacity     = tonumber(ARGV[1])
local refill_rate  = tonumber(ARGV[2])
local now          = tonumber(ARGV[3])
local ttl          = tonumber(ARGV[4])

local data = redis.call("HMGET", key, "tokens", "last_refill")
local tokens      = tonumber(data[1])
local last_refill = tonumber(data[2])

if tokens == nil then
    tokens      = capacity
    last_refill = now
end

local elapsed = math.max(0, now - last_refill)
tokens = math.min(capacity, tokens + elapsed * refill_rate)

local allowed     = 0
local retry_after = 0

if tokens >= 1 then
    tokens  = tokens - 1
    allowed = 1
else
    retry_after = math.ceil((1 - tokens) / refill_rate * 1000)
end

redis.call("HMSET", key, "tokens", tokens, "last_refill", now)
redis.call("EXPIRE", key, ttl)

return { string.format("%.4f", tokens), allowed, retry_after }
"""


@dataclass
class RateLimitResult:
    allowed: bool
    tokens_remaining: float
    retry_after_ms: int
    client_key: str


class TokenBucketLimiter:
    def __init__(self, redis: Redis):
        self._redis = redis
        self._script_sha: Optional[str] = None

    async def _load_script(self) -> str:
        if self._script_sha is None:
            self._script_sha = await self._redis.script_load(LUA_TOKEN_BUCKET)
        return self._script_sha

    async def check(
        self,
        client_id: str,
        route: str,
        capacity: int,
        refill_rate: float,
    ) -> RateLimitResult:
        bucket_key = f"rl:{client_id}:{route}"
        now = time.time()

        try:
            sha = await self._load_script()
            result = await self._redis.evalsha(
                sha,
                1,
                bucket_key,
                str(capacity),
                str(refill_rate),
                str(now),
                str(settings.bucket_ttl),
            )
            tokens_remaining = float(result[0])
            allowed          = bool(int(result[1]))
            retry_after_ms   = int(result[2])

            if not allowed:
                logger.warning(
                    "RATE_LIMITED client=%s route=%s retry_after_ms=%d",
                    client_id, route, retry_after_ms,
                )

            return RateLimitResult(
                allowed=allowed,
                tokens_remaining=tokens_remaining,
                retry_after_ms=retry_after_ms,
                client_key=bucket_key,
            )

        except RedisError as exc:
            logger.error("Redis error during rate check: %s", exc)
            if settings.fail_open:
                logger.warning("Fail-OPEN: allowing request despite Redis error")
                return RateLimitResult(
                    allowed=True,
                    tokens_remaining=-1,
                    retry_after_ms=0,
                    client_key=bucket_key,
                )
            else:
                logger.warning("Fail-CLOSED: rejecting request due to Redis error")
                return RateLimitResult(
                    allowed=False,
                    tokens_remaining=0,
                    retry_after_ms=1000,
                    client_key=bucket_key,
                )


# ---------------------------------------------------------------------------
# Redis connection pool
# ---------------------------------------------------------------------------
_redis_pool: Optional[Redis] = None


async def get_redis() -> Redis:
    global _redis_pool
    if _redis_pool is None:
        _redis_pool = aioredis.Redis(
            host=settings.redis_host,
            port=settings.redis_port,
            db=settings.redis_db,
            password=settings.redis_password or None,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
            retry_on_timeout=True,
        )
    return _redis_pool


async def close_redis():
    global _redis_pool
    if _redis_pool:
        await _redis_pool.aclose()
        _redis_pool = None
