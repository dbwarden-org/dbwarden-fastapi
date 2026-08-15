"""Verified (Approved) conformance suite.

These run dbwarden's shared conformance harness so a reviewer can confirm this
plugin respects the contract.

Reference: https://docs.dbwarden.org/plugins/developing/approved-standard/
"""
from __future__ import annotations

from dbwarden import plugin_conformance as conformance

import dbwarden_fastapi

DISTRIBUTION = "dbwarden-fastapi"
PACKAGE = "dbwarden_fastapi"

# Hooks this plugin promises to register. Keep in sync with setup().
VALUE_HOOKS = (
    "session_factory",
    "sync_session_factory",
    "clickhouse_session_factory",
    "clickhouse_sync_session_factory",
    "lifespan",
    "health_routes",
    "migration_routes",
)


def test_entry_point_is_declared() -> None:
    conformance.assert_entry_point_declared(DISTRIBUTION)


def test_import_has_no_side_effects() -> None:
    conformance.assert_import_has_no_side_effects(PACKAGE)


def test_setup_registers_hooks() -> None:
    conformance.assert_setup_registers(
        dbwarden_fastapi.setup,
        plugin=DISTRIBUTION,
        value_hooks=VALUE_HOOKS,
    )



def test_hook_signature_compliance() -> None:
    conformance.assert_hook_signatures(dbwarden_fastapi.setup)


def test_core_imports_resolve() -> None:
    conformance.assert_core_imports_resolve(PACKAGE)


def test_api_version_is_declared() -> None:
    conformance.assert_api_version_declared(PACKAGE)


def test_idempotent_setup() -> None:
    conformance.assert_idempotent_setup(dbwarden_fastapi.setup, plugin=DISTRIBUTION)
