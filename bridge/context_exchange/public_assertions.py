"""Evidence-bound public assertions and their governed public release path."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core import ResourceKind, SemanticGovernanceService, SemanticResource
from bridge.semantic_core.canonical import canonical_json, content_digest

from .contracts import ApprovalDecision, ContextExchangeError, ContextVisibility, required_approval_roles
from .public_ingest import PublicIngestError, PublicSourceRecord, PublicSourceRights, require_redistributable


class PublicAssertionError(ContextExchangeError):
    """Raised when a public claim lacks source, rights, or review evidence."""


@dataclass(frozen=True)
class PublicAssertionCandidate:
    candidate_id: str
    tenant_id: str
    submitted_by: str
    subject_key: str
    statement: str
    confidence: float
    source: PublicSourceRecord
    valid_time: Mapping[str, Any]
    candidate_digest: str

    @classmethod
    def create(
        cls,
        *,
        candidate_id: str,
        tenant_id: str,
        submitted_by: str,
        subject_key: str,
        statement: str,
        confidence: float,
        source: PublicSourceRecord,
        valid_time: Mapping[str, Any] | None = None,
    ) -> "PublicAssertionCandidate":
        if not all(isinstance(item, str) and item.strip() for item in (candidate_id, tenant_id, submitted_by, subject_key, statement)):
            raise PublicAssertionError("candidate identity and statement must be non-empty strings")
        if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
            raise PublicAssertionError("confidence must be between 0 and 1")
        payload = {
            "api_version": "aof.public-assertion-candidate/v1", "candidate_id": candidate_id.strip(),
            "tenant_id": tenant_id.strip(), "submitted_by": submitted_by.strip(), "subject_key": subject_key.strip(), "statement": statement.strip(),
            "confidence": float(confidence), "source": source.to_dict(), "valid_time": dict(valid_time or {}),
        }
        return cls(
            candidate_id=payload["candidate_id"], tenant_id=payload["tenant_id"], submitted_by=payload["submitted_by"], subject_key=payload["subject_key"],
            statement=payload["statement"], confidence=payload["confidence"], source=source,
            valid_time=payload["valid_time"], candidate_digest=content_digest(payload),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.public-assertion-candidate/v1", "candidate_id": self.candidate_id,
            "tenant_id": self.tenant_id, "submitted_by": self.submitted_by, "subject_key": self.subject_key, "statement": self.statement,
            "confidence": self.confidence, "source": self.source.to_dict(), "valid_time": dict(self.valid_time),
            "candidate_digest": self.candidate_digest,
        }


class SqlitePublicAssertionRepository:
    """Immutable candidate store; publication belongs to AOF release governance."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS public_assertion_candidates (
                    tenant_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    candidate_digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    intake_decision_id TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, candidate_id)
                )
                """
            )

    def put(self, candidate: PublicAssertionCandidate, *, intake_decision_id: str) -> PublicAssertionCandidate:
        payload = canonical_json(candidate.to_dict())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT candidate_digest FROM public_assertion_candidates WHERE tenant_id = ? AND candidate_id = ?",
                (candidate.tenant_id, candidate.candidate_id),
            ).fetchone()
            if row is not None:
                if row[0] != candidate.candidate_digest:
                    raise PublicAssertionError("public assertion candidate cannot be overwritten")
                return candidate
            connection.execute(
                "INSERT INTO public_assertion_candidates (tenant_id, candidate_id, candidate_digest, payload, intake_decision_id) VALUES (?, ?, ?, ?, ?)",
                (candidate.tenant_id, candidate.candidate_id, candidate.candidate_digest, payload, intake_decision_id),
            )
        return candidate

    def get(self, candidate_id: str, *, tenant_id: str) -> tuple[PublicAssertionCandidate, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT candidate_digest, payload, intake_decision_id FROM public_assertion_candidates WHERE tenant_id = ? AND candidate_id = ?",
                (tenant_id, candidate_id),
            ).fetchone()
        if row is None:
            return None
        candidate = _candidate_from_dict(json.loads(row[1]))
        if candidate.candidate_digest != row[0]:
            raise PublicAssertionError("stored public assertion candidate integrity check failed")
        return candidate, row[2]

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


@dataclass(frozen=True)
class PublishedPublicAssertion:
    tenant_id: str
    candidate_id: str
    subject_key: str
    statement: str
    confidence: float
    source_url: str
    source_content_hash: str
    release_id: str
    release_digest: str
    valid_time: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id, "candidate_id": self.candidate_id, "subject_key": self.subject_key,
            "statement": self.statement, "confidence": self.confidence, "source_url": self.source_url,
            "source_content_hash": self.source_content_hash, "release_id": self.release_id,
            "release_digest": self.release_digest, "valid_time": dict(self.valid_time),
        }


@dataclass(frozen=True)
class PublicAssertionRevocation:
    tenant_id: str
    candidate_id: str
    reason: str
    decision_id: str
    superseded_by: str | None = None


class SqlitePublicKnowledgeRepository:
    """A query index containing only assertions that reached a public release."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS published_public_assertions (
                    tenant_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    subject_key TEXT NOT NULL,
                    statement TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, candidate_id)
                )
                """
            )
            connection.execute("CREATE INDEX IF NOT EXISTS idx_public_assertion_subject ON published_public_assertions (tenant_id, subject_key)")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS public_assertion_revocations (
                    tenant_id TEXT NOT NULL,
                    candidate_id TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    decision_id TEXT NOT NULL,
                    superseded_by TEXT,
                    PRIMARY KEY (tenant_id, candidate_id)
                )
                """
            )

    def put(self, value: PublishedPublicAssertion) -> PublishedPublicAssertion:
        payload = canonical_json(value.to_dict())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM published_public_assertions WHERE tenant_id = ? AND candidate_id = ?",
                (value.tenant_id, value.candidate_id),
            ).fetchone()
            if row is not None:
                current = self._from_payload(row[0])
                if current != value:
                    raise PublicAssertionError("published public assertion cannot be overwritten")
                return current
            connection.execute(
                "INSERT INTO published_public_assertions (tenant_id, candidate_id, subject_key, statement, payload) VALUES (?, ?, ?, ?, ?)",
                (value.tenant_id, value.candidate_id, value.subject_key, value.statement, payload),
            )
        return value

    def conflicts(self, *, tenant_id: str, subject_key: str, statement: str) -> tuple[PublishedPublicAssertion, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT item.payload FROM published_public_assertions item WHERE item.tenant_id = ? AND item.subject_key = ? "
                "AND item.statement != ? AND NOT EXISTS (SELECT 1 FROM public_assertion_revocations rev "
                "WHERE rev.tenant_id = item.tenant_id AND rev.candidate_id = item.candidate_id) ORDER BY item.candidate_id",
                (tenant_id, subject_key, statement),
            ).fetchall()
        return tuple(self._from_payload(row[0]) for row in rows)

    def revoke(self, value: PublicAssertionRevocation) -> PublicAssertionRevocation:
        with self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM published_public_assertions WHERE tenant_id = ? AND candidate_id = ?",
                (value.tenant_id, value.candidate_id),
            ).fetchone()
            if exists is None:
                raise PublicAssertionError("published public assertion not found")
            row = connection.execute(
                "SELECT reason, decision_id, superseded_by FROM public_assertion_revocations WHERE tenant_id = ? AND candidate_id = ?",
                (value.tenant_id, value.candidate_id),
            ).fetchone()
            if row is not None:
                current = PublicAssertionRevocation(value.tenant_id, value.candidate_id, row[0], row[1], row[2])
                if current != value:
                    raise PublicAssertionError("public assertion revocation cannot be overwritten")
                return current
            connection.execute(
                "INSERT INTO public_assertion_revocations (tenant_id, candidate_id, reason, decision_id, superseded_by) VALUES (?, ?, ?, ?, ?)",
                (value.tenant_id, value.candidate_id, value.reason, value.decision_id, value.superseded_by),
            )
        return value

    def search(self, *, tenant_id: str, text: str) -> tuple[PublishedPublicAssertion, ...]:
        terms = [item.casefold() for item in text.split() if item.strip()]
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT item.payload FROM published_public_assertions item WHERE item.tenant_id = ? "
                "AND NOT EXISTS (SELECT 1 FROM public_assertion_revocations rev "
                "WHERE rev.tenant_id = item.tenant_id AND rev.candidate_id = item.candidate_id) ORDER BY item.candidate_id",
                (tenant_id,),
            ).fetchall()
        values = tuple(self._from_payload(row[0]) for row in rows)
        if not terms:
            return values
        return tuple(item for item in values if all(term in f"{item.subject_key} {item.statement}".casefold() for term in terms))

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _from_payload(payload: str) -> PublishedPublicAssertion:
        value = json.loads(payload)
        return PublishedPublicAssertion(
            tenant_id=value["tenant_id"], candidate_id=value["candidate_id"], subject_key=value["subject_key"],
            statement=value["statement"], confidence=value["confidence"], source_url=value["source_url"],
            source_content_hash=value["source_content_hash"], release_id=value["release_id"],
            release_digest=value["release_digest"], valid_time=value["valid_time"],
        )


