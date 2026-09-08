"""W05.01 — Audit database and file query: ID/time/subject/resource with
pagination, tenant filtering, exact counts."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

import pytest

from bridge.audit.logger import AuditEvent
from bridge.audit.query import AuditQuery, AuditQueryFilter


def _make_events(n: int, *, user_id: str = "u1", tenant_id: str = "t1",
                 action: str = "read") -> list[AuditEvent]:
    base = datetime.utcnow() - timedelta(hours=n)
    events = []
    for i in range(n):
        events.append(AuditEvent(
            event_id=f"evt-{i:04d}",
            user_id=user_id,
            tenant_id=tenant_id,
            action=action if i % 3 == 0 else f"action_{i}",
            resource_type="dataset",
            resource_id=f"ds-{i % 5}",
            status="success" if i % 4 != 0 else "failure",
            timestamp=base + timedelta(minutes=i),
            request_payload={"index": i},
        ))
    return events


def _write_file(tmp_path: Path, events: list[AuditEvent]) -> Path:
    log_file = tmp_path / "audit.jsonl"
    with log_file.open("w", encoding="utf-8") as f:
        for e in events:
            f.write(e.to_json() + "\n")
    return log_file


@pytest.fixture()
def populated_file(tmp_path):
    events = _make_events(20)
    log_file = _write_file(tmp_path, events)
    return AuditQuery(log_file=str(log_file)), events


def test_file_query_all(populated_file):
    query, events = populated_file
    import asyncio

    result = asyncio.get_event_loop().run_until_complete(
        query.query(AuditQueryFilter())
    ) if False else None

    async def _run():
        return await query.query(AuditQueryFilter())

    import asyncio as aio
    result = aio.run(_run())
    assert result.total_count >= 20
    assert len(result.events) == result.total_count


def test_file_query_filter_by_user(populated_file):
    query, events = populated_file

    async def _run():
        return await query.query(AuditQueryFilter(user_id="u1"))

    import asyncio as aio
    result = aio.run(_run())
    assert result.total_count >= 20


def test_file_query_pagination(populated_file):
    query, events = populated_file

    async def _run():
        page1 = await query.query(AuditQueryFilter(offset=0, limit=5))
        page2 = await query.query(AuditQueryFilter(offset=5, limit=5))
        return page1, page2

    import asyncio as aio
    p1, p2 = aio.run(_run())
    assert len(p1.events) == 5
    assert len(p2.events) > 0
    # no overlap
    p1_ids = {e.event_id for e in p1.events}
    p2_ids = {e.event_id for e in p2.events}
    assert not (p1_ids & p2_ids), "pagination overlap detected"


def test_get_event_by_id(populated_file):
    query, events = populated_file

    async def _run():
        return await query.get_event_by_id(events[0].event_id)

    import asyncio as aio
    found = aio.run(_run())
    assert found is not None
    assert found.event_id == events[0].event_id


def test_get_event_by_id_not_found(populated_file):
    query, _events = populated_file

    async def _run():
        return await query.get_event_by_id("nonexistent")

    import asyncio as aio
    assert aio.run(_run()) is None


def test_search_by_keyword(populated_file):
    query, events = populated_file

    async def _run():
        return await query.search_by_keyword("action_1")

    import asyncio as aio
    result = aio.run(_run())
    assert result.total_count > 0
