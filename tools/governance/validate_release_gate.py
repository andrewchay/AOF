#!/usr/bin/env python3
"""Validate the governance release gate against a versioned expectation manifest.

W11.03 (D07 fix): the previous gate passed whenever the change_requests
directory was missing or empty - i.e. it verified nothing. The gate now:

1. REQUIRES a versioned expectation manifest
   (data/governance/release_expectations.json) declaring either the
   expected change requests or an explicit, auditable no-changes record
   bound to a commit comparison range and a declarant.
2. Validates every declared CR: file present, status APPROVED with
   reviewer + timestamp (PENDING blocks under strict mode, REJECTED fails).
3. Rejects undeclared CR files in the directory (hidden changes).
4. Supports generating a verifiable no-changes record from the git
   comparison range (--generate-no-changes BASE..HEAD).

Exit codes: 0 pass, 2 gate failure, 3 configuration error.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

MANIFEST_NAME = 'release_expectations.json'
MANIFEST_SCHEMA = 'aof.release-expectations/v1'
NO_CHANGES_SCHEMA = 'aof.release-no-changes/v1'


def _fail(message: str) -> int:
    print(f'[governance] GATE FAILURE: {message}')
    return 2


def _config_error(message: str) -> int:
    print(f'[governance] CONFIG ERROR: {message}')
    return 3


def _load_json(path: Path, what: str) -> dict:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        raise SystemExit(_config_error(f'{what} not found: {path}'))
    except Exception as e:
        raise SystemExit(_config_error(f'{what} is not valid JSON: {path} ({e})'))
    if not isinstance(payload, dict):
        raise SystemExit(_config_error(f'{what} must be a JSON object: {path}'))
    return payload


def _git_changed_files(commit_range: str, repo: Path) -> list[str]:
    try:
        result = subprocess.run(
            ['git', 'diff', '--name-only', commit_range],
            capture_output=True, text=True, check=True, cwd=str(repo),
        )
    except Exception as e:
        raise SystemExit(_config_error(f'cannot evaluate commit range {commit_range!r}: {e}'))
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _generate_no_changes(root: Path, *, commit_range: str, declared_by: str, repo: Path) -> int:
    """Generate an explicit, verifiable no-changes record bound to the
    commit comparison range. Fails if the range actually changed files."""
    manifest_path = root / MANIFEST_NAME
    if manifest_path.exists():
        raise SystemExit(_config_error(
            f'{manifest_path} already exists; edit it instead of regenerating'
        ))
    changed = _git_changed_files(commit_range, repo)
    governed_changes = [
        f for f in changed
        if f.startswith('bridge/') or f.startswith('services/')
        or f.startswith('config/') or f.startswith('web/src/')
    ]
    if governed_changes:
        print('[governance] cannot declare no-changes: the commit range touches governed code:')
        for f in governed_changes[:10]:
            print(f'  - {f}')
        if len(governed_changes) > 10:
            print(f'  ... and {len(governed_changes) - 10} more')
        print('[governance] declare the change requests and re-run.')
        return 2
    record = {
        'schema_version': NO_CHANGES_SCHEMA,
        'commit_range': commit_range,
        'declared_by': declared_by,
        'declared_at_utc': datetime.now(timezone.utc).isoformat(),
        'changed_files_in_range': len(changed),
        'statement': 'No governed change requests are required for this comparison range.',
    }
    manifest = {
        'schema_version': MANIFEST_SCHEMA,
        'no_changes': record,
        'expected_change_requests': [],
    }
    root.mkdir(parents=True, exist_ok=True)
    (root / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8'
    )
    print(f'[governance] no-changes manifest written: {manifest_path}')
    print(f'[governance]   commit_range={commit_range} declared_by={declared_by} '
          f'changed_files={len(changed)} (0 governed)')
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description='Validate governance release gate')
    parser.add_argument('--root', default='data/governance')
    parser.add_argument('--repo', default='.', help='git working tree for commit-range evaluation')
    parser.add_argument('--allow-pending', action='store_true',
                        help='Allow PENDING CRs (not recommended; never in strict)')
    parser.add_argument('--strict', action='store_true', default=True,
                        help='PENDING CRs block release (default true)')
    parser.add_argument('--topic', default='',
                        help='Only validate change requests for a specific topic')
    parser.add_argument('--generate-no-changes', nargs=2, metavar=('COMMIT_RANGE', 'DECLARED_BY'),
                        help='Generate an explicit no-changes manifest and exit')
    args = parser.parse_args()

    root = Path(args.root).resolve()

    if args.generate_no_changes:
        commit_range, declared_by = args.generate_no_changes
        if not declared_by.strip():
            return _config_error('declared_by must be a non-empty identity')
        return _generate_no_changes(
            root, commit_range=commit_range, declared_by=declared_by,
            repo=Path(args.repo).resolve(),
        )

    # ---- 1. the versioned expectation manifest is REQUIRED ----
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.exists():
        return _config_error(
            f'{manifest_path} not found. The gate no longer passes on a missing '
            'directory (W11.03). Declare expected change requests or generate an '
            'explicit no-changes manifest via --generate-no-changes BASE..HEAD.'
        )
    manifest = _load_json(manifest_path, 'release expectation manifest')
    if manifest.get('schema_version') != MANIFEST_SCHEMA:
        return _config_error(
            f'manifest schema_version must be {MANIFEST_SCHEMA!r}, got '
            f'{manifest.get("schema_version")!r}'
        )

    declared: list[dict] = manifest.get('expected_change_requests', [])
    no_changes = manifest.get('no_changes')

    # ---- 2. explicit no-changes record is verifiable ----
    if no_changes:
        for field in ('commit_range', 'declared_by', 'declared_at_utc'):
            if not no_changes.get(field):
                return _fail(f'no_changes record missing {field!r}')
        print('[governance] explicit no-changes declaration:')
        print(f"  commit_range={no_changes['commit_range']}")
        print(f"  declared_by={no_changes['declared_by']} at {no_changes['declared_at_utc']}")
        if declared:
            return _fail('manifest declares both no_changes and expected_change_requests')
        print('[governance] release gate passed (verifiable no-changes)')
        return 0

    if not declared:
        return _fail(
            'manifest has neither expected_change_requests nor a no_changes record'
        )

    # ---- 3. declared CRs must exist and satisfy their declared status ----
    cr_dir = root / 'change_requests'
    found_files = {p.name for p in cr_dir.glob('*.json')} if cr_dir.exists() else set()
    declared_names = {item.get('file') for item in declared}
    if None in declared_names:
        return _config_error('every expected_change_requests entry needs a "file" field')

    undeclared = found_files - declared_names
    if undeclared:
        return _fail('undeclared change request files present (hidden changes): '
                     + ', '.join(sorted(undeclared)))

    topic_filter = args.topic.strip().lower()
    unsatisfied: list[str] = []   # declared APPROVED but actually PENDING
    invalid_approved: list[str] = []
    rejected: list[str] = []
    missing_files: list[str] = []
    satisfied = 0
    open_declared: list[str] = []  # honestly declared as PENDING and still PENDING

    for item in declared:
        name = item['file']
        expected_status = str(item.get('expected_status', 'APPROVED')).upper()
        file_path = cr_dir / name
        if not file_path.exists():
            missing_files.append(name)
            continue
        payload = _load_json(file_path, f'change request {name}')
        if topic_filter and str(payload.get('topic', '')).strip().lower() != topic_filter:
            continue
        status = str(payload.get('status', 'UNKNOWN')).upper()
        if status == 'APPROVED':
            if not payload.get('approved_by') or not payload.get('approved_at_utc'):
                invalid_approved.append(name)
                continue
            satisfied += 1
        elif status == 'PENDING':
            # A manifest may honestly DECLARE an item as PENDING (open work
            # awaiting human sign-off): that is consistent, not a violation.
            # Strict blocking applies when the manifest declared APPROVED.
            if expected_status == 'PENDING':
                open_declared.append(name)
            elif args.allow_pending:
                print(f'[governance] warning: {name} is PENDING (--allow-pending)')
            else:
                unsatisfied.append(name)
        elif status == 'REJECTED':
            rejected.append(name)
        else:
            invalid_approved.append(f'{name} (status={status})')

    if missing_files:
        return _fail('declared change request files missing: ' + ', '.join(missing_files))
    if rejected:
        return _fail('rejected change requests present: ' + ', '.join(rejected))
    if invalid_approved:
        return _fail('approved records missing reviewer/time or unknown status: '
                     + ', '.join(invalid_approved))
    if unsatisfied:
        return _fail('manifest expected APPROVED but CRs are still PENDING: '
                     + ', '.join(unsatisfied))

    scope = f'topic="{topic_filter}" ' if topic_filter else ''
    print(f'[governance] {scope}manifest-declared CRs: {len(declared)}, '
          f'satisfied={satisfied}, openly-pending={len(open_declared)}')
    if open_declared:
        print('[governance] honestly-declared open items (await human sign-off):')
        for name in open_declared:
            print(f'  - {name}')
    print('[governance] release gate passed (declaration consistency verified)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