class PublicKnowledgeQueryService:
    """Returns only released public assertions together with their evidence identity."""

    def __init__(self, repository: SqlitePublicKnowledgeRepository) -> None:
        self.repository = repository

    def search(self, *, tenant_id: str, text: str) -> list[dict[str, Any]]:
        return [item.to_dict() for item in self.repository.search(tenant_id=tenant_id, text=text)]


class PublicKnowledgeRevocationService:
    """Appends a revocation decision and removes an assertion only from new reads."""

    def __init__(self, *, repository: SqlitePublicKnowledgeRepository, decision_store: DecisionProvenanceStore) -> None:
        self.repository = repository
        self.decision_store = decision_store

    def revoke(self, *, tenant_id: str, candidate_id: str, actor: str, rationale: str, superseded_by: str | None = None) -> PublicAssertionRevocation:
        entry = self.decision_store.record(
            agent_id=actor, decision_type="public_assertion_revoked", conclusion=f"revoked {candidate_id}",
            rationale=rationale, tenant_id=tenant_id,
            evidence=[{"id": candidate_id, "type": "published_public_assertion"}],
            policies=["policy:public-assertion-revocation-v1"], tags=["public-source", "revocation"],
            metadata={"superseded_by": superseded_by},
        )
        return self.repository.revoke(
            PublicAssertionRevocation(tenant_id, candidate_id, rationale, entry["decision"]["id"], superseded_by)
        )


