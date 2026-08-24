"""Real source snapshots continuously compile only through reproducible gates."""

import json

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ContinuousCompilePolicy,
    ContinuousIngestionService,
    ContinuousKnowledgeCompiler,
    JsonlFileSourceConnector,
    KnowledgeSource,
    ResourceKind,
    SemanticResource,
    SourceConnectorRegistry,
    SqliteContinuousIngestionRepository,
)
from bridge.semantic_core.compilers import (
    CompilationRunRepository,
    CompilationRunService,
    CompilerPolicy,
    default_compiler_registry,
)
from bridge.semantic_core.governance import (
    SemanticGovernancePolicy,
    SemanticGovernanceService,
)


def _write_jsonl(path, records) -> None:
    path.write_text(
        "".join(f"{json.dumps(record, sort_keys=True)}\n" for record in records),
        encoding="utf-8",
    )


def test_file_source_incrementally_compiles_replays_and_blocks_drift(tmp_path) -> None:
    source_file = tmp_path / "customers.jsonl"
    _write_jsonl(
        source_file,
        [{"id": "c1", "name": "Alice"}, {"id": "c2", "name": "Bob"}],
    )
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    ingestion = SqliteContinuousIngestionRepository(tmp_path / "ingestion.sqlite3")
    source = ingestion.register_source(
        KnowledgeSource.create(
            source_id="customers",
            tenant_id="acme",
            source_type="jsonl",
            owner="crm-platform",
            config={
                "path": str(source_file),
                "identity_field": "id",
                "snapshot_mode": "full",
            },
        )
    )
    connectors = SourceConnectorRegistry()
    connectors.register(
        "jsonl", JsonlFileSourceConnector(allowed_roots=[tmp_path])
    )
    ingest = ContinuousIngestionService(
        ingestion, connectors, decisions=decisions
    )
    first = ingest.ingest_once(
        source.source_id, tenant_id="acme", actor="ingestor:sync"
    )
    unchanged = ingest.ingest_once(
        source.source_id,
        tenant_id="acme",
        actor="ingestor:sync",
        attempt_id="poll-2",
    )

    registry = default_compiler_registry()
    governance = SemanticGovernanceService(
        tmp_path / "governance",
        decision_store=decisions,
        compiler_registry=registry,
        access_policy=SemanticGovernancePolicy(),
    )
    compilation_repository = CompilationRunRepository(tmp_path / "compiler" / "acme")
    continuous = ContinuousKnowledgeCompiler(
        ingestion,
        governance,
        CompilationRunService(
            compilation_repository,
            registry=registry,
            decision_store=decisions,
        ),
        decisions,
        policy=ContinuousCompilePolicy(promotion_mode="automatic"),
    )
    resource = SemanticResource.create(
        resource_id="aof://acme/crm/object-type/customer",
        kind=ResourceKind.OBJECT_TYPE,
        name="customer",
        domain="crm",
        owner="crm-platform",
        spec={"source_id": "customers", "identity_field": "id"},
    )
    policy = CompilerPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/continuous-compiler",
            kind=ResourceKind.POLICY,
            name="continuous-compiler",
            domain="platform",
            owner="platform-governance",
            spec={
                "policy_type": "compiler",
                "allowed_compilers": {"semantic-json": ["semantic-json@1"]},
                "required_targets": ["semantic-json"],
            },
        )
    )
    promoted = continuous.run_cycle(
        first.run_id,
        tenant_id="acme",
        release_id="customer-knowledge@1.0.0",
        resources=[resource],
        actor="editor:sync",
        validator="validator:quality",
        targets=["semantic-json"],
        channel="production",
        compiler_policy=policy,
        approver="reviewer:alice",
        compiler="compiler:build",
        replay_compiler="compiler:independent-replay",
        publisher="publisher:release",
    )

    _write_jsonl(
        source_file,
        [
            {"id": "c1", "name": "Alice Chen", "email": "alice@example.test"},
            {"id": "c3", "name": "Carol", "email": "carol@example.test"},
        ],
    )
    changed = ingest.ingest_once(
        source.source_id, tenant_id="acme", actor="ingestor:sync"
    )
    blocked = continuous.run_cycle(
        changed.run_id,
        tenant_id="acme",
        release_id="customer-knowledge@1.1.0",
        resources=[resource],
        actor="editor:sync",
        validator="validator:quality",
        targets=["semantic-json"],
        channel="production",
    )
    changes = ingestion.get_change_set(changed.run_id, tenant_id="acme")

    assert unchanged.run_id == first.run_id
    assert promoted["state"] == "promoted"
    assert compilation_repository.get(promoted["compilation_run_id"]).reproducible
    assert changes.summary == {"added": 1, "updated": 1, "deleted": 1}
    assert changes.schema_drift == {"added_fields": ["email"], "removed_fields": []}
    assert blocked["state"] == "schema_drift_review"
    assert blocked["decision_id"].startswith("decision:ingestion-schema-drift:")
    assert ingestion.verify_all()["valid"] is True
    assert decisions.verify_integrity()["valid"] is True
