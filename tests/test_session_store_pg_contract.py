"""W08.04 — Session ACL store: same contract on SQLite AND PostgreSQL."""

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


@pytest.fixture(params=["sqlite", "postgres"])
def repo(request, tmp_path):
    from bridge.access.session_acl import Session
    from bridge.persistence.sqlite_session_store import SqliteSessionRepository

    def _session(**kw):
        base = dict(tenant_id="t1", session_id="s-1", owner_subject="alice")
        base.update(kw)
        return Session(**base)

    if request.param == "sqlite":
        yield SqliteSessionRepository(tmp_path / "sessions.db"), _session
    elif _pg_up():
        from bridge.persistence.postgres_session_store import PostgresSessionRepository

        repo = PostgresSessionRepository()
        with repo._connect() as connection:
            connection.execute("TRUNCATE agentic_sessions, agentic_session_acl")
            connection.commit()
        yield repo, _session
    else:
        pytest.skip("PostgreSQL infra stack not running")


def test_create_and_get(repo):
    repo_obj, _session = repo
    s = _session()
    repo_obj.create(s)
    fetched = repo_obj.get("t1", "s-1")
    assert fetched is not None
    assert fetched.owner_subject == "alice"


def test_duplicate_create_rejected(repo):
    from bridge.access.session_acl import SessionAclError

    repo_obj, _session = repo
    repo_obj.create(_session())
    with pytest.raises(SessionAclError, match="already exists"):
        repo_obj.create(_session())


def test_acl_mutation_bumps_version(repo):
    from bridge.access.session_acl import SessionAclEntry, SessionVisibility

    repo_obj, _session = repo
    repo_obj.create(_session(visibility=SessionVisibility.TEAM))
    updated = repo_obj.upsert_acl(
        "t1", "s-1", SessionAclEntry(subject_kind="subject", subject_id="bob", can_read=True)
    )
    assert updated.acl_version == 2
    entries = repo_obj.list_acl("t1", "s-1")
    assert len(entries) == 1 and entries[0].subject_id == "bob"


def test_set_state_persists_cross_instance(tmp_path, repo):
    repo_obj, _session = repo
    repo_obj.create(_session())
    repo_obj.set_state("t1", "s-1", __import__("bridge.access.session_acl", fromlist=["SessionState"]).SessionState.FROZEN)

    # fresh instance = cross-process persistence (SQLite: same file; PG: same DB)
    repo2 = type(repo_obj)(repo_obj.database if hasattr(repo_obj, 'database') else repo_obj.dsn)
    fetched = repo2.get("t1", "s-1")
    assert fetched.state == __import__("bridge.access.session_acl", fromlist=["SessionState"]).SessionState.FROZEN
    assert fetched.acl_version == 2
