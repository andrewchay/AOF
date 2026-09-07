"""W09.01 — SQLite connections must be closed after repository operations.

Diagnosis D11: `with self._connect()` relies on sqlite3's connection
context manager which commits the transaction but never closes the
connection, leaking one fd per call under sustained traffic.
"""

from __future__ import annotations

import gc
import sqlite3

import pytest


def test_managed_connection_closes_on_success(tmp_path):
    from bridge.persistence.sqlite_support import managed_sqlite_connection

    db = tmp_path / 't.db'

    def connect():
        return sqlite3.connect(db)

    with managed_sqlite_connection(connect) as conn:
        conn.execute('CREATE TABLE t (x)')

    with pytest.raises(sqlite3.ProgrammingError, match='closed database'):
        conn.execute('SELECT 1')


def test_managed_connection_closes_on_error_and_rolls_back(tmp_path):
    from bridge.persistence.sqlite_support import managed_sqlite_connection

    db = tmp_path / 't.db'
    with managed_sqlite_connection(lambda: sqlite3.connect(db)) as conn:
        conn.execute('CREATE TABLE t (x)')

    with pytest.raises(ValueError):
        with managed_sqlite_connection(lambda: sqlite3.connect(db)) as conn2:
            conn2.execute("INSERT INTO t VALUES (1)")
            raise ValueError('boom')

    # rolled back: insert must not be visible
    with managed_sqlite_connection(lambda: sqlite3.connect(db)) as conn3:
        count = conn3.execute('SELECT COUNT(*) FROM t').fetchone()[0]
    assert count == 0


def test_agentic_repository_does_not_leak_connections(tmp_path):
    """50 sequential save() calls must not keep connections alive."""
    from bridge.semantic_core.agentic_system import SqliteAgenticRunRepository

    repo = SqliteAgenticRunRepository(tmp_path / 'runs.db')
    refs = []
    for i in range(50):
        repo.save({'run_id': f'r-{i}', 'tenant_id': 't1', 'session_id': 's1', 'x': i})
        gc.collect()
        refs = [r for r in refs if r() is not None]

    assert not refs, f'{len(refs)} connections stayed alive across save() calls'
