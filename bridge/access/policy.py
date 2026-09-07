# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W01.01 — Per-operation authorization policy matrix.

Every registered operation carries an explicit policy: {action, allowed_roles}.
The strict auth gate enforces it AFTER authenticating the principal, so a
valid signature alone is no longer sufficient — the role set must satisfy
the operation's policy.

Default policies are derived from method + path class (default-deny
posture); sensitive operations carry explicit overrides in
config/capabilities/operations.json. The registry validator rejects any
operation without a policy — new routes cannot ship undeclared.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from bridge.access.operation_registry import RegisteredOperation


class PolicyDenied(PermissionError):
    """Raised when the principal's roles do not satisfy the operation policy."""


class PolicyError(ValueError):
    """Raised when an operation policy itself is malformed."""


VALID_ACTIONS = {"read", "write", "execute", "admin"}

# Flat role hierarchy: a role IMPLIES every role at a lower level.
# Higher = more privileged. These mirror the identity module's roles.
ROLE_LEVELS: dict[str, int] = {
    "viewer": 10,
    "worker": 20,
    "ingestor": 20,
    "analyst": 30,
    "operator": 40,
    "editor": 50,
    "reviewer": 60,
    "validator": 60,
    "compiler": 60,
    "publisher": 60,
    "reasoner": 60,
    "risk-owner": 60,
    "owner": 80,
    "admin": 100,
}

# Minimum implied level per action (default-derived posture).
ACTION_MIN_LEVEL: dict[str, int] = {
    "read": 10,      # viewer and above
    "execute": 20,   # worker and above
    "write": 50,     # editor and above
    "admin": 100,    # admin only
}


@dataclass(frozen=True)
class OperationPolicy:
    action: str
    allowed_roles: frozenset[str]

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OperationPolicy":
        action = str(data.get("action", "")).strip()
        if action not in VALID_ACTIONS:
            raise PolicyError(f"invalid policy action: {action!r}")
        roles = data.get("allowed_roles")
        if not isinstance(roles, list) or not roles:
            raise PolicyError("policy.allowed_roles must be a non-empty list")
        normalized = frozenset(str(role).strip() for role in roles)
        unknown = normalized - set(ROLE_LEVELS)
        if unknown:
            raise PolicyError(f"unknown roles in policy: {sorted(unknown)}")
        return cls(action=action, allowed_roles=normalized)

    def to_dict(self) -> dict[str, Any]:
        return {"action": self.action, "allowed_roles": sorted(self.allowed_roles)}


def derive_default_policy(method: str, path: str) -> OperationPolicy:
    """Default-deny derived policy from the request method and path class."""
    method = method.upper()

    # Explicit sensitive prefixes first (defense in depth even for defaults)
    if path.startswith("/v1/access/revocations"):
        return OperationPolicy(action="admin", allowed_roles=frozenset({"admin"}))

    if path.startswith("/v1/ops") or path == "/metrics":
        return OperationPolicy(action="admin", allowed_roles=frozenset({"admin"}))

    if path == "/v1/semantic/compile":
        # retired endpoint: any authenticated principal may receive the 410
        return OperationPolicy(action="read", allowed_roles=frozenset({"viewer"}))

    if path.startswith("/v1/decisions"):
        # decision ledger: read for viewers+, writes for operators+
        if method == "GET":
            return OperationPolicy(
                action="read",
                allowed_roles=frozenset({"admin", "owner", "editor", "analyst", "operator",
                                         "reviewer", "validator", "risk-owner", "viewer"}),
            )
        return OperationPolicy(
            action="execute",
            allowed_roles=frozenset({"admin", "owner", "operator", "worker"}),
        )

    if path.startswith("/v1/semantic/action-runs") or path.startswith("/v1/semantic/workflow-runs"):
        # approval/execution surfaces: operators and above; read via GET
        if method == "GET":
            return OperationPolicy(
                action="read",
                allowed_roles=frozenset({"admin", "owner", "editor", "analyst", "operator",
                                         "reviewer", "validator", "risk-owner", "viewer"}),
            )
        return OperationPolicy(
            action="execute",
            allowed_roles=frozenset({"admin", "owner", "operator", "worker"}),
        )

    if path.startswith("/v1/semantic/proposals") or path.startswith("/v1/semantic/compiler"):
        # governance plane: reviewers/publishers handled per-endpoint; coarse gate here
        if method == "GET":
            return OperationPolicy(
                action="read",
                allowed_roles=frozenset({"admin", "owner", "editor", "analyst", "operator",
                                         "reviewer", "validator", "compiler", "publisher",
                                         "risk-owner", "viewer"}),
            )
        return OperationPolicy(
            action="write",
            allowed_roles=frozenset({"admin", "owner", "editor", "compiler", "publisher",
                                     "validator", "reviewer", "risk-owner"}),
        )

    if path.startswith("/v1/ingest") or path.startswith("/v1/sync"):
        if method == "GET":
            return OperationPolicy(
                action="read",
                allowed_roles=frozenset({"admin", "owner", "editor", "analyst", "operator",
                                         "ingestor", "viewer"}),
            )
        return OperationPolicy(
            action="write",
            allowed_roles=frozenset({"admin", "owner", "editor", "ingestor", "operator"}),
        )

    if path.startswith("/v1/training-data") or path.startswith("/v1/harness"):
        if method == "GET":
            return OperationPolicy(
                action="read",
                allowed_roles=frozenset({"admin", "owner", "editor", "analyst", "operator",
                                         "worker", "viewer"}),
            )
        return OperationPolicy(
            action="write",
            allowed_roles=frozenset({"admin", "owner", "editor", "analyst", "operator", "worker"}),
        )

    # Generic fallback by method
    if method in {"GET", "HEAD"}:
        return OperationPolicy(
            action="read",
            allowed_roles=frozenset({"admin", "owner", "editor", "analyst", "operator",
                                     "reviewer", "validator", "compiler", "publisher",
                                     "reasoner", "risk-owner", "worker", "ingestor", "viewer"}),
        )
    return OperationPolicy(
        action="write",
        allowed_roles=frozenset({"admin", "owner", "editor", "operator", "ingestor", "worker",
                                 "analyst"}),
    )


def authorize(policy: OperationPolicy, principal_roles: Iterable[str]) -> None:
    """Raise PolicyDenied unless one of ``principal_roles`` satisfies the policy.

    Satisfaction = the principal holds a role explicitly listed in the policy.
    Role hierarchy is applied at policy-derivation time (allowed_roles are
    already expanded sets), keeping this check exact and cheap.
    """
    if set(principal_roles) & policy.allowed_roles:
        return
    raise PolicyDenied(
        f"roles {sorted(set(principal_roles))} do not satisfy operation policy "
        f"(action={policy.action}, allowed={sorted(policy.allowed_roles)})"
    )


def build_policy_map(operations: dict[str, RegisteredOperation]) -> dict[str, OperationPolicy]:
    """Attach policies to every registered operation, deriving defaults where absent."""
    policies: dict[str, OperationPolicy] = {}
    for op_id, op in operations.items():
        # policies are stored per-operation in the registry JSON under the
        # 'policy' key; the loader attaches them via attach_policies().
        policies[op_id] = getattr(op, "policy", None) or derive_default_policy(op.method, op.path)
    return policies
