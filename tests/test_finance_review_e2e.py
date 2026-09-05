"""One finance domain: SQL, graph, contract evidence, rules, approved work item.

All business data is synthetic. Executors use actual RDFLib, SQLite, Datalog,
and a durable local work-item connector; no enterprise/provider claims.
"""

import json
import sqlite3

import pytest

from fastapi.testclient import TestClient

import services.semantic_middle_layer_api.app as api
from bridge.semantic_core import (
    ActionConnectorRegistry,
    IntentFilter,
    ResourceKind,
    SemanticIntent,
    SemanticResource,
    SignedPrincipalVerifier,
    SqliteAgenticRunRepository,
)
from tests.test_trusted_query_execution import _trusted_query_runtime

QUESTION = "解释2026年9月华东区收入偏差，列出受影响客户、合同条款、回款状态及需人工确认的异常，并创建待审批工单。"
PURPOSE = "finance-review"


def resource(kind, name, **kwargs):
    segment = {
        "PhysicalDataset": "physical-dataset",
        "ObjectType": "object-type",
        "ActionType": "action-type",
        "RuleSet": "rule-set",
        "QueryContract": "query-contract",
    }.get(kind, kind.lower())
    return SemanticResource.create(
        resource_id=f"aof://acme/finance/{segment}/{name}",
        kind=ResourceKind(kind),
        name=name,
        domain="finance",
        owner="finance-governance",
        **kwargs,
    )


def finance_resources():
    dataset = resource(
        "PhysicalDataset", "ledger", spec={"physical_name": "dwd.finance"}
    )
    metrics = [
        resource(
            "Metric",
            name,
            depends_on=[dataset.resource_id],
            spec={"aggregation": "sum", "measure": name},
        )
        for name in ["revenue", "budget", "receipts", "outstanding"]
    ]
    dimensions = [
        resource(
            "Dimension",
            name,
            depends_on=[dataset.resource_id],
            spec={"field": name, "data_type": "string"},
        )
        for name in ["customer", "region", "period"]
    ]
    intent = SemanticIntent.create(
        metrics=[m.resource_id for m in metrics],
        dimensions=[dimensions[0].resource_id],
        filters=[
            IntentFilter.create(
                dimension=dimensions[1].resource_id, operator="eq", value="华东"
            ),
            IntentFilter.create(
                dimension=dimensions[2].resource_id, operator="eq", value="2026-09"
            ),
        ],
        purpose=PURPOSE,
    )
    graph = resource(
        "Ontology",
        "finance",
        spec={
            "format": "turtle",
            "content": """
@prefix ex: <https://finance.example/> .
ex:EastChina ex:customer ex:CustomerA .
ex:CustomerA ex:contract ex:HT001 ; ex:order ex:Order001 .
ex:HT001 ex:clause "验收后30日内付款" .
ex:Order001 ex:receipt ex:Receipt001 .
""",
        },
    )
    contract = resource(
        "Concept",
        "ht001-payment",
        description="合同 HT001 第3.1条：验收后30日内付款。",
        evidence=[
            {
                "evidence_id": "synthetic:HT001:3.1",
                "source_id": "synthetic:HT001",
                "locator": "clause:3.1",
                "quote": "验收后30日内付款",
            }
        ],
    )
    rules = resource(
        "RuleSet",
        "receivables",
        spec={"language": "datalog", "program": "needs_review(X) :- unpaid(X)."},
    )
    obj = resource("ObjectType", "customer")
    function = resource(
        "Function",
        "review-ticket",
        description="创建收入回款异常复核工单",
        spec={
            "connector": "finance-work-items",
            "operation": "create_ticket",
            "side_effects": True,
        },
    )
    analysis = resource(
        "Function",
        "finance-variance",
        description="收入预算偏差与回款分析",
        spec={
            "connector": "local-analysis",
            "operation": "finance_variance",
            "side_effects": False,
        },
    )
    compensate = resource(
        "Function",
        "cancel-ticket",
        spec={
            "connector": "finance-work-items",
            "operation": "cancel_ticket",
            "side_effects": True,
        },
    )
    action = resource(
        "ActionType",
        "review-ticket",
        depends_on=[obj.resource_id, function.resource_id, compensate.resource_id],
        spec={
            "function_id": function.resource_id,
            "compensation_function_id": compensate.resource_id,
            "target_object_type_id": obj.resource_id,
            "effect_class": "reversible",
            "idempotency_scope": "request",
            "required_approval_roles": ["finance-reviewer"],
            "input_schema": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    )
    policy = resource(
        "Policy",
        "actions",
        spec={
            "policy_type": "action",
            "role_actions": {"analyst": [action.resource_id]},
            "action_rules": {
                action.resource_id: {
                    "allowed_purposes": [PURPOSE],
                    "max_impacted_objects": 5,
                }
            },
        },
    )

    def step(name, capability, query=None, **kwargs):
        return {
            "step_id": name,
            "kind": "capability",
            "capability": capability,
            "depends_on": [],
            **({"query": query} if query else {}),
            **kwargs,
        }

    steps = [
        step(
            "ledger",
            "semantic_sql",
            intent.intent_digest,
            parameters={"intent": intent.to_dict()},
        ),
        step("relationships", "graph_search", "https://finance.example/CustomerA"),
        step("contract", "vector_search", "合同 HT001"),
        step("skill", "skill_search", "finance-variance"),
        step(
            "analysis",
            "skill_execute",
            depends_on=["ledger", "skill"],
            data_from="ledger",
            catalog_from="skill",
            function_id=analysis.resource_id,
        ),
        step(
            "rules",
            "rule_search",
            "needs_review",
            depends_on=["ledger"],
            facts_from={
                "step_id": "ledger",
                "bindings": [
                    {
                        "predicate": "unpaid",
                        "fields": ["customer"],
                        "when_positive": "outstanding",
                    }
                ],
            },
        ),
        step(
            "ticket",
            "action_submit",
            depends_on=["rules", "contract", "skill"],
            objects_from="rules",
            object_predicate="needs_review",
            action_type_id=action.resource_id,
            policy_resource_id=policy.resource_id,
            inputs={"reason": "请人工核对合同条款及回款异常；系统未判定违约。"},
        ),
    ]
    recipe = resource(
        "QueryContract",
        "east-china-review",
        depends_on=[m.resource_id for m in metrics]
        + [d.resource_id for d in dimensions]
        + [
            graph.resource_id,
            contract.resource_id,
            rules.resource_id,
            action.resource_id,
        ],
        spec={
            "accepted_queries": [QUESTION],
            "purpose": PURPOSE,
            "agentic_steps": steps,
        },
    )
    return [
        dataset,
        *metrics,
        *dimensions,
        graph,
        contract,
        rules,
        obj,
        function,
        analysis,
        compensate,
        action,
        policy,
        recipe,
    ]


class WorkItems:
    def __init__(self, path, uncertain=False):
        self.path = path
        self.uncertain = uncertain
        with sqlite3.connect(path) as connection:
            connection.execute(
                "CREATE TABLE tickets(id TEXT PRIMARY KEY, payload TEXT, status TEXT)"
            )

    def invoke(self, request):
        if self.uncertain:
            return {
                "outcome": "unknown",
                "effect_applied": None,
                "receipt": {"reason": "connection lost before acknowledgement"},
            }
        from bridge.semantic_core.canonical import content_digest

        key = content_digest(request)
        with sqlite3.connect(self.path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO tickets VALUES (?, ?, ?)",
                (key, json.dumps(request), "open"),
            )
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"ticket_id": key},
        }

    def compensate(self, request):
        return {
            "outcome": "unknown",
            "effect_applied": None,
            "receipt": {"reason": "manual cancellation required"},
        }


