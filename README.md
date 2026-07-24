# dbwarden-fastapi

FastAPI integration for [DBWarden](https://github.com/dbwarden-org/dbwarden).

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

## Trust tier

This is an **official** DBWarden plugin. Its distribution name is classified before any of its code is imported, and `dbwarden plugin add` verifies the PyPI Trusted-Publishing attestation (PEP 740) against `dbwarden-org/dbwarden-fastapi` before installing. It loads automatically once installed, with no `dbwarden plugin trust` step.

## Development

```bash
uv venv && uv pip install -e . -e ../dbwarden pytest
pytest -q
```

The `tests/test_conformance.py` suite runs DBWarden's shared conformance harness (`dbwarden.plugin_conformance`): entry point resolution, no import-time side effects, hook signatures, public-API-only imports, and idempotent `setup()`.

## License

MIT
