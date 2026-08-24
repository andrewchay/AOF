"""Governed source ingestion is cursor-bound, immutable, and restart-safe."""

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
