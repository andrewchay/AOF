# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
"""W07.02 Cognee version and compatibility contract."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _production_version() -> str:
    document = json.loads(
        (ROOT / "config/capabilities/integration-profiles.json").read_text()
    )
    capabilities = document["profiles"]["production"]["capabilities"]
    return next(item for item in capabilities if item["id"] == "cognee")["probe"][
        "version"
    ]


def test_every_installable_cognee_declaration_matches_production_profile():
    version = _production_version()
    pin = f"cognee[neo4j]=={version}"
    paths = (
        "requirements/cognee.in",
        "services/semantic_middle_layer_api/Dockerfile",
        "services/semantic_middle_layer_api/docker-compose.yml",
        "services/semantic_middle_layer_api/docker-compose.dev.yml",
        "services/semantic_middle_layer_api/docker-compose.observability.yml",
        "services/semantic_middle_layer_api/docker-compose.prod.yml",
    )
    for relative_path in paths:
        assert pin in (ROOT / relative_path).read_text(), relative_path


def test_reference_local_lock_excludes_optional_cognee_distribution():
    lock = (ROOT / "requirements/locks/reference-local.lock").read_text().splitlines()
    assert not any(line.lower().startswith("cognee==") for line in lock)


def test_image_contains_executable_cognee_api_compatibility_gate():
    dockerfile = (ROOT / "services/semantic_middle_layer_api/Dockerfile").read_text()
    assert "tools/ci/check_cognee_compatibility.py" in dockerfile
