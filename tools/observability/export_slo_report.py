#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Export SLO snapshot report from AOF API."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import httpx


def _to_md(snapshot: dict) -> str:
    slo = snapshot.get('slo', {})
    checks = snapshot.get('checks', [])
    counters = snapshot.get('counters', {})
    lines = [
        '# AOF SLO Snapshot Report',
        '',
        f"- generated_at_utc: `{datetime.utcnow().isoformat()}Z`",
        f"- sample_size: `{snapshot.get('window', {}).get('sample_size', 0)}`",
        f"- overall_passed: `{snapshot.get('overall_passed')}`",
        '',
        '## SLO Metrics',
        '',
        f"- availability_error_rate: `{slo.get('availability_error_rate')}`",
        f"- latency_ms_p50: `{slo.get('latency_ms_p50')}`",
        f"- latency_ms_p95: `{slo.get('latency_ms_p95')}`",
        f"- latency_ms_p99: `{slo.get('latency_ms_p99')}`",
        '',
        '## Counters',
        '',
        f"- requests_total: `{counters.get('requests_total', 0)}`",
        f"- errors_total: `{counters.get('errors_total', 0)}`",
        '',
        '## Checks',
        '',
    ]
    if not checks:
        lines.append('- no targets configured')
    else:
        for c in checks:
            lines.append(
                f"- {c.get('name')}: current=`{c.get('current')}` "
                f"target=`{c.get('operator')} {c.get('target')}` passed=`{c.get('passed')}`"
            )
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='Export AOF SLO report')
    parser.add_argument('--base-url', default='http://127.0.0.1:8787')
    parser.add_argument('--out-dir', default='reports/slo')
    parser.add_argument('--format', choices=['md', 'json', 'both'], default='both')
    args = parser.parse_args()

    resp = httpx.get(f"{args.base_url.rstrip('/')}/v1/ops/slo", timeout=20.0)
    resp.raise_for_status()
    snapshot = resp.json()

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.format in ('json', 'both'):
        json_file = out_dir / f'slo_snapshot_{ts}.json'
        json_file.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json_file)

    if args.format in ('md', 'both'):
        md_file = out_dir / f'slo_snapshot_{ts}.md'
        md_file.write_text(_to_md(snapshot), encoding='utf-8')
        print(md_file)

    return 0


if __name__ == '__main__':
    raise SystemExit(main())
