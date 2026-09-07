"""W05.02 — Outbox dispatcher + idempotent inbox.

Acceptance: broker停止恢复/重复/乱序不丢事件. The contract is
at-least-once delivery plus an idempotent consumer: a crash between
transport-ack and outbox-mark causes redelivery, and the inbox dedup
makes the consumer effect exactly-once.
"""

from __future__ import annotations

import json


from bridge.audit.logger import FileOutbox
from bridge.audit.outbox_dispatch import (
    FileBrokerTransport,
    Inbox,
    OutboxDispatcher,
    TransportDownError,
)


def _event(n: int) -> dict:
    return {"event_id": f"evt-{n:04d}", "topic": "audit", "seq": n, "payload": {"i": n}}


def _write_outbox(outbox_dir, events: list[dict]) -> FileOutbox:
    outbox = FileOutbox(str(outbox_dir))
    lines = "".join(json.dumps(e) + "\n" for e in events)
    # write one pending file directly (deterministic ordering)
    target = outbox.outbox_dir / "audit_outbox_20260907_000000_test.jsonl"
    target.write_text(lines, encoding="utf-8")
    return outbox


# ---------------------------------------------------------------------------
# Dispatcher happy path
# ---------------------------------------------------------------------------


def test_dispatch_drains_pending_in_order(tmp_path):
    outbox = _write_outbox(tmp_path / "outbox", [_event(i) for i in range(10)])
    delivered_batches: list[list[dict]] = []

    class RecordingTransport:
        def deliver(self, events):
            delivered_batches.append(list(events))

    dispatcher = OutboxDispatcher(
        outbox, transport=RecordingTransport(), inbox=Inbox(tmp_path / "inbox.sqlite")
    )
    report = dispatcher.dispatch_pending()

    assert report.events_dispatched == 10
    assert report.files_marked == 1
    # order preserved across batches
    flat = [e["seq"] for batch in delivered_batches for e in batch]
    assert flat == list(range(10))
    # outbox file marked processed
    assert not list(outbox.outbox_dir.glob("audit_outbox_*.jsonl"))


# ---------------------------------------------------------------------------
# Broker outage: bounded retries, no loss
# ---------------------------------------------------------------------------


def test_broker_outage_keeps_events_pending_without_loss(tmp_path):
    outbox = _write_outbox(tmp_path / "outbox", [_event(i) for i in range(5)])

    class DownTransport:
        attempts = 0
        def deliver(self, events):
            self.attempts += 1
            raise TransportDownError("broker unreachable")

    transport = DownTransport()
    dispatcher = OutboxDispatcher(
        outbox, transport=transport, inbox=Inbox(tmp_path / "inbox.sqlite"),
        max_attempts=3,
    )
    report = dispatcher.dispatch_pending()

    # bounded retries, then dead-lettered (not silently dropped)
    assert report.dead_lettered == 5
    assert transport.attempts == 3
    # the outbox file is NOT marked: nothing lost, events remain for recovery
    assert report.files_marked == 0
    assert list(outbox.outbox_dir.glob("audit_outbox_*.jsonl"))


def test_broker_recovery_completes_the_drain(tmp_path):
    outbox = _write_outbox(tmp_path / "outbox", [_event(i) for i in range(5)])

    class FlakyTransport:
        def __init__(self):
            self.calls = 0
        def deliver(self, events):
            self.calls += 1
            if self.calls <= 2:
                raise TransportDownError("broker restarting")

    dispatcher = OutboxDispatcher(
        outbox, transport=FlakyTransport(), inbox=Inbox(tmp_path / "inbox.sqlite"),
        max_attempts=5,
    )
    report = dispatcher.dispatch_pending()
    assert report.events_dispatched == 5
    assert report.files_marked == 1


# ---------------------------------------------------------------------------
# At-least-once + idempotent consumer (crash window simulation)
# ---------------------------------------------------------------------------


def test_redelivery_after_crash_is_deduped_by_inbox(tmp_path):
    """Crash between transport-ack and outbox-mark: redelivery happens,
    the consumer processes each event EXACTLY once."""
    outbox = _write_outbox(tmp_path / "outbox", [_event(i) for i in range(6)])
    inbox = Inbox(tmp_path / "inbox.sqlite")
    dispatcher = OutboxDispatcher(
        outbox, transport=FileBrokerTransport(tmp_path / "broker"), inbox=inbox
    )
    seen_events: list[dict] = []
    def handler(event):
        seen_events.append(event)

    # read the pending events (the consumer consumes from the batch the
    # dispatcher hands it, not from the outbox file after marking)
    pending = _all_from(outbox)
    assert len(pending) == 6

    # pass 1: deliver + consume. The consumer runs, but the crash happens
    # BEFORE mark_processed (the canonical at-least-once window).
    dispatcher.dispatch_pending()
    p1, _d1 = dispatcher.consume(pending, handler=handler)
    assert p1 == 6

    # simulate the crash: the file was delivered+consumed but NOT marked -
    # un-mark it so pass 2 redelivers the same events
    processed_file = next((tmp_path / "outbox").glob("*.processed"))
    target = processed_file.with_suffix(".jsonl")
    processed_file.rename(target)

    # pass 2: redelivery of the same events (identical content)
    dispatcher.dispatch_pending()
    p2, d2 = dispatcher.consume(pending, handler=handler)

    assert d2 == 6, "all redelivered events must be detected as duplicates"
    ids = [e["event_id"] for e in seen_events]
    assert len(ids) == 6 and len(set(ids)) == 6, "consumer processed an event twice"


def _all_from(outbox: FileOutbox) -> list[dict]:
    events: list[dict] = []
    for f in sorted(outbox.outbox_dir.glob("audit_outbox_*.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                events.append(json.loads(line))
    return events


def test_inbox_rejects_duplicate_and_accepts_new(tmp_path):
    inbox = Inbox(tmp_path / "inbox.sqlite")
    assert inbox.accept("evt-1") is True
    assert inbox.accept("evt-1") is False  # duplicate
    assert inbox.seen("evt-1") is True
    assert inbox.seen("evt-2") is False


def test_file_broker_transport_writes_sent_batches(tmp_path):
    transport = FileBrokerTransport(tmp_path / "broker")
    transport.deliver([_event(1), _event(2)])
    sent_files = list((tmp_path / "broker" / "sent").glob("batch_*.jsonl"))
    assert len(sent_files) == 1
    lines = [json.loads(item) for item in sent_files[0].read_text().splitlines()]
    assert [e["event_id"] for e in lines] == ["evt-0001", "evt-0002"]
