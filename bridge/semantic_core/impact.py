"""Transitive semantic dependency impact over immutable resource revisions."""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from .canonical import content_digest
from .models import ResourceKind, SemanticResource


class SemanticImpactError(ValueError):
    """Raised when a semantic dependency graph is ambiguous."""


@dataclass(frozen=True)
class SemanticImpactReport:
    added_resource_ids: tuple[str, ...]
    removed_resource_ids: tuple[str, ...]
    changed_resource_ids: tuple[str, ...]
    affected_resource_ids: tuple[str, ...]
    affected_mcp_tools: tuple[str, ...]
    affected_contract_ids: tuple[str, ...]
    affected_artifacts: tuple[str, ...]
    report_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.semantic-impact-report/v1",
            "added_resource_ids": list(self.added_resource_ids),
            "removed_resource_ids": list(self.removed_resource_ids),
            "changed_resource_ids": list(self.changed_resource_ids),
            "affected_resource_ids": list(self.affected_resource_ids),
            "affected_mcp_tools": list(self.affected_mcp_tools),
            "affected_contract_ids": list(self.affected_contract_ids),
            "affected_artifacts": list(self.affected_artifacts),
            "report_digest": self.report_digest,
        }


class SemanticImpactAnalyzer:
    """Compute deterministic transitive consumers of changed semantic resources."""

    _MCP_TOOL_KINDS = {
        ResourceKind.QUERY_TEMPLATE,
        ResourceKind.RULE_SET,
        ResourceKind.RETRIEVAL_PROFILE,
    }

    def compare(
        self,
        previous: Iterable[SemanticResource],
        current: Iterable[SemanticResource],
        *,
        compiled_artifacts: Iterable[Mapping[str, Any]] = (),
    ) -> SemanticImpactReport:
        previous_by_id = self._index(previous, "previous")
        current_by_id = self._index(current, "current")
        previous_ids = set(previous_by_id)
        current_ids = set(current_by_id)
        added = tuple(sorted(current_ids - previous_ids))
        removed = tuple(sorted(previous_ids - current_ids))
        changed = tuple(
            sorted(
                resource_id
                for resource_id in previous_ids & current_ids
                if previous_by_id[resource_id].revision_id
                != current_by_id[resource_id].revision_id
            )
        )
        graph_resources = {**previous_by_id, **current_by_id}
        children: dict[str, set[str]] = {}
        for resource in graph_resources.values():
            for dependency in resource.depends_on:
                children.setdefault(dependency, set()).add(resource.resource_id)
        roots = set((*added, *removed, *changed))
        affected: set[str] = set()
        queue = deque(sorted(roots))
        while queue:
            resource_id = queue.popleft()
            for consumer in sorted(children.get(resource_id, ())):
                if consumer in roots or consumer in affected:
                    continue
                affected.add(consumer)
                queue.append(consumer)
        affected_ids = tuple(sorted(affected))
        mcp_tools = tuple(
            f"mcp-tool:{resource_id}"
            for resource_id in affected_ids
            if graph_resources[resource_id].kind in self._MCP_TOOL_KINDS
        )
        contracts = tuple(
            resource_id
            for resource_id in affected_ids
            if graph_resources[resource_id].kind is ResourceKind.QUERY_CONTRACT
        )
        changed_revisions = {
            resource.revision_id
            for resource_id in roots
            for resource in (
                previous_by_id.get(resource_id),
                current_by_id.get(resource_id),
            )
            if resource is not None
        }
        artifacts = []
        for artifact in compiled_artifacts:
            target = artifact.get("target")
            content_hash = artifact.get("content_hash")
            input_revisions = artifact.get("input_revisions", ())
            if (
                isinstance(target, str)
                and target
                and isinstance(content_hash, str)
                and content_hash
                and isinstance(input_revisions, list | tuple)
                and changed_revisions.intersection(str(item) for item in input_revisions)
            ):
                artifacts.append(f"artifact:{target}:{content_hash}")
        affected_artifacts = tuple(sorted(set(artifacts)))
        payload = {
            "api_version": "aof.semantic-impact-report/v1",
            "added_resource_ids": list(added),
            "removed_resource_ids": list(removed),
            "changed_resource_ids": list(changed),
            "affected_resource_ids": list(affected_ids),
            "affected_mcp_tools": list(mcp_tools),
            "affected_contract_ids": list(contracts),
            "affected_artifacts": list(affected_artifacts),
        }
        return SemanticImpactReport(
            added_resource_ids=added,
            removed_resource_ids=removed,
            changed_resource_ids=changed,
            affected_resource_ids=affected_ids,
            affected_mcp_tools=mcp_tools,
            affected_contract_ids=contracts,
            affected_artifacts=affected_artifacts,
            report_digest=content_digest(payload),
        )

    @staticmethod
    def _index(
        resources: Iterable[SemanticResource], label: str
    ) -> dict[str, SemanticResource]:
        values = tuple(resources)
        by_id = {item.resource_id: item for item in values}
        if len(by_id) != len(values):
            raise SemanticImpactError(f"{label} resources contain duplicate IDs")
        return by_id
