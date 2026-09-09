# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "tools" / "ci" / "validate_remediation_register.py"
REGISTER = ROOT / "docs" / "remediation" / "2026-09-05" / "closure-register.json"


def _run(path: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(CHECKER), str(path), *args],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout + result.stderr


def _minimal_register() -> dict[str, object]:
    return {
        "schema_version": "aof.remediation-plan/v1",
        "status": "in_progress",
        "baseline_commit": "a" * 40,
        "closure_rule": "Every item requires evidence and independent review.",
        "finding_ids": ["D01"],
        "work_packages": [
            {
                "id": "W00",
                "title": "Baseline",
                "owner_role": "Engineering",
                "depends_on": [],
                "finding_ids": ["D01"],
            }
        ],
        "items": [
            {
                "id": "W00.01",
                "work_package": "W00",
                "title": "Freeze baseline",
                "finding_ids": ["D01"],
                "owner_role": "Engineering",
                "owner_person": None,
                "depends_on_packages": [],
                "status": "not_started",
                "acceptance": "Baseline is frozen.",
                "implementation_commit": None,
                "tests": [],
                "evidence": [],
                "reviewer": None,
                "closed_at": None,
            }
        ],
        "assessment": {
            "assessed_commit": "b" * 40,
            "assessed_at": "2026-09-09T00:00:00+08:00",
            "status_counts": {
                "closed": 0,
                "in_progress": 0,
                "mitigated": 0,
                "not_started": 1,
            },
        },
        "progress_note": "Current assessment.",
    }


def test_repository_register_is_structurally_valid():
    code, out = _run(REGISTER)
    assert code == 0, out
    assert "80 items" in out


def test_duplicate_json_key_is_rejected(tmp_path: Path):
    path = tmp_path / "register.json"
    path.write_text('{"schema_version":"one","schema_version":"two"}')
    code, out = _run(path)
    assert code == 1
    assert "duplicate JSON key" in out


def test_not_started_item_cannot_claim_implementation(tmp_path: Path):
    payload = _minimal_register()
    item = payload["items"][0]  # type: ignore[index]
    item["implementation_commit"] = "deadbeef"  # type: ignore[index]
    path = tmp_path / "register.json"
    path.write_text(json.dumps(payload))
    code, out = _run(path)
    assert code == 1
    assert "not_started but has implementation evidence" in out


def test_closed_item_requires_independent_review(tmp_path: Path):
    payload = _minimal_register()
    item = payload["items"][0]  # type: ignore[index]
    item.update(  # type: ignore[union-attr]
        status="closed",
        owner_person="implementer@example.com",
        implementation_commit="deadbeef",
        tests=["tests/test_baseline.py::test_frozen"],
        evidence=["artifact://baseline/deadbeef"],
        closed_at="2026-09-09T00:00:00+08:00",
    )
    payload["assessment"]["status_counts"] = {  # type: ignore[index]
        "closed": 1,
        "in_progress": 0,
        "mitigated": 0,
        "not_started": 0,
    }
    path = tmp_path / "register.json"
    path.write_text(json.dumps(payload))
    code, out = _run(path)
    assert code == 1
    assert "independent reviewer" in out


def test_complete_mode_rejects_open_items(tmp_path: Path):
    path = tmp_path / "register.json"
    path.write_text(json.dumps(_minimal_register()))
    code, out = _run(path, "--require-complete")
    assert code == 1
    assert "requires every item to be closed" in out


def test_missing_test_evidence_file_is_rejected(tmp_path: Path):
    payload = _minimal_register()
    item = payload["items"][0]  # type: ignore[index]
    item.update(  # type: ignore[union-attr]
        status="in_progress",
        tests=["tests/does_not_exist.py::test_claim"],
    )
    payload["assessment"]["status_counts"] = {  # type: ignore[index]
        "closed": 0,
        "in_progress": 1,
        "mitigated": 0,
        "not_started": 0,
    }
    # Match the production register depth so the validator resolves repo root.
    path = tmp_path / "repo" / "docs" / "remediation" / "date" / "register.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload))
    code, out = _run(path)
    assert code == 1
    assert "test file does not exist" in out
