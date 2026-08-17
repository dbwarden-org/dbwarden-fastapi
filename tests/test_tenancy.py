from __future__ import annotations

from types import SimpleNamespace

import pytest
from starlette.requests import Request


def _request(headers: dict[str, str] | None = None, base_url: str = "http://host.test"):
    host = base_url.removeprefix("http://").removeprefix("https://")
    raw_headers = [(b"host", host.encode())]
    raw_headers.extend((key.lower().encode(), value.encode()) for key, value in (headers or {}).items())
    return Request({"type": "http", "scheme": "http", "method": "GET", "path": "/", "headers": raw_headers})


@pytest.mark.asyncio
async def test_tenant_resolver_header_host_precedence():
    from dbwarden_fastapi import TenantResolver

    resolver = TenantResolver(source="both", precedence="host")
    assert await resolver(_request({"X-DBWarden-Database": "header"}, "http://host.test:8000")) == "host.test"


@pytest.mark.asyncio
async def test_tenant_resolver_maps_hosts_to_registered_database_names():
    from dbwarden_fastapi import TenantResolver

    resolver = TenantResolver(source="host", host_mapping={"tenant.example.test": "primary"})
    assert await resolver(_request(base_url="http://tenant.example.test")) == "primary"


@pytest.mark.asyncio
async def test_custom_tenant_resolver_is_supported(monkeypatch):
    from dbwarden_fastapi.tenancy import resolve_tenant

    monkeypatch.setattr(
        "dbwarden.config.get_multi_db_config",
        lambda: SimpleNamespace(databases={"custom": object()}),
    )
    monkeypatch.setattr("dbwarden.config.get_database", lambda name: object())

    async def resolver(request):
        return "custom"

    assert await resolve_tenant(_request(), resolver) == "custom"


@pytest.mark.asyncio
async def test_unknown_tenant_returns_not_found(monkeypatch):
    from fastapi import HTTPException
    from dbwarden_fastapi.tenancy import resolve_tenant

    monkeypatch.setattr(
        "dbwarden.config.get_multi_db_config",
        lambda: SimpleNamespace(databases={"primary": object()}),
    )

    with pytest.raises(HTTPException, match="Tenant database not found") as exc_info:
        await resolve_tenant(_request(), lambda request: "missing")

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_tenant_configuration_failures_are_not_hidden_as_not_found(monkeypatch):
    from dbwarden_fastapi.tenancy import resolve_tenant

    monkeypatch.setattr(
        "dbwarden.config.get_multi_db_config",
        lambda: (_ for _ in ()).throw(RuntimeError("configuration unavailable")),
    )

    with pytest.raises(RuntimeError, match="configuration unavailable"):
        await resolve_tenant(_request(), lambda request: "primary")


def test_get_session_accepts_database_handle(monkeypatch):
    from dbwarden.db_handle import DatabaseHandle
    from dbwarden_fastapi.session import _database_name

    assert _database_name(DatabaseHandle("primary", "sqlite")) == "primary"


def test_session_cache_keys_include_credentials_without_exposing_them():
    from dbwarden_fastapi.engines import _to_async_url as engine_async_url
    from dbwarden_fastapi.session import _to_async_url as session_async_url

    first = "postgresql://user:first-secret@db.example.test/app"
    second = "postgresql://user:second-secret@db.example.test/app"
    assert session_async_url(first, "postgresql")[0] != session_async_url(second, "postgresql")[0]
    assert engine_async_url(first, "postgresql")[0] != engine_async_url(second, "postgresql")[0]
