"""W05.02 — Outbox dispatcher + idempotent inbox (at-least-once, exactly-once effect).

The outbox pattern cannot make a broker send transactional with the local
state write, so the contract is at-least-once DELIVERY plus an idempotent
consumer:

- OutboxDispatcher drains the FileOutbox (oldest file first, preserving
  append order) and hands batches to a Transport. A send is retried up to
  max_attempts; a persistently failing event goes to dead-letter instead
  of blocking the queue or being silently dropped.
- The consumer-side Inbox deduplicates by event_id: a redelivered event
  (crash between transport-ack and outbox-mark) is acknowledged exactly
  once - the effect lands exactly once even though delivery may repeat.

The reference transport is a file-based broker (moves batches to a
'sent' directory). Real MQ transports implement the same protocol.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol

from bridge.audit.logger import FileOutbox
from bridge.persistence.sqlite_support import managed_sqlite_connection

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_delivered (
    event_id    TEXT PRIMARY KEY,
    topic       TEXT,
    delivered_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS dead_letter (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    TEXT NOT NULL,
    attempts    INTEGER NOT NULL,
    last_error  TEXT NOT NULL,
    payload     TEXT NOT NULL,
    parked_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class TransportDownError(RuntimeError):
    """Raised by a transport when the broker is unavailable."""


class EventTransport(Protocol):
    """A broker-facing sender. Must be idempotent-tolerant: the dispatcher
    may redeliver an event after a crash between ack and outbox-mark."""

    def deliver(self, events: list[dict[str, Any]]) -> None: ...


class FileBrokerTransport:
    """Reference transport: writes delivered batches under sent/."""

    def __init__(self, broker_dir: str | Path) -> None:
        self.broker_dir = Path(broker_dir)
        (self.broker_dir / "sent").mkdir(parents=True, exist_ok=True)

    def deliver(self, events: list[dict[str, Any]]) -> None:
        if not events:
            return
        import uuid

        target = self.broker_dir / "sent" / f"batch_{uuid.uuid4().hex}.jsonl"
        with target.open("w", encoding="utf-8") as handle:
            for event in events:
                handle.write(json.dumps(event, ensure_ascii=False) + "\n")


class Inbox:
    """Consumer-side dedup store keyed by event_id."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with managed_sqlite_connection(self._connect) as connection:
            connection.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        return connection

    def accept(self, event_id: str, *, topic: str | None = None) -> bool:
        """True if this event_id was NOT seen before (process it);
        False if it is a duplicate (acknowledge and skip)."""
        with managed_sqlite_connection(self._connect) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO inbox_delivered(event_id, topic) VALUES (?, ?)",
                (event_id, topic),
            )
            return cursor.rowcount > 0

    def seen(self, event_id: str) -> bool:
        with managed_sqlite_connection(self._connect) as connection:
            row = connection.execute(
                "SELECT 1 FROM inbox_delivered WHERE event_id = ?", (event_id,)
            ).fetchone()
            return row is not None

    def dead_letter(self, event_id: str, attempts: int, error: str, payload: dict[str, Any]) -> None:
        with managed_sqlite_connection(self._connect) as connection:
            connection.execute(
                "INSERT INTO dead_letter(event_id, attempts, last_error, payload) "
                "VALUES (?, ?, ?, ?)",
                (event_id, attempts, error, json.dumps(payload, ensure_ascii=False)),
            )

    def dead_letter_count(self) -> int:
        with managed_sqlite_connection(self._connect) as connection:
            return connection.execute("SELECT COUNT(*) FROM dead_letter").fetchone()[0]


@dataclass
class DispatchReport:
    batches_sent: int
    events_dispatched: int
    duplicates_skipped: int
    dead_lettered: int
    files_marked: int


class OutboxDispatcher:
    """Drains the FileOutbox through a transport with bounded retries,
    dead-lettering and inbox-backed idempotent consumption."""

    def __init__(
        self,
        outbox: FileOutbox,
        *,
        transport: EventTransport,
        inbox: Inbox,
        max_attempts: int = 3,
    ) -> None:
        self.outbox = outbox
        self.transport = transport
        self.inbox = inbox
        self.max_attempts = max_attempts

    def dispatch_pending(self, *, batch_size: int = 200) -> DispatchReport:
        """One drain pass. Files are processed oldest-first; each file is
        marked processed only after a successful transport ack."""
        report = DispatchReport(
            batches_sent=0, events_dispatched=0, duplicates_skipped=0,
            dead_lettered=0, files_marked=0,
        )
        pending_files = sorted(self.outbox.outbox_dir.glob("audit_outbox_*.jsonl"))
        for filepath in pending_files:
            events = self._read_file(filepath)
            batches = [
                events[i:i + batch_size] for i in range(0, len(events), batch_size)
            ] or [[]]
            all_delivered = True
            for batch in batches:
                result = self._deliver_batch(batch, report)
                if not result:
                    all_delivered = False
                    break  # stop this file at the first failed batch; retry next pass
            if all_delivered:
                self.outbox.mark_processed(str(filepath))
                report.files_marked += 1
        return report

    def _read_file(self, filepath: Path) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        with filepath.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    events.append(json.loads(line))
        return events

    def _deliver_batch(self, batch: list[dict[str, Any]], report: DispatchReport) -> bool:
        attempts = 0
        while attempts < self.max_attempts:
            attempts += 1
            try:
                self.transport.deliver(batch)
                report.batches_sent += 1
                report.events_dispatched += len(batch)
                return True
            except TransportDownError:
                if attempts >= self.max_attempts:
                    break
                continue  # bounded retry: broker outage must not lose events
            except Exception as exc:  # poison payload
                for event in batch:
                    self.inbox.dead_letter(
                        str(event.get("event_id") or event.get("id") or "unknown"),
                        attempts, str(exc), event,
                    )
                    report.dead_lettered += 1
                return False
        for event in batch:
            self.inbox.dead_letter(
                str(event.get("event_id") or event.get("id") or "unknown"),
                attempts, "transport down after max attempts", event,
            )
            report.dead_lettered += 1
        return False

    def consume(self, events: Iterable[dict[str, Any]], *, handler: Callable[[dict[str, Any]], None]) -> tuple[int, int]:
        """Idempotent consumer: process each event exactly once by event_id.

        Returns (processed, duplicates)."""
        processed = duplicates = 0
        for event in events:
            event_id = str(event.get("event_id") or event.get("id") or "")
            if not event_id:
                continue
            if self.inbox.accept(event_id):
                handler(event)
                processed += 1
            else:
                duplicates += 1
        return processed, duplicates
