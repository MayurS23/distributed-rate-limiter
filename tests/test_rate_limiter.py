"""
Test suite for the Distributed Rate Limiting Service.

Run:
    pytest tests/ -v
"""

import time
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient
import fakeredis.aioredis as fakeredis

from app.main import app
from app.rate_limiter import TokenBucketLimiter, RateLimitResult
from app.metrics import MetricsStore
from app.config import settings


@pytest.fixture
def client():
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest_asyncio.fixture
async def fake_redis():
    r = fakeredis.FakeRedis(decode_responses=True)
    yield r
    await r.aclose()


@pytest_asyncio.fixture
async def limiter(fake_redis):
    return TokenBucketLimiter(fake_redis)


# ---------------------------------------------------------------------------
# Token Bucket Unit Tests
# ---------------------------------------------------------------------------

class TestTokenBucket:

    @pytest.mark.asyncio
    async def test_first_request_allowed(self, limiter):
        result = await limiter.check("user:1", "/api/data", capacity=10, refill_rate=1.0)
        assert result.allowed is True
        assert result.tokens_remaining >= 8.9

    @pytest.mark.asyncio
    async def test_burst_exhaustion(self, limiter):
        capacity = 5
        for i in range(capacity):
            r = await limiter.check("user:burst", "/api/data", capacity=capacity, refill_rate=0.1)
            assert r.allowed, f"Request {i+1} should be allowed"

        result = await limiter.check("user:burst", "/api/data", capacity=capacity, refill_rate=0.1)
        assert result.allowed is False
        assert result.retry_after_ms > 0

    @pytest.mark.asyncio
    async def test_refill_over_time(self, limiter, fake_redis):
        capacity, rate = 2, 1.0
        for _ in range(capacity):
            await limiter.check("user:refill", "/api/data", capacity=capacity, refill_rate=rate)

        key = "rl:user:refill:/api/data"
        await fake_redis.hset(key, "last_refill", str(time.time() - 2.0))

        result = await limiter.check("user:refill", "/api/data", capacity=capacity, refill_rate=rate)
        assert result.allowed is True

    @pytest.mark.asyncio
    async def test_different_clients_isolated(self, limiter):
        capacity = 1
        r1 = await limiter.check("user:alice", "/api/data", capacity=capacity, refill_rate=1.0)
        r2 = await limiter.check("user:bob",   "/api/data", capacity=capacity, refill_rate=1.0)
        assert r1.allowed is True
        assert r2.allowed is True

    @pytest.mark.asyncio
    async def test_different_routes_isolated(self, limiter):
        capacity = 1
        r1 = await limiter.check("user:1", "/api/route-a", capacity=capacity, refill_rate=1.0)
        r2 = await limiter.check("user:1", "/api/route-b", capacity=capacity, refill_rate=1.0)
        assert r1.allowed is True
        assert r2.allowed is True

    @pytest.mark.asyncio
    async def test_fail_open_on_redis_error(self):
        broken_redis = AsyncMock()
        broken_redis.script_load.side_effect = Exception("Connection refused")
        lim = TokenBucketLimiter(broken_redis)

        with patch.object(settings, "fail_open", True):
            result = await lim.check("user:x", "/api", capacity=10, refill_rate=1.0)
            assert result.allowed is True

    @pytest.mark.asyncio
    async def test_fail_closed_on_redis_error(self):
        broken_redis = AsyncMock()
        broken_redis.script_load.side_effect = Exception("Connection refused")
        lim = TokenBucketLimiter(broken_redis)

        with patch.object(settings, "fail_open", False):
            result = await lim.check("user:x", "/api", capacity=10, refill_rate=1.0)
            assert result.allowed is False


# ---------------------------------------------------------------------------
# Middleware Integration Tests
# ---------------------------------------------------------------------------

class TestRateLimitMiddleware:

    def test_health_endpoint_exempt(self, client):
        for _ in range(20):
            r = client.get("/health")
            assert r.status_code != 429

    def test_rejected_request_returns_429(self, client):
        with patch("app.middleware.TokenBucketLimiter.check") as mock_check, \
             patch("app.middleware.get_redis") as mock_redis:

            mock_redis.return_value = AsyncMock()
            mock_check.return_value = RateLimitResult(
                allowed=False, tokens_remaining=0.0,
                retry_after_ms=2000, client_key="rl:user:1:/api/data"
            )

            r = client.get("/api/data", headers={"X-User-ID": "1"})
            assert r.status_code == 429
            assert "retry_after" in r.json()
            assert r.headers.get("Retry-After") is not None


# ---------------------------------------------------------------------------
# Metrics Unit Tests
# ---------------------------------------------------------------------------

class TestMetrics:

    def test_record_allowed(self):
        m = MetricsStore()
        m.record_allowed("/api/data")
        snap = m.snapshot()
        assert snap["total_allowed"] == 1
        assert snap["total_rejected"] == 0

    def test_record_rejected(self):
        m = MetricsStore()
        m.record_rejected("/api/data", "user:1")
        snap = m.snapshot()
        assert snap["total_rejected"] == 1

    def test_rejection_rate(self):
        m = MetricsStore()
        m.record_allowed("/api/data")
        m.record_allowed("/api/data")
        m.record_rejected("/api/data", "user:1")
        snap = m.snapshot()
        assert snap["rejection_rate"] == pytest.approx(33.33, rel=0.01)
