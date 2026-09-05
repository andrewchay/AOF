"""Release-pinned action planning, impact, and permission gates."""

from __future__ import annotations

import pytest

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import (
    ActionPolicyError,
    ActionPlanningError,
    ActionRequest,
    GovernedActionPlanner,
    KnowledgeRelease,
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


def _action_runtime(tmp_path):
    resources = _action_resources()
    action = next(item for item in resources if item.kind is ResourceKind.ACTION_TYPE)
    action_id = action.resource_id
    policy = SemanticResource.create(
        resource_id="aof://acme/crm/policy/action-production",
        kind=ResourceKind.POLICY,
        name="action-production",
        domain="crm",
        owner="risk-governance",
        spec={
            "policy_type": "action",
            "role_actions": {"risk-operator": [action_id]},
            "action_rules": {
                action_id: {
                    "allowed_purposes": ["fraud-response"],
                    "max_impacted_objects": 2,
                }
            },
        },
    )
    resources.append(policy)
    release = KnowledgeRelease.build(
        release_id="crm-actions@1.0.0",
        resources=resources,
        scope={"tenant_id": "acme"},
    )
    compiler_policy = CompilerPolicy.from_resource(
        SemanticResource.create(
            resource_id="aof://acme/platform/policy/compiler-actions",
            kind=ResourceKind.POLICY,
            name="compiler-actions",
            domain="platform",
            owner="platform-governance",
            spec={
                "policy_type": "compiler",
                "allowed_compilers": {
                    "semantic-json": ["semantic-json@1"],
                    "actions": ["actions@1"],
                },
                "required_targets": ["actions"],
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
    plan = registry.plan(release, resources=resources, targets=["actions"])
    first = service.execute(
        run_id="crm-actions-001",
        plan=plan,
        policy=compiler_policy,
        release=release,
        resources=resources,
        actor="compiler:ci",
        rationale="Compile governed actions.",
    )
    replay = service.replay(
        first.run_id,
        run_id="crm-actions-002",
        policy=compiler_policy,
        release=release,
        resources=resources,
        actor="compiler:replay-ci",
        rationale="Reproduce governed actions.",
    )
    service.promote(
        replay.run_id,
        channel="production",
        actor="publisher:bob",
        approved_by="reviewer:alice",
        rationale="Promote reproduced action contracts.",
    )
    return repository, action, policy


def test_governed_action_plan_pins_release_policy_and_impact(tmp_path) -> None:
    repository, action, policy = _action_runtime(tmp_path)
    planner = GovernedActionPlanner(repository)
    request = ActionRequest.create(
        channel="production",
        action_type_id=action.resource_id,
        object_ids=["customer:alice"],
        inputs={"reason": "confirmed fraud"},
        purpose="fraud-response",
        idempotency_key="suspend-customer-alice-001",
        policy_resource_id=policy.resource_id,
    )

    plan = planner.plan(request, tenant_id="acme", roles=["risk-operator"])

    assert plan.verify() is True
    assert plan.release_id == "crm-actions@1.0.0"
    assert plan.run_id == "crm-actions-002"
    assert plan.policy_revision == policy.revision_id
    assert plan.impact_report["affected_object_count"] == 1
    assert plan.impact_report["approval_required"] is True
    assert plan.action_type_revision == action.revision_id


def test_governed_action_plan_rejects_role_and_blast_radius_before_execution(
    tmp_path,
) -> None:
    repository, action, policy = _action_runtime(tmp_path)
    planner = GovernedActionPlanner(repository)
    base = {
        "channel": "production",
        "action_type_id": action.resource_id,
        "inputs": {"reason": "confirmed fraud"},
        "purpose": "fraud-response",
        "idempotency_key": "request-001",
        "policy_resource_id": policy.resource_id,
    }

    with pytest.raises(ActionPolicyError, match="action_role_not_allowed"):
        planner.plan(
            ActionRequest.create(**base, object_ids=["customer:alice"]),
            tenant_id="acme",
            roles=["viewer"],
        )
    with pytest.raises(ActionPolicyError, match="action_impact_limit_exceeded"):
        planner.plan(
            ActionRequest.create(
                **base,
                object_ids=["customer:alice", "customer:bob", "customer:carol"],
            ),
            tenant_id="acme",
            roles=["risk-operator"],
        )


def test_governed_action_plan_rejects_invalid_inputs(tmp_path) -> None:
    repository, action, policy = _action_runtime(tmp_path)
    request = ActionRequest.create(
        channel="production",
        action_type_id=action.resource_id,
        object_ids=["customer:alice"],
        inputs={"unexpected": "value"},
        purpose="fraud-response",
        idempotency_key="request-invalid",
        policy_resource_id=policy.resource_id,
    )

    with pytest.raises(ActionPlanningError, match="missing required fields"):
        GovernedActionPlanner(repository).plan(
            request, tenant_id="acme", roles=["risk-operator"]
        )
