# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Deterministic runtime views compiled from one governed Semantic IR release."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..canonical import canonical_data, canonical_json, content_digest
from ..action_contracts import ActionCatalog, ActionContractError
from ..models import ResourceKind, SemanticResource
from .base import CompilationInput, CompiledArtifact, SemanticCompiler, VerificationReport


class _RuntimeJsonCompiler(SemanticCompiler):
    filename = "artifact.json"
    media_type = "application/json"

    def validate(self, compilation: CompilationInput) -> VerificationReport:
        report = super().validate(compilation)
        if not report.valid:
            return report
        if not any(resource.kind in self.supported_kinds for resource in compilation.resources):
            return VerificationReport(False, (f"release has no resources supported by {self.target}",))
        return report

    def compile(self, compilation: CompilationInput, output_dir: Path) -> CompiledArtifact:
        selected = tuple(
            resource for resource in compilation.resources if resource.kind in self.supported_kinds
        )
        path = output_dir / self.filename
        path.write_text(canonical_json(self.payload(compilation, selected)) + "\n", encoding="utf-8")
        return self.artifact(compilation, path, media_type=self.media_type)

    def payload(
        self, compilation: CompilationInput, resources: tuple[SemanticResource, ...]
    ) -> dict[str, Any]:
        return {
            "api_version": f"aof.compiler.{self.target}/v1",
            "source_release_id": compilation.release.release_id,
            "source_release_digest": compilation.release.release_digest,
            "resources": [resource.to_dict() for resource in resources],
        }


class OwlCompiler(_RuntimeJsonCompiler):
    target = "owl"
    version = "1"
    filename = "ontology-bundle.json"
    media_type = "application/vnd.aof.owl-bundle+json"
    supported_kinds = frozenset({ResourceKind.ONTOLOGY, ResourceKind.VOCABULARY})
    ignored_kinds = frozenset(ResourceKind) - supported_kinds


class ShaclCompiler(_RuntimeJsonCompiler):
    target = "shacl"
    version = "1"
    filename = "shapes-bundle.json"
    media_type = "application/vnd.aof.shacl-bundle+json"
    supported_kinds = frozenset({ResourceKind.CONSTRAINT_SET})
    ignored_kinds = frozenset(ResourceKind) - supported_kinds


class DatalogCompiler(_RuntimeJsonCompiler):
    target = "datalog"
    version = "1"
    filename = "rulesets.json"
    media_type = "application/vnd.aof.datalog-bundle+json"
    supported_kinds = frozenset({ResourceKind.RULE_SET})
    ignored_kinds = frozenset(ResourceKind) - supported_kinds

    def payload(
        self, compilation: CompilationInput, resources: tuple[SemanticResource, ...]
    ) -> dict[str, Any]:
        payload = super().payload(compilation, resources)
        payload["rulesets"] = [
            {
                "resource_id": resource.resource_id,
                "revision_id": resource.revision_id,
                "language": resource.spec.get("language", "datalog"),
                "program": resource.spec.get("program", ""),
                "event_subscription": resource.spec.get("event_subscription"),
            }
            for resource in resources
        ]
        return payload


class RagCompiler(_RuntimeJsonCompiler):
    target = "rag"
    version = "1"
    filename = "retrieval-index.json"
    media_type = "application/vnd.aof.rag-index+json"
    supported_kinds = frozenset({
        ResourceKind.RETRIEVAL_PROFILE,
        ResourceKind.CONCEPT,
        ResourceKind.METRIC,
        ResourceKind.DIMENSION,
        ResourceKind.LOGICAL_DATASET,
        ResourceKind.PHYSICAL_DATASET,
        ResourceKind.FIELD_BINDING,
        ResourceKind.RELATION_BINDING,
        ResourceKind.QUERY_TEMPLATE,
    })
    ignored_kinds = frozenset(ResourceKind) - supported_kinds


class ActionCompiler(_RuntimeJsonCompiler):
    target = "actions"
    version = "1"
    filename = "action-catalog.json"
    media_type = "application/vnd.aof.action-catalog+json"
    supported_kinds = frozenset(
        {ResourceKind.ACTION_TYPE, ResourceKind.FUNCTION, ResourceKind.WORKFLOW}
    )
    ignored_kinds = frozenset(ResourceKind) - supported_kinds
    requires_targets = ("semantic-json",)

    def validate(self, compilation: CompilationInput) -> VerificationReport:
        report = super().validate(compilation)
        if not report.valid:
            return report
        try:
            ActionCatalog.build(compilation.release, compilation.resources)
        except ActionContractError as exc:
            return VerificationReport(False, (str(exc),))
        return report

    def payload(
        self, compilation: CompilationInput, resources: tuple[SemanticResource, ...]
    ) -> dict[str, Any]:
        return ActionCatalog.build(compilation.release, compilation.resources).to_dict()


