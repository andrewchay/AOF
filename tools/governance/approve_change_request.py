#!/usr/bin/env python3
# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Approve or reject a semantic governance change request."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description='Approve governance change request')
    parser.add_argument('--file', required=True, help='Path to CR JSON')
    parser.add_argument('--reviewer', required=True)
    parser.add_argument('--action', required=True, choices=['approve', 'reject'])
    parser.add_argument('--comment', default='')
    parser.add_argument('--audit-root', default='data/governance/audit_logs')
    args = parser.parse_args()

    path = Path(args.file).resolve()
    if not path.exists():
        raise SystemExit(f'CR file not found: {path}')

    payload = json.loads(path.read_text(encoding='utf-8'))
    if payload.get('status') != 'PENDING':
        raise SystemExit(f"CR status is not PENDING: {payload.get('status')}")

    now = datetime.utcnow().isoformat() + 'Z'
    payload['status'] = 'APPROVED' if args.action == 'approve' else 'REJECTED'
    payload['approved_by'] = args.reviewer
    payload['approved_at_utc'] = now
    payload['review_comment'] = args.comment
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    audit_root = Path(args.audit_root).resolve()
    audit_root.mkdir(parents=True, exist_ok=True)
    audit_event = {
        'time_utc': now,
        'action': args.action,
        'reviewer': args.reviewer,
        'cr_id': payload.get('id'),
        'file': str(path),
        'status_after': payload.get('status'),
    }
    audit_file = audit_root / f"{payload.get('id', 'unknown')}_{args.action}_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}.json"
    audit_file.write_text(json.dumps(audit_event, ensure_ascii=False, indent=2), encoding='utf-8')

    print(path)
    print(audit_file)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
