"""Tests for DBWardenRouter (status/migrate endpoints) and Redis migration lock."""

from unittest.mock import AsyncMock, MagicMock

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient


class TestMigrationLock:
    """Tests for Redis-backed migration_lock context managers."""

    @pytest.mark.asyncio
    async def test_async_lock_acquire_and_release(self):
        from dbwarden_fastapi.lock import migration_lock

        redis = AsyncMock()
        redis.set.return_value = True

        async with migration_lock(redis, key="test_lock", ttl=30):
            redis.set.assert_awaited_once()
            args, kwargs = redis.set.await_args
            assert args[0] == "test_lock"
            assert kwargs == {"nx": True, "ex": 30}

        redis.eval.assert_awaited_once()
        args = redis.eval.await_args.args
        assert args[1:3] == (1, "test_lock")

    @pytest.mark.asyncio
    async def test_async_lock_already_held(self):
        from dbwarden_fastapi.lock import migration_lock
        from dbwarden.exceptions import LockError

        redis = AsyncMock()
        redis.set.return_value = False

        with pytest.raises(LockError, match="already held"):
            async with migration_lock(redis, key="test_lock"):
                pass

        redis.eval.assert_not_awaited()

    def test_sync_lock_acquire_and_release(self):
        from dbwarden_fastapi.lock import sync_migration_lock

        redis = MagicMock()
        redis.set.return_value = True

        with sync_migration_lock(redis, key="test_lock", ttl=30):
            redis.set.assert_called_once()
            args, kwargs = redis.set.call_args
            assert args[0] == "test_lock"
            assert kwargs == {"nx": True, "ex": 30}

        redis.eval.assert_called_once()
        assert redis.eval.call_args.args[1:3] == (1, "test_lock")

    def test_sync_lock_already_held(self):
        from dbwarden_fastapi.lock import sync_migration_lock
        from dbwarden.exceptions import LockError

        redis = MagicMock()
        redis.set.return_value = False

        with pytest.raises(LockError, match="already held"):
            with sync_migration_lock(redis, key="test_lock"):
                pass

        redis.eval.assert_not_called()

    @pytest.mark.asyncio
    async def test_async_release_uses_the_acquisition_token(self):
        from dbwarden_fastapi.lock import migration_lock

        redis = AsyncMock()
        redis.set.return_value = True

        async with migration_lock(redis, key="test_lock"):
            pass

        assert redis.eval.await_args.args[3] == redis.set.await_args.args[1]

    def test_sync_release_uses_the_acquisition_token(self):
        from dbwarden_fastapi.lock import sync_migration_lock

        redis = MagicMock()
        redis.set.return_value = True

        with sync_migration_lock(redis, key="test_lock"):
            pass

        assert redis.eval.call_args.args[3] == redis.set.call_args.args[1]


class TestStatusEndpoint:
    """Tests for GET /status endpoint."""

    def test_status_returns_all_databases(self, monkeypatch):
        app = FastAPI()

        class FakeDB:
            pass

        class FakeCfg:
            databases = {"primary": FakeDB(), "analytics": FakeDB()}

        monkeypatch.setattr("dbwarden_fastapi.routes.get_multi_db_config", lambda: FakeCfg())

        def fake_status(name):
            from dbwarden_fastapi.routes import DatabaseStatus
            return DatabaseStatus(
                database=name,
                status="ok",
                connected=True,
                pending_migrations=0,
                applied_migrations=3,
                pending_seeds=0,
                applied_seeds=1,
                lock_active=False,
                error=None,
            )

        monkeypatch.setattr("dbwarden_fastapi.routes._compute_migration_status", fake_status)

        from dbwarden_fastapi.routes import DBWardenRouter

        app.include_router(DBWardenRouter(), prefix="/dbwarden")
        client = TestClient(app)
        response = client.get("/dbwarden/status")

        assert response.status_code == 200
        data = response.json()
        assert "databases" in data
        assert "primary" in data["databases"]
        assert "analytics" in data["databases"]

    def test_status_auth_required_when_authenticated(self, monkeypatch):
        app = FastAPI()

        class FakeDB:
            pass

        class FakeCfg:
            databases = {"primary": FakeDB()}

        monkeypatch.setattr("dbwarden_fastapi.routes.get_multi_db_config", lambda: FakeCfg())

        from dbwarden_fastapi.routes import DatabaseStatus

        monkeypatch.setattr(
            "dbwarden_fastapi.routes._compute_migration_status",
            lambda n: DatabaseStatus(
                database=n,
                status="ok",
                connected=True,
                pending_migrations=0,
                applied_migrations=3,
                pending_seeds=0,
                applied_seeds=1,
                lock_active=False,
                error=None,
            ),
        )

        from dbwarden_fastapi.routes import DBWardenRouter

        app.include_router(
            DBWardenRouter(auth_mode="authenticated", api_key="secret"),
            prefix="/dbwarden",
        )
        client = TestClient(app)

        # No API key -> 401
        response = client.get("/dbwarden/status")
        assert response.status_code == 401

        # Wrong API key -> 403
        response = client.get("/dbwarden/status", headers={"X-API-Key": "wrong"})
        assert response.status_code == 403

        # Correct API key -> 200
        response = client.get("/dbwarden/status", headers={"X-API-Key": "secret"})
        assert response.status_code == 200

    def test_authenticated_status_requires_api_key_configuration(self):
        from dbwarden_fastapi.routes import DBWardenRouter

        with pytest.raises(ValueError, match="api_key must be configured"):
            DBWardenRouter(auth_mode="authenticated")


