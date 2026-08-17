"""Behavioral contracts for trusted query snapshot resolution."""

from __future__ import annotations

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    KnowledgeRelease,
    QueryCapability,
    QueryRequest,
    ResourceKind,
    SemanticResource,
    TrustedQueryError,
    TrustedSnapshotResolver,
)
from bridge.semantic_core.compilers import (
    CompilationRunRepository,
    CompilationRunService,
    CompilerPolicy,
    default_compiler_registry,
)


def _promoted_rag_snapshot(tmp_path):
    concept = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
        description="A customer that places sales orders.",
    )
    release = KnowledgeRelease.build(
        release_id="sales-query@1.0.0",
        resources=[concept],
        scope={"tenant_id": "acme"},
    )
    policy = CompilerPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/compiler-production",
            kind=ResourceKind.POLICY,
            name="compiler-production",
            domain="platform",
            owner="platform-governance",
            spec={
                "policy_type": "compiler",
                "allowed_compilers": {"rag": ["rag@1"]},
            },
        )
    )
    registry = default_compiler_registry()
    repository = CompilationRunRepository(tmp_path / "compiler" / "acme")
    service = CompilationRunService(
        repository,
        registry=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    plan = registry.plan(release, resources=[concept], targets=["rag"])
    first = service.execute(
        run_id="sales-query-001",
        plan=plan,
        policy=policy,
        release=release,
        resources=[concept],
        actor="compiler:ci",
        rationale="Compile the trusted search snapshot.",
    )
    replay = service.replay(
        first.run_id,
        run_id="sales-query-002",
        policy=policy,
        release=release,
        resources=[concept],
        actor="compiler:replay-ci",
        rationale="Reproduce the trusted search snapshot.",
    )
    pointer = service.promote(
        replay.run_id,
        channel="production",
        actor="publisher:bob",
        approved_by="reviewer:alice",
        rationale="Promote the reproduced search snapshot.",
    )
    return repository, release, replay, pointer


def test_query_plan_pins_verified_channel_run_release_and_artifact(tmp_path) -> None:
    repository, release, run, pointer = _promoted_rag_snapshot(tmp_path)
    resolver = TrustedSnapshotResolver(repository)
    request = QueryRequest.create(
        channel="production",
        capability=QueryCapability.SEMANTIC_SEARCH,
        query="customer",
        purpose="customer-support",
        parameters={"limit": 5},
    )

    first = resolver.plan(request, tenant_id="acme")
    second = resolver.plan(request, tenant_id="acme")

    assert first.plan_digest == second.plan_digest
    assert first.request_digest == request.request_digest
    assert first.tenant_id == "acme"
    assert first.channel == "production"
    assert first.channel_pointer_digest == pointer["pointer_digest"]
    assert first.run_id == run.run_id
    assert first.run_digest == run.run_digest
    assert first.release_id == release.release_id
    assert first.release_digest == release.release_digest
    assert [artifact.target for artifact in first.artifacts] == ["rag"]
    assert first.artifacts[0].content_hash == run.artifacts[0]["content_hash"]
    assert first.to_dict()["api_version"] == "aof.query-plan/v1"


def test_query_plan_rejects_tampered_runtime_artifact(tmp_path) -> None:
    repository, _, run, _ = _promoted_rag_snapshot(tmp_path)
    artifact = run.artifacts[0]
    path = repository.root / "artifacts" / run.run_id / artifact["target"] / artifact["uri"]
    path.write_text("tampered\n", encoding="utf-8")
    request = QueryRequest.create(
        channel="production",
        capability="semantic_search",
        query="customer",
        purpose="customer-support",
    )

    with pytest.raises(TrustedQueryError, match="content hash mismatch"):
        TrustedSnapshotResolver(repository).plan(request, tenant_id="acme")


def test_query_plan_rejects_cross_tenant_snapshot(tmp_path) -> None:
    repository, _, _, _ = _promoted_rag_snapshot(tmp_path)
    request = QueryRequest.create(
        channel="production",
        capability="semantic_search",
        query="customer",
        purpose="customer-support",
    )

    with pytest.raises(TrustedQueryError, match="tenant"):
        TrustedSnapshotResolver(repository).plan(request, tenant_id="beta")


def test_query_plan_rejects_channel_without_required_capability_artifact(tmp_path) -> None:
    repository, _, _, _ = _promoted_rag_snapshot(tmp_path)
    request = QueryRequest.create(
        channel="production",
        capability=QueryCapability.SPARQL,
        query="ASK { ?subject ?predicate ?object }",
        purpose="ontology-audit",
    )

    with pytest.raises(TrustedQueryError, match="missing artifact targets: owl"):
        TrustedSnapshotResolver(repository).plan(request, tenant_id="acme")
