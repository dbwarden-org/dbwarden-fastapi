from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Literal

from dbwarden_fastapi.engines import dispose_engines
from dbwarden_fastapi.context import migration_context


@asynccontextmanager
async def dbwarden_lifespan(
    app=None,
    *,
    mode: Literal["check", "migrate", "none"] = "check",
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
    background_migrations: bool = False,
    background_migration_readiness: Literal["block", "serve", "fail"] = "block",
    metrics_refresh_interval: float | None = 30.0,
    opentelemetry: bool = False,
):
    """FastAPI lifespan context manager for dbwarden.

    Handles the full engine lifecycle: optional startup schema
    validation (or auto-migration), readiness gate, seed application,
    connection pool warmup, and cleanup on shutdown.

    Usage::

        from contextlib import asynccontextmanager
        from fastapi import FastAPI
        from dbwarden_fastapi import dbwarden_lifespan

        @asynccontextmanager
        async def lifespan(app: FastAPI):
            async with dbwarden_lifespan(app, mode="check"):
                yield

        app = FastAPI(lifespan=lifespan)

    Parameters
    ----------
    app: FastAPI application instance (optional, for router registration).
    mode:
        - ``"check"``: run read-only schema validation at startup
          (default). Raises on drift unless *fail_fast=False*.
        - ``"migrate"``: auto-apply pending migrations at startup.
          Blocked in production unless *allow_in_production=True*.
        - ``"none"``: skip all startup checks.
    database: Target a single database by name.
    all_databases: Target all configured databases.
    dev: Enable dev-mode SQL translation.
    strict_translation: Raise on untranslatable SQL instead of warning.
    with_backup: Back up before migrating (mode="migrate" only).
    backup_dir: Custom backup directory.
    verbose: Enable verbose logging.
    allow_in_production: Allow auto-migration in production.
    fail_fast: Raise immediately on startup check failure (default True).
    only_dev: Only run checks/migration in development environments.
    readiness_gate: When True, raise if any database is unreachable
      after startup checks.
    apply_seeds: Apply pending seed data after migrations.
    pool_warmup: Acquire pool_warmup_size connections before yielding
      to avoid cold-start latency on first requests.
    pool_warmup_size: Number of connections to acquire during warmup.
    background_migrations: Run ``mode="migrate"`` after the application starts.
    background_migration_readiness: ``"block"`` preserves normal startup,
      ``"serve"`` starts while migration runs, and ``"fail"`` refuses to
      start when migrations are pending.
    metrics_refresh_interval: Periodically refresh pending-migration metrics;
      set to ``None`` to disable the periodic task (stale scrapes still refresh).
    opentelemetry: Instrument the app when the optional OpenTelemetry packages
      are installed.
    """
    if background_migration_readiness not in {"block", "serve", "fail"}:
        raise ValueError("background_migration_readiness must be 'block', 'serve', or 'fail'")
    if background_migrations and mode != "migrate":
        raise ValueError("background_migrations requires mode='migrate'")
    if metrics_refresh_interval is not None and metrics_refresh_interval <= 0:
        raise ValueError("metrics_refresh_interval must be greater than zero")

    metrics_refresher = None
    migration_task: asyncio.Task[None] | None = None
    try:
        if opentelemetry and app is not None:
            instrument_app(app)
        if background_migrations and background_migration_readiness == "serve":
            migration_task = asyncio.create_task(asyncio.to_thread(
                _migrate_in_background, database, all_databases, dev,
                strict_translation, with_backup, backup_dir, verbose,
                allow_in_production, fail_fast, only_dev,
            ))
        elif background_migrations and background_migration_readiness == "fail":
            pending = [result.database for result in _startup_results(database, all_databases) if result.pending_migrations]
            if pending:
                raise RuntimeError(f"Startup blocked: pending migrations for {', '.join(pending)}")
        elif mode != "none":
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
                pass
        if readiness_gate:
            _check_readiness(database=database, all_databases=all_databases)
        if apply_seeds:
            await _apply_seeds(database=database, all_databases=all_databases, verbose=verbose)
        if pool_warmup:
            _warmup_pools(database=database, all_databases=all_databases, size=pool_warmup_size)
        if metrics_refresh_interval is not None:
            from dbwarden.metrics import metrics_enabled
            from dbwarden_fastapi.metrics import MetricsRefresher

            if metrics_enabled():
                metrics_refresher = MetricsRefresher(metrics_refresh_interval)
                if app is not None:
                    app.state.dbwarden_metrics_refresher = metrics_refresher
                metrics_refresher.start()
        yield
    finally:
        if metrics_refresher is not None:
            await metrics_refresher.stop()
        if migration_task is not None:
            # asyncio cancellation cannot stop synchronous work submitted to a
            # thread. Await it before disposing engines it may still be using.
            await migration_task
        dispose_engines()


