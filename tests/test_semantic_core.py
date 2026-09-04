from __future__ import annotations

import pytest

from bridge.semantic_core import FactStatus, SemanticService


def _candidate(service: SemanticService):
    source = service.register_source(
        tenant_id="tenant-a", source_type="sql", locator="warehouse://finance/revenue",
        content="SELECT customer_id, amount FROM revenue", metadata={"snapshot": "2026-09-05"},
    )
    evidence = service.add_evidence(
        source_asset_id=source.id, locator="line:1", excerpt="amount is recognised revenue", extractor="manual-v1",
    )
    return service.propose_fact(
        tenant_id="tenant-a", subject="metric:recognised_revenue", predicate="defined_by",
        object_value="SUM(revenue.amount)", evidence_ids=[evidence.id], proposer="analyst-a",
    )


def test_release_requires_evidence_approval_and_tenant_scope(temp_dir):
    service = SemanticService(temp_dir / "semantic")
    fact = _candidate(service)
    assert fact.status is FactStatus.CANDIDATE

    with pytest.raises(ValueError, match="only approved"):
        service.publish_release(tenant_id="tenant-a", fact_ids=[fact.id], approved_by="release-manager")

    proposal_id = next(iter(service.repository.load()["proposals"]))
    approved = service.approve_proposal(proposal_id=proposal_id, reviewer="reviewer-b")
    assert approved.status is FactStatus.APPROVED
    release = service.publish_release(tenant_id="tenant-a", fact_ids=[fact.id], approved_by="release-manager")
    materialized = service.query_release(release_id=release.id, tenant_id="tenant-a")

    assert materialized["release"]["digest"] == release.digest
    assert materialized["facts"][0]["status"] == FactStatus.RELEASED.value
    assert materialized["evidence"]
    assert materialized["sources"]
    with pytest.raises(ValueError, match="not visible"):
        service.query_release(release_id=release.id, tenant_id="tenant-b")


def test_candidate_rejects_missing_or_cross_tenant_evidence(temp_dir):
    service = SemanticService(temp_dir / "semantic")
    with pytest.raises(ValueError, match="at least one evidence"):
        service.propose_fact(
            tenant_id="tenant-a", subject="x", predicate="y", object_value="z", evidence_ids=[], proposer="a",
        )
    source = service.register_source(tenant_id="tenant-b", source_type="doc", locator="doc://b", content="b")
    evidence = service.add_evidence(source_asset_id=source.id, locator="p:1", excerpt="b", extractor="manual-v1")
    with pytest.raises(ValueError, match="fact tenant"):
        service.propose_fact(
            tenant_id="tenant-a", subject="x", predicate="y", object_value="z",
            evidence_ids=[evidence.id], proposer="a",
        )


def test_proposer_cannot_approve_own_change(temp_dir):
    service = SemanticService(temp_dir / "semantic")
    _candidate(service)
    proposal_id = next(iter(service.repository.load()["proposals"]))
    with pytest.raises(ValueError, match="cannot approve"):
        service.approve_proposal(proposal_id=proposal_id, reviewer="analyst-a")


def test_tampered_release_fails_closed(temp_dir):
    service = SemanticService(temp_dir / "semantic")
    fact = _candidate(service)
    proposal_id = next(iter(service.repository.load()["proposals"]))
    service.approve_proposal(proposal_id=proposal_id, reviewer="reviewer-b")
    release = service.publish_release(tenant_id="tenant-a", fact_ids=[fact.id], approved_by="release-manager")
    state = service.repository.load()
    state["releases"][release.id]["digest"] = "not-a-valid-digest"
    service.repository.save(state)
    with pytest.raises(ValueError, match="digest verification"):
        service.query_release(release_id=release.id, tenant_id="tenant-a")
