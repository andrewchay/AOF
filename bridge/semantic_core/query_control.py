"""Signed, tenant-isolated control plane for governed semantic queries."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore

from .identity import SignedPrincipalVerifier
from .models import SemanticResource
from .query_audit import AuditedQueryService
from .query_execution import QueryExecutor
from .query_plans import QueryRequest, TrustedSnapshotResolver
from .query_policy import GovernedQueryExecutor, QueryPolicy, QueryPolicyWaiver
from .compilers import CompilationRunRepository


class QueryControlPlaneError(ValueError):
    """Raised when a signed query request violates the external contract."""


class QueryControlPlane:
    """Shared zero-trust query boundary used by REST and MCP transports."""

    def __init__(
        self,
        root: str | Path,
        *,
        verifier: SignedPrincipalVerifier,
        decision_store: DecisionProvenanceStore,
    ) -> None:
        self.root = Path(root)
        self.verifier = verifier
        self.decision_store = decision_store

    def execute(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        repository = CompilationRunRepository(self.root / principal.tenant_id)
        resolver = TrustedSnapshotResolver(repository)
        request = QueryRequest.create(
            channel=self._required(payload, "channel"),
            capability=self._required(payload, "capability"),
            query=self._required(payload, "query"),
            purpose=self._required(payload, "purpose"),
            parameters=self._mapping(payload.get("parameters", {}), "parameters"),
        )
        plan = resolver.plan(request, tenant_id=principal.tenant_id)
        policy = self._load_policy(
            resolver, plan, self._required(payload, "policy_resource_id")
        )
        raw_waivers = payload.get("waivers", ())
        if not isinstance(raw_waivers, list | tuple):
            raise QueryControlPlaneError("waivers must be a list")
        waivers = tuple(self._waiver(item) for item in raw_waivers)
        result = AuditedQueryService(
            resolver=resolver,
            governed_executor=GovernedQueryExecutor(QueryExecutor(resolver), policy),
            decision_store=self.decision_store,
        ).execute(
            request,
            tenant_id=principal.tenant_id,
            actor=f"principal:{principal.subject}",
            roles=principal.roles,
            rationale=self._required(payload, "rationale"),
            waivers=waivers,
            session_id=self._optional(payload, "session_id"),
        )
        return result.to_dict()

    def _load_policy(
        self,
        resolver: TrustedSnapshotResolver,
        plan: Any,
        policy_resource_id: str,
    ) -> QueryPolicy:
        artifact = resolver.verify_artifact(plan, "semantic-json")
        path = (
            resolver.repository.root
            / "artifacts"
            / plan.run_id
            / artifact.target
            / artifact.uri
        )
        try:
            bundle = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise QueryControlPlaneError("trusted semantic policy bundle is unreadable") from exc
        resource_value = next(
            (
                item
                for item in bundle.get("resources", ())
                if isinstance(item, Mapping)
                and item.get("resource_id") == policy_resource_id
            ),
            None,
        )
        if resource_value is None:
            raise QueryControlPlaneError(
                f"query policy is not in the trusted release: {policy_resource_id}"
            )
        policy = QueryPolicy.from_resource(SemanticResource.from_dict(resource_value))
        if policy.resource_id.split("/", 3)[2] != plan.tenant_id:
            raise QueryControlPlaneError("query policy tenant does not match signed principal")
        return policy

    @staticmethod
    def _waiver(value: Any) -> QueryPolicyWaiver:
        if not isinstance(value, Mapping):
            raise QueryControlPlaneError("waivers must contain semantic objects")
        try:
            waiver = QueryPolicyWaiver.create(
                finding_id=value["finding_id"],
                policy_revision=value["policy_revision"],
                actor=value["actor"],
                rationale=value["rationale"],
                authority=value["authority"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise QueryControlPlaneError("query waiver is invalid") from exc
        supplied = value.get("waiver_id")
        if supplied is not None and supplied != waiver.waiver_id:
            raise QueryControlPlaneError("query waiver digest mismatch")
        return waiver

    @staticmethod
    def _required(payload: Mapping[str, Any], field: str) -> str:
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            raise QueryControlPlaneError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _optional(payload: Mapping[str, Any], field: str) -> str | None:
        value = payload.get(field)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise QueryControlPlaneError(f"{field} must be a non-empty string")
        return value.strip()

    @staticmethod
    def _mapping(value: Any, field: str) -> Mapping[str, Any]:
        if not isinstance(value, Mapping):
            raise QueryControlPlaneError(f"{field} must be an object")
        return value