@pytest.mark.parametrize("uncertain", [False, True])
def test_finance_question_to_evidence_approval_and_durable_work_item(
    tmp_path, monkeypatch, uncertain
):
    resolver, _ = _trusted_query_runtime(
        tmp_path, extra_resources=finance_resources(), extra_targets=["actions"]
    )
    published = resolver.repository.get(
        resolver.repository.get_channel("production")["run_id"]
    )
    warehouse, data = tmp_path / "main.sqlite3", tmp_path / "dwd.sqlite3"
    sqlite3.connect(warehouse).close()
    with sqlite3.connect(data) as connection:
        connection.execute(
            "CREATE TABLE finance(customer TEXT, region TEXT, period TEXT, revenue REAL, budget REAL, receipts REAL, outstanding REAL)"
        )
        connection.executemany(
            "INSERT INTO finance VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                ("CustomerA", "华东", "2026-09", 80, 100, 50, 30),
                ("ExcludedWest", "华西", "2026-09", 999, 999, 0, 999),
                ("ExcludedOld", "华东", "2026-08", 777, 777, 0, 777),
            ],
        )
    for key, value in {
        "AOF_SEMANTIC_IDENTITY_SECRET": "finance-test-key",
        "AOF_COMPILER_STATE_DIR": str(tmp_path / "compiler"),
        "AOF_AGENTIC_RUN_DATABASE": str(tmp_path / "agentic.sqlite3"),
        "AOF_QUERY_SQLITE_DATABASE": str(warehouse),
        "AOF_QUERY_SQLITE_ATTACHMENTS": json.dumps({"dwd": str(data)}),
    }.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(api, "AOF_ROOT", tmp_path)
    monkeypatch.delattr(api.app.state, "agentic_capability_executor", raising=False)
    connectors = ActionConnectorRegistry()
    connectors.register(
        "finance-work-items", WorkItems(tmp_path / "tickets.sqlite3", uncertain)
    )
    monkeypatch.setattr(
        api.app.state, "semantic_action_connectors", connectors, raising=False
    )
    verifier = SignedPrincipalVerifier(
        key_id="identity-key-default", secret=b"finance-test-key"
    )

    def signed(subject, roles, tenant="acme"):
        return verifier.sign_headers(subject=subject, tenant_id=tenant, roles=roles)

    analyst = signed("analyst", ["analyst"])
    client = TestClient(api.app)
    response = client.post(
        "/v1/agentic/runs",
        headers=analyst,
        json={
            "run_id": "finance-001",
            "session_id": "finance-review",
            "query": QUESTION,
            "purpose": PURPOSE,
            "channel": "production",
            "release_id": published.release_id,
            "release_digest": published.release_digest,
            "policy_resource_id": "aof://acme/platform/policy/query-trusted",
            "query_contract_id": "aof://acme/finance/query-contract/east-china-review",
            "allowed_capabilities": [
                "semantic_sql",
                "graph_search",
                "vector_search",
                "skill_search",
                "skill_execute",
                "rule_search",
                "action_submit",
            ],
            "max_steps": 7,
        },
    )
    assert response.status_code == 201, response.text
    run = response.json()
    assert run["status"] == "awaiting_approval"
    results = {item["capability"]: item for item in run["results"]}
    row = results["semantic_sql"]["output"]["data"]["rows"][0]
    assert row == {
        "customer": "CustomerA",
        "budget": 100.0,
        "revenue": 80.0,
        "receipts": 50.0,
        "outstanding": 30.0,
    }
    assert (
        results["vector_search"]["output"]["data"]["hits"][0]["evidence"][0]["locator"]
        == "clause:3.1"
    )
    assert results["rule_search"]["output"]["data"]["derived_facts"][0]["terms"] == [
        "CustomerA"
    ]
    assert "偏差 -20" in results["skill_execute"]["output"]["summary"]
    assert all(
        item["evidence"][0]["release_digest"] == published.release_digest
        for item in run["results"]
    )
    action_id = results["action_submit"]["output"]["action_run_id"]
    with sqlite3.connect(tmp_path / "tickets.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == 0
    other = client.post(
        f"/v1/semantic/action-runs/{action_id}/approve",
        json={"rationale": "foreign tenant"},
        headers=signed("other-reviewer", ["finance-reviewer"], "other"),
    )
    assert other.status_code == 409, other.text
    assert "tenant" in other.text
    approved = client.post(
        f"/v1/semantic/action-runs/{action_id}/approve",
        json={"rationale": "reviewed source evidence"},
        headers=signed("reviewer", ["finance-reviewer"]),
    )
    assert approved.status_code == 200, approved.text
    executed = client.post(
        f"/v1/semantic/action-runs/{action_id}/execute",
        headers=signed("worker", ["action-executor"]),
    )
    assert executed.status_code == 200, executed.text
    expected = "reconciliation_required" if uncertain else "succeeded"
    assert executed.json()["status"] == expected
    refreshed = client.post(
        "/v1/agentic/runs/finance-001/refresh-actions", headers=analyst
    )
    assert refreshed.status_code == 200, refreshed.text
    assert refreshed.json()["status"] == expected
    with sqlite3.connect(tmp_path / "tickets.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM tickets").fetchone()[0] == (
            0 if uncertain else 1
        )
    repo = SqliteAgenticRunRepository(tmp_path / "agentic.sqlite3")
    repo.backup_to(tmp_path / "backup.sqlite3")
    assert (
        SqliteAgenticRunRepository(tmp_path / "backup.sqlite3").get(
            "finance-001", tenant_id="acme"
        )["status"]
        == expected
    )
    assert api._decision_store().verify_integrity()["valid"] is True


def test_analysis_replay_evidence_ignores_transport_run_ids():
    from bridge.semantic_core.business_plan import execute_finance_analysis
    data = {'executed': True, 'rows': [{'customer': 'A', 'revenue': 80, 'budget': 100, 'receipts': 50, 'outstanding': 30}]}
    def context(result_digest):
        return {'release_digest': 'release', 'step': {'data_from': 'sql', 'catalog_from': 'catalog',
            'depends_on': ['sql', 'catalog'], 'function_id': 'function', 'function_revision': 'revision'},
            'previous_results': {'sql': {'output': {'data': data}, 'result_digest': result_digest, 'receipt': {}},
                'catalog': {'output': {'data': {'hits': [{'resource_id': 'function', 'revision_id': 'revision'}]}}}}}
    assert execute_finance_analysis(context('first')) == execute_finance_analysis(context('replay'))
