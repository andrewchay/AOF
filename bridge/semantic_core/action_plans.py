"""Release-pinned action planning with deterministic policy and impact gates."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .canonical import canonical_data, content_digest
from .compilers import CompilationRunRepository
from .models import ResourceKind, SemanticResource


class ActionPlanningError(ValueError):
    """Raised when an action cannot be resolved to a trusted release snapshot."""


class ActionPolicyError(ActionPlanningError):
    """Raised when deterministic action authorization or impact policy rejects a plan."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionPlanningError(f"{field} must be a non-empty string")
    return value.strip()


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ActionPlanningError(f"{field} must be a list")
    result = tuple(sorted({_text(item, field) for item in value}))
    if not result:
        raise ActionPlanningError(f"{field} must be non-empty")
    return result


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class ActionRequest:
    channel: str
    action_type_id: str
    object_ids: tuple[str, ...]
    inputs: Mapping[str, Any]
    purpose: str
    idempotency_key: str
    policy_resource_id: str
    request_digest: str

    @classmethod
    def create(
        cls,
        *,
        channel: str,
        action_type_id: str,
        object_ids: Sequence[str],
        inputs: Mapping[str, Any],
        purpose: str,
        idempotency_key: str,
        policy_resource_id: str,
    ) -> "ActionRequest":
        if not isinstance(inputs, Mapping):
            raise ActionPlanningError("inputs must be a semantic object")
        payload = {
            "api_version": "aof.action-request/v1",
            "channel": _text(channel, "channel"),
            "action_type_id": _text(action_type_id, "action_type_id"),
            "object_ids": list(_strings(object_ids, "object_ids")),
            "inputs": canonical_data(inputs),
            "purpose": _text(purpose, "purpose"),
            "idempotency_key": _text(idempotency_key, "idempotency_key"),
            "policy_resource_id": _text(policy_resource_id, "policy_resource_id"),
        }
        return cls(
            channel=payload["channel"],
            action_type_id=payload["action_type_id"],
            object_ids=tuple(payload["object_ids"]),
            inputs=_freeze(payload["inputs"]),
            purpose=payload["purpose"],
            idempotency_key=payload["idempotency_key"],
            policy_resource_id=payload["policy_resource_id"],
            request_digest=content_digest(payload),
        )


@dataclass(frozen=True)
class ActionPlan:
    tenant_id: str
    channel: str
    channel_version: int
    channel_pointer_digest: str
    request_digest: str
    action_type_id: str
    action_type_revision: str
    function_id: str
    connector: str
    operation: str
    compensation_function_id: str | None
    compensation_operation: str | None
    target_object_type_id: str
    effect_class: str
    idempotency_scope: str
    required_approval_roles: tuple[str, ...]
    object_ids: tuple[str, ...]
    inputs: Mapping[str, Any]
    purpose: str
    idempotency_key: str
    run_id: str
    run_digest: str
    release_id: str
    release_digest: str
    artifact_hashes: Mapping[str, str]
    policy_resource_id: str
    policy_revision: str
    policy_report: Mapping[str, Any]
    impact_report: Mapping[str, Any]
    plan_digest: str

    def _payload(self) -> dict[str, Any]:
        return {
            "api_version": "aof.action-plan/v1",
            "tenant_id": self.tenant_id,
            "channel": self.channel,
            "channel_version": self.channel_version,
            "channel_pointer_digest": self.channel_pointer_digest,
            "request_digest": self.request_digest,
            "action_type_id": self.action_type_id,
            "action_type_revision": self.action_type_revision,
            "function_id": self.function_id,
            "connector": self.connector,
            "operation": self.operation,
            "compensation_function_id": self.compensation_function_id,
            "compensation_operation": self.compensation_operation,
            "target_object_type_id": self.target_object_type_id,
            "effect_class": self.effect_class,
            "idempotency_scope": self.idempotency_scope,
            "required_approval_roles": list(self.required_approval_roles),
            "object_ids": list(self.object_ids),
            "inputs": canonical_data(self.inputs),
            "purpose": self.purpose,
            "idempotency_key": self.idempotency_key,
            "run_id": self.run_id,
            "run_digest": self.run_digest,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "artifact_hashes": canonical_data(self.artifact_hashes),
            "policy_resource_id": self.policy_resource_id,
            "policy_revision": self.policy_revision,
            "policy_report": canonical_data(self.policy_report),
            "impact_report": canonical_data(self.impact_report),
        }

    def verify(self) -> bool:
        return self.plan_digest == content_digest(self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "plan_digest": self.plan_digest}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionPlan":
        payload = dict(value)
        payload.pop("api_version", None)
        for field in ("required_approval_roles", "object_ids"):
            payload[field] = tuple(payload[field])
        plan = cls(**payload)
        if not plan.verify():
            raise ActionPlanningError("action plan digest mismatch")
        return plan


