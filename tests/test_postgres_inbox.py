"""W08.04 — PostgreSQL inbox dedup store: same contract as SQLite."""

from __future__ import annotations

import socket

import pytest


def _pg_up() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 5433), timeout=1)
        s.close()
        return True
    except OSError:
        return False


@pytest.fixture()
def inbox():
    from bridge.persistence.postgres_inbox import PostgresInbox

    inbox = PostgresInbox()
    # clean slate per test (real DB persists across tests)
    with inbox._connect() as conn:
        conn.execute("TRUNCATE inbox_delivered, dead_letter")
        conn.commit()
    return inbox


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL not running")
def test_inbox_accept_and_dedup(inbox):
    assert inbox.accept("evt-001") is True
    assert inbox.accept("evt-001") is False  # duplicate
    assert inbox.seen("evt-001") is True
    assert inbox.seen("evt-002") is False


@pytest.mark.skipif(not _pg_up(), reason="PostgreSQL not running")
def test_dead_letter_store_and_count(inbox):
    inbox.dead_letter("evt-bad", 3, "transport timeout", {"data": "x"})
    assert inbox.dead_letter_count() == 1
