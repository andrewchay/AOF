"""Bitemporal object facts preserve valid-time and knowledge-time history."""

from __future__ import annotations

from bridge.semantic_core import BitemporalObjectStore


def test_point_in_time_snapshot_distinguishes_reality_from_when_it_was_known(
    tmp_path,
) -> None:
    store = BitemporalObjectStore(tmp_path / "objects.sqlite3")
    store.assert_fact(
        tenant_id="acme",
        fact_id="customer-alice-status-2026",
        object_type_id="aof://acme/crm/object-type/customer",
        object_id="customer:alice",
        field="status",
        value="active",
        valid_from="2026-01-01T00:00:00+00:00",
        valid_to=None,
        recorded_at="2026-01-02T00:00:00+00:00",
        source={"type": "crm_snapshot", "id": "snapshot-001"},
    )
    store.assert_fact(
        tenant_id="acme",
        fact_id="customer-alice-status-2026",
        object_type_id="aof://acme/crm/object-type/customer",
        object_id="customer:alice",
        field="status",
        value="suspended",
        valid_from="2026-01-01T00:00:00+00:00",
        valid_to=None,
        recorded_at="2026-01-10T00:00:00+00:00",
        source={"type": "action_run", "id": "action-001"},
        action_run_id="action-001",
    )

    before_correction = store.snapshot(
        tenant_id="acme",
        object_type_id="aof://acme/crm/object-type/customer",
        object_id="customer:alice",
        valid_at="2026-01-05T00:00:00+00:00",
        known_at="2026-01-05T00:00:00+00:00",
    )
    after_correction = store.snapshot(
        tenant_id="acme",
        object_type_id="aof://acme/crm/object-type/customer",
        object_id="customer:alice",
        valid_at="2026-01-05T00:00:00+00:00",
        known_at="2026-01-11T00:00:00+00:00",
    )

    assert before_correction["values"] == {"status": "active"}
    assert after_correction["values"] == {"status": "suspended"}
    assert before_correction["evidence"][0]["version"] == 1
    assert after_correction["evidence"][0]["version"] == 2
    assert after_correction["evidence"][0]["action_run_id"] == "action-001"
    assert before_correction["snapshot_digest"] != after_correction["snapshot_digest"]
    assert store.snapshot(
        tenant_id="other",
        object_type_id="aof://acme/crm/object-type/customer",
        object_id="customer:alice",
        valid_at="2026-01-05T00:00:00+00:00",
        known_at="2026-01-11T00:00:00+00:00",
    )["values"] == {}


def test_bitemporal_store_verifies_and_restores_online_backup(tmp_path) -> None:
    store = BitemporalObjectStore(tmp_path / "objects.sqlite3")
    store.assert_fact(
        tenant_id="acme",
        fact_id="customer-alice-tier-2026",
        object_type_id="aof://acme/crm/object-type/customer",
        object_id="customer:alice",
        field="tier",
        value="gold",
        valid_from="2026-02-01T00:00:00+00:00",
        valid_to="2026-03-01T00:00:00+00:00",
        recorded_at="2026-02-02T00:00:00+00:00",
        source={"type": "crm_event", "id": "event-002"},
    )

    assert store.schema_version() == 1
    assert store.verify_all() == {"valid": True, "fact_version_count": 1, "errors": []}
    backup = store.backup_to(tmp_path / "backup" / "objects.sqlite3")
    restored = BitemporalObjectStore(backup)
    snapshot = restored.snapshot(
        tenant_id="acme",
        object_type_id="aof://acme/crm/object-type/customer",
        object_id="customer:alice",
        valid_at="2026-02-10T00:00:00+00:00",
        known_at="2026-02-10T00:00:00+00:00",
    )

    assert snapshot["values"] == {"tier": "gold"}
    assert restored.verify_all()["valid"] is True
