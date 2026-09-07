# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Signed, auditable queries over released public knowledge assertions."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core.canonical import content_digest
from bridge.semantic_core.identity import SemanticPrincipal, SignedPrincipalVerifier
from bridge.semantic_core.query_runs import HmacQueryEvidenceAttestor, QueryRun, SqliteQueryRunRepository

from .public_assertions import PublicKnowledgeQueryService


class PublicKnowledgeQueryError(ValueError):
    """Raised when a public knowledge query violates its controlled boundary."""


class PublicKnowledgeQueryControl:
    """Records every public-catalog query in the normal AOF QueryRun ledger."""

    def __init__(
        self,
        *,
        query_service: PublicKnowledgeQueryService,
        verifier: SignedPrincipalVerifier,
        decision_store: DecisionProvenanceStore,
        query_runs: SqliteQueryRunRepository,
        evidence_attestor: HmacQueryEvidenceAttestor,
        allowed_purposes: set[str],
    ) -> None:
        self.query_service = query_service
        self.verifier = verifier
        self.decision_store = decision_store
        self.query_runs = query_runs
        self.evidence_attestor = evidence_attestor
        self.allowed_purposes = frozenset(allowed_purposes)

    def execute(self, payload: Mapping[str, Any], *, headers: Mapping[str, str]) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        request = self._request(payload)
        try:
            return self._execute(request, principal)
        except Exception as exc:
            self._persist_failure(request, principal, exc)
            raise

    def replay(self, payload: Mapping[str, Any], *, headers: Mapping[str, str]) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        source_id = self._required(payload, "source_query_run_id")
        source = self.query_runs.get(source_id, tenant_id=principal.tenant_id)
        if source is None or source.status != "succeeded":
            raise PublicKnowledgeQueryError("source public knowledge query run not found or not successful")
        if not self.evidence_attestor.verify(source.attestation or {}, run=source):
            raise PublicKnowledgeQueryError("source public knowledge query run attestation is invalid")
        if self._required(payload, "expected_source_digest") != source.run_digest:
            raise PublicKnowledgeQueryError("public knowledge replay source digest mismatch")
        request = dict(source.request)
        request["query_run_id"] = self._required(payload, "query_run_id")
        request["rationale"] = self._required(payload, "rationale")
        request["source_query_run_id"] = source_id
        expected = source.governed_result["result"]["result_digest"]
        try:
            return self._execute(request, principal, replay_of=source_id, expected_result_digest=expected)
        except Exception as exc:
            self._persist_failure(request, principal, exc)
            raise

    def get_run(self, query_run_id: str, *, headers: Mapping[str, str]) -> dict[str, Any]:
        principal = self.verifier.verify(headers)
        run = self.query_runs.get(query_run_id, tenant_id=principal.tenant_id)
        if run is None:
            raise PublicKnowledgeQueryError("public knowledge query run not found")
        if not self.evidence_attestor.verify(run.attestation or {}, run=run):
            raise PublicKnowledgeQueryError("public knowledge query run attestation is invalid")
        return run.to_dict()

    def _execute(self, request: dict[str, str], principal: SemanticPrincipal, *, replay_of: str | None = None, expected_result_digest: str | None = None) -> dict[str, Any]:
        if request["purpose"] not in self.allowed_purposes:
            raise PublicKnowledgeQueryError("public knowledge query purpose is not allowed")
        actor = principal.actor_for("read")
        records = self.query_service.search(tenant_id=principal.tenant_id, text=request["query"])
        snapshot_digest = content_digest(records)
        policy = {"api_version": "aof.public-knowledge-query-policy/v1", "purpose": request["purpose"], "roles": list(principal.roles), "allowed": True}
        policy_digest = content_digest(policy)
        plan = {"api_version": "aof.public-knowledge-query-plan/v1", "tenant_id": principal.tenant_id, "query": request["query"], "purpose": request["purpose"], "snapshot_digest": snapshot_digest}
        plan_digest = content_digest(plan)
        result = {"records": records, "result_digest": content_digest(records), "data_snapshot": {"snapshot_digest": snapshot_digest, "record_count": len(records)}}
        if expected_result_digest is not None and result["result_digest"] != expected_result_digest:
            raise PublicKnowledgeQueryError("strict replay result no longer matches the persisted query run")
        decision = self.decision_store.record(
            agent_id=actor, decision_type="public_knowledge_query_execute", conclusion="succeeded",
            rationale=request["rationale"], tenant_id=principal.tenant_id,
            evidence=[{"id": item["candidate_id"], "type": "published_public_assertion", "uri": item["source_url"], "content_hash": item["source_content_hash"], "metadata": {"release_digest": item["release_digest"]}} for item in records],
            policies=["policy:public-knowledge-query-v1"], tags=["public-knowledge", request["purpose"]],
            output_entities=[{"id": request["query_run_id"], "type": "public_knowledge_query_result", "content_hash": result["result_digest"]}],
            metadata={"plan_digest": plan_digest, "policy_report_digest": policy_digest, "data_snapshot": result["data_snapshot"]},
        )
        audit_trail = self.decision_store.audit_trail(decision["decision"]["id"])
        evidence_payload = {
            "api_version": "aof.query-evidence-package/v1", "request_digest": content_digest(request),
            "plan_digest": plan_digest, "policy_report_digest": policy_digest,
            "governed_result_digest": result["result_digest"], "execution_decision_id": decision["decision"]["id"],
            "artifact_evidence": [{"evidence_id": item["candidate_id"], "type": "published_public_assertion", "content_hash": item["source_content_hash"]} for item in records],
            "field_evidence": records, "audit_trail": audit_trail,
        }
        evidence_package = {**evidence_payload, "package_digest": content_digest(evidence_payload)}
        governed_result = {"api_version": "aof.public-knowledge-governed-result/v1", "result": result, "governed_result_digest": result["result_digest"], "policy_report": {**policy, "report_digest": policy_digest}}
        run = QueryRun.build(
            query_run_id=request["query_run_id"], tenant_id=principal.tenant_id, actor=actor, request=request,
            compilation_run_id="public-knowledge-catalog", compilation_run_digest=snapshot_digest,
            release_id="public-catalog", release_digest=snapshot_digest, plan_digest=plan_digest,
            policy_report_digest=policy_digest, governed_result=governed_result,
            decisions={"execution": decision["decision"]["id"]}, evidence_package=evidence_package,
            replay_of=replay_of, recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        run = run.with_attestation(self.evidence_attestor.sign(run))
        return self.query_runs.put(run).to_dict()

    def _persist_failure(self, request: dict[str, str], principal: SemanticPrincipal, error: Exception) -> None:
        decision = self.decision_store.record(
            agent_id=f"principal:{principal.subject}", decision_type="public_knowledge_query_failed", conclusion="failed",
            rationale=request["rationale"], tenant_id=principal.tenant_id,
            evidence=[{"id": content_digest(request), "type": "public_knowledge_query_request", "content_hash": content_digest(request)}],
            policies=["policy:public-knowledge-query-v1"], tags=["public-knowledge", "query-failure"],
            metadata={"error": {"type": type(error).__name__, "message": str(error)}},
        )
        run = QueryRun.build_failure(
            query_run_id=request["query_run_id"], tenant_id=principal.tenant_id, actor=f"principal:{principal.subject}",
            request=request, decision_id=decision["decision"]["id"], error={"type": type(error).__name__, "message": str(error)},
            recorded_at=datetime.now(timezone.utc).isoformat(),
        )
        self.query_runs.put(run.with_attestation(self.evidence_attestor.sign(run)))

    @staticmethod
    def _request(payload: Mapping[str, Any]) -> dict[str, str]:
        values = {name: payload.get(name) for name in ("query_run_id", "query", "purpose", "rationale")}
        if not all(isinstance(value, str) and value.strip() for value in values.values()):
            raise PublicKnowledgeQueryError("query_run_id, query, purpose, and rationale are required")
        return {name: str(value).strip() for name, value in values.items()}

    @staticmethod
    def _required(payload: Mapping[str, Any], name: str) -> str:
        value = payload.get(name)
        if not isinstance(value, str) or not value.strip():
            raise PublicKnowledgeQueryError(f"{name} is required")
        return value.strip()
