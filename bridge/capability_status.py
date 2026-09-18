# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""Evaluate required, enabled, and intentionally disabled integrations."""

from __future__ import annotations

import importlib.util
import importlib.metadata
import json
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PROFILE_SCHEMA = "aof.integration-profiles/v1"
DEFAULT_PROFILE_FILE = (
    Path(__file__).resolve().parents[1]
    / "config/capabilities/integration-profiles.json"
)


class CapabilityProfileError(ValueError):
    pass


@dataclass(frozen=True)
class CapabilityState:
    capability_id: str
    required: bool
    enabled: bool
    status: str
    blocking: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "capability_id": self.capability_id,
            "required": self.required,
            "enabled": self.enabled,
            "status": self.status,
            "blocking": self.blocking,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CapabilityProfileReport:
    profile: str
    ready: bool
    capabilities: tuple[CapabilityState, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "aof.capability-profile-report/v1",
            "profile": self.profile,
            "ready": self.ready,
            "capabilities": [item.to_dict() for item in self.capabilities],
        }


def _flag(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise CapabilityProfileError("capability enable flags must be boolean")


def _probe(definition: Mapping[str, Any], timeout: float) -> tuple[bool, str]:
    kind = definition.get("type")
    if kind == "python_import":
        module = str(definition.get("module", "")).strip()
        try:
            available = bool(module and importlib.util.find_spec(module))
        except (ImportError, AttributeError, ValueError):
            available = False
        if not available:
            return False, f"python module {module} is unavailable"
        distribution_name = str(definition.get("distribution", "")).strip()
        if distribution_name:
            try:
                distribution = importlib.metadata.distribution(distribution_name)
            except importlib.metadata.PackageNotFoundError:
                return False, f"python distribution {distribution_name} is unavailable"
            expected_version = str(definition.get("version", "")).strip()
            if expected_version and distribution.version != expected_version:
                return False, f"python distribution {distribution_name} version mismatch"
            if bool(definition.get("reject_editable", False)):
                direct_url = distribution.read_text("direct_url.json")
                if direct_url:
                    try:
                        editable = bool(json.loads(direct_url).get("dir_info", {}).get("editable"))
                    except json.JSONDecodeError:
                        return False, f"python distribution {distribution_name} origin metadata is invalid"
                    if editable:
                        return False, f"python distribution {distribution_name} is editable"
        return True, ""
    if kind == "tcp":
        host, port = str(definition.get("host", "")), int(definition.get("port", 0))
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True, ""
        except OSError:
            return False, f"TCP endpoint {host}:{port} is unavailable"
    if kind == "http":
        url = str(definition.get("url", ""))
        accepted = {int(value) for value in definition.get("accepted_status", [200])}
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                status = int(response.status)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
        except (OSError, ValueError):
            return False, "configured HTTP endpoint is unavailable"
        return status in accepted, f"configured HTTP endpoint returned status {status}"
    raise CapabilityProfileError(f"unsupported capability probe type: {kind!r}")


def evaluate_capability_profile(
    profile: str,
    environment: Mapping[str, str],
    *,
    profile_file: str | Path = DEFAULT_PROFILE_FILE,
    probe: Callable[[Mapping[str, Any], float], tuple[bool, str]] = _probe,
    timeout: float = 1.0,
) -> CapabilityProfileReport:
    document = json.loads(Path(profile_file).read_text(encoding="utf-8"))
    if document.get("schema_version") != PROFILE_SCHEMA:
        raise CapabilityProfileError(f"capability profile schema must be {PROFILE_SCHEMA}")
    definition = document.get("profiles", {}).get(profile)
    if not isinstance(definition, Mapping):
        raise CapabilityProfileError(f"unknown capability profile: {profile}")
    states = []
    seen = set()
    for item in definition.get("capabilities", []):
        capability_id = str(item.get("id", "")).strip()
        if not capability_id or capability_id in seen:
            raise CapabilityProfileError("capability ids must be non-empty and unique")
        seen.add(capability_id)
        required = bool(item.get("required", False))
        enabled_env = item.get("enabled_env")
        enabled = required or _flag(
            environment.get(str(enabled_env)) if enabled_env else None,
            bool(item.get("default_enabled", False)),
        )
        if not enabled:
            states.append(
                CapabilityState(
                    capability_id,
                    required,
                    False,
                    "disabled_optional",
                    False,
                    "optional capability is not enabled",
                )
            )
            continue
        available, reason = probe(item.get("probe", {}), timeout)
        states.append(
            CapabilityState(
                capability_id,
                required,
                True,
                "ready" if available else "unavailable",
                not available,
                "probe passed" if available else reason,
            )
        )
    return CapabilityProfileReport(
        profile,
        not any(item.blocking for item in states),
        tuple(states),
    )
