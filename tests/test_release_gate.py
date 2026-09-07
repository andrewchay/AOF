"""W11.03 — Release gate driven by a versioned expectation manifest.

D07 finding: the old gate passed whenever the change_requests directory
was missing or empty, i.e. it verified nothing. The new gate requires a
versioned manifest, validates declaration consistency, rejects hidden
changes, and supports verifiable no-changes records.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

GATE = Path(__file__).resolve().parents[1] / 'tools' / 'governance' / 'validate_release_gate.py'


def _run(*args: str) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(GATE), *args], capture_output=True, text=True
    )
    return result.returncode, result.stdout + result.stderr


def _write(root: Path, manifest: dict, crs: dict[str, dict]) -> None:
    (root / 'change_requests').mkdir(parents=True, exist_ok=True)
    (root / 'release_expectations.json').write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8'
    )
    for name, payload in crs.items():
        (root / 'change_requests' / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8'
        )


def _manifest(crs: list[dict]) -> dict:
    return {
        'schema_version': 'aof.release-expectations/v1',
        'expected_change_requests': crs,
    }


def _cr(status: str, **extra) -> dict:
    payload = {'cr_id': 'cr-1', 'status': status, 'topic': 'demo'}
    payload.update(extra)
    return payload


def test_missing_manifest_is_a_config_error(tmp_path):
    """D07 复现：目录不存在时旧 gate 直接 pass；新 gate 必须失败。"""
    code, out = _run('--root', str(tmp_path))
    assert code == 3
    assert 'not found' in out


def _init_repo(tmp_path, *, with_governed_change: bool = False):
    """Hermetic git repo: initial commit, optional governed change on top."""
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        subprocess.run(["git", *args], cwd=repo, capture_output=True, check=True)
    git("init", "-q")
    git("config", "user.email", "gate@test")
    git("config", "user.name", "gate")
    (repo / "README.md").write_text("x")
    git("add", ".")
    git("commit", "-qm", "init")
    base = subprocess.run(
        ["git", "rev-list", "--max-parents=0", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()
    if with_governed_change:
        (repo / "bridge").mkdir()
        (repo / "bridge" / "module.py").write_text("changed")
        git("add", ".")
        git("commit", "-qm", "governed change")
    return repo, base


def test_no_changes_generation_and_pass(tmp_path):
    repo, head = _init_repo(tmp_path)
    gov = tmp_path / "gov"
    code, out = _run('--root', str(gov), '--repo', str(repo),
                     '--generate-no-changes', head, 'gate-admin')
    assert code == 0
    manifest = json.loads((gov / 'release_expectations.json').read_text())
    assert manifest['no_changes']['declared_by'] == 'gate-admin'
    assert manifest['no_changes']['commit_range'] == head

    code, out = _run('--root', str(gov))
    assert code == 0
    assert 'verifiable no-changes' in out


def test_no_changes_generation_refuses_governed_changes(tmp_path):
    """修复分支本体的 governed 变更不能谎报为 no-changes。"""
    repo, base = _init_repo(tmp_path, with_governed_change=True)
    gov = tmp_path / "gov"
    code, out = _run('--root', str(gov), '--repo', str(repo),
                     '--generate-no-changes', f'{base}..HEAD', 'gate-admin')
    assert code == 2
    assert 'cannot declare no-changes' in out


def test_declared_approved_satisfied_passes(tmp_path):
    _write(
        tmp_path,
        _manifest([{'file': 'cr-1.json', 'expected_status': 'APPROVED'}]),
        {'cr-1.json': _cr('APPROVED', approved_by='reviewer:bob',
                          approved_at_utc='2026-09-06T00:00:00Z')},
    )
    code, out = _run('--root', str(tmp_path))
    assert code == 0


def test_declared_approved_but_pending_blocks(tmp_path):
    _write(
        tmp_path,
        _manifest([{'file': 'cr-1.json', 'expected_status': 'APPROVED'}]),
        {'cr-1.json': _cr('PENDING')},
    )
    code, out = _run('--root', str(tmp_path))
    assert code == 2
    assert 'still PENDING' in out

    code, out = _run('--root', str(tmp_path), '--allow-pending')
    assert code == 0


def test_declared_pending_is_honest_open_item(tmp_path):
    _write(
        tmp_path,
        _manifest([{'file': 'cr-1.json', 'expected_status': 'PENDING'}]),
        {'cr-1.json': _cr('PENDING')},
    )
    code, out = _run('--root', str(tmp_path))
    assert code == 0
    assert 'openly-pending=1' in out


def test_approved_without_reviewer_fails(tmp_path):
    _write(
        tmp_path,
        _manifest([{'file': 'cr-1.json', 'expected_status': 'APPROVED'}]),
        {'cr-1.json': _cr('APPROVED')},
    )
    code, out = _run('--root', str(tmp_path))
    assert code == 2
    assert 'missing reviewer/time' in out


def test_hidden_undeclared_cr_fails(tmp_path):
    _write(
        tmp_path,
        _manifest([{'file': 'cr-1.json', 'expected_status': 'APPROVED'}]),
        {
            'cr-1.json': _cr('APPROVED', approved_by='reviewer:bob',
                             approved_at_utc='2026-09-06T00:00:00Z'),
            'cr-hidden.json': _cr('APPROVED', approved_by='x',
                                  approved_at_utc='2026-09-06T00:00:00Z'),
        },
    )
    code, out = _run('--root', str(tmp_path))
    assert code == 2
    assert 'undeclared change request files' in out


def test_rejected_cr_fails(tmp_path):
    _write(
        tmp_path,
        _manifest([{'file': 'cr-1.json', 'expected_status': 'APPROVED'}]),
        {'cr-1.json': _cr('REJECTED')},
    )
    code, out = _run('--root', str(tmp_path))
    assert code == 2
    assert 'rejected change requests' in out


def test_missing_declared_file_fails(tmp_path):
    _write(
        tmp_path,
        _manifest([{'file': 'cr-missing.json', 'expected_status': 'APPROVED'}]),
        {},
    )
    code, out = _run('--root', str(tmp_path))
    assert code == 2
    assert 'files missing' in out


def test_repo_baseline_manifest_is_consistent():
    """仓库基线：修复分支的 CR 已由 owner:Andrewchay 签核（APPROVED），
    声明（expected_status=APPROVED）与文件状态一致。"""
    repo_root = Path(__file__).resolve().parents[1]
    code, out = _run('--root', str(repo_root / 'data' / 'governance'))
    assert code == 0
    assert 'satisfied=1' in out
    assert 'openly-pending=0' in out