class PublicAssertionService:
    """Creates candidates, validates rights, and publishes via AOF governance."""

    def __init__(self, *, repository: SqlitePublicAssertionRepository, knowledge_repository: SqlitePublicKnowledgeRepository, governance: SemanticGovernanceService, decision_store: DecisionProvenanceStore) -> None:
        self.repository = repository
        self.knowledge_repository = knowledge_repository
        self.governance = governance
        self.decision_store = decision_store

    def intake(self, candidate: PublicAssertionCandidate, *, actor: str, rationale: str) -> PublicAssertionCandidate:
        current = self.repository.get(candidate.candidate_id, tenant_id=candidate.tenant_id)
        if current is not None:
            if current[0].candidate_digest != candidate.candidate_digest:
                raise PublicAssertionError("public assertion candidate cannot be overwritten")
            return current[0]
        decision = self.decision_store.record(
            agent_id=actor, decision_type="public_assertion_candidate_created",
            conclusion=f"created public assertion candidate {candidate.candidate_id}", rationale=rationale,
            tenant_id=candidate.tenant_id,
            evidence=[{"id": candidate.source.source_id, "type": "public_source", "uri": candidate.source.source_url, "content_hash": candidate.source.content_hash, "metadata": {"rights": candidate.source.rights.to_dict()}}],
            policies=["policy:public-assertion-candidate-v1"], tags=["public-source", "candidate"],
            output_entities=[{"id": candidate.candidate_id, "type": "public_assertion_candidate", "content_hash": candidate.candidate_digest}],
        )
        return self.repository.put(candidate, intake_decision_id=decision["decision"]["id"])

    def publish(
        self,
        *,
        candidate_id: str,
        tenant_id: str,
        proposal_id: str,
        release_id: str,
        approvals: Iterable[ApprovalDecision],
        rationale: str,
    ) -> dict[str, Any]:
        stored = self.repository.get(candidate_id, tenant_id=tenant_id)
        if stored is None:
            raise PublicAssertionError("public assertion candidate not found")
        candidate, intake_decision_id = stored
        try:
            require_redistributable(candidate.source)
        except PublicIngestError as exc:
            raise PublicAssertionError(str(exc)) from exc
        conflicts = self.knowledge_repository.conflicts(tenant_id=tenant_id, subject_key=candidate.subject_key, statement=candidate.statement)
        if conflicts:
            self.decision_store.record(
                agent_id="validator:public-conflict", decision_type="public_assertion_conflict_detected",
                conclusion=f"publication blocked by {len(conflicts)} conflicting released assertion(s)", rationale=rationale,
                parent_decision_ids=[intake_decision_id], tenant_id=tenant_id,
                evidence=[{"id": item.candidate_id, "type": "published_public_assertion", "content_hash": item.release_digest} for item in conflicts],
                policies=["policy:public-assertion-conflict-v1"], tags=["public-source", "conflict"],
                metadata={"candidate_id": candidate.candidate_id, "subject_key": candidate.subject_key},
            )
            raise PublicAssertionError("public assertion conflicts with an existing released assertion")
        approval_values = tuple(approvals)
        _validate_public_approvals(candidate, approval_values)
        review_ids = self._record_reviews(candidate, intake_decision_id, approval_values, rationale)
        resource = _resource_for(candidate)
        proposal = self.governance.create_proposal(
            proposal_id=proposal_id, release_id=release_id, resources=[resource], actor=f"editor:{candidate.submitted_by}",
            rationale=rationale,
            scope={"tenant_id": tenant_id, "visibility": "public-governed", "source_rights": candidate.source.rights.to_dict(), "context_review_decision_ids": review_ids},
        )
        by_role = {item.role: item for item in approval_values if item.conclusion == "approved"}
        self.governance.validate(proposal["proposal_id"], actor=f"validator:{by_role['privacy-reviewer'].actor}")
        self.governance.approve(proposal["proposal_id"], actor=f"reviewer:{by_role['domain-approver'].actor}", rationale=rationale)
        self.governance.compile(proposal["proposal_id"], actor="compiler:public-source", targets=["semantic-json"])
        published = self.governance.publish(proposal["proposal_id"], actor=f"publisher:{by_role['publisher'].actor}")
        release = published["release"]
        self.knowledge_repository.put(
            PublishedPublicAssertion(
                tenant_id=tenant_id, candidate_id=candidate.candidate_id, subject_key=candidate.subject_key,
                statement=candidate.statement, confidence=candidate.confidence, source_url=candidate.source.source_url,
                source_content_hash=candidate.source.content_hash, release_id=release["release_id"],
                release_digest=release["release_digest"], valid_time=candidate.valid_time,
            )
        )
        return published

    def _record_reviews(self, candidate: PublicAssertionCandidate, parent: str, approvals: tuple[ApprovalDecision, ...], rationale: str) -> list[str]:
        by_role = {item.role: item for item in approvals if item.conclusion == "approved"}
        parents = [parent]
        result = []
        for role in sorted(required_approval_roles(ContextVisibility.PUBLIC_GOVERNED)):
            approval = by_role[role]
            entry = self.decision_store.record(
                agent_id=f"{role}:{approval.actor}", decision_type=f"public_assertion_{role}_approval",
                conclusion=f"approved public assertion {candidate.candidate_id}", rationale=rationale,
                parent_decision_ids=parents, tenant_id=candidate.tenant_id,
                evidence=[{"id": candidate.candidate_id, "type": "public_assertion_candidate", "content_hash": candidate.candidate_digest}],
                policies=["policy:public-assertion-publication-v1"], tags=["public-source", "approval", role],
                metadata={"external_approval_id": approval.decision_id},
            )
            decision_id = entry["decision"]["id"]
            result.append(decision_id)
            parents = [decision_id]
        return result


