#!/usr/bin/env python3
"""Validate governance gate before release."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_change_request(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as e:
        raise SystemExit(f'Invalid change request JSON: {path} ({e})')
    if not isinstance(payload, dict):
        raise SystemExit(f'Invalid change request payload type: {path}')
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate governance release gate')
    parser.add_argument('--root', default='data/governance')
    parser.add_argument('--strict', action='store_true', help='Fail when unresolved CR exists (default true)')
    parser.add_argument('--allow-pending', action='store_true', help='Allow PENDING CRs (not recommended)')
    parser.add_argument(
        '--topic',
        default='',
        help='Only validate change requests for a specific topic (slug-insensitive)',
    )
    args = parser.parse_args()

    strict = True if not args.allow_pending else args.strict

    root = Path(args.root).resolve()
    cr_dir = root / 'change_requests'
    if not cr_dir.exists():
        print(f'[governance] no change request dir: {cr_dir} (pass)')
        return 0

    files = sorted(cr_dir.glob('*.json'))
    if not files:
        print(f'[governance] no change requests under {cr_dir} (pass)')
        return 0

    topic_filter = args.topic.strip().lower()
    if topic_filter:
        filtered: list[Path] = []
        for file_path in files:
            payload = _load_change_request(file_path)
            payload_topic = str(payload.get('topic', '')).strip().lower()
            if payload_topic == topic_filter:
                filtered.append(file_path)
        files = filtered
        if not files:
            print(f'[governance] no change requests for topic="{topic_filter}" (pass)')
            return 0

    status_groups: dict[str, list[str]] = {'PENDING': [], 'APPROVED': [], 'REJECTED': [], 'UNKNOWN': []}
    invalid_approved: list[str] = []

    for file_path in files:
        payload = _load_change_request(file_path)
        status = str(payload.get('status', 'UNKNOWN')).upper()
        if status not in status_groups:
            status = 'UNKNOWN'
        status_groups[status].append(file_path.name)

        if status == 'APPROVED':
            if not payload.get('approved_by') or not payload.get('approved_at_utc'):
                invalid_approved.append(file_path.name)

    scope = f'topic="{topic_filter}" ' if topic_filter else ''
    print(f'[governance] {scope}change request summary:')
    print(f"  total={len(files)} approved={len(status_groups['APPROVED'])} "
          f"rejected={len(status_groups['REJECTED'])} pending={len(status_groups['PENDING'])} "
          f"unknown={len(status_groups['UNKNOWN'])}")

    if invalid_approved:
        print('[governance] invalid approved records (missing reviewer/time):')
        for name in invalid_approved:
            print(f'  - {name}')
        return 2

    if status_groups['UNKNOWN']:
        print('[governance] unknown status records:')
        for name in status_groups['UNKNOWN']:
            print(f'  - {name}')
        return 2

    if strict and status_groups['PENDING']:
        print('[governance] pending CRs block release:')
        for name in status_groups['PENDING']:
            print(f'  - {name}')
        return 2

    print('[governance] release gate passed')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
