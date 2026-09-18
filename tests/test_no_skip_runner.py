# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

RUNNER = Path(__file__).resolve().parents[1] / "tools" / "ci" / "run_pytest_no_skips.py"


def _run(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(RUNNER), "-q", "-o", "addopts=", str(path)],
        capture_output=True,
        text=True,
    )


def test_runner_accepts_profile_without_skips(tmp_path: Path):
    test_file = tmp_path / "test_profile.py"
    test_file.write_text("def test_ok():\n    assert True\n")
    result = _run(test_file)
    assert result.returncode == 0, result.stdout + result.stderr


def test_runner_rejects_any_skip(tmp_path: Path):
    test_file = tmp_path / "test_profile.py"
    test_file.write_text("import pytest\n\ndef test_skip():\n    pytest.skip('missing service')\n")
    result = _run(test_file)
    assert result.returncode == 1
    assert "enterprise profile rejected skipped tests" in result.stdout
    assert "SKIPPED" in result.stdout