def _validate_public_approvals(candidate: PublicAssertionCandidate, approvals: tuple[ApprovalDecision, ...]) -> None:
    approved = {item.role: item for item in approvals if item.conclusion == "approved"}
    missing = required_approval_roles(ContextVisibility.PUBLIC_GOVERNED) - set(approved)
    if missing:
        raise PublicAssertionError(f"missing required approvals: {', '.join(sorted(missing))}")
    if approved["publisher"].actor == candidate.submitted_by:
        raise PublicAssertionError("submitter cannot be the final publisher")


def _resource_for(candidate: PublicAssertionCandidate) -> SemanticResource:
    name = f"public-{content_digest({'candidate': candidate.candidate_id}).split(':', 1)[1][:20]}"
    evidence = {
        "evidence_id": f"public:{candidate.source.source_id}", "source_uri": candidate.source.source_url,
        "content_hash": candidate.source.content_hash, "observed_at": candidate.source.observed_at,
        "rights": candidate.source.rights.to_dict(),
    }
    return SemanticResource.create(
        resource_id=f"aof://{candidate.tenant_id}/context/context-assertion/{name}", kind=ResourceKind.CONTEXT_ASSERTION,
        name=name, domain="context", owner=candidate.submitted_by, description=candidate.statement,
        tags=["public-source", "public-governed"], evidence=[evidence], valid_time=candidate.valid_time,
        security_policy={"visibility": "public-governed", "redistribution": candidate.source.rights.redistribution},
        spec={"candidate_id": candidate.candidate_id, "subject_key": candidate.subject_key, "confidence": candidate.confidence, "source_id": candidate.source.source_id},
    )


def _candidate_from_dict(value: Mapping[str, Any]) -> PublicAssertionCandidate:
    source = value["source"]
    rights = PublicSourceRights.create(**source["rights"])
    record = PublicSourceRecord(source_id=source["source_id"], source_url=source["source_url"], title=source["title"], content=source["content"], observed_at=source["observed_at"], rights=rights, etag=source["etag"], last_modified=source["last_modified"])
    candidate = PublicAssertionCandidate.create(candidate_id=value["candidate_id"], tenant_id=value["tenant_id"], submitted_by=value["submitted_by"], subject_key=value["subject_key"], statement=value["statement"], confidence=value["confidence"], source=record, valid_time=value["valid_time"])
    if candidate.candidate_digest != value.get("candidate_digest"):
        raise PublicAssertionError("candidate_digest does not match candidate content")
    return candidate
