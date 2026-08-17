# dbwarden-fastapi

[![Python](https://img.shields.io/badge/Python-3.12.7%2B-3776AB?logo=python&logoColor=white&style=for-the-badge)](https://www.python.org/downloads/)
[![PyPI](https://img.shields.io/pypi/v/dbwarden-fastapi?logo=pypi&logoColor=white&style=for-the-badge)](https://pypi.org/project/dbwarden-fastapi/)
[![CI](https://img.shields.io/github/actions/workflow/status/dbwarden-org/dbwarden-fastapi/test.yml?logo=github&logoColor=white&style=for-the-badge)](https://github.com/dbwarden-org/dbwarden-fastapi/actions/workflows/test.yml)

FastAPI integration for [dbwarden](https://github.com/dbwarden-org/dbwarden).

Provides request-scoped sessions, a migration-aware lifespan, and health/status routers.

## Hooks

| Hook | Provides |
|---|---|
| `session_factory` / `sync_session_factory` | Request-scoped `AsyncSession` / `Session` dependencies, backing `database_config(...).async_session` and `.sync_session` |
| `clickhouse_session_factory` / `clickhouse_sync_session_factory` | Shared ClickHouse client dependencies |
| `lifespan` | Startup schema check or auto-migration, optional readiness gate, seed application, and pool warmup; disposes engines on shutdown |
| `health_routes` | `GET /`, `/liveness`, `/readiness`, `/{database_name}` |
| `migration_routes` | `GET /status` and `POST /migrate` |

Also exported directly: `dbwarden_lifespan`, `DBWardenRouter`, `DBWardenHealthRouter`, `MetricsRouter`, `MetricsMiddleware`, `QueryTracingMiddleware`, `PoolMetricsCollector`, `migration_lock`, `sync_migration_lock`, `override_database`, and `migration_state`.

## Automatic schemas

Re-exports [`schemap`](https://pypi.org/project/schemap/)'s `@auto_schema` and `SchemaConfig`, so Pydantic schemas can be generated from your SQLAlchemy models:

```python
from dbwarden_fastapi import auto_schema

@auto_schema
class User(Base): ...
```

## Usage

```python
from fastapi import FastAPI
from dbwarden_fastapi import DBWardenHealthRouter, dbwarden_lifespan

app = FastAPI(lifespan=lambda app: dbwarden_lifespan(app, mode="check"))
app.include_router(DBWardenHealthRouter(), prefix="/health")
```

## Installation

```bash
dbwarden plugin add dbwarden-fastapi
```

Optional extras: `[metrics]` for Prometheus counters, `[redis]` for the distributed `migration_lock`, `[clickhouse]` for ClickHouse sessions.

## Tenancy and lifecycle

`get_tenant_session()` selects a registered database for each request.
`TenantResolver(source="header")` reads `X-DBWarden-Database` by default. Use
`source="host"`, `source="both", precedence="host"`, or `host_mapping` to map
hostnames to registered database names. A custom sync or async callable accepting
`Request` may also be supplied. `get_session()` also accepts a registered
`DatabaseHandle` or `DbwardenDatabase` class.

For `dbwarden_lifespan(mode="migrate")`, `background_migrations=True` supports
`background_migration_readiness="block"` (default), `"serve"`, or `"fail"`.
Metrics refresh during lifespan every 30 seconds by default and refreshes stale
scrapes; set `metrics_refresh_interval=None` to disable the periodic task.
Set `opentelemetry=True` with the `[opentelemetry]` extra to instrument FastAPI.

## Trust tier

This is an **official** dbwarden plugin. Its distribution name is classified before any of its code is imported, and `dbwarden plugin add` verifies the PyPI Trusted-Publishing attestation (PEP 740) against `dbwarden-org/dbwarden-fastapi` before installing. It loads automatically once installed, with no `dbwarden plugin trust` step.

## Development

```bash
uv venv && uv pip install -e . -e ../dbwarden pytest
pytest -q
```

The `tests/test_conformance.py` suite runs dbwarden's shared conformance harness (`dbwarden.plugin_conformance`): entry point resolution, no import-time side effects, hook signatures, public-API-only imports, and idempotent `setup()`.

## License

MIT
