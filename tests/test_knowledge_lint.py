"""OKF Knowledge Lint 单元测试."""

from __future__ import annotations

from pathlib import Path

from tools.knowledge_lint import OKFLinter, _parse_frontmatter, _resolve_okf_path


def _write(directory: Path, rel_path: str, content: str) -> None:
    p = directory / rel_path
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


# ---------- frontmatter 解析 ----------

def test_parse_frontmatter_tolerant():
    """容错解析 frontmatter；无 frontmatter 返回空 dict 不抛错."""
    meta, body = _parse_frontmatter("---\ntype: person\ntitle: Alice\ndescription: \"a\n\"engineer\"\n---\nbody text")
    assert meta["type"] == "person"
    assert "body text" in body


def test_parse_frontmatter_missing_returns_empty():
    meta, body = _parse_frontmatter("no frontmatter here")
    assert meta == {}
    assert body == "no frontmatter here"


# ---------- 路径解析 ----------

def test_resolve_okf_path():
    assert _resolve_okf_path("", "person/alice.md") == "person/alice.md"
    assert _resolve_okf_path("person", "../company/tech.md") == "company/tech.md"
    assert _resolve_okf_path("", "/company/tech.md") == "company/tech.md"
    assert _resolve_okf_path("a/b", "../../c.md") == "c.md"


# ---------- Lint 断链 ----------

def _make_broken_bundle(root: Path) -> None:
    _write(root, "index.md", "---\ntype: bundle_index\ntitle: T\n---\n\n[Ok](person/alice)\n")
    _write(root, "person/alice.md", "---\ntype: person\ntitle: Alice\n---\n\n[Bob](person/bob)\n")
    _write(root, "company/tech.md", "---\ntype: company\ntitle: Tech\n---\n\n[Alice](../person/alice)\n")


def test_lint_detects_broken_link(tmp_path):
    _make_broken_bundle(tmp_path)
    report = OKFLinter().lint(tmp_path)
    broken = [i for i in report.issues if i.category == "broken_link"]
    assert any("person/bob" in i.message for i in broken)  # 指向不存在的 bob.md
    # alice 与 tech 互链应为 Ok（存在）
    assert not any("person/alice" in i.message for i in broken)
    assert report.concepts_scanned == 3


# ---------- Lint 重复 ----------

def _make_duplicate_bundle(root: Path) -> None:
    _write(root, "person/alice.md", "---\ntype: person\ntitle: Alice\n---\ncontent A\n")
    _write(root, "person/alice2.md", "---\ntype: person\ntitle: Alice\n---\ncontent A\n")


def test_lint_detects_duplicates(tmp_path):
    _make_duplicate_bundle(tmp_path)
    report = OKFLinter().lint(tmp_path)
    dup = [i for i in report.issues if i.category == "duplicate"]
    # 至少有一个重复问题（title+type 重复 或 内容 hash 重复）
    assert dup
    assert any("title+type" in i.message for i in dup)


# ---------- Lint 口径冲突 ----------

def _make_conflict_bundle(root: Path) -> None:
    _write(root, "metric/rev.md", "---\ntype: metric\ntitle: Revenue\ndescription: Q1\ncurrency: USD\n---\n")
    _write(root, "metric/rev_alt.md", "---\ntype: metric\ntitle: Revenue\ndescription: Q2\ncurrency: CNY\n---\n")


def test_lint_detects_consistency_conflict(tmp_path):
    _make_conflict_bundle(tmp_path)
    report = OKFLinter().lint(tmp_path)
    conflicts = [i for i in report.issues if i.category == "consistency"]
    assert conflicts
    assert any("currency" in i.message for i in conflicts)
