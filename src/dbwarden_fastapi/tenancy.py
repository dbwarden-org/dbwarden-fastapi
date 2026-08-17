from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Literal

from fastapi import HTTPException, Request

TenantSource = Literal["header", "host", "both"]
TenantPrecedence = Literal["header", "host"]
TenantCallable = Callable[[Request], str | None | Awaitable[str | None]]


class TenantResolver:
    """Resolve a configured database name from a request.

    ``source="both"`` uses ``precedence`` when both values are present.
    The host value excludes its port.
    """

    def __init__(
        self,
        *,
        source: TenantSource = "header",
        header_name: str = "X-DBWarden-Database",
        precedence: TenantPrecedence = "header",
        host_mapping: Mapping[str, str] | None = None,
    ) -> None:
        if source not in {"header", "host", "both"}:
            raise ValueError("source must be 'header', 'host', or 'both'")
        if precedence not in {"header", "host"}:
            raise ValueError("precedence must be 'header' or 'host'")
        self.source = source
        self.header_name = header_name
        self.precedence = precedence
        self.host_mapping = dict(host_mapping or {})

    async def __call__(self, request: Request) -> str | None:
        header = request.headers.get(self.header_name) if self.source != "host" else None
        host = request.url.hostname if self.source != "header" else None
        if host is not None:
            host = self.host_mapping.get(host, host)
        if self.source == "header":
            return header
        if self.source == "host":
            return host
        values = (header, host) if self.precedence == "header" else (host, header)
        return next((value for value in values if value), None)


async def resolve_tenant(request: Request, resolver: TenantResolver | TenantCallable) -> str:
    """Resolve and validate a tenant database name for a request."""
    tenant: Any = resolver(request)
    if inspect.isawaitable(tenant):
        tenant = await tenant
    if not isinstance(tenant, str) or not tenant:
        raise HTTPException(status_code=400, detail="Tenant could not be resolved")
    from dbwarden.config import get_database, get_multi_db_config

    config = get_multi_db_config()
    if tenant not in config.databases:
        raise HTTPException(status_code=404, detail="Tenant database not found")
    get_database(tenant)
    return tenant
