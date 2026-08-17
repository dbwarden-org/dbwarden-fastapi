from __future__ import annotations

import contextvars
import threading
import time
from dataclasses import dataclass
from typing import Any

from dbwarden.metrics import metrics_enabled


@dataclass
class _QueryStats:
    query_count: int = 0
    total_query_time: float = 0.0
    slowest_query_time: float = 0.0
    slow_queries: int = 0
    slow_query_threshold_ms: int = 100


_query_stats: contextvars.ContextVar[_QueryStats | None] = contextvars.ContextVar(
    "dbwarden_query_stats", default=None
)
_tracing_events_registered = False
_tracing_events_lock = threading.Lock()


class QueryTracingMiddleware:
    """ASGI middleware that emits per-request structured query tracing logs."""

    def __init__(self, app, slow_query_threshold_ms: int = 100):
        self.app = app
        self.slow_query_threshold_ms = slow_query_threshold_ms

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        import logging

        logger = logging.getLogger("dbwarden.tracing")
        _register_tracing_events()
        start = time.monotonic()
        stats = _QueryStats(slow_query_threshold_ms=self.slow_query_threshold_ms)
        stats_token = _query_stats.set(stats)

        try:
            await self.app(scope, receive, send)
        finally:
            _query_stats.reset(stats_token)
            duration = time.monotonic() - start
            extras: dict[str, Any] = {
                "path": scope.get("path", "/"),
                "method": scope.get("method", ""),
                "request_duration_ms": round(duration * 1000, 2),
                "query_count": stats.query_count,
                "total_query_time_ms": round(stats.total_query_time * 1000, 2),
                "slowest_query_time_ms": round(stats.slowest_query_time * 1000, 2),
                "slow_queries": stats.slow_queries,
            }
            if stats.slow_queries > 0:
                logger.warning("Slow queries detected", extra=extras)
            else:
                logger.info("Request tracing", extra=extras)

            if metrics_enabled():
                try:
                    from dbwarden.metrics import observe_migration_duration
                    observe_migration_duration("_db_query", scope.get("path", "/"), duration)
                except Exception:
                    pass


class PoolMetricsCollector:
    """Collector for SQLAlchemy connection pool metrics."""

    def __init__(self):
        self._engines: dict[str, Any] = {}

    def register(self, name: str, engine) -> None:
        self._engines[name] = engine

    def collect(self) -> dict[str, dict[str, int]]:
        metrics: dict[str, dict[str, int]] = {}
        for name, engine in self._engines.items():
            pool = getattr(engine, "pool", None)
            if pool is None:
                continue
            try:
                metrics[name] = {
                    "pool_size": pool.size(),
                    "checked_out": pool.checkedout(),
                    "overflow": pool.overflow(),
                    "checked_in": pool.size() - pool.checkedout(),
                }
            except Exception:
                metrics[name] = {"pool_size": 0, "checked_out": 0, "overflow": 0, "checked_in": 0}
        return metrics


def _register_tracing_events() -> None:
    """Register process-wide SQLAlchemy listeners once; request state is contextual."""
    global _tracing_events_registered
    if _tracing_events_registered:
        return

    with _tracing_events_lock:
        if _tracing_events_registered:
            return

        from sqlalchemy import event
        from sqlalchemy.engine import Engine

        @event.listens_for(Engine, "before_cursor_execute")
        def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            if _query_stats.get() is not None:
                context._dbwarden_query_start = time.monotonic()

        @event.listens_for(Engine, "after_cursor_execute")
        def after_cursor_execute(conn, cursor, statement, parameters, context, executemany):
            _record_query(context)

        @event.listens_for(Engine, "handle_error")
        def handle_error(exception_context):
            _record_query(exception_context.execution_context)

        _tracing_events_registered = True


def _record_query(context: Any) -> None:
    stats = _query_stats.get()
    start = getattr(context, "_dbwarden_query_start", None)
    if stats is None or start is None:
        return

    elapsed = time.monotonic() - start
    stats.query_count += 1
    stats.total_query_time += elapsed
    stats.slowest_query_time = max(stats.slowest_query_time, elapsed)
    if elapsed * 1000 > stats.slow_query_threshold_ms:
        stats.slow_queries += 1


__all__ = [
    "PoolMetricsCollector",
    "QueryTracingMiddleware",
]
