"""W08.04 — PostgreSQL implementation of the inbox dedup store.

Same contract as bridge/audit/outbox_dispatch.Inbox (accept/seen/
dead_letter), adapted to PostgreSQL (psycopg, ON CONFLICT).
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg

_SCHEMA = """
CREATE TABLE IF NOT EXISTS inbox_delivered (
    event_id     TEXT PRIMARY KEY,
    topic        TEXT,
    delivered_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS dead_letter (
    id         BIGSERIAL PRIMARY KEY,
    event_id   TEXT NOT NULL,
    attempts   INTEGER NOT NULL,
    last_error TEXT NOT NULL,
    payload    TEXT NOT NULL,
    parked_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


class PostgresInbox:
    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get(
            "AOF_INBOX_PG_DSN", "postgresql://aof:aof-dev-only@127.0.0.1:5433/aof_control"
        )
        self._init_schema()

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(self.dsn, row_factory=None)

    def _init_schema(self) -> None:
        with self._connect() as connection:
            connection.execute(_SCHEMA)
            connection.commit()

    def accept(self, event_id: str, *, topic: str | None = None) -> bool:
        """True if new (process it), False if duplicate (skip)."""
        with self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO inbox_delivered(event_id, topic) VALUES (%s, %s) "
                "ON CONFLICT (event_id) DO NOTHING",
                (event_id, topic),
            )
            connection.commit()
            return cursor.rowcount > 0

    def seen(self, event_id: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM inbox_delivered WHERE event_id = %s", (event_id,)
            ).fetchone()
            return row is not None

    def dead_letter(self, event_id: str, attempts: int, error: str, payload: dict[str, Any]) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO dead_letter(event_id, attempts, last_error, payload) "
                "VALUES (%s, %s, %s, %s)",
                (event_id, attempts, error, json.dumps(payload, ensure_ascii=False)),
            )
            connection.commit()

    def dead_letter_count(self) -> int:
        with self._connect() as connection:
            row = connection.execute("SELECT COUNT(*) FROM dead_letter").fetchone()
            return int(row[0]) if row else 0  # type: ignore[index]
