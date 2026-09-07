"""W06.01 — Session ACL model (D09).

Today agentic sessions are keyed by (tenant_id, session_id) only: any
subject with a generic tenant read role can open every session, which
cannot express private business collaboration.

This module defines the Session entity and its access control:

- visibility=private         : owner only (DEFAULT — unknown sessions are private)
- visibility=team            : owner + explicit ACL entries (subjects/groups)
- visibility=business-object : ACL entries bound to a business object id

Every ACL mutation bumps acl_version; readers must authorize against the
CURRENT version (cached authorizations keyed by acl_version become
invalid automatically). Frozen sessions reject writes; deleted sessions
reject everything (tombstone semantics — see W06.05 for payload erasure).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class SessionAclError(ValueError):
    """Raised when a session access or mutation is not permitted."""


class SessionVisibility(str, Enum):
    PRIVATE = "private"
    TEAM = "team"
    BUSINESS_OBJECT = "business-object"


class SessionState(str, Enum):
    ACTIVE = "active"
    FROZEN = "frozen"   # legal hold: reads allowed, writes rejected
    DELETED = "deleted"  # tombstone: everything rejected


class SessionAction(str, Enum):
    READ = "read"
    WRITE = "write"
    SHARE = "share"    # mutate ACL entries
    DELETE = "delete"


@dataclass(frozen=True)
class SessionAclEntry:
    """One grant on a session. subject_kind: 'subject' or 'group'."""

    subject_kind: str
    subject_id: str
    can_read: bool = True
    can_write: bool = False
    can_share: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "subject_kind": self.subject_kind,
            "subject_id": self.subject_id,
            "can_read": self.can_read,
            "can_write": self.can_write,
            "can_share": self.can_share,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SessionAclEntry":
        return cls(
            subject_kind=str(data["subject_kind"]),
            subject_id=str(data["subject_id"]),
            can_read=bool(data.get("can_read", True)),
            can_write=bool(data.get("can_write", False)),
            can_share=bool(data.get("can_share", False)),
        )


@dataclass(frozen=True)
class Session:
    tenant_id: str
    session_id: str
    owner_subject: str
    visibility: SessionVisibility = SessionVisibility.PRIVATE
    business_object_id: str | None = None
    acl_version: int = 1
    retention_policy_id: str | None = None
    state: SessionState = SessionState.ACTIVE
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "session_id": self.session_id,
            "owner_subject": self.owner_subject,
            "visibility": self.visibility.value,
            "business_object_id": self.business_object_id,
            "acl_version": self.acl_version,
            "retention_policy_id": self.retention_policy_id,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Session":
        return cls(
            tenant_id=str(data["tenant_id"]),
            session_id=str(data["session_id"]),
            owner_subject=str(data["owner_subject"]),
            visibility=SessionVisibility(str(data.get("visibility", "private"))),
            business_object_id=data.get("business_object_id"),
            acl_version=int(data.get("acl_version", 1)),
            retention_policy_id=data.get("retention_policy_id"),
            state=SessionState(str(data.get("state", "active"))),
            created_at=str(data.get("created_at") or datetime.now(timezone.utc).isoformat()),
            updated_at=str(data.get("updated_at") or datetime.now(timezone.utc).isoformat()),
        )


def authorize(
    session: Session,
    acl_entries: list[SessionAclEntry],
    *,
    subject_id: str,
    groups: tuple[str, ...] = (),
    action: SessionAction,
) -> None:
    """Authorize ``action`` on ``session`` for ``subject_id``.

    Raises SessionAclError on denial. Owner is always fully authorized
    (except on deleted sessions). Deleted sessions reject everything;
    frozen sessions reject write/share/delete.
    """
    if session.state == SessionState.DELETED:
        raise SessionAclError(f"session is deleted: {session.session_id}")

    is_owner = subject_id == session.owner_subject

    if session.state == SessionState.FROZEN and action != SessionAction.READ:
        if not is_owner:
            raise SessionAclError(
                f"session is frozen (legal hold): {session.session_id}"
            )

    if is_owner:
        return

    if session.visibility == SessionVisibility.PRIVATE:
        raise SessionAclError(
            f"session is private to its owner: {session.session_id}"
        )

    # team / business-object: explicit ACL entries decide
    granted: SessionAclEntry | None = None
    for entry in acl_entries:
        if entry.subject_kind == "subject" and entry.subject_id == subject_id:
            granted = entry
            break
        if entry.subject_kind == "group" and entry.subject_id in groups:
            granted = entry
            break
    if granted is None:
        raise SessionAclError(
            f"subject {subject_id!r} has no ACL entry on session {session.session_id}"
        )

    allowed = {
        SessionAction.READ: granted.can_read,
        SessionAction.WRITE: granted.can_write,
        SessionAction.SHARE: granted.can_share,
        SessionAction.DELETE: granted.can_share,  # delete requires share-level trust
    }[action]
    if not allowed:
        raise SessionAclError(
            f"subject {subject_id!r} lacks {action.value} permission on session {session.session_id}"
        )
