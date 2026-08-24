"""Production chain: governed release to event, action, twin, and audit evidence."""

from __future__ import annotations

import json

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionConnectorRegistry,
    ActionControlPlane,
    BitemporalObjectStore,
    DomainEvent,
    IncrementalActionRuleRuntime,
    KnowledgeRelease,
    PublishedEventSubscriptionResolver,
    ResourceKind,
    SemanticResource,
    SignedPrincipalVerifier,
    SqliteActionRunRepository,
)
from bridge.semantic_core.compilers import (
    CompilationRunService,
    CompilerPolicy,
    SqliteCompilationRunRepository,
    default_compiler_registry,
)
from tests.test_action_compiler import _action_resources
from tests.test_action_runs import SuccessfulCrmConnector


def _headers(subject: str, *roles: str) -> dict[str, str]:
    return SignedPrincipalVerifier(
        key_id="identity-v1", secret=b"production-action-secret"
    ).sign_headers(subject=subject, tenant_id="acme", roles=roles)


def test_release_event_action_twin_chain_is_replayable_and_auditable(tmp_path) -> None:
    resources = _action_resources()
    action = next(item for item in resources if item.kind is ResourceKind.ACTION_TYPE)
    customer = next(item for item in resources if item.kind is ResourceKind.OBJECT_TYPE)
    policy = SemanticResource.create(
        resource_id="aof://acme/crm/policy/action-production",
        kind=ResourceKind.POLICY,
        name="action-production",
        domain="crm",
        owner="risk-governance",
        spec={
            "policy_type": "action",
            "role_actions": {"risk-operator": [action.resource_id]},
            "action_rules": {
                action.resource_id: {
                    "allowed_purposes": ["fraud-response"],
                    "max_impacted_objects": 1,
                }
            },
        },
    )
    rules = SemanticResource.create(
        resource_id="aof://acme/crm/rule-set/risk-actions",
        kind=ResourceKind.RULE_SET,
        name="risk-actions",
        domain="crm",
        owner="risk-governance",
        depends_on=[action.resource_id, policy.resource_id],
        spec={
            "language": "datalog",
            "program": "should_suspend(X) :- risk_signal(X).",
            "event_subscription": {
                "event_types": ["crm.risk_signal_detected"],
                "trigger_predicate": "should_suspend",
                "action_type_id": action.resource_id,
                "purpose": "fraud-response",
                "policy_resource_id": policy.resource_id,
            },
        },
    )
    resources.extend([policy, rules])
    release = KnowledgeRelease.build(
        release_id="crm-controlled-runtime@1.0.0",
        resources=resources,
        scope={"tenant_id": "acme"},
    )
    compiler_policy = CompilerPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/compiler-controlled-runtime",
            kind=ResourceKind.POLICY,
            name="compiler-controlled-runtime",
            domain="platform",
            owner="platform-governance",
            spec={
                "policy_type": "compiler",
                "allowed_compilers": {
                    "semantic-json": ["semantic-json@1"],
                    "actions": ["actions@1"],
                    "datalog": ["datalog@1"],
                    "agent-sdk": ["agent-sdk@1"],
                    "mcp": ["mcp@1"],
                },
            },
        )
    )
    decisions = DecisionProvenanceStore(tmp_path / "decision-provenance.jsonl")
    compiler = SqliteCompilationRunRepository(tmp_path / "compiler" / "acme")
    registry = default_compiler_registry()
    compilation = CompilationRunService(
        compiler, registry=registry, decision_store=decisions
    )
    plan = registry.plan(
        release,
        resources=resources,
        targets=["datalog", "agent-sdk", "mcp"],
    )
    first = compilation.execute(
        run_id="controlled-runtime-001",
        plan=plan,
        policy=compiler_policy,
        release=release,
        resources=resources,
        actor="compiler:ci",
        rationale="Compile the governed controlled-action runtime.",
    )
    replay = compilation.replay(
        first.run_id,
        run_id="controlled-runtime-002",
        policy=compiler_policy,
        release=release,
        resources=resources,
        actor="compiler:replay-ci",
        rationale="Prove deterministic controlled-action artifacts.",
    )
    compilation.promote(
        replay.run_id,
        channel="production",
        actor="publisher:bob",
        approved_by="reviewer:alice",
        rationale="Publish the reproduced controlled-action runtime.",
    )

    subscription = PublishedEventSubscriptionResolver(compiler).resolve(
        channel="production",
        tenant_id="acme",
        ruleset_resource_id=rules.resource_id,
    )
    event = DomainEvent.create(
        event_id="risk-alice-001",
        tenant_id="acme",
        event_type="crm.risk_signal_detected",
        occurred_at="2026-08-24T10:00:00+00:00",
        facts=[{"predicate": "risk_signal", "terms": ["customer:alice"]}],
        source={"type": "crm", "id": "risk-signal-001"},
    )
    event_path = tmp_path / "events.sqlite3"
    event_runtime = IncrementalActionRuleRuntime(event_path, decision_store=decisions)
    trigger = event_runtime.process(subscription, event)["triggers"][0]

    connector = SuccessfulCrmConnector()
    connectors = ActionConnectorRegistry()
    connectors.register("crm-core", connector)
    action_path = tmp_path / "action-runs.sqlite3"
    control = ActionControlPlane(
        compiler,
        verifier=SignedPrincipalVerifier(
            key_id="identity-v1", secret=b"production-action-secret"
        ),
        action_runs=SqliteActionRunRepository(action_path),
        connectors=connectors,
        decision_store=decisions,
    )
    request = {
        "channel": "production",
        "action_type_id": trigger["action_type_id"],
        "object_ids": trigger["object_ids"],
        "inputs": {"reason": f"derived from {trigger['trigger_id']}"},
        "purpose": trigger["purpose"],
        "idempotency_key": trigger["trigger_id"],
        "policy_resource_id": trigger["policy_resource_id"],
    }
    action_plan = control.plan(
        request, headers=_headers("operator", "risk-operator")
    )
    submitted = control.submit(
        {
            **request,
            "expected_plan_digest": action_plan["plan_digest"],
            "rationale": "Submit the exact event-derived plan.",
        },
        headers=_headers("operator", "risk-operator"),
    )
    approved = control.approve(
        {"run_id": submitted["run_id"], "rationale": "Risk evidence reviewed."},
        headers=_headers("reviewer", "risk-reviewer"),
    )
    completed = control.execute(
        {"run_id": approved["run_id"]},
        headers=_headers("worker", "action-executor"),
    )

    twin_path = tmp_path / "objects.sqlite3"
    twin = BitemporalObjectStore(twin_path)
    twin.assert_fact(
        tenant_id="acme",
        fact_id="customer-alice-status",
        object_type_id=customer.resource_id,
        object_id="customer:alice",
        field="status",
        value="suspended",
        valid_from="2026-08-24T10:00:00+00:00",
        valid_to=None,
        recorded_at="2026-08-24T10:01:00+00:00",
        source={"type": "action_run", "id": completed["run_id"]},
        action_run_id=completed["run_id"],
    )
    snapshot = BitemporalObjectStore(twin_path).snapshot(
        tenant_id="acme",
        object_type_id=customer.resource_id,
        object_id="customer:alice",
        valid_at="2026-08-24T10:02:00+00:00",
        known_at="2026-08-24T10:02:00+00:00",
    )

    artifacts = {item["target"]: item for item in replay.artifacts}
    sdk = json.loads(
        (
            compiler.root
            / "artifacts"
            / replay.run_id
            / "agent-sdk"
            / artifacts["agent-sdk"]["uri"]
        ).read_text(encoding="utf-8")
    )
    mcp = json.loads(
        (
            compiler.root
            / "artifacts"
            / replay.run_id
            / "mcp"
            / artifacts["mcp"]["uri"]
        ).read_text(encoding="utf-8")
    )
    restarted_actions = SqliteActionRunRepository(action_path)
    restarted_events = IncrementalActionRuleRuntime(event_path, decision_store=decisions)

    assert trigger["release_digest"] == release.release_digest
    assert action_plan["release_digest"] == trigger["release_digest"]
    assert completed["status"] == "succeeded"
    assert connector.calls == 1
    assert snapshot["values"] == {"status": "suspended"}
    assert snapshot["evidence"][0]["action_run_id"] == completed["run_id"]
    assert restarted_actions.get(completed["run_id"]).run_digest == completed["run_digest"]
    assert restarted_actions.verify_all()["valid"] is True
    assert restarted_events.process(subscription, event)["triggers"] == [trigger]
    assert restarted_events.verify_all()["valid"] is True
    assert sdk["operations"][0]["source_release_digest"] == release.release_digest
    assert any(tool.get("kind") == "action" for tool in mcp["mcp_tools"])
    assert decisions.verify_integrity()["valid"] is True
