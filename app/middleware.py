"""
Rate Limiting Middleware for FastAPI.
"""

import logging
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from app.config import settings
from app.rate_limiter import TokenBucketLimiter, get_redis
from app.metrics import metrics

logger = logging.getLogger(__name__)

EXEMPT_ROUTES = {"/health", "/docs", "/openapi.json", "/redoc", "/metrics"}


def _resolve_client_id(request: Request) -> tuple[str, str]:
    user_id = request.headers.get("X-User-ID")
    role    = request.headers.get("X-User-Role", "free").lower()

    if user_id:
        return f"user:{user_id}", role

    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        ip = forwarded_for.split(",")[0].strip()
    else:
        ip = request.client.host if request.client else "unknown"

    return f"ip:{ip}", "anonymous"


def _resolve_limits(route: str, role: str) -> tuple[int, float]:
    if route in settings.route_limits:
        rl = settings.route_limits[route]
        return rl["capacity"], rl["refill_rate"]

    if role in settings.role_limits:
        rl = settings.role_limits[role]
        return rl["capacity"], rl["refill_rate"]

    return settings.default_capacity, settings.default_refill_rate


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        route = request.url.path

        if route in EXEMPT_ROUTES:
            return await call_next(request)

        client_id, role = _resolve_client_id(request)
        capacity, refill_rate = _resolve_limits(route, role)

        redis   = await get_redis()
        limiter = TokenBucketLimiter(redis)
        result  = await limiter.check(client_id, route, capacity, refill_rate)

        headers = {
            "X-RateLimit-Limit":     str(capacity),
            "X-RateLimit-Remaining": f"{result.tokens_remaining:.2f}",
            "X-RateLimit-Policy":    f"capacity={capacity};rate={refill_rate}/s",
        }

        if result.allowed:
            metrics.record_allowed(route)
            response = await call_next(request)
            for k, v in headers.items():
                response.headers[k] = v
            return response

        metrics.record_rejected(route, client_id)
        retry_after_sec = max(1, result.retry_after_ms // 1000)
        headers["Retry-After"] = str(retry_after_sec)

        return JSONResponse(
            status_code=429,
            content={
                "error":       "Too Many Requests",
                "message":     f"Rate limit exceeded. Retry after {retry_after_sec}s.",
                "retry_after": retry_after_sec,
                "client":      client_id,
            },
            headers=headers,
        )