class TestMigrateEndpoint:
    """Tests for POST /migrate endpoint."""

    def test_migrate_success(self, monkeypatch):
        app = FastAPI()

        class FakeDB:
            pass

        class FakeCfg:
            databases = {"primary": FakeDB()}

        monkeypatch.setattr("dbwarden_fastapi.routes.get_multi_db_config", lambda: FakeCfg())
        monkeypatch.setattr("dbwarden_fastapi.routes.migrate_cmd", lambda **kw: None)

        from dbwarden_fastapi.routes import DBWardenRouter

        app.include_router(DBWardenRouter(), prefix="/dbwarden")
        client = TestClient(app)
        response = client.post("/dbwarden/migrate", json={"database": "primary"})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True
        assert data["database"] == "primary"

    def test_migrate_all_databases_when_no_database_specified(self, monkeypatch):
        app = FastAPI()

        class FakeDB:
            pass

        class FakeCfg:
            databases = {"primary": FakeDB(), "analytics": FakeDB()}

        monkeypatch.setattr("dbwarden_fastapi.routes.get_multi_db_config", lambda: FakeCfg())
        monkeypatch.setattr("dbwarden_fastapi.routes.migrate_cmd", lambda **kw: None)

        from dbwarden_fastapi.routes import DBWardenRouter

        app.include_router(DBWardenRouter(), prefix="/dbwarden")
        client = TestClient(app)
        response = client.post("/dbwarden/migrate", json={})

        assert response.status_code == 200
        data = response.json()
        assert data["success"] is True

    def test_migrate_unknown_database_returns_404(self, monkeypatch):
        app = FastAPI()

        class FakeDB:
            pass

        class FakeCfg:
            databases = {"primary": FakeDB()}

        monkeypatch.setattr("dbwarden_fastapi.routes.get_multi_db_config", lambda: FakeCfg())

        from dbwarden_fastapi.routes import DBWardenRouter

        app.include_router(DBWardenRouter(), prefix="/dbwarden")
        client = TestClient(app)
        response = client.post("/dbwarden/migrate", json={"database": "unknown"})

        assert response.status_code == 404

    def test_migrate_auth_required_when_authenticated(self, monkeypatch):
        app = FastAPI()

        class FakeDB:
            pass

        class FakeCfg:
            databases = {"primary": FakeDB()}

        monkeypatch.setattr("dbwarden_fastapi.routes.get_multi_db_config", lambda: FakeCfg())
        monkeypatch.setattr("dbwarden_fastapi.routes.migrate_cmd", lambda **kw: None)

        from dbwarden_fastapi.routes import DBWardenRouter

        app.include_router(
            DBWardenRouter(auth_mode="authenticated", api_key="secret"),
            prefix="/dbwarden",
        )
        client = TestClient(app)

        # No API key -> 401
        response = client.post("/dbwarden/migrate", json={"database": "primary"})
        assert response.status_code == 401

        # Correct API key -> 200
        response = client.post(
            "/dbwarden/migrate",
            json={"database": "primary"},
            headers={"X-API-Key": "secret"},
        )
        assert response.status_code == 200

    def test_migrate_error_returns_500(self, monkeypatch):
        app = FastAPI()

        class FakeDB:
            pass

        class FakeCfg:
            databases = {"primary": FakeDB()}

        monkeypatch.setattr("dbwarden_fastapi.routes.get_multi_db_config", lambda: FakeCfg())
        monkeypatch.setattr(
            "dbwarden_fastapi.routes.migrate_cmd",
            lambda **kw: (_ for _ in ()).throw(RuntimeError("migration failed")),
        )

        from dbwarden_fastapi.routes import DBWardenRouter

        app.include_router(DBWardenRouter(), prefix="/dbwarden")
        client = TestClient(app)
        response = client.post("/dbwarden/migrate", json={"database": "primary"})

        assert response.status_code == 500
        data = response.json()
        assert data["success"] is False
        assert data["message"] == "Migration failed"

    def test_migrate_with_dry_run(self, monkeypatch):
        app = FastAPI()

        class FakeDB:
            pass

        class FakeCfg:
            databases = {"primary": FakeDB()}

        monkeypatch.setattr("dbwarden_fastapi.routes.get_multi_db_config", lambda: FakeCfg())
        captured = {}

        def fake_migrate(**kw):
            captured.update(kw)

        monkeypatch.setattr("dbwarden_fastapi.routes.migrate_cmd", fake_migrate)

        from dbwarden_fastapi.routes import DBWardenRouter

        app.include_router(DBWardenRouter(), prefix="/dbwarden")
        client = TestClient(app)
        response = client.post(
            "/dbwarden/migrate",
            json={"database": "primary", "dry_run": True},
        )

        assert response.status_code == 200
        assert captured.get("dry_run") is True
