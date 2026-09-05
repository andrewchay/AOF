"""Trusted query requests and plans pinned to verified runtime snapshots."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from .canonical import canonical_data, content_digest
from .compilers import CompilationRunRepository
from .semantic_query import SemanticIntent, SemanticQueryCompileError


class TrustedQueryError(ValueError):
    """Raised when a query cannot be pinned to a trusted runtime snapshot."""


class QueryCapability(str, Enum):
    SEMANTIC_SEARCH = "semantic_search"
    SEMANTIC_SQL = "semantic_sql"
    GRAPH = "graph"
    DATALOG = "datalog"
    SPARQL = "sparql"
    QUERY_TEMPLATE = "query_template"


_CAPABILITY_TARGETS = {
    QueryCapability.SEMANTIC_SEARCH: ("rag",),
    QueryCapability.SEMANTIC_SQL: ("semantic-json",),
    QueryCapability.GRAPH: ("owl",),
    QueryCapability.DATALOG: ("datalog",),
    QueryCapability.SPARQL: ("owl",),
    QueryCapability.QUERY_TEMPLATE: ("semantic-json", "mcp"),
}


def _non_empty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise TrustedQueryError(f"{field} must be a non-empty string")
    return value.strip()


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _file_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return f"sha256:{digest.hexdigest()}"


@dataclass(frozen=True)
class QueryRequest:
    channel: str
    capability: QueryCapability
    query: str
    purpose: str
    parameters: Mapping[str, Any]
    request_digest: str

    @classmethod
    def create(
        cls,
        *,
        channel: str,
        capability: QueryCapability | str,
        query: str,
        purpose: str,
        parameters: Mapping[str, Any] | None = None,
    ) -> "QueryRequest":
        try:
            normalized_capability = (
                capability if isinstance(capability, QueryCapability) else QueryCapability(capability)
            )
        except ValueError as exc:
            raise TrustedQueryError(f"unsupported query capability: {capability}") from exc
        payload = {
            "api_version": "aof.query-request/v1",
            "channel": _non_empty(channel, "channel"),
            "capability": normalized_capability.value,
            "query": _non_empty(query, "query"),
            "purpose": _non_empty(purpose, "purpose"),
            "parameters": canonical_data(dict(parameters or {})),
        }
        return cls(
            channel=payload["channel"],
            capability=normalized_capability,
            query=payload["query"],
            purpose=payload["purpose"],
            parameters=_freeze(payload["parameters"]),
            request_digest=content_digest(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.query-request/v1",
            "channel": self.channel,
            "capability": self.capability.value,
            "query": self.query,
            "purpose": self.purpose,
            "parameters": canonical_data(self.parameters),
            "request_digest": self.request_digest,
        }


@dataclass(frozen=True)
class QueryArtifactRef:
    target: str
    compiler: str
    uri: str
    media_type: str
    content_hash: str
    release_digest: str
    input_revisions: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "target": self.target,
            "compiler": self.compiler,
            "uri": self.uri,
            "media_type": self.media_type,
            "content_hash": self.content_hash,
            "release_digest": self.release_digest,
            "input_revisions": list(self.input_revisions),
        }


@dataclass(frozen=True)
class QueryPlan:
    tenant_id: str
    channel: str
    channel_version: int
    channel_pointer_digest: str
    request_digest: str
    capability: QueryCapability
    query: str
    purpose: str
    parameters: Mapping[str, Any]
    run_id: str
    run_digest: str
    release_id: str
    release_digest: str
    compiler_policy_resource_id: str
    compiler_policy_revision: str
    resolved_resource_ids: tuple[str, ...]
    artifacts: tuple[QueryArtifactRef, ...]
    plan_digest: str

    def _payload(self) -> dict[str, Any]:
        return {
            "api_version": "aof.query-plan/v1",
            "tenant_id": self.tenant_id,
            "channel": self.channel,
            "channel_version": self.channel_version,
            "channel_pointer_digest": self.channel_pointer_digest,
            "request_digest": self.request_digest,
            "capability": self.capability.value,
            "query": self.query,
            "purpose": self.purpose,
            "parameters": canonical_data(self.parameters),
            "run_id": self.run_id,
            "run_digest": self.run_digest,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "compiler_policy_resource_id": self.compiler_policy_resource_id,
            "compiler_policy_revision": self.compiler_policy_revision,
            "resolved_resource_ids": list(self.resolved_resource_ids),
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
        }

    def verify(self) -> bool:
        return self.plan_digest == content_digest(self._payload())

    def to_dict(self) -> dict[str, Any]:
        return {**self._payload(), "plan_digest": self.plan_digest}


class TrustedSnapshotResolver:
    """Resolve a channel into an immutable, byte-verified query plan."""

    def __init__(self, repository: CompilationRunRepository) -> None:
        self.repository = repository

    def plan(self, request: QueryRequest, *, tenant_id: str) -> QueryPlan:
        tenant_id = _non_empty(tenant_id, "tenant_id")
        pointer = self.repository.get_channel(request.channel)
        if pointer is None:
            raise TrustedQueryError(f"compilation channel not found: {request.channel}")
        self._verify_pointer(pointer, request.channel)
        run = self.repository.get(str(pointer["run_id"]))
        if run is None:
            raise TrustedQueryError(f"compilation run not found: {pointer['run_id']}")
        if run.tenant_id != tenant_id:
            raise TrustedQueryError("compilation run tenant does not match query tenant")
        if not run.reproducible:
            raise TrustedQueryError("query snapshot requires an independently reproducible run")
        artifacts_by_target = {str(item.get("target", "")): item for item in run.artifacts}
        if len(artifacts_by_target) != len(run.artifacts):
            raise TrustedQueryError("compilation run contains duplicate artifact targets")
        required_targets = _CAPABILITY_TARGETS[request.capability]
        if request.capability is QueryCapability.SEMANTIC_SEARCH and request.parameters.get('retrieval_mode') == 'skill':
            required_targets = ('semantic-json',)
        missing = [target for target in required_targets if target not in artifacts_by_target]
        if missing:
            raise TrustedQueryError(
                f"query capability requires missing artifact targets: {', '.join(missing)}"
            )
        artifacts = tuple(
            self._verify_artifact(run, artifacts_by_target[target]) for target in required_targets
        )
        for key, actual in (("expected_release_id", run.release_id), ("expected_release_digest", run.release_digest)):
            if key in request.parameters and request.parameters[key] != actual:
                raise TrustedQueryError("query resolved a different trusted release snapshot")
        if request.capability is QueryCapability.SEMANTIC_SQL and request.parameters.get("natural_language"):
            from .natural_query import ground_intent

            semantic = self._artifact_payload(run, artifacts[0])
            intent = ground_intent(request.query, semantic.get("resources", ()), request.purpose)
            parameters = dict(request.parameters)
            parameters.pop("natural_language")
            parameters["intent"] = intent.to_dict()
            return self.plan(QueryRequest.create(channel=request.channel, capability=request.capability,
                query=intent.intent_digest, purpose=request.purpose, parameters=parameters), tenant_id=tenant_id)
        resolved_resource_ids = self._resolved_resource_ids(request, run, artifacts)
        payload = {
            "api_version": "aof.query-plan/v1",
            "tenant_id": tenant_id,
            "channel": request.channel,
            "channel_version": int(pointer["version"]),
            "channel_pointer_digest": str(pointer["pointer_digest"]),
            "request_digest": request.request_digest,
            "capability": request.capability.value,
            "query": request.query,
            "purpose": request.purpose,
            "parameters": canonical_data(request.parameters),
            "run_id": run.run_id,
            "run_digest": run.run_digest,
            "release_id": run.release_id,
            "release_digest": run.release_digest,
            "compiler_policy_resource_id": run.policy_resource_id,
            "compiler_policy_revision": run.policy_revision,
            "resolved_resource_ids": list(resolved_resource_ids),
            "artifacts": [artifact.to_dict() for artifact in artifacts],
        }
        return QueryPlan(
            tenant_id=tenant_id,
            channel=request.channel,
            channel_version=int(pointer["version"]),
            channel_pointer_digest=str(pointer["pointer_digest"]),
            request_digest=request.request_digest,
            capability=request.capability,
            query=request.query,
            purpose=request.purpose,
            parameters=_freeze(request.parameters),
            run_id=run.run_id,
            run_digest=run.run_digest,
            release_id=run.release_id,
            release_digest=run.release_digest,
            compiler_policy_resource_id=run.policy_resource_id,
            compiler_policy_revision=run.policy_revision,
            resolved_resource_ids=resolved_resource_ids,
            artifacts=artifacts,
            plan_digest=content_digest(payload),
        )

    def _resolved_resource_ids(
        self,
        request: QueryRequest,
        run: Any,
        artifacts: tuple[QueryArtifactRef, ...],
    ) -> tuple[str, ...]:
        payloads = {
            artifact.target: self._artifact_payload(run, artifact) for artifact in artifacts
        }
        resources = [
            item
            for payload in payloads.values()
            for item in payload.get("resources", ())
            if isinstance(item, Mapping) and isinstance(item.get("resource_id"), str)
        ]
        by_id = {str(item["resource_id"]): item for item in resources}
        selected: set[str] = set()
        if request.capability is QueryCapability.SEMANTIC_SQL:
            raw_intent = request.parameters.get("intent")
            if not isinstance(raw_intent, Mapping):
                raise TrustedQueryError("semantic_sql requires a typed intent object")
            try:
                intent = SemanticIntent.from_dict(raw_intent)
            except SemanticQueryCompileError as exc:
                raise TrustedQueryError(str(exc)) from exc
            selected.update(intent.metrics)
            selected.update(intent.dimensions)
            selected.update(item.dimension for item in intent.filters)
        elif request.capability is QueryCapability.QUERY_TEMPLATE:
            matches = [
                resource_id
                for resource_id, item in by_id.items()
                if item.get("kind") == "QueryTemplate"
                and (resource_id == request.query or item.get("name") == request.query)
            ]
            if len(matches) != 1:
                raise TrustedQueryError(
                    f"governed query template resolution is ambiguous or missing: {request.query}"
                )
            selected.add(matches[0])
        else:
            selected.update(by_id)
        queue = list(selected)
        while queue:
            resource_id = queue.pop()
            resource = by_id.get(resource_id)
            if resource is None:
                raise TrustedQueryError(
                    f"query references a resource absent from the trusted artifact: {resource_id}"
                )
            dependencies = resource.get("depends_on", ())
            if not isinstance(dependencies, list | tuple):
                raise TrustedQueryError("compiled semantic resource has invalid depends_on")
            for dependency in dependencies:
                dependency_id = str(dependency)
                if dependency_id not in selected:
                    selected.add(dependency_id)
                    queue.append(dependency_id)
        return tuple(sorted(selected))

    def _artifact_payload(
        self, run: Any, artifact: QueryArtifactRef
    ) -> Mapping[str, Any]:
        path = (
            self.repository.root
            / "artifacts"
            / run.run_id
            / artifact.target
            / artifact.uri
        )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise TrustedQueryError(
                f"query artifact is not valid JSON: {artifact.target}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise TrustedQueryError(
                f"query artifact must contain a semantic object: {artifact.target}"
            )
        return payload

    def verify_artifact(self, plan: QueryPlan, target: str) -> QueryArtifactRef:
        """Verify a supplemental artifact from the exact run pinned by a query plan."""
        if not plan.verify():
            raise TrustedQueryError("query plan digest mismatch")
        run = self.repository.get(plan.run_id)
        if run is None:
            raise TrustedQueryError(f"compilation run not found: {plan.run_id}")
        if (
            run.tenant_id != plan.tenant_id
            or run.run_digest != plan.run_digest
            or run.release_digest != plan.release_digest
        ):
            raise TrustedQueryError("query plan does not match its compilation run")
        artifact = next(
            (item for item in run.artifacts if item.get("target") == target), None
        )
        if artifact is None:
            raise TrustedQueryError(
                f"trusted query boundary requires missing artifact target: {target}"
            )
        return self._verify_artifact(run, artifact)

    @staticmethod
    def _verify_pointer(pointer: Mapping[str, Any], channel: str) -> None:
        if pointer.get("api_version") != "aof.compilation-channel/v1":
            raise TrustedQueryError("unsupported compilation channel api_version")
        history = pointer.get("history")
        if not isinstance(history, list | tuple) or not history:
            raise TrustedQueryError("compilation channel history is empty")
        if pointer.get("channel") != channel:
            raise TrustedQueryError("compilation channel identity mismatch")
        if pointer.get("version") != len(history):
            raise TrustedQueryError("compilation channel version does not match history")
        previous_run_id = None
        for event in history:
            event_payload = {key: canonical_data(value) for key, value in event.items() if key != "event_digest"}
            if event.get("event_digest") != content_digest(event_payload):
                raise TrustedQueryError("compilation channel event digest mismatch")
            if event.get("previous_run_id") != previous_run_id:
                raise TrustedQueryError("compilation channel history is not contiguous")
            previous_run_id = event.get("run_id")
        if pointer.get("run_id") != previous_run_id:
            raise TrustedQueryError("compilation channel head does not match history")

    def _verify_artifact(self, run: Any, value: Mapping[str, Any]) -> QueryArtifactRef:
        target = _non_empty(str(value.get("target", "")), "artifact target")
        compiler = _non_empty(str(value.get("compiler", "")), "artifact compiler")
        if run.compiler_lock.get(target) != compiler:
            raise TrustedQueryError(f"artifact compiler does not match run lock: {target}")
        if value.get("release_digest") != run.release_digest:
            raise TrustedQueryError(f"artifact release digest does not match run: {target}")
        uri = _non_empty(str(value.get("uri", "")), "artifact uri")
        root = (self.repository.root / "artifacts" / run.run_id / target).resolve()
        path = (root / uri).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise TrustedQueryError(f"artifact uri escapes query snapshot: {target}") from exc
        if not path.is_file():
            raise TrustedQueryError(f"query artifact is missing: {target}")
        content_hash = _non_empty(str(value.get("content_hash", "")), "artifact content_hash")
        if _file_digest(path) != content_hash:
            raise TrustedQueryError(f"query artifact content hash mismatch: {target}")
        return QueryArtifactRef(
            target=target,
            compiler=compiler,
            uri=uri,
            media_type=_non_empty(str(value.get("media_type", "")), "artifact media_type"),
            content_hash=content_hash,
            release_digest=run.release_digest,
            input_revisions=tuple(str(item) for item in value.get("input_revisions", ())),
        )
