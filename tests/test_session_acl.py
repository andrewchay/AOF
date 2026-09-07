"""W06.01 — Session ACL model contract tests (D09).

Core negative: a same-tenant user must NOT be able to open another
user's private session. Default is private; team/business-object
visibility requires explicit ACL entries; acl_version bumps on mutation;
frozen sessions reject writes; deleted sessions reject everything.
"""

from __future__ import annotations

import pytest

from bridge.access.session_acl import (
    Session,
    SessionAclEntry,
    SessionAclError,
    SessionAction,
    SessionState,
    SessionVisibility,
    authorize,
)
from bridge.persistence.sqlite_session_store import SqliteSessionRepository


def _session(**kw) -> Session:
    base = dict(tenant_id="tenant-a", session_id="s-1", owner_subject="alice")
    base.update(kw)
    return Session(**base)


# ---------------------------------------------------------------------------
# authorize() — pure decision function
# ---------------------------------------------------------------------------


def test_default_visibility_is_private():
    assert _session().visibility == SessionVisibility.PRIVATE


def test_owner_always_allowed():
    s = _session()
    for action in (SessionAction.READ, SessionAction.WRITE, SessionAction.SHARE):
        authorize(s, [], subject_id="alice", action=action)


def test_same_tenant_other_user_denied_on_private_session():
    """D09 core negative: same tenant does NOT imply session access."""
    s = _session()
    with pytest.raises(SessionAclError, match="private"):
        authorize(s, [], subject_id="bob", action=SessionAction.READ)


def test_team_visibility_requires_acl_entry():
    s = _session(visibility=SessionVisibility.TEAM)
    with pytest.raises(SessionAclError, match="no ACL entry"):
        authorize(s, [], subject_id="bob", action=SessionAction.READ)

    entry = SessionAclEntry(subject_kind="subject", subject_id="bob", can_read=True)
    authorize(s, [entry], subject_id="bob", action=SessionAction.READ)
    with pytest.raises(SessionAclError, match="write"):
        authorize(s, [entry], subject_id="bob", action=SessionAction.WRITE)


def test_group_acl_entry_grants_members():
    s = _session(visibility=SessionVisibility.TEAM)
    entry = SessionAclEntry(subject_kind="group", subject_id="finance-team", can_read=True, can_write=True)
    authorize(s, [entry], subject_id="carol", groups=("finance-team",), action=SessionAction.WRITE)
    with pytest.raises(SessionAclError):
        authorize(s, [entry], subject_id="dave", groups=("other-team",), action=SessionAction.READ)


def test_frozen_session_rejects_writes_for_non_owner():
    s = _session(state=SessionState.FROZEN, visibility=SessionVisibility.TEAM)
    entry = SessionAclEntry(subject_kind="subject", subject_id="bob", can_read=True, can_write=True)
    authorize(s, [entry], subject_id="bob", action=SessionAction.READ)
    with pytest.raises(SessionAclError, match="frozen"):
        authorize(s, [entry], subject_id="bob", action=SessionAction.WRITE)


def test_deleted_session_rejects_everything_including_owner():
    s = _session(state=SessionState.DELETED)
    with pytest.raises(SessionAclError, match="deleted"):
        authorize(s, [], subject_id="alice", action=SessionAction.READ)


# ---------------------------------------------------------------------------
# SqliteSessionRepository — persistence + acl_version
# ---------------------------------------------------------------------------


def test_repository_create_get_roundtrip(tmp_path):
    repo = SqliteSessionRepository(tmp_path / "sessions.db")
    s = _session()
    repo.create(s)

    fetched = repo.get("tenant-a", "s-1")
    assert fetched is not None
    assert fetched.owner_subject == "alice"
    assert fetched.visibility == SessionVisibility.PRIVATE
    assert fetched.acl_version == 1


def test_repository_duplicate_create_rejected(tmp_path):
    repo = SqliteSessionRepository(tmp_path / "sessions.db")
    repo.create(_session())
    with pytest.raises(SessionAclError, match="already exists"):
        repo.create(_session())


def test_acl_mutation_bumps_version_atomically(tmp_path):
    repo = SqliteSessionRepository(tmp_path / "sessions.db")
    repo.create(_session(visibility=SessionVisibility.TEAM))

    updated = repo.upsert_acl(
        "tenant-a", "s-1",
        SessionAclEntry(subject_kind="subject", subject_id="bob", can_read=True),
    )
    assert updated.acl_version == 2

    entries = repo.list_acl("tenant-a", "s-1")
    assert len(entries) == 1
    assert entries[0].subject_id == "bob"

    updated = repo.delete_acl("tenant-a", "s-1", "subject", "bob")
    assert updated.acl_version == 3
    assert repo.list_acl("tenant-a", "s-1") == []


def test_set_state_bumps_version_and_persists(tmp_path):
    repo = SqliteSessionRepository(tmp_path / "sessions.db")
    repo.create(_session())

    frozen = repo.set_state("tenant-a", "s-1", SessionState.FROZEN)
    assert frozen.state == SessionState.FROZEN
    assert frozen.acl_version == 2

    # fresh repository instance = cross-process read
    repo2 = SqliteSessionRepository(tmp_path / "sessions.db")
    fetched = repo2.get("tenant-a", "s-1")
    assert fetched.state == SessionState.FROZEN
    assert fetched.acl_version == 2
