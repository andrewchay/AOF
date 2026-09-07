#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""W11.02 — Closure gate: verify the required job matrix concluded successfully.

GitHub required checks can accept skipped/neutral conclusions, so the
platform green alone is not trusted. This gate reads the ACTUAL result of
every required job and fails on failure/skipped/cancelled/absence.

Usage in CI (closure-gate job):
    python tools/ci/check_required_jobs.py \\
        --job lint-type=success --job unit-contract=success ...

Exit codes: 0 all required jobs succeeded, 1 matrix not satisfied.
"""

from __future__ import annotations

import argparse
import sys

REQUIRED_JOBS = (
    'lint-type',
    'unit-contract',
    'governance',
    'api-contract',
)

ACCEPTABLE = {'success'}


def main() -> int:
    parser = argparse.ArgumentParser(description='Verify required job matrix')
    parser.add_argument(
        '--job', action='append', default=[],
        metavar='NAME=RESULT',
        help='job conclusion; repeat per job (defaults validate the standard matrix)',
    )
    args = parser.parse_args()

    conclusions: dict[str, str] = {}
    for item in args.job:
        if '=' not in item:
            print(f'FAIL: --job expects NAME=RESULT, got {item!r}')
            return 1
        name, _, result = item.partition('=')
        conclusions[name.strip()] = result.strip().lower()

    failures: list[str] = []
    for job in REQUIRED_JOBS:
        result = conclusions.get(job)
        if result is None:
            failures.append(f'{job}: MISSING (no conclusion reported)')
        elif result not in ACCEPTABLE:
            failures.append(f'{job}: result={result!r} (want success)')

    # jobs reported but not required are noted (configuration drift signal)
    extra = sorted(set(conclusions) - set(REQUIRED_JOBS))
    for name in extra:
        print(f'note: non-required job reported: {name}={conclusions[name]}')

    if failures:
        print('closure gate: REQUIRED JOB MATRIX NOT SATISFIED')
        for f in failures:
            print(f'  FAIL {f}')
        return 1
    print(f'closure gate: all {len(REQUIRED_JOBS)} required jobs succeeded')
    return 0


if __name__ == '__main__':
    sys.exit(main())
