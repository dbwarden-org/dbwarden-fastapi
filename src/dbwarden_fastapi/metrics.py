from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Request
from fastapi.responses import PlainTextResponse

from dbwarden_fastapi.runtime import resolved_databases
from dbwarden.metrics import (
    generate_metrics,
    metrics_enabled,
    set_pending_migrations,
)


def MetricsRouter() -> APIRouter:
    """Create a FastAPI ``APIRouter`` with a ``GET /metrics`` endpoint.

    The endpoint returns Prometheus text-format metrics.
    It is only active when ``prometheus_client`` is installed **and**
    ``DBWARDEN_METRICS=true`` is set.
    """
    router = APIRouter()

    @router.get("/metrics")
    async def metrics(request: Request) -> PlainTextResponse:
        if not metrics_enabled():
            return PlainTextResponse(
                "# Metrics disabled (set DBWARDEN_METRICS=true to enable)\n",
                media_type="text/plain; version=0.0.4",
                status_code=200,
            )
        refresher = getattr(request.app.state, "dbwarden_metrics_refresher", None)
        if refresher is not None:
            await refresher.refresh_if_stale()
        else:
            # MetricsRouter can be used without dbwarden_lifespan.
            refresher = MetricsRefresher()
            request.app.state.dbwarden_metrics_refresher = refresher
            await refresher.refresh_if_stale()
        return PlainTextResponse(
            generate_metrics(),
            media_type="text/plain; version=0.0.4",
        )

    return router


class MetricsMiddleware:
    """ASGI middleware that tracks request duration without database work."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if metrics_enabled() and scope["type"] == "http":
            start = time.time()
            try:
                await self.app(scope, receive, send)
            finally:
                duration = time.time() - start
                try:
                    from dbwarden.metrics import observe_migration_duration

                    observe_migration_duration("_http_request", scope.get("path", "/"), duration)
                except Exception:
                    pass
        else:
            await self.app(scope, receive, send)


class MetricsRefresher:
    """Refresh pending-migration gauges periodically and on stale scrapes."""

    def __init__(self, interval: float = 30.0) -> None:
        if interval <= 0:
            raise ValueError("metrics_refresh_interval must be greater than zero")
        self.interval = interval
        self.last_refresh = 0.0
        self._task: asyncio.Task[None] | None = None
        self._lock = asyncio.Lock()

    async def refresh_if_stale(self) -> None:
        if time.monotonic() - self.last_refresh >= self.interval:
            await self.refresh()

    async def refresh(self) -> None:
        async with self._lock:
            if time.monotonic() - self.last_refresh < self.interval:
                return
            try:
                databases = resolved_databases(all_databases=True)
            except Exception:
                databases = []
            for name in databases:
                try:
                    from dbwarden_fastapi.runtime import compute_pending_migrations

                    set_pending_migrations(name, compute_pending_migrations(name))
                except Exception:
                    # A scrape must remain available when one database is down.
                    pass
            self.last_refresh = time.monotonic()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _run(self) -> None:
        while True:
            await self.refresh()
            await asyncio.sleep(self.interval)
