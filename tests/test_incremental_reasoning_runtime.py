"""Persistent reasoning changes invalidate derived facts deterministically."""

import sqlite3

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    IncrementalReasoningError,
    ReasoningFactChange,
    SqliteIncrementalReasoningRuntime,
)


def test_assert_retract_invalidates_proof_and_survives_restart(tmp_path) -> None:
    path = tmp_path / "reasoning.sqlite3"
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    runtime = SqliteIncrementalReasoningRuntime(path, decision_store=decisions)
    program = """
    at_risk(X) :- late_payment(X).
    requires_review(X) :- at_risk(X).
    """
    asserted = ReasoningFactChange.create(
        change_id="risk-change-001",
        tenant_id="acme",
        assertions=[("late_payment", ["customer:alice"])],
        source={"type": "domain_event", "id": "payment-001"},
    )

    first = runtime.apply(
        ruleset_id="crm-risk@1",
        program=program,
        change=asserted,
        actor="engine:reasoning",
    )
    replayed_change = runtime.apply(
        ruleset_id="crm-risk@1",
        program=program,
        change=asserted,
        actor="engine:reasoning",
    )
    restarted = SqliteIncrementalReasoningRuntime(path, decision_store=decisions)
    before = restarted.query(
        tenant_id="acme", ruleset_id="crm-risk@1", predicate="requires_review"
    )
    proof = restarted.explain(
        tenant_id="acme",
        ruleset_id="crm-risk@1",
        fact_id=before[0]["fact_id"],
    )
    retracted = restarted.apply(
        ruleset_id="crm-risk@1",
        program=program,
        change=ReasoningFactChange.create(
            change_id="risk-change-002",
            tenant_id="acme",
            retractions=[("late_payment", ["customer:alice"])],
            source={"type": "correction", "id": "payment-001-retracted"},
        ),
        actor="engine:reasoning",
    )

    assert first.status == "succeeded"
    assert replayed_change.run_id == first.run_id
    assert [item["terms"] for item in before] == [["customer:alice"]]
    assert proof["proof"]["rule_id"].endswith("rule-3")
    assert retracted.delta == {
        "asserted_added": [],
        "asserted_removed": ["late_payment(customer:alice)"],
        "derived_added": [],
        "derived_removed": [
            "at_risk(customer:alice)",
            "requires_review(customer:alice)",
        ],
    }
    assert restarted.query(
        tenant_id="acme", ruleset_id="crm-risk@1", predicate="requires_review"
    ) == []
    assert restarted.replay(first.run_id, tenant_id="acme")["reproduced"] is True
    assert restarted.verify_all()["valid"] is True
    assert decisions.verify_integrity()["valid"] is True


def test_ruleset_state_is_tenant_isolated_version_pinned_and_tamper_evident(
    tmp_path,
) -> None:
    path = tmp_path / "reasoning.sqlite3"
    runtime = SqliteIncrementalReasoningRuntime(
        path,
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    program = "eligible(X) :- verified(X)."
    runtime.apply(
        ruleset_id="eligibility@1",
        program=program,
        change=ReasoningFactChange.create(
            change_id="verify-alice",
            tenant_id="acme",
            assertions=[("verified", ["alice"])],
            source={"type": "fixture", "id": "verify-alice"},
        ),
        actor="engine:reasoning",
    )

    with pytest.raises(IncrementalReasoningError, match="state not found"):
        runtime.query(tenant_id="other", ruleset_id="eligibility@1")
    with pytest.raises(IncrementalReasoningError, match="cannot change its program"):
        runtime.apply(
            ruleset_id="eligibility@1",
            program="blocked(X) :- verified(X).",
            change=ReasoningFactChange.create(
                change_id="swap-program",
                tenant_id="acme",
                assertions=[("verified", ["bob"])],
                source={"type": "fixture", "id": "swap-program"},
            ),
            actor="engine:reasoning",
        )

    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE reasoning_state SET state_digest='sha256:tampered' "
            "WHERE tenant_id='acme' AND ruleset_id='eligibility@1'"
        )
    verification = runtime.verify_all()

    assert verification["valid"] is False
    assert verification["errors"] == ["state/acme/eligibility@1"]
    with pytest.raises(IncrementalReasoningError, match="state digest mismatch"):
        runtime.query(tenant_id="acme", ruleset_id="eligibility@1")


def test_out_of_order_changes_use_effective_time_not_arrival_order(tmp_path) -> None:
    runtime = SqliteIncrementalReasoningRuntime(
        tmp_path / "reasoning.sqlite3",
        decision_store=DecisionProvenanceStore(tmp_path / "decisions.jsonl"),
    )
    program = "alert(X) :- signal(X)."
    runtime.apply(
        ruleset_id="signals@1",
        program=program,
        change=ReasoningFactChange.create(
            change_id="retract-newer",
            tenant_id="acme",
            effective_at="2026-08-24T11:00:00+00:00",
            retractions=[("signal", ["alice"])],
            source={"type": "event", "id": "retract-newer"},
        ),
        actor="engine:reasoning",
    )
    late_arrival = runtime.apply(
        ruleset_id="signals@1",
        program=program,
        change=ReasoningFactChange.create(
            change_id="assert-older",
            tenant_id="acme",
            effective_at="2026-08-24T10:00:00+00:00",
            assertions=[("signal", ["alice"])],
            source={"type": "event", "id": "assert-older"},
        ),
        actor="engine:reasoning",
    )

    assert late_arrival.delta["asserted_added"] == []
    assert runtime.query(
        tenant_id="acme", ruleset_id="signals@1", predicate="alert"
    ) == []