def _agent_action_contracts(compilation: CompilationInput) -> list[dict[str, Any]]:
    catalog = ActionCatalog.build(compilation.release, compilation.resources)
    contracts = []
    for action in catalog.action_types:
        request_schema = {
            "type": "object",
            "properties": {
                "channel": {"type": "string"},
                "object_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                },
                "inputs": canonical_data(action["input_schema"]),
                "purpose": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "policy_resource_id": {"type": "string"},
            },
            "required": [
                "channel",
                "object_ids",
                "inputs",
                "purpose",
                "idempotency_key",
                "policy_resource_id",
            ],
            "additionalProperties": False,
        }
        payload = {
            "api_version": "aof.agent-action-contract/v1",
            "source_release_id": compilation.release.release_id,
            "source_release_digest": compilation.release.release_digest,
            "action_type_id": action["resource_id"],
            "action_type_revision": action["revision_id"],
            "effect_class": action["effect_class"],
            "idempotency_scope": action["idempotency_scope"],
            "required_approval_roles": canonical_data(
                action["required_approval_roles"]
            ),
            "input_schema": canonical_data(action["input_schema"]),
            "request_schema": request_schema,
            "control_plane": {
                "plan": "/v1/semantic/actions/plan",
                "submit": "/v1/semantic/action-runs",
                "approve": "/v1/semantic/action-runs/{run_id}/approve",
                "execute": "/v1/semantic/action-runs/{run_id}/execute",
                "get": "/v1/semantic/action-runs/{run_id}",
            },
        }
        contracts.append({**payload, "contract_digest": content_digest(payload)})
    return contracts


class AgentSdkCompiler(_RuntimeJsonCompiler):
    target = "agent-sdk"
    version = "1"
    filename = "agent-action-sdk.json"
    media_type = "application/vnd.aof.agent-action-sdk+json"
    supported_kinds = frozenset(
        {ResourceKind.ACTION_TYPE, ResourceKind.FUNCTION, ResourceKind.WORKFLOW}
    )
    ignored_kinds = frozenset(ResourceKind) - supported_kinds
    requires_targets = ("actions", "semantic-json")

    def validate(self, compilation: CompilationInput) -> VerificationReport:
        report = super().validate(compilation)
        if not report.valid:
            return report
        try:
            ActionCatalog.build(compilation.release, compilation.resources)
        except ActionContractError as exc:
            return VerificationReport(False, (str(exc),))
        return report

    def payload(
        self, compilation: CompilationInput, resources: tuple[SemanticResource, ...]
    ) -> dict[str, Any]:
        return {
            "api_version": "aof.agent-action-sdk/v1",
            "source_release_id": compilation.release.release_id,
            "source_release_digest": compilation.release.release_digest,
            "operations": _agent_action_contracts(compilation),
        }


class McpCompiler(_RuntimeJsonCompiler):
    target = "mcp"
    version = "1"
    filename = "mcp-catalog.json"
    media_type = "application/vnd.aof.mcp-catalog+json"
    supported_kinds = frozenset(ResourceKind)
    requires_targets = ("semantic-json",)

    def payload(
        self, compilation: CompilationInput, resources: tuple[SemanticResource, ...]
    ) -> dict[str, Any]:
        payload = super().payload(compilation, resources)
        payload["mcp_resources"] = [
            {
                "uri": resource.resource_id,
                "name": resource.display_name or resource.name,
                "description": resource.description,
                "mime_type": "application/vnd.aof.semantic-resource+json",
                "revision_id": resource.revision_id,
            }
            for resource in resources
        ]
        payload["mcp_tools"] = [
            {
                "name": "query_" + resource.name.replace("-", "_"),
                "description": resource.description or f"Execute {resource.name}",
                "resource_id": resource.resource_id,
                "revision_id": resource.revision_id,
            }
            for resource in resources
            if resource.kind in {
                ResourceKind.QUERY_TEMPLATE,
                ResourceKind.RULE_SET,
                ResourceKind.RETRIEVAL_PROFILE,
            }
        ]
        if any(
            resource.kind is ResourceKind.ACTION_TYPE
            for resource in compilation.resources
        ):
            payload["mcp_tools"].extend(
                {
                    "name": "plan_" + contract["action_type_id"].rsplit("/", 1)[-1].replace("-", "_"),
                    "description": "Plan a governed action through AOF; this tool never calls the connector directly.",
                    "kind": "action",
                    "mutating": False,
                    "action_type_id": contract["action_type_id"],
                    "action_type_revision": contract["action_type_revision"],
                    "input_schema": contract["request_schema"],
                    "control_plane": contract["control_plane"],
                    "contract_digest": contract["contract_digest"],
                }
                for contract in _agent_action_contracts(compilation)
            )
            payload["mcp_tools"].sort(key=lambda item: item["name"])
        return payload
