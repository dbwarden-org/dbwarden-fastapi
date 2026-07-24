"""Router factories behind the ``health_routes`` and ``migration_routes`` hooks.

Deliberately no ``from __future__ import annotations``: FastAPI resolves route
signatures against this module's globals, and postponed evaluation would leave
the response models as unresolvable forward references.
"""

import os

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from fastapi.security import APIKeyHeader

from dbwarden.commands.migrate import migrate_cmd
from dbwarden.config import get_multi_db_config
from dbwarden.repositories import check_lock

from dbwarden_fastapi.health import (
    HealthResponse,
    LivenessResponse,
    ReadinessResponse,
    _aggregate_status,
    _status_to_code,
)
from dbwarden_fastapi.routes import (
    DatabaseStatus,
    MigrateRequest,
    MigrateResponse,
    StatusResponse,
    _compute_applied_migrations,
    _compute_applied_seeds,
    _compute_pending_seeds,
)
from dbwarden_fastapi.runtime import (
    check_database_health,
    check_startup,
    compute_pending_migrations,
)


def health_routes(*, auth_mode: str = "open", api_key: str | None = None):
    router = APIRouter()
    mode = os.environ.get("DBWARDEN_HEALTH_AUTH", auth_mode)

    async def require_auth(key: str | None = Depends(APIKeyHeader(name="X-API-Key", auto_error=False))) -> None:
        if mode == "open":
            return
        if not key:
            raise HTTPException(status_code=401, detail="API key required")
        if api_key and key != api_key:
            raise HTTPException(status_code=403, detail="Invalid API key")

    @router.get("/", response_model=HealthResponse)
    async def overall_health(_: None = Depends(require_auth)) -> JSONResponse:
        results = check_startup(all_databases=True)
        payload = [r.to_database_health() for r in results]
        status = _aggregate_status(payload)
        code = _status_to_code(status)
        return JSONResponse(status_code=code, content=HealthResponse(status=status, databases=payload).model_dump())

    @router.get("/liveness")
    async def liveness_route(_: None = Depends(require_auth)) -> LivenessResponse:
        return LivenessResponse(status="alive")

    @router.get("/readiness")
    async def readiness_route(_: None = Depends(require_auth)) -> JSONResponse:
        results = check_startup(all_databases=True)
        payload = [r.to_database_health() for r in results]
        status = _aggregate_status(payload)
        code = 200 if status == "ok" else 503
        return JSONResponse(status_code=code, content=ReadinessResponse(status=status, databases=payload).model_dump())

    @router.get("/{database_name}", response_model=HealthResponse)
    async def one_database_health(database_name: str, _: None = Depends(require_auth)) -> JSONResponse:
        cfg = get_multi_db_config()
        if database_name not in cfg.databases:
            raise HTTPException(status_code=404, detail="Database not found")
        result = check_database_health(database_name)
        payload = [result.to_database_health()]
        status = _aggregate_status(payload)
        code = _status_to_code(status)
        return JSONResponse(status_code=code, content=HealthResponse(status=status, databases=payload).model_dump())

    return router


def migration_routes(*, auth_mode: str = "open", api_key: str | None = None):

    router = APIRouter()
    mode = os.environ.get("DBWARDEN_MIGRATE_AUTH", auth_mode)
    key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

    async def require_auth(key: str | None = Depends(key_header)) -> None:
        if mode == "open":
            return
        if not key:
            raise HTTPException(status_code=401, detail="API key required")
        if api_key and key != api_key:
            raise HTTPException(status_code=403, detail="Invalid API key")

    def compute_status(db_name: str) -> DatabaseStatus:
        pending = compute_pending_migrations(db_name)
        applied = _compute_applied_migrations(db_name)
        pending_seeds = _compute_pending_seeds(db_name)
        applied_seeds = _compute_applied_seeds(db_name)
        lock_active = check_lock(db_name)
        error = None
        connected = True
        try:
            from dbwarden.database.connection import get_db_connection
            from sqlalchemy import text

            with get_db_connection(db_name) as conn:
                conn.execute(text("SELECT 1"))
        except Exception as exc:
            connected = False
            error = str(exc)
        status = "error" if not connected else "degraded" if pending > 0 or pending_seeds > 0 else "ok"
        return DatabaseStatus(
            database=db_name,
            status=status,
            connected=connected,
            pending_migrations=pending,
            applied_migrations=applied,
            pending_seeds=pending_seeds,
            applied_seeds=applied_seeds,
            lock_active=lock_active,
            error=error,
        )

    @router.get("/status", response_model=StatusResponse)
    async def dbwarden_status(_: None = Depends(require_auth)) -> JSONResponse:
        cfg = get_multi_db_config()
        return JSONResponse(content=StatusResponse(databases={name: compute_status(name) for name in cfg.databases}).model_dump())

    @router.post("/migrate", response_model=MigrateResponse)
    async def dbwarden_migrate(body: MigrateRequest, _: None = Depends(require_auth)) -> JSONResponse:
        target_db = body.database
        if target_db is not None and target_db not in get_multi_db_config().databases:
            raise HTTPException(status_code=404, detail="Database not found")
        try:
            migrate_cmd(
                count=body.count,
                to_version=body.to_version,
                verbose=False,
                database=target_db,
                dry_run=body.dry_run,
            )
        except Exception as exc:
            return JSONResponse(
                status_code=500,
                content=MigrateResponse(success=False, message=str(exc), database=target_db).model_dump(),
            )
        action = "Dry-run completed" if body.dry_run else "Migration completed"
        return JSONResponse(content=MigrateResponse(success=True, message=action, database=target_db).model_dump())

    return router


__all__ = ["health_routes", "migration_routes"]
