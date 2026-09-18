# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""W07.06 required and optional integration profile behavior."""

from __future__ import annotations

import json

import pytest

from bridge.capability_status import CapabilityProfileError, evaluate_capability_profile


def _manifest(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": "aof.integration-profiles/v1",
                "profiles": {
                    "complete": {
                        "capabilities": [
                            {"id": "database", "required": True, "probe": {"type": "fake", "name": "database"}},
                            {"id": "cognee", "required": False, "enabled_env": "ENABLE_COGNEE", "default_enabled": False, "probe": {"type": "fake", "name": "cognee"}},
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_disabled_optional_capability_has_explicit_nonblocking_status(tmp_path):
    report = evaluate_capability_profile(
        "complete",
        {},
        profile_file=_manifest(tmp_path),
        probe=lambda definition, timeout: (True, ""),
    )
    assert report.ready is True
    assert report.capabilities[1].to_dict() == {
        "capability_id": "cognee",
        "required": False,
        "enabled": False,
        "status": "disabled_optional",
        "blocking": False,
        "reason": "optional capability is not enabled",
    }


def test_enabled_optional_failure_blocks_complete_profile(tmp_path):
    report = evaluate_capability_profile(
        "complete",
        {"ENABLE_COGNEE": "true"},
        profile_file=_manifest(tmp_path),
        probe=lambda definition, timeout: (
            definition["name"] != "cognee",
            "dependency unavailable",
        ),
    )
    assert report.ready is False
    assert report.capabilities[1].status == "unavailable"
    assert report.capabilities[1].blocking is True


def test_required_failure_always_blocks_and_success_is_ready(tmp_path):
    failed = evaluate_capability_profile(
        "complete",
        {},
        profile_file=_manifest(tmp_path),
        probe=lambda definition, timeout: (
            definition["name"] != "database",
            "database unavailable",
        ),
    )
    assert failed.ready is False
    ready = evaluate_capability_profile(
        "complete",
        {"ENABLE_COGNEE": "true"},
        profile_file=_manifest(tmp_path),
        probe=lambda definition, timeout: (True, ""),
    )
    assert ready.ready is True
    assert all(item.status == "ready" for item in ready.capabilities)


def test_unknown_profile_and_invalid_flags_fail_closed(tmp_path):
    with pytest.raises(CapabilityProfileError, match="unknown"):
        evaluate_capability_profile("missing", {}, profile_file=_manifest(tmp_path))
    with pytest.raises(CapabilityProfileError, match="boolean"):
        evaluate_capability_profile(
            "complete",
            {"ENABLE_COGNEE": "sometimes"},
            profile_file=_manifest(tmp_path),
            probe=lambda definition, timeout: (True, ""),
        )


def test_nested_missing_python_module_returns_unavailable_without_traceback(tmp_path):
    profile = tmp_path / "missing-module.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "aof.integration-profiles/v1",
                "profiles": {
                    "missing": {
                        "capabilities": [
                            {
                                "id": "nested",
                                "required": True,
                                "probe": {
                                    "type": "python_import",
                                    "module": "parent_that_does_not_exist.child",
                                },
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    report = evaluate_capability_profile("missing", {}, profile_file=profile)
    assert report.ready is False
    assert report.capabilities[0].status == "unavailable"


def test_production_probe_rejects_editable_distribution(tmp_path, monkeypatch):
    class EditableDistribution:
        version = "1.5.4"

        @staticmethod
        def read_text(name):
            assert name == "direct_url.json"
            return json.dumps({"dir_info": {"editable": True}})

    monkeypatch.setattr("bridge.capability_status.importlib.util.find_spec", lambda name: object())
    monkeypatch.setattr(
        "bridge.capability_status.importlib.metadata.distribution",
        lambda name: EditableDistribution(),
    )
    profile = tmp_path / "editable.json"
    profile.write_text(
        json.dumps(
            {
                "schema_version": "aof.integration-profiles/v1",
                "profiles": {
                    "production": {
                        "capabilities": [
                            {
                                "id": "cognee",
                                "required": True,
                                "probe": {
                                    "type": "python_import",
                                    "module": "cognee",
                                    "distribution": "cognee",
                                    "version": "1.5.4",
                                    "reject_editable": True,
                                },
                            }
                        ]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    report = evaluate_capability_profile("production", {}, profile_file=profile)
    assert report.ready is False
    assert report.capabilities[0].reason == "python distribution cognee is editable"
