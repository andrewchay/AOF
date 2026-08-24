"""Persistent event subscriptions derive idempotent, auditable action triggers."""

from __future__ import annotations

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    DomainEvent,
    EventRuleSubscription,
    IncrementalActionRuleRuntime,
    KnowledgeRelease,
    PublishedEventSubscriptionResolver,
    ResourceKind,
    SemanticResource,
)
from bridge.semantic_core.compilers import (
    CompilationRunRepository,
    CompilationRunService,
    CompilerPolicy,
    default_compiler_registry,
)
from tests.test_action_compiler import _action_resources


def test_incremental_rule_emits_each_derived_action_trigger_once(tmp_path) -> None:
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    path = tmp_path / "events.sqlite3"
    runtime = IncrementalActionRuleRuntime(path, decision_store=decisions)
    subscription = EventRuleSubscription.create(
        subscription_id="crm-risk-signals-v1",
        tenant_id="acme",
        release_id="crm-actions@1.0.0",
        release_digest="sha256:release",
        event_types=["crm.risk_signal_detected"],
        program="should_suspend(X) :- risk_signal(X).",
        trigger_predicate="should_suspend",
        action_type_id="aof://acme/crm/action-type/suspend-customer",
        purpose="fraud-response",
        policy_resource_id="aof://acme/crm/policy/action-production",
    )
    alice = DomainEvent.create(
        event_id="event-alice-001",
        tenant_id="acme",
        event_type="crm.risk_signal_detected",
        occurred_at="2026-08-24T10:00:00+00:00",
        facts=[{"predicate": "risk_signal", "terms": ["customer:alice"]}],
        source={"type": "crm", "id": "risk-001"},
    )
    duplicate_signal = DomainEvent.create(
        event_id="event-alice-002",
        tenant_id="acme",
        event_type="crm.risk_signal_detected",
        occurred_at="2026-08-24T10:01:00+00:00",
        facts=[{"predicate": "risk_signal", "terms": ["customer:alice"]}],
        source={"type": "crm", "id": "risk-002"},
    )

    first = runtime.process(subscription, alice)
    replay = runtime.process(subscription, alice)
    no_new_trigger = runtime.process(subscription, duplicate_signal)
    restarted = IncrementalActionRuleRuntime(path, decision_store=decisions)
    bob = DomainEvent.create(
        event_id="event-bob-001",
        tenant_id="acme",
        event_type="crm.risk_signal_detected",
        occurred_at="2026-08-24T10:02:00+00:00",
        facts=[{"predicate": "risk_signal", "terms": ["customer:bob"]}],
        source={"type": "crm", "id": "risk-003"},
    )
    after_restart = restarted.process(subscription, bob)

    assert first == replay
    assert len(first["triggers"]) == 1
    assert first["triggers"][0]["object_ids"] == ["customer:alice"]
    assert first["triggers"][0]["event_digest"] == alice.event_digest
    assert first["triggers"][0]["subscription_digest"] == subscription.subscription_digest
    assert no_new_trigger["triggers"] == []
    assert after_restart["triggers"][0]["object_ids"] == ["customer:bob"]
    assert restarted.verify_all() == {
        "valid": True,
        "event_count": 3,
        "processed_count": 3,
        "trigger_count": 2,
        "errors": [],
    }
    assert decisions.verify_integrity()["valid"] is True


def test_event_subscription_is_resolved_from_one_reproducible_release(tmp_path) -> None:
    resources = _action_resources()
    action = next(item for item in resources if item.kind is ResourceKind.ACTION_TYPE)
    rules = SemanticResource.create(
        resource_id="aof://acme/crm/rule-set/risk-actions",
        kind=ResourceKind.RULE_SET,
        name="risk-actions",
        domain="crm",
        owner="risk-governance",
        depends_on=[action.resource_id],
        spec={
            "language": "datalog",
            "program": "should_suspend(X) :- risk_signal(X).",
            "event_subscription": {
                "event_types": ["crm.risk_signal_detected"],
                "trigger_predicate": "should_suspend",
                "action_type_id": action.resource_id,
                "purpose": "fraud-response",
                "policy_resource_id": "aof://acme/crm/policy/action-production",
            },
        },
    )
    resources.append(rules)
    release = KnowledgeRelease.build(
        release_id="crm-event-actions@1.0.0",
        resources=resources,
        scope={"tenant_id": "acme"},
    )
    registry = default_compiler_registry()
    repository = CompilationRunRepository(tmp_path / "compiler" / "acme")
    service = CompilationRunService(
        repository,
        registry=registry,
        decision_store=DecisionProvenanceStore(tmp_path / "compile-decisions.jsonl"),
    )
    policy = CompilerPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/compiler-event-actions",
            kind=ResourceKind.POLICY,
            name="compiler-event-actions",
            domain="platform",
            owner="platform-governance",
            spec={
                "policy_type": "compiler",
                "allowed_compilers": {
                    "semantic-json": ["semantic-json@1"],
                    "actions": ["actions@1"],
                    "datalog": ["datalog@1"],
                },
            },
        )
    )
    compile_plan = registry.plan(
        release, resources=resources, targets=["actions", "datalog"]
    )
    first = service.execute(
        run_id="event-actions-001",
        plan=compile_plan,
        policy=policy,
        release=release,
        resources=resources,
        actor="compiler:ci",
        rationale="Compile event action subscription.",
    )
    replay = service.replay(
        first.run_id,
        run_id="event-actions-002",
        policy=policy,
        release=release,
        resources=resources,
        actor="compiler:replay-ci",
        rationale="Reproduce event action subscription.",
    )
    service.promote(
        replay.run_id,
        channel="production",
        actor="publisher:bob",
        approved_by="reviewer:alice",
        rationale="Publish reproduced event action subscription.",
    )

    subscription = PublishedEventSubscriptionResolver(repository).resolve(
        channel="production",
        tenant_id="acme",
        ruleset_resource_id=rules.resource_id,
    )

    assert subscription.release_id == release.release_id
    assert subscription.release_digest == release.release_digest
    assert subscription.action_type_id == action.resource_id
    assert subscription.program == rules.spec["program"]
