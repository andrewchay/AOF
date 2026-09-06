"""W01.01 — Default-deny operation registry.

Every public operation (REST method+path, MCP tool) must be registered in
config/capabilities/operations.json with a classification. New operations
that are not registered fail validation instead of silently inheriting
anonymous access.

Classifications:
- public-diagnostic : anonymous health/readiness/telemetry probes
- governed          : semantic governance plane, per-endpoint principal checks
- legacy            : pre-governance endpoints, covered by the strict auth gate
- retired-410       : explicitly retired, must keep returning 410
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

VALID_CLASSIFICATIONS = {
    "public-diagnostic",
    "governed",
    "legacy",
    "retired-410",
}

DEFAULT_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "capabilities" / "operations.json"
)


@dataclass(frozen=True)
class RegisteredOperation:
    operation_id: str
    method: str
    path: str
    classification: str
    public: bool
    auth: str
    policy: Any = None  # OperationPolicy | None (public ops may omit)


class OperationRegistryError(ValueError):
    """Raised when an operation is missing from or invalid in the registry."""


def load_registry(path: str | Path | None = None) -> dict[str, RegisteredOperation]:
    registry_path = Path(path) if path else DEFAULT_REGISTRY_PATH
    if not registry_path.exists():
        raise OperationRegistryError(f"operation registry not found: {registry_path}")
    raw = json.loads(registry_path.read_text(encoding="utf-8"))
    operations: dict[str, RegisteredOperation] = {}
    from bridge.access.policy import OperationPolicy, derive_default_policy

    for item in raw.get("operations", []):
        op = RegisteredOperation(
            operation_id=item["operation_id"],
            method=item["method"],
            path=item["path"],
            classification=item["classification"],
            public=bool(item["public"]),
            auth=item["auth"],
        )
        if op.classification not in VALID_CLASSIFICATIONS:
            raise OperationRegistryError(
                f"invalid classification {op.classification!r} for {op.operation_id}"
            )
        if op.operation_id in operations:
            raise OperationRegistryError(f"duplicate operation_id: {op.operation_id}")
        # W01.01: every non-public operation MUST declare a policy.
        policy_raw = item.get("policy")
        if policy_raw is not None:
            try:
                policy = OperationPolicy.from_dict(policy_raw)
            except Exception as exc:
                raise OperationRegistryError(
                    f"invalid policy for {op.operation_id}: {exc}"
                ) from exc
        elif op.public:
            policy = None  # anonymous diagnostic/retired paths need no role policy
        else:
            raise OperationRegistryError(
                f"operation {op.operation_id} has no authorization policy "
                "(W01.01: new routes cannot ship undeclared)"
            )
        object.__setattr__(op, "policy", policy)
        operations[op.operation_id] = op
    if not operations:
        raise OperationRegistryError("operation registry is empty")
    return operations


def validate_fastapi_app(app: Any, path: str | Path | None = None) -> None:
    """Assert every registered FastAPI route exists in the registry.

    Raises OperationRegistryError listing any unregistered route — new routes
    must be added to the registry together with their classification.
    """
    registry = load_registry(path)
    from fastapi.routing import APIRoute  # local import: only needed for FastAPI apps

    unregistered: list[str] = []
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in route.methods - {"HEAD", "OPTIONS"}:
            op_id = f"{method.lower()}:{route.path}"
            if op_id not in registry:
                unregistered.append(op_id)
    if unregistered:
        raise OperationRegistryError(
            "unregistered operations (add them to config/capabilities/operations.json "
            f"with a classification): {sorted(unregistered)}"
        )


def validate_retired_endpoints(app: Any, path: str | Path | None = None) -> None:
    """Retired operations must keep answering 410 via their handler."""
    registry = load_registry(path)
    retired = {op.path for op in registry.values() if op.classification == "retired-410"}
    if not retired:
        return
    from fastapi.routing import APIRoute

    for route in app.routes:
        if isinstance(route, APIRoute) and route.path in retired:
            for method in route.methods - {"HEAD", "OPTIONS"}:
                op_id = f"{method.lower()}:{route.path}"
                op = registry.get(op_id)
                if op is None:
                    raise OperationRegistryError(f"retired route missing from registry: {op_id}")
