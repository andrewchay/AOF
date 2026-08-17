"""Real repository assets through one SQL, graph, RAG, and Datalog query DAG."""

from __future__ import annotations

import sqlite3

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    FederatedQueryExecutor,
    FederatedQueryPlanner,
    FederatedQueryRequest,
    FederatedQueryStep,
    KnowledgeRelease,
    QueryExecutor,
    ResourceKind,
    SemanticIntent,
    SemanticResource,
    SqliteSemanticSqlExecutor,
    TrustedQueryError,
    TrustedSnapshotResolver,
)
from bridge.semantic_core.compilers import (
    CompilationRunRepository,
    CompilationRunService,
    CompilerPolicy,
    default_compiler_registry,
)
from tests.test_semantic_release_e2e import _real_resources


def test_real_assets_execute_as_one_snapshot_bound_cross_engine_dag(tmp_path) -> None:
    resources = _real_resources()
    resources.append(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/query-real-e2e",
            kind=ResourceKind.POLICY,
            name="query-real-e2e",
            domain="platform",
            owner="security-governance",
            spec={
                "policy_type": "query",
                "role_capabilities": {
                    "analyst": ["semantic_sql", "semantic_search", "graph", "datalog"]
                },
            },
        )
    )
    release = KnowledgeRelease.build(
        release_id="genshin-query@e2e.1",
        resources=resources,
        scope={"tenant_id": "acme"},
    )
    targets = ["semantic-json", "owl", "rag", "datalog"]
    compiler_policy = CompilerPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/compiler-real-query",
            kind=ResourceKind.POLICY,
            name="compiler-real-query",
            domain="platform",
            owner="platform-governance",
            spec={
                "policy_type": "compiler",
                "allowed_compilers": {
                    target: [f"{target}@1"] for target in targets
                },
                "required_targets": targets,
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
        run_id="genshin-query-001",
        plan=compile_plan,
        policy=compiler_policy,
        release=release,
        resources=resources,
        actor="compiler:real-e2e",
        rationale="Compile real repository assets for trusted query execution.",
    )
    replay = service.replay(
        first.run_id,
        run_id="genshin-query-002",
        policy=compiler_policy,
        release=release,
        resources=resources,
        actor="compiler:real-e2e-replay",
        rationale="Reproduce every cross-engine artifact.",
    )
    service.promote(
        replay.run_id,
        channel="production",
        actor="publisher:bob",
        approved_by="reviewer:alice",
        rationale="Promote the reproducible real-asset query runtime.",
    )

    main_database = tmp_path / "warehouse.sqlite3"
    community_database = tmp_path / "dim-community.sqlite3"
    sqlite3.connect(main_database).close()
    with sqlite3.connect(community_database) as connection:
        connection.execute(
            "CREATE TABLE dim_community_post_info_snapshot "
            "(logdate TEXT NOT NULL, post_id INTEGER NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO dim_community_post_info_snapshot VALUES (?, ?)",
            [("2026-08-17", 1), ("2026-08-17", 2), ("2026-08-18", 3)],
        )

    resolver = TrustedSnapshotResolver(repository)
    executor = QueryExecutor(resolver)
    executor.registry.replace(
        "semantic_sql",
        SqliteSemanticSqlExecutor(
            resolver,
            database=main_database,
            attachments={"dim_community": community_database},
        ),
    )
    intent = SemanticIntent.create(
        metrics=["aof://acme/genshin/metric/post_cnt_30d"],
        dimensions=[
            "aof://acme/genshin/dimension/"
            "logdate-dim_community.dim_community_post_info_snapshot.logdate"
        ],
        purpose="trusted-knowledge-e2e",
    )
    request = FederatedQueryRequest.create(
        channel="production",
        purpose="trusted-knowledge-e2e",
        steps=[
            FederatedQueryStep.create(
                step_id="01-search", capability="semantic_search", query="genshin"
            ),
            FederatedQueryStep.create(
                step_id="02-graph",
                capability="graph",
                query="http://genshin.mihoyo.com/ontology#PlayableCharacter",
                parameters={"direction": "out"},
                depends_on=["01-search"],
            ),
            FederatedQueryStep.create(
                step_id="03-rules",
                capability="datalog",
                query="searchable",
                parameters={
                    "facts": [{"predicate": "indexed", "terms": ["raiden"]}]
                },
                depends_on=["02-graph"],
            ),
            FederatedQueryStep.create(
                step_id="04-sql",
                capability="semantic_sql",
                query=intent.intent_digest,
                parameters={"intent": intent.to_dict()},
                depends_on=["03-rules"],
            ),
        ],
    )
    plan = FederatedQueryPlanner(resolver).plan(request, tenant_id="acme")

    result = FederatedQueryExecutor(executor).execute(plan)
    result_value = result.to_dict()

    assert result.status == "succeeded"
    assert {item["target"] for item in result_value["evidence"]} == {
        "semantic-json",
        "owl",
        "rag",
        "datalog",
        "data-snapshot",
    }
    assert result_value["step_results"]["01-search"]["data"]["count"] >= 1
    assert result_value["step_results"]["02-graph"]["data"]["count"] >= 1
    assert result_value["step_results"]["03-rules"]["data"]["derived_count"] == 1
    sql_data = result_value["step_results"]["04-sql"]["data"]
    assert sql_data["executed"] is True
    assert sql_data["rows"] == [
        {"logdate": "2026-08-17", "post_cnt_30d": 2},
        {"logdate": "2026-08-18", "post_cnt_30d": 1},
    ]
    assert FederatedQueryExecutor(executor).execute(plan).result_digest == result.result_digest

    with pytest.raises(TrustedQueryError, match="tenant"):
        FederatedQueryPlanner(resolver).plan(request, tenant_id="other")

    empty_database = tmp_path / "empty-dim-community.sqlite3"
    sqlite3.connect(empty_database).close()
    failing = QueryExecutor(resolver)
    failing.registry.replace(
        "semantic_sql",
        SqliteSemanticSqlExecutor(
            resolver,
            database=main_database,
            attachments={"dim_community": empty_database},
        ),
    )
    with pytest.raises(
        TrustedQueryError, match="federated query step failed: 04-sql"
    ):
        FederatedQueryExecutor(failing).execute(plan)
