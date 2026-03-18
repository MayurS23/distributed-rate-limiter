"""
In-process metrics for the rate limiter.
"""

import time
from collections import defaultdict
from typing import Dict


class RouteMetrics:
    def __init__(self):
        self.allowed  = 0
        self.rejected = 0


class MetricsStore:
    def __init__(self):
        self._routes: Dict[str, RouteMetrics] = defaultdict(RouteMetrics)
        self._rejected_clients = []
        self._started_at = time.time()

    def record_allowed(self, route: str):
        self._routes[route].allowed += 1

    def record_rejected(self, route: str, client_id: str):
        self._routes[route].rejected += 1
        self._rejected_clients.append({
            "client": client_id,
            "route":  route,
            "ts":     time.time(),
        })
        if len(self._rejected_clients) > 100:
            self._rejected_clients.pop(0)

    def snapshot(self) -> dict:
        total_allowed  = sum(r.allowed  for r in self._routes.values())
        total_rejected = sum(r.rejected for r in self._routes.values())
        return {
            "uptime_seconds": round(time.time() - self._started_at, 1),
            "total_allowed":  total_allowed,
            "total_rejected": total_rejected,
            "rejection_rate": (
                round(total_rejected / (total_allowed + total_rejected) * 100, 2)
                if (total_allowed + total_rejected) > 0 else 0.0
            ),
            "by_route": {
                route: {"allowed": m.allowed, "rejected": m.rejected}
                for route, m in self._routes.items()
            },
            "recent_rejections": self._rejected_clients[-10:],
        }


metrics = MetricsStore()
