"""
Configuration for the Distributed Rate Limiting Service.
All values can be overridden via environment variables.
"""

from pydantic_settings import BaseSettings
from typing import Dict


class Settings(BaseSettings):
    # Redis
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_password: str = ""

    # Default token bucket settings (per user/IP)
    default_capacity: int = 10          # Max burst: 10 requests
    default_refill_rate: float = 1.0    # 1 token/second = 60 req/min

    # Per-route overrides (route_path -> (capacity, refill_rate))
    route_limits: Dict[str, Dict] = {
        "/api/heavy":  {"capacity": 3,  "refill_rate": 0.5},
        "/api/search": {"capacity": 20, "refill_rate": 5.0},
    }

    # Per-role overrides
    role_limits: Dict[str, Dict] = {
        "premium": {"capacity": 50,   "refill_rate": 10.0},
        "admin":   {"capacity": 1000, "refill_rate": 100.0},
        "free":    {"capacity": 5,    "refill_rate": 0.5},
    }

    # Fail-open (True) = allow requests if Redis is down
    # Fail-closed (False) = reject requests if Redis is down
    fail_open: bool = True

    # Token bucket TTL in Redis (seconds)
    bucket_ttl: int = 3600

    app_title: str = "Distributed Rate Limiter"
    app_version: str = "1.0.0"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


settings = Settings()
