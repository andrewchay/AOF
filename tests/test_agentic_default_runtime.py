"""Default REST path: signed policy, actual artifacts, RDF and read-only SQL."""

import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

import services.semantic_middle_layer_api.app as api
from bridge.semantic_core import QueryRequest, SemanticResource, ResourceKind
from tests.test_agentic_api import headers
from tests.test_trusted_query_execution import _trusted_query_runtime


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    skill = SemanticResource.create(
        resource_id="aof://acme/sales/function/customer-review",
        kind=ResourceKind.FUNCTION,
        name="customer-review",
        domain="sales",
        owner="operations",
        description="Review customer records and escalate discrepancies.",
    )
    resolver, _ = _trusted_query_runtime(tmp_path, extra_resources=[skill])
    snapshot = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="graph",
            query="https://example.test/Customer",
            purpose="weekly-review",
        ),
        tenant_id="acme",
    )
    warehouse = tmp_path / "warehouse.sqlite3"
    sqlite3.connect(warehouse).close()
    data = tmp_path / "dwd.sqlite3"
    with sqlite3.connect(data) as connection:
        connection.execute(
            "CREATE TABLE order_detail(paid_amount REAL, order_date TEXT)"
        )
        connection.executemany(
            "INSERT INTO order_detail VALUES (?, ?)",
            [(10, "2026-09-01"), (7, "2026-09-02")],
        )
    for key, value in {
        "AOF_SEMANTIC_IDENTITY_SECRET": "agentic-api-secret",
        "AOF_COMPILER_STATE_DIR": str(tmp_path / "compiler"),
        "AOF_AGENTIC_RUN_DATABASE": str(tmp_path / "agentic.sqlite3"),
        "AOF_QUERY_SQLITE_DATABASE": str(warehouse),
        "AOF_QUERY_SQLITE_ATTACHMENTS": json.dumps({"dwd": str(data)}),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(api, "AOF_ROOT", tmp_path)
    monkeypatch.delattr(api.app.state, "agentic_capability_executor", raising=False)
    body = dict(
        run_id="actual-1",
        session_id="review",
        query="gmv 指标多少，Customer 有哪些关系？",
        purpose="weekly-review",
        channel="production",
        release_id=snapshot.release_id,
        release_digest=snapshot.release_digest,
        policy_resource_id="aof://acme/platform/policy/query-trusted",
        allowed_capabilities=[
            "semantic_sql",
            "graph_search",
            "vector_search",
            "skill_search",
            "rag_retrieve",
        ],
    )
    return TestClient(api.app), body


def test_default_api_executes_sql_graph_and_replays_without_memory_mutation(runtime):
    client, body = runtime
    response = client.post("/v1/agentic/runs", json=body, headers=headers("analyst"))
    assert response.status_code == 201, response.text
    run = response.json()
    sql, graph = run["results"]
    assert sql["output"]["data"]["executed"] is True
    assert list(sql["output"]["data"]["rows"][0].values()) == [17.0]
    assert graph["output"]["data"]["count"] == 1
    assert graph["output"]["data"]["node"] == "https://example.test/Customer"
    assert all(result["receipt"]["decision_id"] for result in run["results"])
    replay = client.post(
        "/v1/agentic/runs/actual-1/replay",
        json={"run_id": "actual-2"},
        headers=headers("analyst"),
    )
    assert replay.status_code == 201, replay.text
    assert replay.json()["reproducible"] is True
    persisted = client.get(
        "/v1/agentic/runs/actual-2", headers=headers("analyst")
    ).json()
    assert persisted["reproducible"] is True
    assert (
        client.get(
            "/v1/agentic/sessions/review/memory", headers=headers("analyst")
        ).json()["count"]
        == 2
    )


@pytest.mark.parametrize(
    "query,capability",
    [
        ("Customer 相似资料", "vector_search"),
        ("如何使用 customer-review", "skill_search"),
        ("Customer 资料", "rag_retrieve"),
    ],
)
def test_default_retrieval_returns_published_resources(runtime, query, capability):
    client, body = runtime
    response = client.post(
        "/v1/agentic/runs", json=body | {"query": query}, headers=headers("analyst")
    )
    assert response.status_code == 201, response.text
    result = response.json()["results"][0]
    assert result["capability"] == capability
    assert result["output"]["data"]["count"] > 0
    if capability == "skill_search":
        assert result["output"]["data"]["hits"][0]["kind"] == "Function"


def test_wrong_release_fails_before_business_execution_and_persists_failure(
    runtime, monkeypatch
):
    client, body = runtime

    def forbidden(*args, **kwargs):
        pytest.fail("query executor must not run against a mismatched release")

    monkeypatch.setattr(
        "bridge.semantic_core.query_execution.QueryExecutor.execute", forbidden
    )
    response = client.post(
        "/v1/agentic/runs",
        json=body | {"release_digest": "wrong"},
        headers=headers("analyst"),
    )
    assert response.status_code == 422
    assert (
        client.get("/v1/agentic/runs/actual-1", headers=headers("analyst")).json()[
            "status"
        ]
        == "failed"
    )


def test_repeated_id_never_reexecutes_tools(runtime, monkeypatch):
    client, body = runtime
    assert (
        client.post(
            "/v1/agentic/runs", json=body, headers=headers("analyst")
        ).status_code
        == 201
    )

    def forbidden(*args, **kwargs):
        pytest.fail("duplicate run executed tools")

    monkeypatch.setattr(
        "bridge.semantic_core.query_execution.QueryExecutor.execute", forbidden
    )
    assert (
        client.post(
            "/v1/agentic/runs", json=body, headers=headers("analyst")
        ).status_code
        == 409
    )
