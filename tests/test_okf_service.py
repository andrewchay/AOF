"""OKF Knowledge Service 共享逻辑单元测试."""

from __future__ import annotations

from pathlib import Path

import exporters.okf_service as svc


def _write_bundle(root: Path) -> Path:
    (root / "person").mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text(
        "---\ntype: bundle_index\ntitle: T\n---\n\n## person\n\n- [Alice](person/alice) — A software engineer\n",
        encoding="utf-8",
    )
    (root / "log.md").write_text("## 2026-01-01T00:00:00Z\n\n- action: init\n", encoding="utf-8")
    (root / "person" / "alice.md").write_text(
        "---\ntype: person\ntitle: Alice\ndescription: A software engineer\ntags: [\"eng\"]\n"
        "---\n\nA software engineer\n",
        encoding="utf-8",
    )
    (root / "company").mkdir(parents=True, exist_ok=True)
    (root / "company" / "techcorp.md").write_text(
        "---\ntype: company\ntitle: TechCorp\ndescription: A tech company\ntags: [\"company\"]\n---\n\nA tech company\n",
        encoding="utf-8",
    )
    return root


def test_default_bundle_dir_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AOF_OKF_DIR", str(tmp_path / "my_bundle"))
    assert svc.default_bundle_dir(Path(".")) == tmp_path / "my_bundle"


def test_default_bundle_dir_spec(tmp_path, monkeypatch):
    monkeypatch.delenv("AOF_OKF_DIR", raising=False)
    spec = {"okf": {"dir": str(tmp_path / "from_spec")}}
    assert svc.default_bundle_dir(tmp_path, spec) == tmp_path / "from_spec"


def test_default_bundle_dir_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("AOF_OKF_DIR", raising=False)
    assert svc.default_bundle_dir(tmp_path) == tmp_path / "okf_bundle"


def test_resolve_concept_path_accepts_no_suffix(tmp_path):
    bundle = _write_bundle(tmp_path)
    assert svc.resolve_concept_path(bundle, "person/alice") == (bundle / "person" / "alice.md")


def test_resolve_concept_path_rejects_traversal(tmp_path):
    bundle = _write_bundle(tmp_path)
    assert svc.resolve_concept_path(bundle, "../index.md") is None
    assert svc.resolve_concept_path(bundle, "/etc/passwd") is None


def test_read_index(tmp_path):
    bundle = _write_bundle(tmp_path)
    assert svc.read_index(bundle)["exists"] is True
    assert "[Alice](person/alice)" in svc.read_index(bundle)["index_md"]


def test_get_concept(tmp_path):
    bundle = _write_bundle(tmp_path)
    r = svc.get_concept(bundle, "person/alice.md")
    assert r["exists"] is True
    assert "title: Alice" in r["concept"]


def test_get_concept_rejects_traversal(tmp_path):
    bundle = _write_bundle(tmp_path)
    r = svc.get_concept(bundle, "../index.md")
    assert r["exists"] is False
    assert "路径非法" in r["error"]


def test_search_concepts(tmp_path):
    bundle = _write_bundle(tmp_path)
    by_type = svc.search_concepts(bundle, node_type="person")
    assert by_type["count"] == 1
    assert by_type["results"][0]["title"] == "Alice"
    by_query = svc.search_concepts(bundle, query="tech")
    assert any(r["title"] == "TechCorp" for r in by_query["results"])


def test_search_no_match(tmp_path):
    bundle = _write_bundle(tmp_path)
    assert svc.search_concepts(bundle, query="zzz")["count"] == 0


def test_lint_bundle_no_issues(tmp_path):
    bundle = _write_bundle(tmp_path)
    report = svc.lint_bundle(bundle)
    assert report["error_count"] == 0
    assert "issues" in report


def test_list_bundles(tmp_path):
    _write_bundle(tmp_path / "bundle1")
    _write_bundle(tmp_path / "bundle2")
    listing = svc.list_bundles(tmp_path)
    assert listing["count"] == 2
    names = {b["name"] for b in listing["bundles"]}
    assert names == {"bundle1", "bundle2"}
