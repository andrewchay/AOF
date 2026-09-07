# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Event, reasoning, simulation, workflow, and action form one audit chain."""

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionConnectorRegistry,
    ActionRequest,
    ActionRunService,
    BitemporalObjectStore,
    DomainEvent,
    EnterpriseRuntimeControlPlane,
    EventRuleSubscription,
    GovernedActionPlanner,
    IncrementalActionRuleRuntime,
    SignedPrincipalVerifier,
    SimulationRequest,
    SqliteActionRunRepository,
    SqliteBitemporalSimulationService,
    SqliteIncrementalReasoningRuntime,
    SqliteWorkflowRunRepository,
    WorkflowPlan,
    WorkflowRunService,
)
from tests.test_action_planning import _action_runtime


class EnterpriseConnector:
    def __init__(self) -> None:
        self.calls = 0

    def invoke(self, request):
        self.calls += 1
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"request_id": "crm-enterprise-001"},
        }

    def compensate(self, request):
        return {
            "outcome": "succeeded",
            "effect_applied": True,
            "receipt": {"request_id": "crm-enterprise-compensation"},
        }


def _headers(verifier, subject, roles):
    return verifier.sign_headers(subject=subject, tenant_id="acme", roles=roles)


def test_event_to_reasoning_simulation_workflow_and_retraction_survives_restart(
    tmp_path,
) -> None:
    compiled, action, policy = _action_runtime(tmp_path)
    decisions = DecisionProvenanceStore(tmp_path / "runtime-decisions.jsonl")
    event_path = tmp_path / "events.sqlite3"
    event_runtime = IncrementalActionRuleRuntime(
        event_path, decision_store=decisions
    )
    published_run = compiled.get(compiled.get_channel("production")["run_id"])
    subscription = EventRuleSubscription.create(
        subscription_id="risk-actions@1",
        tenant_id="acme",
        release_id="crm-actions@1.0.0",
        release_digest=published_run.release_digest,
        event_types=["crm.risk_signal"],
        program="should_suspend(X) :- risk_signal(X).",
        trigger_predicate="should_suspend",
        action_type_id=action.resource_id,
        purpose="fraud-response",
        policy_resource_id=policy.resource_id,
    )
    event = DomainEvent.create(
        event_id="risk-alice-001",
        tenant_id="acme",
        event_type="crm.risk_signal",
        occurred_at="2026-08-24T10:00:00+00:00",
        facts=[{"predicate": "risk_signal", "terms": ["customer:alice"]}],
        source={"type": "crm", "id": "risk-alice-001"},
    )
    trigger_result = event_runtime.process(subscription, event)
    trigger = trigger_result["triggers"][0]

    action_plan = GovernedActionPlanner(compiled).plan(
        ActionRequest.create(
            channel="production",
            action_type_id=trigger["action_type_id"],
            object_ids=trigger["object_ids"],
            inputs={"reason": "release-pinned risk signal"},
            purpose=trigger["purpose"],
            idempotency_key=trigger["trigger_id"],
            policy_resource_id=trigger["policy_resource_id"],
        ),
        tenant_id="acme",
        roles=["risk-operator"],
    )
    object_path = tmp_path / "objects.sqlite3"
    objects = BitemporalObjectStore(object_path)
    objects.assert_fact(
        tenant_id="acme",
        fact_id="alice-status",
        object_type_id=action_plan.target_object_type_id,
        object_id="customer:alice",
        field="status",
        value="active",
        valid_from="2026-08-24T00:00:00+00:00",
        valid_to=None,
        recorded_at="2026-08-24T00:01:00+00:00",
        source={"type": "crm", "id": "customer:alice"},
    )
    simulation_request = SimulationRequest.create(
        simulation_id="risk-alice-impact-001",
        action_plan=action_plan,
        valid_at="2026-08-24T10:00:00+00:00",
        known_at="2026-08-24T10:00:00+00:00",
        ruleset_id="action-impact@1",
        program='eligible_for_action(X) :- status(X, "active").',
        field_predicates={"status": "status"},
        assumptions={},
        outcome_predicates=["eligible_for_action"],
    )
    workflow_plan = WorkflowPlan.create(
        workflow_id="aof://acme/crm/workflow/customer-risk-response",
        workflow_revision="sha256:workflow-runtime-v1",
        idempotency_key=trigger["trigger_id"],
        nodes={"suspend": action_plan},
        dependencies={"suspend": []},
    )

    connector = EnterpriseConnector()
    connectors = ActionConnectorRegistry()
    connectors.register("crm-core", connector)
    action_path = tmp_path / "actions.sqlite3"
    workflow_path = tmp_path / "workflows.sqlite3"
    reasoning_path = tmp_path / "reasoning.sqlite3"
    simulation_path = tmp_path / "simulations.sqlite3"
    actions = ActionRunService(
        SqliteActionRunRepository(action_path),
        connectors=connectors,
        decision_store=decisions,
    )
    workflows = WorkflowRunService(
        SqliteWorkflowRunRepository(workflow_path),
        actions=actions,
        decision_store=decisions,
    )
    simulations = SqliteBitemporalSimulationService(
        simulation_path, objects=objects, decision_store=decisions
    )
    reasoning = SqliteIncrementalReasoningRuntime(
        reasoning_path, decision_store=decisions
    )
    verifier = SignedPrincipalVerifier(
        key_id="runtime-identity", secret=b"runtime-secret"
    )
    control = EnterpriseRuntimeControlPlane(
        verifier=verifier,
        reasoning=reasoning,
        workflows=workflows,
        simulations=simulations,
    )

    inferred = control.apply_reasoning(
        {
            "ruleset_id": "risk-actions@1",
            "program": subscription.program,
            "change": {
                "change_id": event.event_id,
                "effective_at": event.occurred_at,
                "assertions": list(event.facts),
                "retractions": [],
                "source": {"type": "domain_event", "id": event.event_id},
            },
        },
        headers=_headers(verifier, "reasoner", ["reasoner"]),
    )
    simulated = control.simulate(
        {"request": simulation_request.to_dict()},
        headers=_headers(verifier, "analyst", ["analyst"]),
    )
    started = control.start_workflow(
        {"plan": workflow_plan.to_dict(), "rationale": "Execute reviewed trigger."},
        headers=_headers(verifier, "operator", ["operator"]),
    )
    approved = control.approve_workflow_node(
        started["run_id"],
        "suspend",
        {"rationale": "Impact simulation reviewed."},
        headers=_headers(verifier, "reviewer", ["reviewer", "risk-reviewer"]),
    )
    completed = control.advance_workflow(
        approved["run_id"],
        headers=_headers(verifier, "worker", ["worker"]),
    )
    retracted = control.apply_reasoning(
        {
            "ruleset_id": "risk-actions@1",
            "program": subscription.program,
            "change": {
                "change_id": "risk-alice-retracted",
                "effective_at": "2026-08-24T11:00:00+00:00",
                "assertions": [],
                "retractions": list(event.facts),
                "source": {"type": "domain_event", "id": "risk-alice-retracted"},
            },
        },
        headers=_headers(verifier, "reasoner", ["reasoner"]),
    )

    assert event_runtime.process(subscription, event) == trigger_result
    assert inferred["delta"]["derived_added"] == [
        "should_suspend(customer:alice)"
    ]
    assert simulated["state"] == "review"
    assert completed["status"] == "succeeded" and connector.calls == 1
    assert retracted["delta"]["derived_removed"] == [
        "should_suspend(customer:alice)"
    ]
    assert SqliteIncrementalReasoningRuntime(
        reasoning_path, decision_store=decisions
    ).verify_all()["valid"] is True
    assert SqliteWorkflowRunRepository(workflow_path).verify_all()["valid"] is True
    assert SqliteActionRunRepository(action_path).verify_all()["valid"] is True
    assert SqliteBitemporalSimulationService(
        simulation_path,
        objects=BitemporalObjectStore(object_path),
        decision_store=decisions,
    ).verify_all()["valid"] is True
    assert IncrementalActionRuleRuntime(
        event_path, decision_store=decisions
    ).verify_all()["valid"] is True
    assert decisions.verify_integrity()["valid"] is True

    backup_root = tmp_path / "backup"
    reasoning_backup = SqliteIncrementalReasoningRuntime(
        reasoning_path, decision_store=decisions
    ).backup_to(backup_root / "reasoning.sqlite3")
    workflow_backup = SqliteWorkflowRunRepository(workflow_path).backup_to(
        backup_root / "workflows.sqlite3"
    )
    simulation_backup = SqliteBitemporalSimulationService(
        simulation_path,
        objects=BitemporalObjectStore(object_path),
        decision_store=decisions,
    ).backup_to(backup_root / "simulations.sqlite3")
    assert SqliteIncrementalReasoningRuntime(
        reasoning_backup, decision_store=decisions
    ).verify_all()["valid"] is True
    assert SqliteWorkflowRunRepository(workflow_backup).verify_all()["valid"] is True
    assert SqliteBitemporalSimulationService(
        simulation_backup,
        objects=BitemporalObjectStore(object_path),
        decision_store=decisions,
    ).verify_all()["valid"] is True
