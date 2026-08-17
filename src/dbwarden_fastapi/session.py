from __future__ import annotations

from collections.abc import AsyncGenerator, Callable
import hashlib
import threading
from typing import Any

from fastapi import Request
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dbwarden.config import get_database
from dbwarden_fastapi.runtime import runtime_flags
from dbwarden_fastapi.tenancy import TenantCallable, TenantResolver, resolve_tenant

_SESSION_FACTORIES: dict[str, async_sessionmaker[AsyncSession]] = {}
_SESSION_LOCK = threading.Lock()


def _to_async_url(url: str, database_type: str) -> tuple[str, str]:
    """Convert URL to async driver.
    
    Returns:
        tuple of (safe_cache_key, async_url)
        - safe_cache_key: digest of the full URL for use as a dict key
        - async_url: Full URL with async driver for engine creation
    """
    parsed = make_url(url)
    drivername = parsed.drivername

    # Include credentials without retaining the URL itself; tenants with rotated
    # credentials must never share an engine.
    safe_key = hashlib.sha256(url.encode()).hexdigest()

    if "+" in drivername:
        return safe_key, parsed.render_as_string(hide_password=False)

    if database_type == "postgresql" or drivername.startswith("postgres"):
        drivername = "postgresql+asyncpg"
    elif database_type == "sqlite" or drivername.startswith("sqlite"):
        drivername = "sqlite+aiosqlite"
    else:
        raise ValueError(
            f"get_session currently supports async PostgreSQL and SQLite drivers. Unsupported database_type: {database_type}"
        )

    full_async_url = parsed.set(drivername=drivername).render_as_string(hide_password=False)
    return safe_key, full_async_url


def _database_name(database: str | Any | None) -> str | None:
    """Accept registered DatabaseHandle objects and declarative database classes."""
    if database is None or isinstance(database, str):
        return database
    handle = getattr(database, "handle", database)
    name = getattr(handle, "_name", None)
    if not isinstance(name, str):
        raise TypeError("database must be a database name, DatabaseHandle, or DbwardenDatabase class")
    return name


def _session_factory(database: str | Any | None = None, dev: bool = False) -> async_sessionmaker[AsyncSession]:
    with runtime_flags(dev=dev, strict_translation=False):
        config = get_database(_database_name(database))
    
    url = config.sqlalchemy_url_async or config.sqlalchemy_url
    cache_key, async_url = _to_async_url(url, config.database_type)
    
    # Thread-safe check and create
    with _SESSION_LOCK:
        if cache_key in _SESSION_FACTORIES:
            return _SESSION_FACTORIES[cache_key]

        engine = create_async_engine(async_url, future=True)
        factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
        _SESSION_FACTORIES[cache_key] = factory
        return factory


def get_session(database: str | Any | None = None, *, dev: bool = False) -> Callable[[], AsyncGenerator[AsyncSession, None]]:
    """Return a FastAPI dependency that yields AsyncSession.

    Example:
        session_dep = get_session()
        async def route(session: Annotated[AsyncSession, Depends(session_dep)]):
            ...
    """

    async def _dependency() -> AsyncGenerator[AsyncSession, None]:
        factory = _session_factory(database=database, dev=dev)
        async with factory() as session:
            yield session

    return _dependency


def get_tenant_session(
    resolver: TenantResolver | TenantCallable | None = None,
    *,
    dev: bool = False,
) -> Callable[..., AsyncGenerator[AsyncSession, None]]:
    """Return a request-scoped session dependency selected by tenant.

    By default tenants are configured database names from the
    ``X-DBWarden-Database`` header. Declarative ``DbwardenDatabase``
    registrations are supported.
    """
    tenant_resolver = resolver if resolver is not None else TenantResolver()

    async def _dependency(request: Request) -> AsyncGenerator[AsyncSession, None]:
        factory = _session_factory(await resolve_tenant(request, tenant_resolver), dev=dev)
        async with factory() as session:
            yield session

    return _dependency
