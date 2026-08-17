"""Unified deterministic execution over trusted query plans."""

from __future__ import annotations

from dataclasses import replace

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    KnowledgeRelease,
    QueryCapability,
    QueryExecutor,
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


def _trusted_query_runtime(tmp_path):
    ontology = SemanticResource.create(
        resource_id="aof://acme/sales/ontology/sales",
        kind=ResourceKind.ONTOLOGY,
        name="sales",
        domain="sales",
        owner="knowledge-team",
        spec={
            "format": "turtle",
            "content": "@prefix ex: <https://example.test/> . ex:Customer a ex:Entity .",
        },
    )
    rules = SemanticResource.create(
        resource_id="aof://acme/sales/rule-set/customer",
        kind=ResourceKind.RULE_SET,
        name="customer",
        domain="sales",
        owner="knowledge-team",
        spec={"language": "datalog", "program": "eligible(X) :- customer(X)."},
    )
    concept = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer",
        kind=ResourceKind.CONCEPT,
        name="customer",
        domain="sales",
        owner="knowledge-team",
        description="A customer account.",
    )
    profile = SemanticResource.create(
        resource_id="aof://acme/sales/retrieval-profile/default",
        kind=ResourceKind.RETRIEVAL_PROFILE,
        name="default",
        domain="sales",
        owner="knowledge-team",
        spec={"strategy": "hybrid", "top_k": 10},
    )
    template = SemanticResource.create(
        resource_id="aof://acme/sales/query-template/customer-by-region",
        kind=ResourceKind.QUERY_TEMPLATE,
        name="customer-by-region",
        domain="sales",
        owner="knowledge-team",
        spec={
            "template": "SELECT * FROM customers WHERE region = ${region}",
            "required_parameters": ["region"],
        },
    )
    resources = [ontology, rules, concept, profile, template]
    release = KnowledgeRelease.build(
        release_id="sales-query@2.0.0",
        resources=resources,
        scope={"tenant_id": "acme"},
    )
    targets = ["owl", "datalog", "rag", "mcp"]
    policy = CompilerPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/compiler-production",
            kind=ResourceKind.POLICY,
            name="compiler-production",
            domain="platform",
            owner="platform-governance",
            spec={
                "policy_type": "compiler",
                "allowed_compilers": {
                    **{target: [f"{target}@1"] for target in targets},
                    "semantic-json": ["semantic-json@1"],
                },
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
    compile_plan = registry.plan(release, resources=resources, targets=targets)
    first = service.execute(
        run_id="sales-query-all-001",
        plan=compile_plan,
        policy=policy,
        release=release,
        resources=resources,
        actor="compiler:ci",
        rationale="Compile the unified query runtime.",
    )
    replay = service.replay(
        first.run_id,
        run_id="sales-query-all-002",
        policy=policy,
        release=release,
        resources=resources,
        actor="compiler:replay-ci",
        rationale="Reproduce the unified query runtime.",
    )
    service.promote(
        replay.run_id,
        channel="production",
        actor="publisher:bob",
        approved_by="reviewer:alice",
        rationale="Promote the reproduced unified query runtime.",
    )
    resolver = TrustedSnapshotResolver(repository)
    return resolver, QueryExecutor(resolver)


def test_unified_executor_returns_digest_bound_semantic_search_result(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    request = QueryRequest.create(
        channel="production",
        capability=QueryCapability.SEMANTIC_SEARCH,
        query="customer",
        purpose="customer-support",
        parameters={"limit": 3},
    )
    plan = resolver.plan(request, tenant_id="acme")

    result = executor.execute(plan)

    assert result.status == "succeeded"
    assert result.capability is QueryCapability.SEMANTIC_SEARCH
    assert result.plan_digest == plan.plan_digest
    assert result.run_digest == plan.run_digest
    assert result.data["count"] >= 1
    assert result.data["hits"][0]["resource_id"] == "aof://acme/sales/concept/customer"
    assert result.evidence[0]["target"] == "rag"
    assert result.result_digest.startswith("sha256:")
    assert result.to_dict()["api_version"] == "aof.query-result/v1"


def test_unified_executor_runs_datalog_from_the_same_query_contract(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability=QueryCapability.DATALOG,
            query="eligible",
            purpose="eligibility-check",
            parameters={"facts": [{"predicate": "customer", "terms": ["alice"]}]},
        ),
        tenant_id="acme",
    )

    result = executor.execute(plan)

    assert result.data["derived_count"] == 1
    assert result.data["derived_facts"][0]["predicate"] == "eligible"
    assert result.data["derived_facts"][0]["terms"] == ("alice",)
    assert result.evidence[0]["target"] == "datalog"


def test_unified_executor_runs_read_only_sparql_from_the_same_query_contract(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability=QueryCapability.SPARQL,
            query="ASK { <https://example.test/Customer> a <https://example.test/Entity> }",
            purpose="ontology-audit",
        ),
        tenant_id="acme",
    )

    result = executor.execute(plan)

    assert result.data["type"] == "ASK"
    assert result.data["boolean"] is True
    assert result.data["snapshot_id"] == f"release:{plan.release_digest}"
    assert result.evidence[0]["target"] == "owl"


def test_unified_executor_renders_governed_query_template_without_side_effects(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability=QueryCapability.QUERY_TEMPLATE,
            query="customer-by-region",
            purpose="regional-customer-analysis",
            parameters={"region": "'east'"},
        ),
        tenant_id="acme",
    )

    result = executor.execute(plan)

    assert result.data["template_resource_id"] == (
        "aof://acme/sales/query-template/customer-by-region"
    )
    assert result.data["rendered_query"] == "SELECT * FROM customers WHERE region = 'east'"
    assert result.data["executed"] is False
    assert [item["target"] for item in result.evidence] == ["semantic-json", "mcp"]


def test_unified_executor_rejects_plan_content_with_a_stale_digest(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_search",
            query="customer",
            purpose="customer-support",
        ),
        tenant_id="acme",
    )
    tampered = replace(plan, run_id="attacker-selected-run")

    with pytest.raises(TrustedQueryError, match="query plan digest mismatch"):
        executor.execute(tampered)
