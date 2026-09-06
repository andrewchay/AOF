"""W11.02 — Closure gate: the required job matrix must be fully satisfied.

GitHub required checks may accept skipped/neutral conclusions, so the
summary gate reads actual conclusions and rejects failure, skipped,
cancelled and absence alike.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

CHECKER = Path(__file__).resolve().parents[1] / 'tools' / 'ci' / 'check_required_jobs.py'


def _run(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(CHECKER), *args], capture_output=True, text=True
    )
    return result.returncode, result.stdout + result.stderr


def test_all_satisfied_passes():
    code, out = _run(
        '--job', 'lint-type=success',
        '--job', 'unit-contract=success',
        '--job', 'governance=success',
        '--job', 'api-contract=success',
    )
    assert code == 0
    assert 'all 4 required jobs succeeded' in out


def test_missing_job_fails():
    code, out = _run('--job', 'lint-type=success', '--job', 'unit-contract=success')
    assert code == 1
    assert 'MISSING' in out
    assert 'governance' in out and 'api-contract' in out


def test_failure_and_skipped_and_cancelled_fail():
    for bad in ('failure', 'skipped', 'cancelled', 'neutral'):
        code, out = _run(
            '--job', 'lint-type=success',
            '--job', 'unit-contract=success',
            '--job', 'governance=success',
            '--job', f'api-contract={bad}',
        )
        assert code == 1, f'{bad} must not satisfy the gate'
        assert bad in out


def test_non_required_jobs_are_noted_not_blocking():
    code, out = _run(
        '--job', 'lint-type=success',
        '--job', 'unit-contract=success',
        '--job', 'governance=success',
        '--job', 'api-contract=success',
        '--job', 'experimental-thing=skipped',
    )
    assert code == 0
    assert 'non-required job reported: experimental-thing' in out


def test_malformed_argument_fails():
    code, _out = _run('--job', 'lint-type')
    assert code == 1
