"""Governed lifecycle for source-grounded semantic facts.

Prototype status (K01): an early SemanticFact/Release lifecycle that is
NOT connected to REST/MCP or any tests. The canonical contracts are
models.py (SemanticResource) and releases.py (KnowledgeRelease). Do not
use this module for new development; see
docs/remediation/2026-09-05/implementation-plan.md.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .contracts import Evidence, FactStatus, Release, SemanticFact, SourceAsset, to_record, utc_now
from .repository import SemanticRepository


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


class SemanticService:
    def __init__(self, root: Path):
        self.repository = SemanticRepository(root)

    def register_source(
        self,
        *,
        tenant_id: str,
        source_type: str,
        locator: str,
        content: str,
        classification: str = "internal",
        metadata: dict[str, Any] | None = None,
    ) -> SourceAsset:
        if not tenant_id or not source_type or not locator or not content:
            raise ValueError("tenant_id, source_type, locator, and content are required")
        source = SourceAsset(
            id=_new_id("src"), tenant_id=tenant_id, source_type=source_type,
            locator=locator, content_digest=_digest(content), observed_at=utc_now(),
            classification=classification, metadata=metadata or {},
        )
        state = self.repository.load()
        state["sources"][source.id] = to_record(source)
        self.repository.save(state)
        return source

    def add_evidence(
        self, *, source_asset_id: str, locator: str, excerpt: str, extractor: str,
    ) -> Evidence:
        if not locator or not excerpt or not extractor:
            raise ValueError("locator, excerpt, and extractor are required")
        state = self.repository.load()
        source = state["sources"].get(source_asset_id)
        if not source:
            raise ValueError("evidence source asset does not exist")
        evidence = Evidence(
            id=_new_id("ev"), source_asset_id=source_asset_id, locator=locator,
            excerpt=excerpt, content_digest=_digest(excerpt), extractor=extractor,
            extracted_at=utc_now(),
        )
        state["evidence"][evidence.id] = to_record(evidence)
        self.repository.save(state)
        return evidence

    def propose_fact(
        self,
        *,
        tenant_id: str,
        subject: str,
        predicate: str,
        object_value: str,
        evidence_ids: list[str],
        proposer: str,
        confidence: float = 1.0,
        metadata: dict[str, Any] | None = None,
    ) -> SemanticFact:
        if not subject or not predicate or not object_value or not proposer:
            raise ValueError("subject, predicate, object_value, and proposer are required")
        if not evidence_ids:
            raise ValueError("a semantic fact requires at least one evidence reference")
        if not 0.0 <= confidence <= 1.0:
            raise ValueError("confidence must be in [0, 1]")
        state = self.repository.load()
        evidence_rows = [state["evidence"].get(evidence_id) for evidence_id in evidence_ids]
        if any(row is None for row in evidence_rows):
            raise ValueError("a semantic fact references missing evidence")
        for row in evidence_rows:
            source = state["sources"].get(row["source_asset_id"])
            if not source or source["tenant_id"] != tenant_id:
                raise ValueError("evidence must belong to the fact tenant")
        fact = SemanticFact(
            id=_new_id("fact"), tenant_id=tenant_id, subject=subject, predicate=predicate,
            object_value=object_value, evidence_ids=tuple(evidence_ids),
            status=FactStatus.CANDIDATE, asserted_at=utc_now(), confidence=confidence,
            metadata={**(metadata or {}), "proposer": proposer},
        )
        proposal = {"id": _new_id("proposal"), "fact_id": fact.id, "tenant_id": tenant_id,
                    "proposer": proposer, "status": "pending", "created_at": utc_now()}
        state["facts"][fact.id] = to_record(fact)
        state["proposals"][proposal["id"]] = proposal
        self.repository.save(state)
        return fact

    def approve_proposal(self, *, proposal_id: str, reviewer: str) -> SemanticFact:
        if not reviewer:
            raise ValueError("reviewer is required")
        state = self.repository.load()
        proposal = state["proposals"].get(proposal_id)
        if not proposal or proposal["status"] != "pending":
            raise ValueError("proposal is missing or no longer pending")
        if proposal["proposer"] == reviewer:
            raise ValueError("a proposer cannot approve their own semantic change")
        fact = state["facts"].get(proposal["fact_id"])
        if not fact or fact["status"] != FactStatus.CANDIDATE.value:
            raise ValueError("proposal fact is not a candidate")
        fact["status"] = FactStatus.APPROVED.value
        fact.setdefault("metadata", {})["approved_by"] = reviewer
        fact["metadata"]["approved_at"] = utc_now()
        proposal["status"] = "approved"
        proposal["reviewer"] = reviewer
        proposal["reviewed_at"] = utc_now()
        self.repository.save(state)
        return SemanticFact(**{**fact, "status": FactStatus(fact["status"]), "evidence_ids": tuple(fact["evidence_ids"])})

    def publish_release(self, *, tenant_id: str, fact_ids: list[str], approved_by: str) -> Release:
        if not fact_ids or not approved_by:
            raise ValueError("fact_ids and approved_by are required")
        if len(set(fact_ids)) != len(fact_ids):
            raise ValueError("a release cannot contain duplicate facts")
        state = self.repository.load()
        facts = [state["facts"].get(fact_id) for fact_id in fact_ids]
        if any(fact is None for fact in facts):
            raise ValueError("release references a missing fact")
        if any(fact["tenant_id"] != tenant_id for fact in facts):
            raise ValueError("release facts must belong to one tenant")
        if any(fact["status"] != FactStatus.APPROVED.value for fact in facts):
            raise ValueError("only approved facts can be released")
        previous = [release for release in state["releases"].values() if release["tenant_id"] == tenant_id]
        previous_id = max(previous, key=lambda release: release["released_at"])["id"] if previous else None
        release = Release(
            id=_new_id("release"), tenant_id=tenant_id, fact_ids=tuple(sorted(fact_ids)),
            digest=_digest({"tenant_id": tenant_id, "fact_ids": sorted(fact_ids)}),
            approved_by=approved_by, released_at=utc_now(), previous_release_id=previous_id,
        )
        state["releases"][release.id] = to_record(release)
        for fact in facts:
            fact["status"] = FactStatus.RELEASED.value
            fact.setdefault("metadata", {})["release_id"] = release.id
        self.repository.save(state)
        return release

    def query_release(self, *, release_id: str, tenant_id: str) -> dict[str, Any]:
        state = self.repository.load()
        release = state["releases"].get(release_id)
        if not release or release["tenant_id"] != tenant_id:
            raise ValueError("release is not visible to this tenant")
        expected_digest = _digest({"tenant_id": tenant_id, "fact_ids": sorted(release["fact_ids"])})
        if release.get("digest") != expected_digest:
            raise ValueError("release digest verification failed")
        facts = [state["facts"][fact_id] for fact_id in release["fact_ids"]]
        evidence = {
            evidence_id: state["evidence"][evidence_id]
            for fact in facts for evidence_id in fact["evidence_ids"]
        }
        sources = {
            row["source_asset_id"]: state["sources"][row["source_asset_id"]]
            for row in evidence.values()
        }
        return {"release": release, "facts": facts, "evidence": evidence, "sources": sources}
