"""Governed ActionType, Function, and Workflow compilation contracts."""

from __future__ import annotations

import json

import pytest

from bridge.semantic_core import KnowledgeRelease, ResourceKind, SemanticResource
from bridge.semantic_core.compilers import CompilerError, default_compiler_registry


def _action_resources() -> list[SemanticResource]:
    customer = SemanticResource.create(
        resource_id="aof://acme/crm/object-type/customer",
        kind=ResourceKind.OBJECT_TYPE,
        name="customer",
        domain="crm",
        owner="crm-platform",
    )
    function = SemanticResource.create(
        resource_id="aof://acme/crm/function/suspend-customer",
        kind=ResourceKind.FUNCTION,
        name="suspend-customer",
        domain="crm",
        owner="crm-platform",
        spec={
            "connector": "crm-core",
            "operation": "suspend_customer",
            "side_effects": True,
            "timeout_ms": 5000,
        },
    )
    compensation = SemanticResource.create(
        resource_id="aof://acme/crm/function/resume-customer",
        kind=ResourceKind.FUNCTION,
        name="resume-customer",
        domain="crm",
        owner="crm-platform",
        spec={
            "connector": "crm-core",
            "operation": "resume_customer",
            "side_effects": True,
            "timeout_ms": 5000,
        },
    )
    action = SemanticResource.create(
        resource_id="aof://acme/crm/action-type/suspend-customer",
        kind=ResourceKind.ACTION_TYPE,
        name="suspend-customer",
        domain="crm",
        owner="risk-operations",
        depends_on=[
            customer.resource_id,
            function.resource_id,
            compensation.resource_id,
        ],
        spec={
            "function_id": function.resource_id,
            "compensation_function_id": compensation.resource_id,
            "target_object_type_id": customer.resource_id,
            "effect_class": "reversible",
            "idempotency_scope": "object",
            "required_approval_roles": ["risk-reviewer"],
            "input_schema": {
                "type": "object",
                "properties": {"reason": {"type": "string"}},
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    )
    workflow = SemanticResource.create(
        resource_id="aof://acme/crm/workflow/customer-risk-response",
        kind=ResourceKind.WORKFLOW,
        name="customer-risk-response",
        domain="crm",
        owner="risk-operations",
        depends_on=[action.resource_id],
        spec={
            "nodes": [
                {
                    "node_id": "suspend",
                    "action_type_id": action.resource_id,
                    "depends_on": [],
                }
            ]
        },
    )
    return [customer, function, compensation, action, workflow]


def test_release_compiles_deterministic_action_catalog(tmp_path) -> None:
    resources = _action_resources()
    release = KnowledgeRelease.build(
        release_id="crm-actions@1.0.0",
        resources=resources,
        scope={"tenant_id": "acme"},
    )
    registry = default_compiler_registry()

    plan = registry.plan(release, resources=resources, targets=["actions"])
    artifact = registry.compile(
        "actions", release, tmp_path / "first", resources=resources
    )
    replay = registry.compile(
        "actions", release, tmp_path / "replay", resources=resources
    )
    payload = json.loads((tmp_path / "first" / artifact.uri).read_text())

    assert plan.valid is True
    assert [step.target for step in plan.steps] == ["semantic-json", "actions"]
    assert artifact.content_hash == replay.content_hash
    assert payload["source_release_digest"] == release.release_digest
    assert payload["action_types"][0]["function_id"].endswith(
        "/function/suspend-customer"
    )
    assert payload["workflows"][0]["execution_order"] == ["suspend"]


def test_action_compiler_rejects_embedded_connector_credentials(tmp_path) -> None:
    resources = _action_resources()
    original = resources[1]
    resources[1] = SemanticResource.create(
        resource_id=original.resource_id,
        kind=original.kind,
        name=original.name,
        domain=original.domain,
        owner=original.owner,
        spec={**dict(original.spec), "token": "must-never-enter-a-release"},
    )
    release = KnowledgeRelease.build(
        release_id="crm-actions@unsafe",
        resources=resources,
        scope={"tenant_id": "acme"},
    )
    registry = default_compiler_registry()

    plan = registry.plan(release, resources=resources, targets=["actions"])

    assert plan.valid is False
    assert "cannot embed secret field" in plan.diagnostics[0]["message"]
    with pytest.raises(CompilerError, match="cannot embed secret field"):
        registry.compile("actions", release, tmp_path, resources=resources)


def test_action_compiler_rejects_cyclic_workflow() -> None:
    resources = _action_resources()
    action = next(item for item in resources if item.kind is ResourceKind.ACTION_TYPE)
    workflow = resources[-1]
    resources[-1] = SemanticResource.create(
        resource_id=workflow.resource_id,
        kind=workflow.kind,
        name=workflow.name,
        domain=workflow.domain,
        owner=workflow.owner,
        depends_on=workflow.depends_on,
        spec={
            "nodes": [
                {
                    "node_id": "suspend",
                    "action_type_id": action.resource_id,
                    "depends_on": ["notify"],
                },
                {
                    "node_id": "notify",
                    "action_type_id": action.resource_id,
                    "depends_on": ["suspend"],
                },
            ]
        },
    )
    release = KnowledgeRelease.build(
        release_id="crm-actions@cycle",
        resources=resources,
        scope={"tenant_id": "acme"},
    )

    plan = default_compiler_registry().plan(
        release, resources=resources, targets=["actions"]
    )

    assert plan.valid is False
    assert "dependency graph contains a cycle" in plan.diagnostics[0]["message"]
