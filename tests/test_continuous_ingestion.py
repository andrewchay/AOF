"""Governed source ingestion is cursor-bound, immutable, and restart-safe."""

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    KnowledgeSource,
    SourceBatch,
    SourceConnectorRegistry,
    SqliteContinuousIngestionRepository,
    ContinuousIngestionService,
)


class FixtureSourceConnector:
    def fetch(self, source, cursor):
        assert source.source_id == "crm-customers"
        assert cursor is None
        return SourceBatch.create(
            cursor_from=cursor,
            cursor_to="offset:2",
            records=[
                {"id": "customer:alice", "name": "Alice"},
                {"id": "customer:bob", "name": "Bob"},
            ],
            source_snapshot={"fixture": "crm-v1", "row_count": 2},
        )


class EvolvingFixtureConnector:
    def fetch(self, source, cursor):
        if cursor is None:
            return SourceBatch.create(
                cursor_from=None,
                cursor_to="snapshot:1",
                records=[
                    {"id": "customer:alice", "name": "Alice"},
                    {"id": "customer:bob", "name": "Bob"},
                ],
                source_snapshot={"snapshot": 1},
            )
        return SourceBatch.create(
            cursor_from="snapshot:1",
            cursor_to="snapshot:2",
            records=[
                {
                    "id": "customer:alice",
                    "name": "Alice Chen",
                    "email": "alice@example.test",
                },
                {
                    "id": "customer:carol",
                    "name": "Carol",
                    "email": "carol@example.test",
                },
            ],
            source_snapshot={"snapshot": 2},
        )


def test_registered_source_ingests_immutable_restart_safe_run(tmp_path) -> None:
    path = tmp_path / "continuous-ingestion.sqlite3"
    repository = SqliteContinuousIngestionRepository(path)
    source = repository.register_source(
        KnowledgeSource.create(
            source_id="crm-customers",
            tenant_id="acme",
            source_type="fixture",
            owner="crm-platform",
            config={"dataset": "customers"},
        )
    )
    connectors = SourceConnectorRegistry()
    connectors.register("fixture", FixtureSourceConnector())

    run = ContinuousIngestionService(repository, connectors).ingest_once(
        source.source_id, tenant_id="acme", actor="ingestor:worker-1"
    )
    restarted = SqliteContinuousIngestionRepository(path)
    stored = restarted.get_run(run.run_id, tenant_id="acme")

    assert run.status == "succeeded"
    assert run.cursor_from is None
    assert run.cursor_to == "offset:2"
    assert run.record_count == 2
    assert run.source_revision == source.revision_id
    assert run.source_snapshot_digest.startswith("sha256:")
    assert stored.run_digest == run.run_digest
    assert restarted.get_source("crm-customers", tenant_id="acme").cursor == "offset:2"
    assert restarted.verify_all() == {
        "valid": True,
        "source_count": 1,
        "run_count": 1,
        "errors": [],
    }


def test_full_snapshot_emits_deterministic_changes_and_schema_drift(tmp_path) -> None:
    repository = SqliteContinuousIngestionRepository(tmp_path / "changes.sqlite3")
    source = repository.register_source(
        KnowledgeSource.create(
            source_id="crm-evolving",
            tenant_id="acme",
            source_type="fixture-evolving",
            owner="crm-platform",
            config={"identity_field": "id", "snapshot_mode": "full"},
        )
    )
    connectors = SourceConnectorRegistry()
    connectors.register("fixture-evolving", EvolvingFixtureConnector())
    service = ContinuousIngestionService(repository, connectors)

    first = service.ingest_once(
        source.source_id, tenant_id="acme", actor="ingestor:one"
    )
    second = service.ingest_once(
        source.source_id, tenant_id="acme", actor="ingestor:two"
    )
    changes = repository.get_change_set(second.run_id, tenant_id="acme")

    assert repository.get_change_set(first.run_id, tenant_id="acme").summary == {
        "added": 2,
        "updated": 0,
        "deleted": 0,
    }
    assert [item["entity_id"] for item in changes.added] == ["customer:carol"]
    assert [item["entity_id"] for item in changes.updated] == ["customer:alice"]
    assert [item["entity_id"] for item in changes.deleted] == ["customer:bob"]
    assert changes.schema_drift == {"added_fields": ["email"], "removed_fields": []}
    assert changes.change_set_digest == second.change_set_digest
    assert repository.verify_all()["valid"] is True


def test_connector_failure_is_persisted_without_advancing_cursor(tmp_path) -> None:
    class BrokenConnector:
        def fetch(self, source, cursor):
            raise TimeoutError("warehouse request outcome is unknown")

    repository = SqliteContinuousIngestionRepository(tmp_path / "failed.sqlite3")
    source = repository.register_source(
        KnowledgeSource.create(
            source_id="warehouse-orders",
            tenant_id="acme",
            source_type="broken",
            owner="data-platform",
            config={"dataset": "orders"},
        )
    )
    connectors = SourceConnectorRegistry()
    connectors.register("broken", BrokenConnector())

    failed = ContinuousIngestionService(repository, connectors).ingest_once(
        source.source_id,
        tenant_id="acme",
        actor="ingestor:worker",
        attempt_id="attempt-001",
    )

    assert failed.status == "failed"
    assert failed.error == {
        "type": "TimeoutError",
        "message": "warehouse request outcome is unknown",
    }
    assert repository.get_source(source.source_id, tenant_id="acme").cursor is None
    assert repository.list_runs(tenant_id="acme")[0].run_digest == failed.run_digest
    assert repository.verify_all()["valid"] is True


def test_unchanged_snapshot_reuses_prior_run_and_records_decision_audit(tmp_path) -> None:
    class StableConnector:
        def fetch(self, source, cursor):
            return SourceBatch.create(
                cursor_from=cursor,
                cursor_to="snapshot:stable",
                records=[{"id": "asset:1", "name": "Handbook"}],
                source_snapshot={"snapshot": "stable"},
            )

    repository = SqliteContinuousIngestionRepository(tmp_path / "idempotent.sqlite3")
    source = repository.register_source(
        KnowledgeSource.create(
            source_id="stable-assets",
            tenant_id="acme",
            source_type="stable",
            owner="knowledge-platform",
            config={"snapshot_mode": "full"},
        )
    )
    connectors = SourceConnectorRegistry()
    connectors.register("stable", StableConnector())
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    service = ContinuousIngestionService(repository, connectors, decisions=decisions)

    first = service.ingest_once(
        source.source_id, tenant_id="acme", actor="ingestor:worker"
    )
    repeated = service.ingest_once(
        source.source_id,
        tenant_id="acme",
        actor="ingestor:worker",
        attempt_id="retry-1",
    )

    assert repeated.run_id == first.run_id
    assert len(repository.list_runs(tenant_id="acme")) == 1
    audit = decisions.find_precedents(
        "knowledge_ingestion_succeeded", tenant_id="acme"
    )
    assert len(audit) == 1
    assert audit[0]["decision"]["decision"]["evidence"][0]["id"] == first.run_id
