# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W12.01 generated delivery-surface inventory."""

from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path

import mcp_server

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_counts_real_rest_mcp_cli_and_ui_surfaces():
    manifest = json.loads(
        (ROOT / "config/capabilities/capability-manifest.json").read_text(encoding="utf-8")
    )
    operations = json.loads(
        (ROOT / "config/capabilities/operations.json").read_text(encoding="utf-8")
    )["operations"]
    rest = [item for item in operations if item["method"] != "TOOL"]

    assert manifest["summary"]["http_operations"] == len(rest)
    assert manifest["summary"]["mcp_tools"] == len(mcp_server.build_server().tools)
    assert manifest["surfaces"]["cli"] == sorted(path.name for path in ROOT.glob("aof_*.py"))
    assert manifest["summary"]["ui_routes"] == len(manifest["surfaces"]["ui_routes"])
    for dependency in manifest["surfaces"]["dependency_profiles"]:
        path = ROOT / dependency["path"]
        assert path.is_file()
        assert dependency["source_digest"] == "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        assert dependency["dependency_count"] == len(dependency["declared_dependencies"])

    deployments = manifest["surfaces"]["deployment_profiles"]
    assert manifest["summary"]["deployment_profiles"] == len(deployments)
    ci_profile = next(item for item in deployments if item["profile_id"] == "ci-required-jobs")
    assert {"governance", "api-contract", "enterprise-integration", "image-smoke"} <= set(ci_profile["jobs"])
    for profile in deployments:
        source = ROOT / profile["source"]
        assert source.is_file()
        assert profile["source_digest"] == "sha256:" + hashlib.sha256(source.read_bytes()).hexdigest()


def test_inventory_and_readme_are_regeneratable_without_drift():
    result = subprocess.run(
        [sys.executable, "tools/ci/generate_capability_inventory.py", "--check"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
