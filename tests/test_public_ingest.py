import pytest

from bridge.context_exchange import (
    PublicIngestError,
    PublicSourceBatch,
    PublicSourceIngestor,
    PublicSourceRecord,
    PublicSourceRights,
    SqlitePublicSourceRepository,
    require_redistributable,
)
from bridge.decision_provenance import DecisionProvenanceStore


def _record(content: str, *, rights: str = "allowed") -> PublicSourceRecord:
    return PublicSourceRecord(
        source_id="notice-1", source_url="https://public.example/notice-1", title="Notice",
        content=content, observed_at="2026-08-24T00:00:00Z",
        rights=PublicSourceRights.create(license_id="CC-BY-4.0", redistribution=rights), etag="v1",
    )


def test_public_source_revisions_are_immutable_replayable_and_audited(tmp_path) -> None:
    repository = SqlitePublicSourceRepository(tmp_path / "public.sqlite3")
    decisions = DecisionProvenanceStore(tmp_path / "decisions.jsonl")
    ingestor = PublicSourceIngestor(repository=repository, decision_store=decisions)
    initial = PublicSourceBatch("public-notices", (_record("First version"),), "2026-08-24T01:00:00Z", "cursor-1", True)
    changed = PublicSourceBatch("public-notices", (_record("Second version"),), "2026-08-24T02:00:00Z", None, True)

    assert len(ingestor.ingest(initial, actor="connector:public", rationale="Initial collection.")) == 1
    assert ingestor.ingest(initial, actor="connector:public", rationale="Retry.") == ()
    assert len(ingestor.ingest(changed, actor="connector:public", rationale="Source updated.")) == 1
    assert [record.content for record in repository.replay(source_key="public-notices", source_id="notice-1")] == ["First version", "Second version"]
    assert repository.watermark("public-notices")["watermark"] == "2026-08-24T02:00:00Z"
    assert len(decisions._entries()) == 2


def test_incomplete_collection_does_not_advance_watermark_or_allow_public_release(tmp_path) -> None:
    repository = SqlitePublicSourceRepository(tmp_path / "public.sqlite3")
    batch = PublicSourceBatch("public-notices", (_record("Restricted", rights="restricted"),), "2026-08-24T01:00:00Z", "cursor-2", False)
    repository.ingest(batch)

    assert repository.watermark("public-notices")["watermark"] == "0"
    assert repository.watermark("public-notices")["status"] == "incomplete"
    with pytest.raises(PublicIngestError, match="allowed redistribution"):
        require_redistributable(_record("Restricted", rights="restricted"))
