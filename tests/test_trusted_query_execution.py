"""Unified deterministic execution over trusted query plans."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from dataclasses import replace

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    AuditedQueryService,
    KnowledgeRelease,
    QueryCapability,
    QueryExecutor,
    QueryExecutorRegistry,
    QueryExecutionLimits,
    GovernedQueryExecutor,
    FederatedQueryExecutor,
    FederatedQueryPlanner,
    FederatedQueryRequest,
    FederatedQueryStep,
    HmacQueryEvidenceAttestor,
    QueryPolicy,
    QueryControlPlane,
    QueryPolicyWaiver,
    QueryRequest,
    ResourceKind,
    SemanticResource,
    SemanticIntent,
    SignedPrincipalVerifier,
    SqliteSemanticSqlExecutor,
    TrustedQueryError,
    TrustedSnapshotResolver,
    TrustedRuntimeTelemetry,
)
from bridge.semantic_core.compilers import (
    CompilationRunRepository,
    CompilationRunService,
    CompilerPolicy,
    default_compiler_registry,
)


def _trusted_query_runtime(
    tmp_path, compiler_root=None, repository_factory=CompilationRunRepository
):
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
    secret_concept = SemanticResource.create(
        resource_id="aof://acme/sales/concept/customer-secret",
        kind=ResourceKind.CONCEPT,
        name="customer-secret",
        domain="sales",
        owner="restricted-team",
        description="A restricted customer risk profile.",
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
    dataset = SemanticResource.create(
        resource_id="aof://acme/sales/physical-dataset/order-detail",
        kind=ResourceKind.PHYSICAL_DATASET,
        name="order-detail",
        domain="sales",
        owner="data-platform",
        spec={"physical_name": "dwd.order_detail"},
    )
    metric = SemanticResource.create(
        resource_id="aof://acme/sales/metric/gmv",
        kind=ResourceKind.METRIC,
        name="gmv",
        domain="sales",
        owner="data-platform",
        depends_on=[dataset.resource_id],
        spec={"aggregation": "sum", "measure": "paid_amount"},
    )
    dimension = SemanticResource.create(
        resource_id="aof://acme/sales/dimension/order-date",
        kind=ResourceKind.DIMENSION,
        name="order-date",
        domain="sales",
        owner="data-platform",
        depends_on=[dataset.resource_id],
        spec={"field": "order_date", "data_type": "date"},
    )
    query_policy = SemanticResource.create(
        resource_id="aof://acme/platform/policy/query-trusted",
        kind=ResourceKind.POLICY,
        name="query-trusted",
        domain="platform",
        owner="security-governance",
        spec={
            "policy_type": "query",
            "role_capabilities": {
                "analyst": [
                    "semantic_search",
                    "semantic_sql",
                    "graph",
                    "datalog",
                    "sparql",
                    "query_template",
                ]
            },
        },
    )
    resources = [
        ontology,
        rules,
        concept,
        secret_concept,
        profile,
        template,
        dataset,
        metric,
        dimension,
        query_policy,
    ]
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
    repository = repository_factory((compiler_root or tmp_path / "compiler") / "acme")
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


def test_sqlite_executor_runs_deterministic_semantic_sql_against_real_data(tmp_path) -> None:
    resolver, _ = _trusted_query_runtime(tmp_path)
    main_database = tmp_path / "warehouse-main.sqlite3"
    attached_database = tmp_path / "dwd.sqlite3"
    sqlite3.connect(main_database).close()
    with sqlite3.connect(attached_database) as connection:
        connection.execute(
            "CREATE TABLE order_detail "
            "(order_date TEXT NOT NULL, paid_amount REAL NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO order_detail VALUES (?, ?)",
            [
                ("2026-08-17", 10.0),
                ("2026-08-17", 15.5),
                ("2026-08-18", 7.0),
            ],
        )
    intent = SemanticIntent.create(
        metrics=["aof://acme/sales/metric/gmv"],
        dimensions=["aof://acme/sales/dimension/order-date"],
        purpose="daily-sales-report",
    )
    registry = QueryExecutorRegistry()
    registry.register(
        "semantic_sql",
        SqliteSemanticSqlExecutor(
            resolver,
            database=main_database,
            attachments={"dwd": attached_database},
        ),
    )
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_sql",
            query=intent.intent_digest,
            purpose="daily-sales-report",
            parameters={"intent": intent.to_dict()},
        ),
        tenant_id="acme",
    )

    result = QueryExecutor(resolver, registry=registry).execute(plan)

    assert result.data["executed"] is True
    assert result.to_dict()["data"]["rows"] == [
        {"gmv": 25.5, "order_date": "2026-08-17"},
        {"gmv": 7.0, "order_date": "2026-08-18"},
    ]
    assert result.data["data_snapshot"]["snapshot_digest"].startswith("sha256:")


def test_sqlite_executor_fails_closed_on_resource_budgets_and_cancellation(
    tmp_path, monkeypatch
) -> None:
    resolver, _ = _trusted_query_runtime(tmp_path)
    main_database = tmp_path / "warehouse-main.sqlite3"
    attached_database = tmp_path / "dwd.sqlite3"
    sqlite3.connect(main_database).close()
    with sqlite3.connect(attached_database) as connection:
        connection.execute(
            "CREATE TABLE order_detail "
            "(order_date TEXT NOT NULL, paid_amount REAL NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO order_detail VALUES (?, ?)",
            [("2026-08-17", 10.0), ("2026-08-18", 7.0)],
        )
    intent = SemanticIntent.create(
        metrics=["aof://acme/sales/metric/gmv"],
        dimensions=["aof://acme/sales/dimension/order-date"],
        purpose="daily-sales-report",
    )
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_sql",
            query=intent.intent_digest,
            purpose="daily-sales-report",
            parameters={"intent": intent.to_dict()},
        ),
        tenant_id="acme",
    )

    bounded = SqliteSemanticSqlExecutor(
        resolver,
        database=main_database,
        attachments={"dwd": attached_database},
        limits=QueryExecutionLimits(max_rows=1),
    )
    with pytest.raises(TrustedQueryError, match="row limit exceeded"):
        bounded(plan)

    cancelled = threading.Event()
    cancelled.set()
    cancellable = SqliteSemanticSqlExecutor(
        resolver,
        database=main_database,
        attachments={"dwd": attached_database},
        cancel_check=cancelled.is_set,
        limits=QueryExecutionLimits(progress_interval=1),
    )
    with pytest.raises(TrustedQueryError, match="cancelled"):
        cancellable(plan)

    vm_bounded = SqliteSemanticSqlExecutor(
        resolver,
        database=main_database,
        attachments={"dwd": attached_database},
        limits=QueryExecutionLimits(max_vm_steps=1, progress_interval=1),
    )
    with pytest.raises(TrustedQueryError, match="VM step limit exceeded"):
        vm_bounded(plan)

    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(
        "bridge.semantic_core.query_execution.time.monotonic",
        lambda: next(ticks, 1.0),
    )
    timed = SqliteSemanticSqlExecutor(
        resolver,
        database=main_database,
        attachments={"dwd": attached_database},
        limits=QueryExecutionLimits(timeout_ms=1, progress_interval=1),
    )
    with pytest.raises(TrustedQueryError, match="timeout"):
        timed(plan)


def test_query_execution_limits_parse_production_environment_fail_closed() -> None:
    limits = QueryExecutionLimits.from_environment(
        {
            "AOF_QUERY_TIMEOUT_MS": "2500",
            "AOF_QUERY_MAX_ROWS": "500",
            "AOF_QUERY_MAX_VM_STEPS": "750000",
            "AOF_QUERY_PROGRESS_INTERVAL": "250",
        }
    )
    assert limits == QueryExecutionLimits(
        timeout_ms=2500,
        max_rows=500,
        max_vm_steps=750000,
        progress_interval=250,
    )
    with pytest.raises(TrustedQueryError, match="AOF_QUERY_MAX_ROWS"):
        QueryExecutionLimits.from_environment({"AOF_QUERY_MAX_ROWS": "unbounded"})


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


def test_unified_executor_compiles_published_semantic_intent_to_sql(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    intent = SemanticIntent.create(
        metrics=["aof://acme/sales/metric/gmv"],
        dimensions=["aof://acme/sales/dimension/order-date"],
        purpose="daily-sales-report",
    )
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_sql",
            query=intent.intent_digest,
            purpose="daily-sales-report",
            parameters={"intent": intent.to_dict()},
        ),
        tenant_id="acme",
    )

    result = executor.execute(plan)

    assert result.data["executed"] is False
    assert result.data["sql_plan"]["intent_digest"] == intent.intent_digest
    assert result.data["sql_plan"]["sql"] == (
        'SELECT "order_date" AS "order_date", SUM("paid_amount") AS "gmv" '
        'FROM "dwd"."order_detail" GROUP BY "order_date" ORDER BY "order_date"'
    )
    assert result.evidence[0]["target"] == "semantic-json"


def test_unified_executor_traverses_published_graph_snapshot(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="graph",
            query="https://example.test/Customer",
            purpose="relationship-investigation",
            parameters={"direction": "out", "limit": 10},
        ),
        tenant_id="acme",
    )

    result = executor.execute(plan)

    assert result.data["snapshot_id"] == f"release:{plan.release_digest}"
    assert result.to_dict()["data"]["edges"] == [
        {
            "subject": "https://example.test/Customer",
            "predicate": "http://www.w3.org/1999/02/22-rdf-syntax-ns#type",
            "object": "https://example.test/Entity",
        }
    ]
    assert result.evidence[0]["target"] == "owl"


def test_query_executor_spi_preserves_trusted_result_envelope(tmp_path) -> None:
    resolver, _ = _trusted_query_runtime(tmp_path)
    registry = QueryExecutorRegistry()
    registry.register(
        QueryCapability.SEMANTIC_SEARCH,
        lambda plan: {"query": plan.query, "source": "enterprise-search-spi"},
    )
    executor = QueryExecutor(resolver, registry=registry)
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_search",
            query="customer",
            purpose="customer-support",
        ),
        tenant_id="acme",
    )

    result = executor.execute(plan)

    assert result.data["source"] == "enterprise-search-spi"
    assert result.plan_digest == plan.plan_digest
    assert result.release_digest == plan.release_digest
    assert result.evidence[0]["target"] == "rag"


def test_federated_query_dag_executes_one_release_with_unified_evidence(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    request = FederatedQueryRequest.create(
        channel="production",
        purpose="customer-eligibility-investigation",
        steps=[
            FederatedQueryStep.create(
                step_id="search",
                capability="semantic_search",
                query="customer",
            ),
            FederatedQueryStep.create(
                step_id="relations",
                capability="graph",
                query="https://example.test/Customer",
                parameters={"direction": "out"},
                depends_on=["search"],
            ),
            FederatedQueryStep.create(
                step_id="eligibility",
                capability="datalog",
                query="eligible",
                parameters={
                    "facts": [{"predicate": "customer", "terms": ["alice"]}]
                },
                depends_on=["relations"],
            ),
        ],
    )
    plan = FederatedQueryPlanner(resolver).plan(request, tenant_id="acme")

    result = FederatedQueryExecutor(executor).execute(plan)

    assert [step.step_id for step in plan.steps] == [
        "search",
        "relations",
        "eligibility",
    ]
    assert result.status == "succeeded"
    assert result.release_digest == plan.release_digest
    assert result.to_dict()["step_results"]["eligibility"]["data"][
        "derived_count"
    ] == 1
    assert [item["target"] for item in result.to_dict()["evidence"]] == [
        "datalog",
        "owl",
        "rag",
    ]
    assert result.result_digest.startswith("sha256:")


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


def test_query_policy_requires_exact_waiver_and_emits_field_evidence(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_search",
            query="customer",
            purpose="customer-support",
            parameters={"limit": 3, "fields": ["resource_id", "name"]},
        ),
        tenant_id="acme",
    )
    policy_resource = SemanticResource.create(
        resource_id="aof://acme/platform/policy/query-production",
        kind=ResourceKind.POLICY,
        name="query-production",
        domain="platform",
        owner="security-governance",
        spec={
            "policy_type": "query",
            "role_capabilities": {"analyst": ["semantic_search"]},
            "capability_rules": {
                "semantic_search": {
                    "allowed_purposes": ["customer-support"],
                    "max_limit": 2,
                    "allowed_fields": ["resource_id", "name"],
                }
            },
            "waiver_allowed_codes": ["query_limit_exceeded"],
        },
    )
    policy = QueryPolicy.from_resource(policy_resource)
    rejected = policy.evaluate(plan, roles=["analyst"])
    assert rejected.conforms is False
    assert rejected.findings[0].code == "query_limit_exceeded"

    waiver = QueryPolicyWaiver.create(
        finding_id=rejected.findings[0].finding_id,
        policy_revision=policy_resource.revision_id,
        actor="risk-owner:alice",
        rationale="Temporary support burst approved for incident INC-42.",
        authority="INC-42",
    )
    governed = GovernedQueryExecutor(executor, policy).execute(
        plan, roles=["analyst"], waivers=[waiver]
    )

    assert governed.result.status == "succeeded"
    assert governed.policy_report.conforms is True
    assert governed.policy_report.findings[0].waiver_id == waiver.waiver_id
    assert [item["field"] for item in governed.policy_report.field_evidence] == [
        "name",
        "resource_id",
    ]
    assert governed.governed_result_digest.startswith("sha256:")


def test_query_policy_blocks_role_purpose_resource_and_field_violations(tmp_path) -> None:
    resolver, _ = _trusted_query_runtime(tmp_path)
    denied_resource = "aof://acme/sales/concept/customer"
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_search",
            query="customer",
            purpose="bulk-export",
            parameters={
                "resource_ids": [denied_resource],
                "fields": ["secret_note"],
            },
        ),
        tenant_id="acme",
    )
    policy = QueryPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/query-restricted",
            kind=ResourceKind.POLICY,
            name="query-restricted",
            domain="platform",
            owner="security-governance",
            spec={
                "policy_type": "query",
                "role_capabilities": {"analyst": ["semantic_search"]},
                "capability_rules": {
                    "semantic_search": {
                        "allowed_purposes": ["customer-support"],
                        "denied_resource_ids": [denied_resource],
                        "allowed_fields": ["resource_id", "name"],
                    }
                },
                "waiver_allowed_codes": [
                    "query_role_not_allowed",
                    "query_purpose_not_allowed",
                ],
            },
        )
    )

    report = policy.evaluate(plan, roles=["guest"])

    assert {item.code for item in report.findings} == {
        "query_field_not_allowed",
        "query_purpose_not_allowed",
        "query_resource_denied",
        "query_role_not_allowed",
    }
    role_finding = next(item for item in report.findings if item.code == "query_role_not_allowed")
    assert role_finding.waiver_allowed is False
    assert report.conforms is False


def test_query_policy_scope_hides_unauthorized_resources_and_fields(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    public_resource = "aof://acme/sales/concept/customer"
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_search",
            query="customer",
            purpose="customer-support",
        ),
        tenant_id="acme",
    )
    policy = QueryPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/query-visible-customer",
            kind=ResourceKind.POLICY,
            name="query-visible-customer",
            domain="platform",
            owner="security-governance",
            spec={
                "policy_type": "query",
                "role_capabilities": {"analyst": ["semantic_search"]},
                "capability_rules": {
                    "semantic_search": {
                        "allowed_resource_ids": [public_resource],
                        "allowed_fields": ["name"],
                    }
                },
            },
        )
    )

    governed = GovernedQueryExecutor(executor, policy).execute(
        plan, roles=["analyst"]
    )

    assert governed.policy_report.resource_scope == (public_resource,)
    assert governed.policy_report.field_scope == ("name",)
    assert governed.result.to_dict()["data"]["hits"] == [{"name": "customer"}]


def test_query_policy_checks_resolved_semantic_sql_dependency_closure(tmp_path) -> None:
    resolver, _ = _trusted_query_runtime(tmp_path)
    metric_id = "aof://acme/sales/metric/gmv"
    dimension_id = "aof://acme/sales/dimension/order-date"
    dataset_id = "aof://acme/sales/physical-dataset/order-detail"
    intent = SemanticIntent.create(
        metrics=[metric_id],
        dimensions=[dimension_id],
        purpose="daily-sales-report",
    )
    plan = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_sql",
            query=intent.intent_digest,
            purpose="daily-sales-report",
            parameters={"intent": intent.to_dict()},
        ),
        tenant_id="acme",
    )
    policy = QueryPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/query-sql-limited",
            kind=ResourceKind.POLICY,
            name="query-sql-limited",
            domain="platform",
            owner="security-governance",
            spec={
                "policy_type": "query",
                "role_capabilities": {"analyst": ["semantic_sql"]},
                "capability_rules": {
                    "semantic_sql": {
                        "allowed_resource_ids": [metric_id, dimension_id],
                    }
                },
            },
        )
    )

    report = policy.evaluate(plan, roles=["analyst"])

    assert plan.resolved_resource_ids == (dimension_id, metric_id, dataset_id)
    assert report.conforms is False
    assert [(item.code, item.subject) for item in report.findings] == [
        ("query_resource_not_allowed", dataset_id)
    ]


def test_audited_query_emits_verifiable_causal_evidence_package(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    policy = QueryPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/query-audit",
            kind=ResourceKind.POLICY,
            name="query-audit",
            domain="platform",
            owner="security-governance",
            spec={
                "policy_type": "query",
                "role_capabilities": {"analyst": ["semantic_search"]},
                "capability_rules": {
                    "semantic_search": {
                        "allowed_purposes": ["customer-support"],
                        "max_limit": 10,
                        "allowed_fields": ["resource_id", "name"],
                    }
                },
                "waiver_allowed_codes": ["query_limit_exceeded"],
            },
        )
    )
    request = QueryRequest.create(
        channel="production",
        capability="semantic_search",
        query="customer",
        purpose="customer-support",
        parameters={"limit": 11, "fields": ["resource_id"]},
    )
    planned = resolver.plan(request, tenant_id="acme")
    finding = policy.evaluate(planned, roles=["analyst"]).findings[0]
    waiver = QueryPolicyWaiver.create(
        finding_id=finding.finding_id,
        policy_revision=policy.revision_id,
        actor="risk-owner:alice",
        rationale="Incident response exception approved.",
        authority="INC-99",
    )
    service = AuditedQueryService(
        resolver=resolver,
        governed_executor=GovernedQueryExecutor(executor, policy),
        decision_store=decisions,
    )

    audited = service.execute(
        request,
        tenant_id="acme",
        actor="agent:support",
        roles=["analyst"],
        rationale="Answer a governed customer support question.",
        waivers=[waiver],
    )

    assert audited.evidence_package.verify() is True
    assert audited.evidence_package.governed_result_digest == (
        audited.governed_result.governed_result_digest
    )
    trail = decisions.audit_trail(audited.execution_decision_id)
    query_nodes = [
        node["decision"]
        for node in trail["causal_chain"]["nodes"]
        if node["decision"]["decision_type"].startswith("semantic_query_")
    ]
    assert {node["decision_type"] for node in query_nodes} == {
        "semantic_query_plan",
        "semantic_query_policy",
        "semantic_query_policy_waiver",
        "semantic_query_execute",
    }
    assert trail["integrity"]["valid"] is True
    assert audited.evidence_package.artifact_evidence[0]["target"] == "rag"
    assert audited.evidence_package.field_evidence[0]["field"] == "resource_id"

    precedents = decisions.find_precedents(
        "semantic_query_execute", tags=["semantic_search", "customer-support"]
    )
    assert precedents[0]["decision"]["decision"]["id"] == audited.execution_decision_id


def test_query_evidence_package_detects_tampering(tmp_path) -> None:
    resolver, executor = _trusted_query_runtime(tmp_path)
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    policy = QueryPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/query-audit",
            kind=ResourceKind.POLICY,
            name="query-audit",
            domain="platform",
            owner="security-governance",
            spec={
                "policy_type": "query",
                "role_capabilities": {"analyst": ["semantic_search"]},
            },
        )
    )
    audited = AuditedQueryService(
        resolver=resolver,
        governed_executor=GovernedQueryExecutor(executor, policy),
        decision_store=decisions,
    ).execute(
        QueryRequest.create(
            channel="production",
            capability="semantic_search",
            query="customer",
            purpose="customer-support",
        ),
        tenant_id="acme",
        actor="agent:support",
        roles=["analyst"],
        rationale="Answer a governed customer support question.",
    )

    tampered = replace(
        audited.evidence_package,
        governed_result_digest="sha256:attacker-controlled",
    )

    assert tampered.verify() is False


def _query_headers(capability_role: str = "analyst") -> dict[str, str]:
    return SignedPrincipalVerifier(
        key_id="query-identity", secret=b"query-identity-secret"
    ).sign_headers(subject="alice", tenant_id="acme", roles=[capability_role])


def test_signed_query_control_plane_executes_all_compiled_engines(tmp_path) -> None:
    _trusted_query_runtime(tmp_path)
    telemetry = TrustedRuntimeTelemetry()
    control = QueryControlPlane(
        tmp_path / "compiler",
        verifier=SignedPrincipalVerifier(
            key_id="query-identity", secret=b"query-identity-secret"
        ),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
        telemetry=telemetry,
    )
    base = {
        "channel": "production",
        "policy_resource_id": "aof://acme/platform/policy/query-trusted",
        "rationale": "Verify the governed cross-engine query platform.",
    }
    requests = [
        {**base, "capability": "semantic_search", "query": "customer", "purpose": "support"},
        {
            **base,
            "capability": "datalog",
            "query": "eligible",
            "purpose": "eligibility",
            "parameters": {"facts": [{"predicate": "customer", "terms": ["alice"]}]},
        },
        {
            **base,
            "capability": "sparql",
            "query": "ASK { <https://example.test/Customer> a <https://example.test/Entity> }",
            "purpose": "ontology-audit",
        },
        {
            **base,
            "capability": "query_template",
            "query": "customer-by-region",
            "purpose": "regional-analysis",
            "parameters": {"region": "'east'"},
        },
    ]

    results = [control.execute(payload, headers=_query_headers()) for payload in requests]

    assert [item["governed_result"]["result"]["capability"] for item in results] == [
        "semantic_search",
        "datalog",
        "sparql",
        "query_template",
    ]
    assert all(item["evidence_package"]["package_digest"].startswith("sha256:") for item in results)
    observed = telemetry.snapshot()
    assert observed["operations"]["query.execute"]["succeeded"] == 4
    assert observed["last_correlation"]["release_id"] == "sales-query@2.0.0"


def test_rest_and_mcp_query_boundaries_share_signed_control_plane(tmp_path, monkeypatch) -> None:
    from fastapi.testclient import TestClient

    import mcp_server
    import services.semantic_middle_layer_api.app as api_module

    compiler_root = tmp_path / "data" / "semantic_compiler"
    _trusted_query_runtime(tmp_path, compiler_root=compiler_root)
    monkeypatch.setattr(api_module, "AOF_ROOT", tmp_path)
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_SECRET", "query-identity-secret")
    monkeypatch.setenv("AOF_SEMANTIC_IDENTITY_KEY_ID", "query-identity")
    monkeypatch.setenv("AOF_COMPILER_STATE_DIR", str(compiler_root))
    monkeypatch.setenv(
        "AOF_DECISION_PROVENANCE_FILE",
        str(tmp_path / "data" / "audit" / "decision_provenance.jsonl"),
    )
    payload = {
        "channel": "production",
        "capability": "semantic_search",
        "query": "customer",
        "purpose": "support",
        "policy_resource_id": "aof://acme/platform/policy/query-trusted",
        "rationale": "Exercise the signed transport boundary.",
    }

    client = TestClient(api_module.app)
    response = client.post(
        "/v1/semantic/query",
        json={**payload, "query_run_id": "rest-query-001"},
        headers=_query_headers(),
    )
    server = mcp_server.build_server()
    mcp_result = asyncio.run(
        server.tools["aof_semantic_query"].handler(
            {
                **payload,
                "query_run_id": "mcp-query-001",
                "principal_headers": _query_headers(),
            }
        )
    )
    stored = client.get(
        "/v1/semantic/query-runs/rest-query-001", headers=_query_headers()
    )
    replay = client.post(
        "/v1/semantic/query-runs/replay",
        json={
            "source_query_run_id": "rest-query-001",
            "expected_source_digest": response.json()["run_digest"],
            "query_run_id": "rest-query-002",
            "rationale": "Verify strict REST replay.",
        },
        headers=_query_headers(),
    )
    mcp_stored = asyncio.run(
        server.tools["aof_semantic_query_get_run"].handler(
            {
                "query_run_id": "mcp-query-001",
                "principal_headers": _query_headers(),
            }
        )
    )

    assert response.status_code == 200
    assert stored.status_code == 200
    assert replay.status_code == 201
    assert response.json()["governed_result"]["result"]["capability"] == "semantic_search"
    assert response.json()["attestation"]["signature"]
    assert stored.json() == response.json()
    assert replay.json()["replay_of"] == "rest-query-001"
    assert mcp_result["governed_result"]["result"]["capability"] == "semantic_search"
    assert mcp_stored == mcp_result


def test_query_control_plane_rejects_bad_identity_and_unpublished_policy(tmp_path) -> None:
    _trusted_query_runtime(tmp_path)
    control = QueryControlPlane(
        tmp_path / "compiler",
        verifier=SignedPrincipalVerifier(
            key_id="query-identity", secret=b"query-identity-secret"
        ),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    payload = {
        "channel": "production",
        "capability": "semantic_search",
        "query": "customer",
        "purpose": "support",
        "policy_resource_id": "aof://acme/platform/policy/not-published",
        "rationale": "Verify boundary rejection.",
    }
    bad_headers = _query_headers()
    bad_headers["x-aof-principal-signature"] = "bad"

    with pytest.raises(ValueError, match="signature"):
        control.execute(payload, headers=bad_headers)
    with pytest.raises(ValueError, match="not in the trusted release"):
        control.execute(payload, headers=_query_headers())


def test_query_control_plane_persists_and_strictly_replays_signed_runs(tmp_path) -> None:
    _trusted_query_runtime(tmp_path)
    attestor = HmacQueryEvidenceAttestor(
        key_id="query-evidence", secret=b"query-evidence-secret"
    )
    control = QueryControlPlane(
        tmp_path / "compiler",
        verifier=SignedPrincipalVerifier(
            key_id="query-identity", secret=b"query-identity-secret"
        ),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
        evidence_attestor=attestor,
    )
    payload = {
        "query_run_id": "query-001",
        "channel": "production",
        "capability": "semantic_search",
        "query": "customer",
        "purpose": "support",
        "policy_resource_id": "aof://acme/platform/policy/query-trusted",
        "rationale": "Persist an immutable governed query.",
    }

    first = control.execute(payload, headers=_query_headers())
    stored = control.get_run("query-001", headers=_query_headers())
    replayed = control.replay(
        {
            "source_query_run_id": "query-001",
            "expected_source_digest": first["run_digest"],
            "query_run_id": "query-002",
            "rationale": "Strictly replay the pinned query.",
        },
        headers=_query_headers(),
    )

    assert stored == first
    assert first["status"] == "succeeded"
    assert attestor.verify(first["attestation"], run=control.query_runs.get(
        "query-001", tenant_id="acme"
    ))
    assert replayed["replay_of"] == "query-001"
    assert replayed["plan_digest"] == first["plan_digest"]
    assert replayed["governed_result"]["governed_result_digest"] == (
        first["governed_result"]["governed_result_digest"]
    )

    with pytest.raises(ValueError, match="source digest"):
        control.replay(
            {
                "source_query_run_id": "query-001",
                "expected_source_digest": "sha256:wrong",
                "query_run_id": "query-003",
                "rationale": "Reject a non-exact replay source.",
            },
            headers=_query_headers(),
        )

    CompilationRunRepository(tmp_path / "compiler" / "acme").advance_channel(
        "production",
        {
            "action": "rollback",
            "run_id": "sales-query-all-001",
            "previous_run_id": "sales-query-all-002",
            "actor": "publisher:test",
            "decision_id": "decision:test-channel-move",
        },
    )
    with pytest.raises(ValueError, match="snapshot no longer matches"):
        control.replay(
            {
                "source_query_run_id": "query-001",
                "expected_source_digest": first["run_digest"],
                "query_run_id": "query-004",
                "rationale": "Reject a replay after channel movement.",
            },
            headers=_query_headers(),
        )


def test_strict_query_replay_rejects_changed_data_snapshot(tmp_path) -> None:
    _trusted_query_runtime(tmp_path)
    main_database = tmp_path / "replay-main.sqlite3"
    attached_database = tmp_path / "replay-dwd.sqlite3"
    sqlite3.connect(main_database).close()
    with sqlite3.connect(attached_database) as connection:
        connection.execute(
            "CREATE TABLE order_detail "
            "(order_date TEXT NOT NULL, paid_amount REAL NOT NULL)"
        )
        connection.execute(
            "INSERT INTO order_detail VALUES (?, ?)", ("2026-08-18", 10.0)
        )

    def executor_factory(resolver):
        executor = QueryExecutor(resolver)
        executor.registry.replace(
            "semantic_sql",
            SqliteSemanticSqlExecutor(
                resolver,
                database=main_database,
                attachments={"dwd": attached_database},
            ),
        )
        return executor

    control = QueryControlPlane(
        tmp_path / "compiler",
        verifier=SignedPrincipalVerifier(
            key_id="query-identity", secret=b"query-identity-secret"
        ),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
        executor_factory=executor_factory,
    )
    intent = SemanticIntent.create(
        metrics=["aof://acme/sales/metric/gmv"],
        dimensions=["aof://acme/sales/dimension/order-date"],
        purpose="daily-sales-report",
    )
    first = control.execute(
        {
            "query_run_id": "snapshot-query-001",
            "channel": "production",
            "capability": "semantic_sql",
            "query": intent.intent_digest,
            "purpose": "daily-sales-report",
            "parameters": {"intent": intent.to_dict()},
            "policy_resource_id": "aof://acme/platform/policy/query-trusted",
            "rationale": "Bind the first warehouse snapshot.",
        },
        headers=_query_headers(),
    )
    with sqlite3.connect(attached_database) as connection:
        connection.execute(
            "INSERT INTO order_detail VALUES (?, ?)", ("2026-08-18", 5.0)
        )

    with pytest.raises(ValueError, match="result no longer matches"):
        control.replay(
            {
                "source_query_run_id": "snapshot-query-001",
                "expected_source_digest": first["run_digest"],
                "query_run_id": "snapshot-query-002",
                "rationale": "Do not disguise a refreshed warehouse as strict replay.",
            },
            headers=_query_headers(),
        )
    assert control.query_runs.get("snapshot-query-002", tenant_id="acme") is None


def test_failed_governed_query_is_persisted_as_signed_terminal_run(tmp_path) -> None:
    _trusted_query_runtime(tmp_path)
    control = QueryControlPlane(
        tmp_path / "compiler",
        verifier=SignedPrincipalVerifier(
            key_id="query-identity", secret=b"query-identity-secret"
        ),
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )

    with pytest.raises(ValueError, match="graph direction"):
        control.execute(
            {
                "query_run_id": "failed-query-001",
                "channel": "production",
                "capability": "graph",
                "query": "https://example.test/Customer",
                "purpose": "ontology-audit",
                "parameters": {"direction": "sideways"},
                "policy_resource_id": "aof://acme/platform/policy/query-trusted",
                "rationale": "Capture a deterministic executor failure.",
            },
            headers=_query_headers(),
        )

    failed = control.get_run("failed-query-001", headers=_query_headers())
    assert failed["status"] == "failed"
    assert failed["error"]["type"] == "TrustedQueryError"
    assert failed["decisions"]["failure"].startswith("decision:")
    assert failed["attestation"]["signature"]
