"""Ingestion ChangeSets enter governance and only reproducible releases promote."""

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ContinuousIngestionService,
    ContinuousKnowledgeCompiler,
    KnowledgeSource,
    ResourceKind,
    SemanticResource,
    SourceBatch,
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


class CustomerFixture:
    def fetch(self, source, cursor):
        return SourceBatch.create(
            cursor_from=cursor,
            cursor_to="snapshot:1",
            records=[{"id": "customer:alice", "name": "Alice"}],
            source_snapshot={"database": "crm", "transaction": 101},
        )


def test_clean_change_set_requires_review_then_replay_before_promotion(
    tmp_path,
) -> None:
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    ingestion = SqliteContinuousIngestionRepository(tmp_path / "ingestion.sqlite3")
    source = ingestion.register_source(
        KnowledgeSource.create(
            source_id="crm-customers",
            tenant_id="acme",
            source_type="fixture",
            owner="crm-platform",
            config={"identity_field": "id", "snapshot_mode": "full"},
        )
    )
    connectors = SourceConnectorRegistry()
    connectors.register("fixture", CustomerFixture())
    ingested = ContinuousIngestionService(ingestion, connectors).ingest_once(
        source.source_id, tenant_id="acme", actor="ingestor:worker"
    )
    resource = SemanticResource.create(
        resource_id="aof://acme/crm/object-type/customer",
        kind=ResourceKind.OBJECT_TYPE,
        name="customer",
        domain="crm",
        owner="crm-platform",
        spec={"source_id": source.source_id, "identity_field": "id"},
    )
    registry = default_compiler_registry()
    governance = SemanticGovernanceService(
        tmp_path / "governance",
        decision_store=decisions,
        compiler_registry=registry,
        access_policy=SemanticGovernancePolicy(),
    )
    compilation_repository = CompilationRunRepository(tmp_path / "compiler" / "acme")
    compilation = CompilationRunService(
        compilation_repository,
        registry=registry,
        decision_store=decisions,
    )
    continuous = ContinuousKnowledgeCompiler(
        ingestion, governance, compilation, decisions
    )

    staged = continuous.stage(
        ingested.run_id,
        tenant_id="acme",
        release_id="crm-knowledge@1.0.0",
        resources=[resource],
        actor="editor:ingestion",
        validator="validator:semantic-gate",
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
    promoted = continuous.approve_compile_promote(
        staged["proposal_id"],
        compiler_policy=policy,
        targets=["semantic-json"],
        channel="production",
        approver="reviewer:bob",
        compiler="compiler:ci",
        replay_compiler="compiler:replay-ci",
        publisher="publisher:carol",
    )

    assert staged["state"] == "review"
    assert staged["change_set_digest"] == ingested.change_set_digest
    assert promoted["state"] == "promoted"
    assert (
        compilation_repository.get(promoted["compilation_run_id"]).reproducible is True
    )
    assert (
        compilation_repository.get_channel("production")["run_id"]
        == promoted["compilation_run_id"]
    )
    trail = decisions.audit_trail(
        compilation_repository.get_channel("production")["history"][-1]["decision_id"]
    )
    decision_types = {
        node["decision"]["decision_type"] for node in trail["causal_chain"]["nodes"]
    }
    assert "knowledge_ingestion_accepted" in decision_types
    assert "semantic_compile_replay" in decision_types
    assert decisions.verify_integrity()["valid"] is True