@dataclass(frozen=True)
class ActionPolicy:
    resource_id: str
    revision_id: str
    role_actions: Mapping[str, tuple[str, ...]]
    action_rules: Mapping[str, Mapping[str, Any]]

    @classmethod
    def from_resource(cls, resource: SemanticResource) -> "ActionPolicy":
        if resource.kind is not ResourceKind.POLICY or resource.spec.get("policy_type") != "action":
            raise ActionPolicyError("action policy must be a Policy with policy_type action")
        supported = {"policy_type", "role_actions", "action_rules"}
        unknown = sorted(set(resource.spec) - supported)
        if unknown:
            raise ActionPolicyError(f"unknown action policy fields: {', '.join(unknown)}")
        raw_roles = resource.spec.get("role_actions")
        if not isinstance(raw_roles, Mapping) or not raw_roles:
            raise ActionPolicyError("role_actions must be a non-empty mapping")
        roles = {
            _text(role, "action role"): _strings(actions, "role actions")
            for role, actions in raw_roles.items()
        }
        raw_rules = resource.spec.get("action_rules", {})
        if not isinstance(raw_rules, Mapping):
            raise ActionPolicyError("action_rules must be a mapping")
        rules = {}
        for action_id, value in raw_rules.items():
            if not isinstance(value, Mapping):
                raise ActionPolicyError("action rule must be a semantic object")
            unknown_rule = sorted(
                set(value) - {"allowed_purposes", "max_impacted_objects"}
            )
            if unknown_rule:
                raise ActionPolicyError(
                    f"unknown action rule fields: {', '.join(unknown_rule)}"
                )
            maximum = value.get("max_impacted_objects")
            if maximum is not None and (
                isinstance(maximum, bool) or not isinstance(maximum, int) or maximum < 1
            ):
                raise ActionPolicyError("max_impacted_objects must be positive")
            rules[_text(action_id, "action rule id")] = _freeze(
                {
                    "allowed_purposes": list(
                        _strings(value.get("allowed_purposes", ()), "allowed_purposes")
                    )
                    if value.get("allowed_purposes")
                    else [],
                    "max_impacted_objects": maximum,
                }
            )
        return cls(
            resource_id=resource.resource_id,
            revision_id=resource.revision_id,
            role_actions=MappingProxyType(roles),
            action_rules=MappingProxyType(rules),
        )

    def evaluate(
        self,
        *,
        request: ActionRequest,
        roles: Iterable[str],
        impact_report: Mapping[str, Any],
    ) -> dict[str, Any]:
        normalized_roles = tuple(sorted({_text(role, "roles") for role in roles}))
        findings = []
        if not any(
            request.action_type_id in self.role_actions.get(role, ())
            for role in normalized_roles
        ):
            findings.append("action_role_not_allowed")
        rule = self.action_rules.get(request.action_type_id, MappingProxyType({}))
        allowed_purposes = set(rule.get("allowed_purposes", ()))
        if allowed_purposes and request.purpose not in allowed_purposes:
            findings.append("action_purpose_not_allowed")
        maximum = rule.get("max_impacted_objects")
        if maximum is not None and impact_report["affected_object_count"] > maximum:
            findings.append("action_impact_limit_exceeded")
        payload = {
            "api_version": "aof.action-policy-report/v1",
            "policy_resource_id": self.resource_id,
            "policy_revision": self.revision_id,
            "request_digest": request.request_digest,
            "roles": list(normalized_roles),
            "conforms": not findings,
            "findings": sorted(findings),
        }
        report = {**payload, "report_digest": content_digest(payload)}
        if findings:
            raise ActionPolicyError(
                f"action policy rejected the plan: {', '.join(sorted(findings))}"
            )
        return report


