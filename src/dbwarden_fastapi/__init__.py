from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from contextlib import asynccontextmanager
from typing import Any

from dbwarden_fastapi.auto_schema import auto_schema, SchemaConfig
from dbwarden_fastapi.context import (
    check_schema_on_startup,
    migrate_on_startup,
    migration_context,
)
from dbwarden_fastapi.engines import dispose_engines
from dbwarden_fastapi.health import DBWardenHealthRouter
from dbwarden_fastapi.lifespan import dbwarden_lifespan
from dbwarden_fastapi.lock import migration_lock, sync_migration_lock
from dbwarden_fastapi.metrics import MetricsMiddleware, MetricsRouter
from dbwarden_fastapi.observation import PoolMetricsCollector, QueryTracingMiddleware
from dbwarden_fastapi.routers import health_routes, migration_routes
from dbwarden_fastapi.routes import DBWardenRouter
from dbwarden_fastapi.session import get_session
from dbwarden_fastapi.testing import migration_state, override_database

__version__ = "0.1.2"

# The dbwarden plugin contract this package targets. Core refuses to load a
# plugin declaring a version it does not provide, so a mismatched pairing fails
# at load with one clear message instead of somewhere inside a migration.
DBWARDEN_PLUGIN_API = 1



def session_factory(database: str | None = None, *, dev: bool = False):
    from dbwarden_fastapi.engines import _async_session_factory

    async def _dependency() -> AsyncGenerator[Any, None]:
        factory = _async_session_factory(database, dev=dev)
        async with factory() as session:
            yield session

    return _dependency


def sync_session_factory(database: str | None = None, *, dev: bool = False):
    from dbwarden_fastapi.engines import _sync_session_factory

    def _dependency() -> Generator[Any, None, None]:
        factory = _sync_session_factory(database, dev=dev)
        with factory() as session:
            yield session

    return _dependency


def clickhouse_session_factory(database: str | None = None, *, dev: bool = False):
    from dbwarden.config import get_database
    from dbwarden_fastapi.engines import _CLICKHOUSE_ASYNC_CLIENTS, _parse_clickhouse_url
    from dbwarden_fastapi.runtime import runtime_flags

    async def _dependency() -> AsyncGenerator[Any, None]:
        name = database or "default"
        if name not in _CLICKHOUSE_ASYNC_CLIENTS:
            import clickhouse_connect

            with runtime_flags(dev=dev, strict_translation=False):
                config = get_database(database)
            _CLICKHOUSE_ASYNC_CLIENTS[name] = await clickhouse_connect.get_async_client(
                **_parse_clickhouse_url(config.sqlalchemy_url)
            )
        yield _CLICKHOUSE_ASYNC_CLIENTS[name]

    return _dependency


def clickhouse_sync_session_factory(database: str | None = None, *, dev: bool = False):
    from dbwarden.config import get_database
    from dbwarden_fastapi.engines import _CLICKHOUSE_SYNC_CLIENTS, _parse_clickhouse_url
    from dbwarden_fastapi.runtime import runtime_flags

    def _dependency() -> Generator[Any, None, None]:
        name = database or "default"
        if name not in _CLICKHOUSE_SYNC_CLIENTS:
            import clickhouse_connect

            with runtime_flags(dev=dev, strict_translation=False):
                config = get_database(database)
            _CLICKHOUSE_SYNC_CLIENTS[name] = clickhouse_connect.get_client(
                **_parse_clickhouse_url(config.sqlalchemy_url)
            )
        yield _CLICKHOUSE_SYNC_CLIENTS[name]

    return _dependency


@asynccontextmanager
async def lifespan_hook(
    app=None,
    *,
    mode: str = "check",
    database: str | None = None,
    all_databases: bool = False,
    dev: bool = False,
    strict_translation: bool = False,
    with_backup: bool = False,
    backup_dir: str | None = None,
    verbose: bool = False,
    allow_in_production: bool = False,
    fail_fast: bool = True,
    only_dev: bool = False,
    readiness_gate: bool = False,
    apply_seeds: bool = False,
    pool_warmup: bool = False,
    pool_warmup_size: int = 3,
):
    from dbwarden_fastapi.engines import dispose_engines
    from dbwarden_fastapi.lifespan import _apply_seeds, _check_readiness, _warmup_pools
    from dbwarden_fastapi.context import migration_context

    try:
        if mode != "none":
            async with migration_context(
                mode=mode,
                database=database,
                all_databases=all_databases,
                dev=dev,
                strict_translation=strict_translation,
                with_backup=with_backup,
                backup_dir=backup_dir,
                verbose=verbose,
                allow_in_production=allow_in_production,
                fail_fast=fail_fast,
                only_dev=only_dev,
            ):
                if readiness_gate:
                    _check_readiness(database=database, all_databases=all_databases)
                if apply_seeds:
                    await _apply_seeds(database=database, all_databases=all_databases, verbose=verbose)
                if pool_warmup:
                    _warmup_pools(database=database, all_databases=all_databases, size=pool_warmup_size)
                yield
        else:
            yield
    finally:
        dispose_engines()



def setup(registrar) -> None:
    registrar.register("session_factory", session_factory)
    registrar.register("sync_session_factory", sync_session_factory)
    registrar.register("clickhouse_session_factory", clickhouse_session_factory)
    registrar.register("clickhouse_sync_session_factory", clickhouse_sync_session_factory)
    registrar.register("lifespan", lifespan_hook)
    registrar.register("health_routes", health_routes)
    registrar.register("migration_routes", migration_routes)


__all__ = [
    "DBWardenHealthRouter",
    "DBWardenRouter",
    "MetricsMiddleware",
    "MetricsRouter",
    "PoolMetricsCollector",
    "QueryTracingMiddleware",
    "SchemaConfig",
    "auto_schema",
    "check_schema_on_startup",
    "clickhouse_session_factory",
    "clickhouse_sync_session_factory",
    "dbwarden_lifespan",
    "dispose_engines",
    "get_session",
    "health_routes",
    "lifespan_hook",
    "migrate_on_startup",
    "migration_context",
    "migration_lock",
    "migration_routes",
    "migration_state",
    "override_database",
    "session_factory",
    "setup",
    "sync_migration_lock",
    "sync_session_factory",
]
