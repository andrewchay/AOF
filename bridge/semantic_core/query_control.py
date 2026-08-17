"""Signed, tenant-isolated control plane for governed semantic queries."""

from __future__ import annotations

import json
import hashlib
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore

from .canonical import canonical_data, content_digest
from .identity import SemanticPrincipal, SignedPrincipalVerifier
from .models import SemanticResource
from .query_audit import AuditedQueryService
from .query_execution import QueryExecutor
from .query_plans import QueryRequest, TrustedQueryError, TrustedSnapshotResolver
from .query_policy import GovernedQueryExecutor, QueryPolicy, QueryPolicyWaiver
from .query_runs import (
    HmacQueryEvidenceAttestor,
    QueryRun,
    QueryRunError,
    SqliteQueryRunRepository,
)
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
        query_runs: SqliteQueryRunRepository | None = None,
        evidence_attestor: HmacQueryEvidenceAttestor | None = None,
        executor_factory: Callable[[TrustedSnapshotResolver], QueryExecutor] | None = None,
    ) -> None:
        self.root = Path(root)
        self.verifier = verifier
        self.decision_store = decision_store
        self.query_runs = query_runs or SqliteQueryRunRepository(
            self.root / "query-runs.sqlite3"
        )
        self.evidence_attestor = evidence_attestor or HmacQueryEvidenceAttestor(
            key_id=f"{verifier.key_id}:query-evidence",
            secret=hashlib.sha256(b"aof-query-evidence\0" + verifier.secret).digest(),
        )
        self.executor_factory = executor_factory or QueryExecutor

    def execute(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        normalized = dict(payload)
        normalized["query_run_id"] = self._query_run_id(payload)
        try:
            return self._execute(normalized, principal=principal, replay_of=None)
        except Exception as exc:
            try:
                self._persist_failure(normalized, principal=principal, error=exc)
            except Exception as audit_exc:
                raise QueryControlPlaneError(
                    f"{exc}; failure audit persistence failed: {audit_exc}"
                ) from exc
            raise

    def _execute(
        self,
        payload: Mapping[str, Any],
        *,
        principal: SemanticPrincipal,
        replay_of: str | None,
        expected_governed_result_digest: str | None = None,
    ) -> dict[str, Any]:
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
            governed_executor=GovernedQueryExecutor(self.executor_factory(resolver), policy),
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
        result_value = result.to_dict()
        if (
            expected_governed_result_digest is not None
            and result.governed_result.governed_result_digest
            != expected_governed_result_digest
        ):
            raise QueryControlPlaneError(
                "strict replay result no longer matches the persisted query run"
            )
        query_result = result.governed_result.result
        request_value = self._request_value(payload)
        query_run = QueryRun.build(
            query_run_id=self._query_run_id(payload),
            tenant_id=principal.tenant_id,
            actor=f"principal:{principal.subject}",
            request=request_value,
            compilation_run_id=query_result.run_id,
            compilation_run_digest=query_result.run_digest,
            release_id=query_result.release_id,
            release_digest=query_result.release_digest,
            plan_digest=query_result.plan_digest,
            policy_report_digest=result.governed_result.policy_report.report_digest,
            governed_result=result_value["governed_result"],
            decisions=result_value["decisions"],
            evidence_package=result_value["evidence_package"],
            replay_of=replay_of,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        query_run = query_run.with_attestation(self.evidence_attestor.sign(query_run))
        return self.query_runs.put(query_run).to_dict()

    def _persist_failure(
        self,
        payload: Mapping[str, Any],
        *,
        principal: SemanticPrincipal,
        error: Exception,
    ) -> None:
        request_value = self._request_value(payload)
        request_digest = content_digest(request_value)
        error_value = {"type": type(error).__name__, "message": str(error)}
        decision_id = self.decision_store.record(
            agent_id=f"principal:{principal.subject}",
            decision_type="semantic_query_failed",
            conclusion="failed",
            rationale=str(payload.get("rationale") or "Governed query failed."),
            evidence=[
                {
                    "id": request_digest,
                    "type": "query_request",
                    "content_hash": request_digest,
                }
            ],
            output_entities=[
                {
                    "id": str(payload["query_run_id"]),
                    "type": "failed_query_run",
                }
            ],
            tags=[
                str(payload.get("capability", "unknown")),
                str(payload.get("purpose", "unknown")),
                "query-failure",
            ],
            tenant_id=principal.tenant_id,
            metadata={"error": error_value},
        )["decision"]["id"]
        run = QueryRun.build_failure(
            query_run_id=str(payload["query_run_id"]),
            tenant_id=principal.tenant_id,
            actor=f"principal:{principal.subject}",
            request=request_value,
            decision_id=decision_id,
            error=error_value,
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        run = run.with_attestation(self.evidence_attestor.sign(run))
        self.query_runs.put(run)

    @staticmethod
    def _request_value(payload: Mapping[str, Any]) -> dict[str, Any]:
        return canonical_data(
            {
                "channel": payload.get("channel"),
                "capability": payload.get("capability"),
                "query": payload.get("query"),
                "purpose": payload.get("purpose"),
                "parameters": payload.get("parameters", {}),
                "policy_resource_id": payload.get("policy_resource_id"),
                "waivers": payload.get("waivers", []),
            }
        )

    def get_run(
        self, query_run_id: str, *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        run = self.query_runs.get(query_run_id, tenant_id=principal.tenant_id)
        if run is None:
            raise QueryControlPlaneError(f"query run not found: {query_run_id}")
        self._verify_attestation(run)
        return run.to_dict()

    def replay(
        self, payload: Mapping[str, Any], *, headers: Mapping[str, str]
    ) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        source_id = self._required(payload, "source_query_run_id")
        source = self.query_runs.get(source_id, tenant_id=principal.tenant_id)
        if source is None:
            raise QueryControlPlaneError(f"query run not found: {source_id}")
        expected = self._required(payload, "expected_source_digest")
        if expected != source.run_digest:
            raise QueryControlPlaneError("query replay source digest mismatch")
        self._verify_attestation(source)
        repository = CompilationRunRepository(self.root / principal.tenant_id)
        resolver = TrustedSnapshotResolver(repository)
        request = QueryRequest.create(
            channel=str(source.request["channel"]),
            capability=str(source.request["capability"]),
            query=str(source.request["query"]),
            purpose=str(source.request["purpose"]),
            parameters=self._mapping(source.request.get("parameters", {}), "parameters"),
        )
        try:
            current = resolver.plan(request, tenant_id=principal.tenant_id)
        except TrustedQueryError as exc:
            raise QueryControlPlaneError(
                "strict replay snapshot no longer matches the persisted query run"
            ) from exc
        if (
            current.plan_digest != source.plan_digest
            or current.run_id != source.compilation_run_id
            or current.run_digest != source.compilation_run_digest
            or current.release_digest != source.release_digest
        ):
            raise QueryControlPlaneError(
                "strict replay snapshot no longer matches the persisted query run"
            )
        replay_payload = {
            **canonical_data(source.request),
            "query_run_id": self._required(payload, "query_run_id"),
            "rationale": self._required(payload, "rationale"),
            "session_id": self._optional(payload, "session_id"),
        }
        return self._execute(
            replay_payload,
            principal=principal,
            replay_of=source_id,
            expected_governed_result_digest=str(
                source.governed_result.get("governed_result_digest", "")
            ),
        )

    def _verify_attestation(self, run: QueryRun) -> None:
        if run.attestation is None or not self.evidence_attestor.verify(
            run.attestation, run=run
        ):
            raise QueryRunError("query evidence attestation is invalid")

    @staticmethod
    def _query_run_id(payload: Mapping[str, Any]) -> str:
        value = payload.get("query_run_id")
        if value is None:
            return f"query-{uuid.uuid4().hex}"
        if not isinstance(value, str) or not value.strip():
            raise QueryControlPlaneError("query_run_id must be a non-empty string")
        return value.strip()

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