class GovernedActionPlanner:
    """Resolve, validate, impact-check, and authorize an action before execution."""

    def __init__(self, repository: CompilationRunRepository) -> None:
        self.repository = repository

    def plan(
        self, request: ActionRequest, *, tenant_id: str, roles: Iterable[str]
    ) -> ActionPlan:
        tenant = _text(tenant_id, "tenant_id")
        pointer = self.repository.get_channel(request.channel)
        if pointer is None:
            raise ActionPlanningError(f"action channel not found: {request.channel}")
        pointer_payload = {
            key: value for key, value in pointer.items() if key != "pointer_digest"
        }
        if pointer.get("pointer_digest") != content_digest(pointer_payload):
            raise ActionPlanningError("action channel pointer digest mismatch")
        run = self.repository.get(str(pointer["run_id"]))
        if run is None or not run.reproducible:
            raise ActionPlanningError("action snapshot requires a reproducible run")
        if run.tenant_id != tenant:
            raise ActionPlanningError("action run tenant does not match principal tenant")
        artifacts = {str(item["target"]): item for item in run.artifacts}
        missing = sorted({"actions", "semantic-json"} - set(artifacts))
        if missing:
            raise ActionPlanningError(
                f"action plan requires missing artifacts: {', '.join(missing)}"
            )
        action_catalog = self._artifact(run.run_id, artifacts["actions"], run.release_digest)
        semantic = self._artifact(
            run.run_id, artifacts["semantic-json"], run.release_digest
        )
        action = next(
            (
                item
                for item in action_catalog.get("action_types", ())
                if item.get("resource_id") == request.action_type_id
            ),
            None,
        )
        if action is None:
            raise ActionPlanningError(
                f"action type is not published: {request.action_type_id}"
            )
        functions = {
            item.get("resource_id"): item
            for item in action_catalog.get("functions", ())
            if isinstance(item, Mapping)
        }
        function = functions.get(action.get("function_id"))
        if function is None:
            raise ActionPlanningError("action function is missing from the catalog")
        compensation_id = action.get("compensation_function_id")
        compensation = functions.get(compensation_id) if compensation_id else None
        if compensation_id and compensation is None:
            raise ActionPlanningError("action compensation function is missing from the catalog")
        if compensation is not None and compensation.get("connector") != function.get("connector"):
            raise ActionPlanningError(
                "action and compensation functions must use the same connector"
            )
        self._validate_inputs(request.inputs, action.get("input_schema"))
        policy_resource = next(
            (
                SemanticResource.from_dict(item)
                for item in semantic.get("resources", ())
                if item.get("resource_id") == request.policy_resource_id
            ),
            None,
        )
        if policy_resource is None:
            raise ActionPolicyError(
                f"action policy is not published: {request.policy_resource_id}"
            )
        workflows = sorted(
            item["resource_id"]
            for item in action_catalog.get("workflows", ())
            if any(
                node.get("action_type_id") == request.action_type_id
                for node in item.get("nodes", ())
            )
        )
        impact_payload = {
            "api_version": "aof.action-impact-report/v1",
            "request_digest": request.request_digest,
            "action_type_id": request.action_type_id,
            "effect_class": action["effect_class"],
            "affected_object_count": len(request.object_ids),
            "affected_object_ids": list(request.object_ids),
            "dependent_workflow_ids": workflows,
            "approval_required": bool(action.get("required_approval_roles")),
        }
        impact = {**impact_payload, "report_digest": content_digest(impact_payload)}
        policy = ActionPolicy.from_resource(policy_resource)
        policy_report = policy.evaluate(
            request=request, roles=roles, impact_report=impact
        )
        payload = {
            "api_version": "aof.action-plan/v1",
            "tenant_id": tenant,
            "channel": request.channel,
            "channel_version": int(pointer["version"]),
            "channel_pointer_digest": str(pointer["pointer_digest"]),
            "request_digest": request.request_digest,
            "action_type_id": request.action_type_id,
            "action_type_revision": action["revision_id"],
            "function_id": action["function_id"],
            "connector": function["connector"],
            "operation": function["operation"],
            "compensation_function_id": compensation_id,
            "compensation_operation": (
                compensation["operation"] if compensation is not None else None
            ),
            "target_object_type_id": action["target_object_type_id"],
            "effect_class": action["effect_class"],
            "idempotency_scope": action["idempotency_scope"],
            "required_approval_roles": list(action["required_approval_roles"]),
            "object_ids": list(request.object_ids),
            "inputs": canonical_data(request.inputs),
            "purpose": request.purpose,
            "idempotency_key": request.idempotency_key,
            "run_id": run.run_id,
            "run_digest": run.run_digest,
            "release_id": run.release_id,
            "release_digest": run.release_digest,
            "artifact_hashes": {
                target: str(artifacts[target]["content_hash"])
                for target in ("actions", "semantic-json")
            },
            "policy_resource_id": policy.resource_id,
            "policy_revision": policy.revision_id,
            "policy_report": policy_report,
            "impact_report": impact,
        }
        return ActionPlan(
            tenant_id=tenant,
            channel=request.channel,
            channel_version=int(pointer["version"]),
            channel_pointer_digest=str(pointer["pointer_digest"]),
            request_digest=request.request_digest,
            action_type_id=request.action_type_id,
            action_type_revision=str(action["revision_id"]),
            function_id=str(action["function_id"]),
            connector=str(function["connector"]),
            operation=str(function["operation"]),
            compensation_function_id=(str(compensation_id) if compensation_id else None),
            compensation_operation=(
                str(compensation["operation"]) if compensation is not None else None
            ),
            target_object_type_id=str(action["target_object_type_id"]),
            effect_class=str(action["effect_class"]),
            idempotency_scope=str(action["idempotency_scope"]),
            required_approval_roles=tuple(action["required_approval_roles"]),
            object_ids=request.object_ids,
            inputs=_freeze(request.inputs),
            purpose=request.purpose,
            idempotency_key=request.idempotency_key,
            run_id=run.run_id,
            run_digest=run.run_digest,
            release_id=run.release_id,
            release_digest=run.release_digest,
            artifact_hashes=_freeze(payload["artifact_hashes"]),
            policy_resource_id=policy.resource_id,
            policy_revision=policy.revision_id,
            policy_report=_freeze(policy_report),
            impact_report=_freeze(impact),
            plan_digest=content_digest(payload),
        )

    def _artifact(
        self, run_id: str, artifact: Mapping[str, Any], release_digest: str
    ) -> dict[str, Any]:
        path = (
            Path(self.repository.root)
            / "artifacts"
            / run_id
            / str(artifact["target"])
            / str(artifact["uri"])
        )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ActionPlanningError(f"action artifact is missing: {path.name}") from exc
        digest = f"sha256:{hashlib.sha256(raw).hexdigest()}"
        if digest != artifact.get("content_hash"):
            raise ActionPlanningError("action artifact content hash mismatch")
        payload = json.loads(raw)
        source_digest = payload.get("source_release_digest")
        if source_digest is None and isinstance(payload.get("source_release"), Mapping):
            source_digest = payload["source_release"].get("release_digest")
        if source_digest != release_digest:
            raise ActionPlanningError("action artifact release digest mismatch")
        return payload

    @staticmethod
    def _validate_inputs(inputs: Mapping[str, Any], schema: Any) -> None:
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise ActionPlanningError("published action input schema is invalid")
        required = schema.get("required", ())
        if not isinstance(required, list | tuple):
            raise ActionPlanningError("action input schema required must be a list")
        missing = sorted(set(required) - set(inputs))
        if missing:
            raise ActionPlanningError(
                f"action inputs missing required fields: {', '.join(missing)}"
            )
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise ActionPlanningError("action input schema properties must be an object")
        if schema.get("additionalProperties") is False:
            unknown = sorted(set(inputs) - set(properties))
            if unknown:
                raise ActionPlanningError(
                    f"action inputs contain unknown fields: {', '.join(unknown)}"
                )
        python_types = {
            "string": str,
            "integer": int,
            "number": (int, float),
            "boolean": bool,
            "object": Mapping,
            "array": (list, tuple),
        }
        for field, value in inputs.items():
            expected = properties.get(field, {}).get("type")
            expected_type = python_types.get(expected)
            if expected_type is not None and (
                isinstance(value, bool) and expected in {"integer", "number"}
                or not isinstance(value, expected_type)
            ):
                raise ActionPlanningError(
                    f"action input field has invalid type: {field}"
                )
