# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Counterfactual simulations compare deterministic bitemporal snapshots."""

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionRequest,
    BitemporalObjectStore,
    GovernedActionPlanner,
    SimulationRequest,
    SqliteBitemporalSimulationService,
)
from tests.test_action_planning import _action_runtime


def test_counterfactual_impact_is_blocked_without_invoking_connectors(tmp_path) -> None:
    compiled, action, policy = _action_runtime(tmp_path)
    action_plan = GovernedActionPlanner(compiled).plan(
        ActionRequest.create(
            channel="production",
            action_type_id=action.resource_id,
            object_ids=["customer:alice"],
            inputs={"reason": "risk review"},
            purpose="fraud-response",
            idempotency_key="simulate-alice-001",
            policy_resource_id=policy.resource_id,
        ),
        tenant_id="acme",
        roles=["risk-operator"],
    )
    objects = BitemporalObjectStore(tmp_path / "objects.sqlite3")
    objects.assert_fact(
        tenant_id="acme",
        fact_id="alice-risk-level",
        object_type_id=action_plan.target_object_type_id,
        object_id="customer:alice",
        field="risk_level",
        value="low",
        valid_from="2026-08-24T00:00:00+00:00",
        valid_to=None,
        recorded_at="2026-08-24T00:01:00+00:00",
        source={"type": "crm", "id": "alice"},
    )
    request = SimulationRequest.create(
        simulation_id="simulation-alice-high-risk",
        action_plan=action_plan,
        valid_at="2026-08-24T12:00:00+00:00",
        known_at="2026-08-24T12:00:00+00:00",
        ruleset_id="risk-simulation@1",
        program='requires_review(X) :- risk_level(X, "high").',
        field_predicates={"risk_level": "risk_level"},
        assumptions={
            "assertions": [("risk_level", ["customer:alice", "high"])],
            "retractions": [("risk_level", ["customer:alice", "low"])],
        },
        outcome_predicates=["requires_review"],
        blocking_predicates=["requires_review"],
    )
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    service = SqliteBitemporalSimulationService(
        tmp_path / "simulations.sqlite3",
        objects=objects,
        decision_store=decisions,
    )

    result = service.simulate(request, actor="simulator:risk-engine")
    duplicate = service.simulate(request, actor="simulator:risk-engine")
    replay = service.replay(result.run_id, tenant_id="acme")

    assert result.state == "blocked"
    assert result.baseline_outcomes == ()
    assert result.candidate_outcomes[0]["predicate"] == "requires_review"
    assert result.impact == {
        "added": ["requires_review(customer:alice)"],
        "removed": [],
        "affected_object_ids": ["customer:alice"],
    }
    assert duplicate.run_id == result.run_id
    assert replay["reproduced"] is True
    assert service.verify_all()["valid"] is True
    assert decisions.verify_integrity()["valid"] is True


def test_simulation_respects_knowledge_time_before_and_after_correction(
    tmp_path,
) -> None:
    compiled, action, policy = _action_runtime(tmp_path)
    plan = GovernedActionPlanner(compiled).plan(
        ActionRequest.create(
            channel="production",
            action_type_id=action.resource_id,
            object_ids=["customer:alice"],
            inputs={"reason": "point-in-time"},
            purpose="fraud-response",
            idempotency_key="simulate-known-at",
            policy_resource_id=policy.resource_id,
        ),
        tenant_id="acme",
        roles=["risk-operator"],
    )
    objects = BitemporalObjectStore(tmp_path / "objects.sqlite3")
    base = {
        "tenant_id": "acme",
        "fact_id": "alice-risk-level",
        "object_type_id": plan.target_object_type_id,
        "object_id": "customer:alice",
        "field": "risk_level",
        "valid_from": "2026-08-24T00:00:00+00:00",
        "valid_to": None,
        "source": {"type": "crm", "id": "alice"},
    }
    objects.assert_fact(
        **base, value="low", recorded_at="2026-08-24T00:01:00+00:00"
    )
    objects.assert_fact(
        **base, value="high", recorded_at="2026-08-24T13:00:00+00:00"
    )
    service = SqliteBitemporalSimulationService(
        tmp_path / "simulations.sqlite3",
        objects=objects,
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )

    def request(simulation_id, known_at):
        return SimulationRequest.create(
            simulation_id=simulation_id,
            action_plan=plan,
            valid_at="2026-08-24T12:00:00+00:00",
            known_at=known_at,
            ruleset_id="risk-simulation@1",
            program='requires_review(X) :- risk_level(X, "high").',
            field_predicates={"risk_level": "risk_level"},
            assumptions={},
            outcome_predicates=["requires_review"],
            blocking_predicates=["requires_review"],
        )

    before = service.simulate(
        request("before-correction", "2026-08-24T12:00:00+00:00"),
        actor="simulator:risk-engine",
    )
    after = service.simulate(
        request("after-correction", "2026-08-24T14:00:00+00:00"),
        actor="simulator:risk-engine",
    )

    assert before.state == "review" and before.candidate_outcomes == ()
    assert after.state == "blocked"
    assert after.candidate_outcomes[0]["terms"] == ["customer:alice"]
