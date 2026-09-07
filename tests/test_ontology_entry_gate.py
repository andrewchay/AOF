# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Tests for AOF ontology adherence gate + public ontology injection API (阶段 B)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from bridge.cognee_add_runner import _check_ontology_adherence
from bridge.errors import OntologyConfigError as E


def _spec(ontology=None):
    return {"ontology": ontology or {}, "dataset": "d"}


def test_no_ontology_no_env_is_silent():
    with patch.dict("os.environ", {}, clear=False):
        r = _check_ontology_adherence(_spec())
    assert r == {"ontology_spec": False, "file_exists": None, "via_env": False, "used_env_vars": []}


def test_spec_ontology_missing_file_reported():
    r = _check_ontology_adherence(_spec({"file": "/no/such/file.owl"}))
    assert r["ontology_spec"] is True
    assert r["file_exists"] is False
    assert r["via_env"] is False


def test_env_injection_without_spec_detected():
    with patch.dict("os.environ", {"ONTOLOGY_FILE_PATH": "/x.owl"}, clear=False):
        r = _check_ontology_adherence(_spec())
    assert r["via_env"] is True
    assert r["used_env_vars"] == ["ONTOLOGY_FILE_PATH"]
    assert r["ontology_spec"] is False


def test_spec_plus_env_flags_coexist():
    with patch.dict("os.environ", {"ONTOLOGY_RESOLVER": "rdflib"}, clear=False):
        r = _check_ontology_adherence(_spec({"file": "/x.owl"}))
    assert r["ontology_spec"] is True
    assert r["via_env"] is True


def test_apply_ontology_none_without_spec():
    from bridge.ontology_adapter import apply_ontology
    assert apply_ontology(_spec()) is None


def test_apply_ontology_missing_file_raises():
    from bridge.ontology_adapter import apply_ontology
    with pytest.raises(E):
        apply_ontology(_spec({"file": "/no/such/file.owl"}))


def test_apply_ontology_builds_resolver():
    pytest.importorskip(
        "cognee",
        reason="requires the externally provisioned Cognee ontology runtime",
    )
    from bridge.ontology_adapter import apply_ontology
    spec = _spec({"file": "examples/cso_validation/data/cso_oncology.owl", "matching_cutoff": 0.8})
    cfg = apply_ontology(spec)
    assert cfg and "ontology_config" in cfg
    resolver = cfg["ontology_config"].get("ontology_resolver")
    assert resolver is not None
    assert hasattr(resolver, "ontology_file")
