# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Validated, deterministic action contracts compiled from Semantic IR."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterable, Mapping

from .canonical import canonical_data, content_digest
from .models import ResourceKind, SemanticResource
from .releases import KnowledgeRelease


class ActionContractError(ValueError):
    """Raised when an action resource cannot be safely compiled."""


_SAFE_NAME = re.compile(r"^[a-z][a-z0-9._-]*$")
_FORBIDDEN_SECRET_KEYS = {
    "api_key",
    "credential",
    "credentials",
    "password",
    "secret",
    "token",
}


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ActionContractError(f"{field} must be a non-empty string")
    return value.strip()


def _reject_embedded_secrets(value: Any, path: str = "spec") -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = str(key).strip().lower()
            if normalized in _FORBIDDEN_SECRET_KEYS:
                raise ActionContractError(
                    f"action contracts cannot embed secret field: {path}.{key}"
                )
            _reject_embedded_secrets(item, f"{path}.{key}")
    elif isinstance(value, list | tuple):
        for index, item in enumerate(value):
            _reject_embedded_secrets(item, f"{path}[{index}]")


@dataclass(frozen=True)
class ActionCatalog:
    """Release-pinned executable contract catalog with no connector credentials."""

    release_id: str
    release_digest: str
    functions: tuple[Mapping[str, Any], ...]
    action_types: tuple[Mapping[str, Any], ...]
    workflows: tuple[Mapping[str, Any], ...]
    catalog_digest: str

    @classmethod
    def build(
        cls, release: KnowledgeRelease, resources: Iterable[SemanticResource]
    ) -> "ActionCatalog":
        ordered = tuple(sorted(resources, key=lambda item: item.resource_id))
        by_id = {item.resource_id: item for item in ordered}
        functions = tuple(
            cls._function(item)
            for item in ordered
            if item.kind is ResourceKind.FUNCTION
        )
        actions = tuple(
            cls._action_type(item, by_id)
            for item in ordered
            if item.kind is ResourceKind.ACTION_TYPE
        )
        workflows = tuple(
            cls._workflow(item, by_id)
            for item in ordered
            if item.kind is ResourceKind.WORKFLOW
        )
        if not actions:
            raise ActionContractError("release has no ActionType resources")
        payload = {
            "api_version": "aof.action-catalog/v1",
            "source_release_id": release.release_id,
            "source_release_digest": release.release_digest,
            "functions": canonical_data(functions),
            "action_types": canonical_data(actions),
            "workflows": canonical_data(workflows),
        }
        return cls(
            release_id=release.release_id,
            release_digest=release.release_digest,
            functions=tuple(MappingProxyType(item) for item in functions),
            action_types=tuple(MappingProxyType(item) for item in actions),
            workflows=tuple(MappingProxyType(item) for item in workflows),
            catalog_digest=content_digest(payload),
        )

    @staticmethod
    def _function(resource: SemanticResource) -> dict[str, Any]:
        spec = canonical_data(resource.spec)
        _reject_embedded_secrets(spec)
        timeout = spec.get("timeout_ms", 30_000)
        if isinstance(timeout, bool) or not isinstance(timeout, int) or timeout < 1:
            raise ActionContractError("function timeout_ms must be a positive integer")
        side_effects = spec.get("side_effects")
        if not isinstance(side_effects, bool):
            raise ActionContractError("function side_effects must be explicit boolean")
        return {
            "resource_id": resource.resource_id,
            "revision_id": resource.revision_id,
            "connector": _required_text(spec.get("connector"), "function connector"),
            "operation": _required_text(spec.get("operation"), "function operation"),
            "side_effects": side_effects,
            "timeout_ms": timeout,
        }

    @staticmethod
    def _action_type(
        resource: SemanticResource, resources: Mapping[str, SemanticResource]
    ) -> dict[str, Any]:
        spec = canonical_data(resource.spec)
        _reject_embedded_secrets(spec)
        function_id = _required_text(spec.get("function_id"), "action function_id")
        compensation_function_id = spec.get("compensation_function_id")
        object_type_id = _required_text(
            spec.get("target_object_type_id"), "action target_object_type_id"
        )
        effect = spec.get("effect_class")
        if effect not in {"reversible", "irreversible"}:
            raise ActionContractError(
                "action effect_class must be reversible or irreversible"
            )
        expected = {
            function_id: ResourceKind.FUNCTION,
            object_type_id: ResourceKind.OBJECT_TYPE,
        }
        if effect == "reversible":
            compensation_function_id = _required_text(
                compensation_function_id,
                "reversible action compensation_function_id",
            )
            expected[compensation_function_id] = ResourceKind.FUNCTION
        elif compensation_function_id is not None:
            raise ActionContractError(
                "irreversible actions cannot declare compensation_function_id"
            )
        for resource_id, kind in expected.items():
            dependency = resources.get(resource_id)
            if dependency is None or dependency.kind is not kind:
                raise ActionContractError(
                    f"action dependency must resolve to {kind.value}: {resource_id}"
                )
            if resource_id not in resource.depends_on:
                raise ActionContractError(
                    f"action dependency must be declared in depends_on: {resource_id}"
                )
        idempotency = spec.get("idempotency_scope")
        if idempotency not in {"object", "request", "tenant"}:
            raise ActionContractError(
                "action idempotency_scope must be object, request, or tenant"
            )
        schema = spec.get("input_schema")
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            raise ActionContractError("action input_schema must be a JSON object schema")
        roles = spec.get("required_approval_roles", ())
        if not isinstance(roles, list | tuple):
            raise ActionContractError("required_approval_roles must be a list")
        normalized_roles = sorted({_required_text(item, "approval role") for item in roles})
        if effect == "irreversible" and not normalized_roles:
            raise ActionContractError("irreversible actions require an approval role")
        return {
            "resource_id": resource.resource_id,
            "revision_id": resource.revision_id,
            "function_id": function_id,
            "compensation_function_id": compensation_function_id,
            "target_object_type_id": object_type_id,
            "effect_class": effect,
            "idempotency_scope": idempotency,
            "required_approval_roles": normalized_roles,
            "input_schema": canonical_data(schema),
        }

    @classmethod
    def _workflow(
        cls, resource: SemanticResource, resources: Mapping[str, SemanticResource]
    ) -> dict[str, Any]:
        spec = canonical_data(resource.spec)
        _reject_embedded_secrets(spec)
        raw_nodes = spec.get("nodes")
        if not isinstance(raw_nodes, list | tuple) or not raw_nodes:
            raise ActionContractError("workflow nodes must be a non-empty list")
        nodes = {}
        for raw in raw_nodes:
            if not isinstance(raw, Mapping):
                raise ActionContractError("workflow nodes must be semantic objects")
            node_id = _required_text(raw.get("node_id"), "workflow node_id")
            if not _SAFE_NAME.fullmatch(node_id) or node_id in nodes:
                raise ActionContractError(f"invalid or duplicate workflow node_id: {node_id}")
            action_id = _required_text(
                raw.get("action_type_id"), "workflow action_type_id"
            )
            action = resources.get(action_id)
            if action is None or action.kind is not ResourceKind.ACTION_TYPE:
                raise ActionContractError(
                    f"workflow action_type_id is not published: {action_id}"
                )
            if action_id not in resource.depends_on:
                raise ActionContractError(
                    f"workflow action must be declared in depends_on: {action_id}"
                )
            dependencies = raw.get("depends_on", ())
            if not isinstance(dependencies, list | tuple):
                raise ActionContractError("workflow node depends_on must be a list")
            nodes[node_id] = {
                "node_id": node_id,
                "action_type_id": action_id,
                "depends_on": sorted(
                    {_required_text(item, "workflow dependency") for item in dependencies}
                ),
            }
        order = cls._topological_order(nodes)
        return {
            "resource_id": resource.resource_id,
            "revision_id": resource.revision_id,
            "nodes": [nodes[node_id] for node_id in sorted(nodes)],
            "execution_order": order,
        }

    @staticmethod
    def _topological_order(nodes: Mapping[str, Mapping[str, Any]]) -> list[str]:
        unknown = sorted(
            {
                dependency
                for node in nodes.values()
                for dependency in node["depends_on"]
                if dependency not in nodes
            }
        )
        if unknown:
            raise ActionContractError(
                f"workflow contains unknown node dependencies: {', '.join(unknown)}"
            )
        remaining = {key: set(value["depends_on"]) for key, value in nodes.items()}
        order = []
        while remaining:
            ready = sorted(key for key, dependencies in remaining.items() if not dependencies)
            if not ready:
                raise ActionContractError("workflow dependency graph contains a cycle")
            for node_id in ready:
                order.append(node_id)
                remaining.pop(node_id)
            for dependencies in remaining.values():
                dependencies.difference_update(ready)
        return order

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.action-catalog/v1",
            "source_release_id": self.release_id,
            "source_release_digest": self.release_digest,
            "functions": canonical_data(self.functions),
            "action_types": canonical_data(self.action_types),
            "workflows": canonical_data(self.workflows),
            "catalog_digest": self.catalog_digest,
        }