def _startup_results(database: str | None, all_databases: bool):
    from dbwarden_fastapi.runtime import check_startup

    return check_startup(database=database, all_databases=all_databases)


def _migrate_in_background(
    database: str | None, all_databases: bool, dev: bool, strict_translation: bool,
    with_backup: bool, backup_dir: str | None, verbose: bool,
    allow_in_production: bool, fail_fast: bool, only_dev: bool,
) -> None:
    """Run synchronous core migration work off the event loop."""
    from dbwarden_fastapi.context import migrate_on_startup

    try:
        migrate_on_startup(
            database=database, all_databases=all_databases, dev=dev,
            strict_translation=strict_translation, with_backup=with_backup,
            backup_dir=backup_dir, verbose=verbose,
            allow_in_production=allow_in_production, fail_fast=fail_fast,
            only_dev=only_dev,
        )
    except Exception:
        logging.getLogger(__name__).exception("Background dbwarden migration failed")


def instrument_app(app) -> None:
    """Instrument a FastAPI app if OpenTelemetry is installed."""
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    except ImportError as exc:
        raise RuntimeError(
            "opentelemetry-instrumentation-fastapi is required when opentelemetry=True"
        ) from exc
    FastAPIInstrumentor.instrument_app(app)


def _check_readiness(database: str | None = None, all_databases: bool = False) -> None:
    """Raise RuntimeError if any target database is unreachable."""
    from dbwarden_fastapi.runtime import check_startup, resolved_databases

    targets = resolved_databases(all_databases) if all_databases else (
        [database] if database else resolved_databases(True)
    )
    for name in targets:
        try:
            from dbwarden_fastapi.runtime import check_database_health
            result = check_database_health(name)
            if result.status != "ok":
                raise RuntimeError(f"Readiness gate failed: database '{name}' status is '{result.status}'")
        except Exception as exc:
            if not isinstance(exc, RuntimeError):
                raise RuntimeError(f"Readiness gate failed: database '{name}' unreachable: {exc}") from exc
            raise


async def _apply_seeds(database: str | None = None, all_databases: bool = False, verbose: bool = False) -> None:
    """Apply pending seed data."""
    from dbwarden.commands.seeds import seed_apply_cmd

    if all_databases:
        from dbwarden.config import get_multi_db_config
        for db_name in get_multi_db_config().databases:
            seed_apply_cmd(database=db_name, verbose=verbose)
    else:
        seed_apply_cmd(database=database, verbose=verbose)


def _warmup_pools(database: str | None = None, all_databases: bool = False, size: int = 3) -> None:
    """Acquire connections from engine pools to reduce cold-start latency."""
    from dbwarden.config import get_database, get_multi_db_config
    from sqlalchemy import create_engine, text

    targets = list(get_multi_db_config().databases.keys()) if all_databases else (
        [database] if database else [get_multi_db_config().default]
    )
    if size < 1:
        raise ValueError("pool_warmup_size must be greater than zero")
    for name in targets:
        try:
            config = get_database(name)
            url = config.sqlalchemy_url_sync or config.sqlalchemy_url
            if url and not str(url).startswith("clickhouse"):
                engine = create_engine(url)
                connections = []
                try:
                    for _ in range(min(size, 5)):
                        try:
                            conn = engine.connect()
                            conn.execute(text("SELECT 1"))
                            connections.append(conn)
                        except Exception:
                            break
                finally:
                    for conn in connections:
                        conn.close()
                    engine.dispose()
        except Exception:
            pass
