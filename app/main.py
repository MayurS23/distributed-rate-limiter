"""
Distributed Rate Limiting Service — FastAPI Application

Run locally:
    uvicorn app.main:app --reload

Run with Docker:
    docker compose up --build
"""

import logging
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, Header
from fastapi.responses import JSONResponse

from app.config import settings
from app.middleware import RateLimitMiddleware
from app.rate_limiter import get_redis, close_redis
from app.metrics import metrics

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Connecting to Redis at %s:%d", settings.redis_host, settings.redis_port)
    redis = await get_redis()
    try:
        await redis.ping()
        logger.info("Redis connection OK")
    except Exception as e:
        logger.warning("Redis not reachable at startup: %s", e)
    yield
    logger.info("Shutting down — closing Redis connection")
    await close_redis()


app = FastAPI(
    title=settings.app_title,
    version=settings.app_version,
    description=(
        "Production-grade distributed rate limiting service using "
        "Token Bucket algorithm backed by Redis."
    ),
    lifespan=lifespan,
)

app.add_middleware(RateLimitMiddleware)


# ---------------------------------------------------------------------------
# System endpoints (exempt from rate limiting)
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
async def health():
    """Liveness check. Pings Redis and reports status."""
    redis = await get_redis()
    try:
        await redis.ping()
        redis_status = "ok"
    except Exception:
        redis_status = "unavailable"

    return {
        "status":    "ok",
        "redis":     redis_status,
        "fail_open": settings.fail_open,
        "version":   settings.app_version,
    }


@app.get("/metrics", tags=["System"])
async def get_metrics():
    """Rate limiter metrics: allowed/rejected counts per route."""
    return metrics.snapshot()


# ---------------------------------------------------------------------------
# Demo API endpoints (rate-limited)
# ---------------------------------------------------------------------------

@app.get("/api/data", tags=["Demo API"])
async def get_data(
    x_user_id:   Optional[str] = Header(None),
    x_user_role: Optional[str] = Header(None),
):
    """General endpoint — default limit (10 burst, 1/sec)."""
    return {
        "message": "Here is your data! 🎉",
        "user":    x_user_id or "anonymous",
        "role":    x_user_role or "free",
    }


@app.get("/api/search", tags=["Demo API"])
async def search(
    q: str = "hello",
    x_user_id: Optional[str] = Header(None),
):
    """Search endpoint — higher limit (20 burst, 5/sec)."""
    return {
        "query":   q,
        "results": [f"Result {i} for '{q}'" for i in range(1, 4)],
    }


@app.get("/api/heavy", tags=["Demo API"])
async def heavy_operation(x_user_id: Optional[str] = Header(None)):
    """Expensive endpoint — tight limit (3 burst, 0.5/sec)."""
    return {"message": "Heavy computation complete ✅"}


@app.get("/api/admin", tags=["Demo API"])
async def admin_endpoint(
    x_user_id:   Optional[str] = Header(None),
    x_user_role: Optional[str] = Header(None),
):
    """Admin endpoint — role-based limit (1000 burst, 100/sec for admin)."""
    return {
        "message": "Admin access granted",
        "user":    x_user_id,
        "role":    x_user_role,
    }


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception: %s", exc, exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "detail": str(exc)},
    )
