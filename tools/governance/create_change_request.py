#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Create a semantic governance change request file."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def _slug(text: str) -> str:
    out = ''.join(ch.lower() if ch.isalnum() else '_' for ch in text.strip())
    while '__' in out:
        out = out.replace('__', '_')
    return out.strip('_') or 'change'


def main() -> int:
    parser = argparse.ArgumentParser(description='Create governance change request')
    parser.add_argument('--topic', required=True)
    parser.add_argument('--title', required=True)
    parser.add_argument('--owner', required=True)
    parser.add_argument('--impact', default='medium', choices=['low', 'medium', 'high'])
    parser.add_argument('--summary', default='')
    parser.add_argument('--changes', default='[]', help='JSON list of change items')
    parser.add_argument('--root', default='data/governance')
    args = parser.parse_args()

    ts = datetime.utcnow().strftime('%Y%m%d_%H%M%S')
    cr_id = f"CR-{ts}-{_slug(args.topic)}"
    out_dir = Path(args.root).resolve() / 'change_requests'
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        change_items = json.loads(args.changes)
        if not isinstance(change_items, list):
            raise ValueError('changes must be a JSON list')
    except Exception as e:
        raise SystemExit(f'Invalid --changes JSON: {e}')

    payload = {
        'id': cr_id,
        'status': 'PENDING',
        'topic': args.topic,
        'title': args.title,
        'owner': args.owner,
        'impact': args.impact,
        'summary': args.summary,
        'changes': change_items,
        'created_at_utc': datetime.utcnow().isoformat() + 'Z',
        'approved_at_utc': None,
        'approved_by': None,
    }

    path = out_dir / f'{cr_id}.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    print(path)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
