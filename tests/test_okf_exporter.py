"""OKF / LLM Wiki 导出器单元测试."""

from __future__ import annotations

import asyncio

from exporters.okf_exporter import OKFExporter, export_dataset_to_okf


def _node(nid: str, name: str, ntype: str, description: str = "") -> dict:
    return {
        "id": nid,
        "name": name,
        "type": ntype,
        "description": description,
        "tags": ["test"],
        "created_at": "2026-01-01T00:00:00+00:00",
    }


def _edge(source: str, relation: str, target: str) -> dict:
    return {"source": source, "relation": relation, "target": target}


# ---------- frontmatter ----------

def test_frontmatter_okf_standard_fields():
    """frontmatter 应包含 OKF 标准字段 type/title/description/resource/tags/timestamp."""
    exporter = OKFExporter()
    node = _node("p1", "Alice", "person", "A software engineer")
    fm = exporter._build_frontmatter(node)

    assert "type: person" in fm
    assert "title: Alice" in fm
    assert "description: A software engineer" in fm
    assert "resource: \"aof://p1\"" in fm or "resource: aof://p1" in fm
    assert "tags:" in fm
    assert "timestamp:" in fm
    assert fm.startswith("---\n")
    assert fm.endswith("---")


def test_frontmatter_missing_fields_is_tolerant():
    """容错消费模型：缺 description/tags 等字段不应抛错."""
    exporter = OKFExporter()
    # 只给最小字段，甚至不含 name/type 外的字段
    minimal = {"id": "p9", "name": "Minimal"}
    fm = exporter._build_frontmatter(minimal)
    assert fm.startswith("---\n")
    assert "type: entity" in fm
    assert "title: Minimal" in fm


def test_frontmatter_special_chars_quoted():
    """含冒号/引号的值应被正确引号包裹."""
    exporter = OKFExporter()
    node = {"id": "x", "name": 'Weird: "name"', "type": "thing"}
    fm = exporter._build_frontmatter(node)
    assert 'title: "Weird: \\"name\\""' in fm


# ---------- 交叉链接 / concept body ----------

def test_concept_body_relations_links():
    """关系应渲染为指向其他 Concept 的相对链接."""
    exporter = OKFExporter()
    nodes = [_node("a", "Alice", "person"), _node("b", "Bob", "person")]
    edges = [_edge("a", "knows", "b")]
    node_index = {
        "a": {"path": "person/alice", "title": "Alice"},
        "b": {"path": "person/bob", "title": "Bob"},
    }
    body = exporter._build_concept_body(nodes[0], edges, node_index)
    assert "## Relations (out)" in body
    assert "[Bob](person/bob)" in body
    assert "knows" in body


def test_concept_relation_missing_target_falls_back():
    """目标缺省（不在 index）时应退化为纯文本，不抛错."""
    exporter = OKFExporter()
    node = _node("a", "Alice", "person")
    edges = [_edge("a", "related_to", "ghost")]
    body = exporter._build_concept_body(node, edges, {})
    # 找不到目标，Relation 段不包含链接，也不应抛错
    assert "ghost" not in body


# ---------- index.md / log.md ----------

def test_index_md_progressive_disclosure():
    """index.md 应是一个按 type 分组的渐进式披露目录."""
    exporter = OKFExporter()
    by_type = {
        "person": [("person/alice", "Alice", "A software engineer"), ("person/bob", "Bob", "")],
        "company": [("company/tech", "TechCorp", "A tech company")],
    }
    index = exporter._build_index_md(by_type, "Test Bundle")
    assert index.startswith("---\n")
    assert "title: Test Bundle" in index
    assert "## person" in index
    assert "## company" in index
    assert "[Alice](person/alice)" in index
    assert "[Bob](person/bob)" in index
    # 无描述也应渲染
    assert "[Bob](person/bob)" in index


def test_log_md_append_only():
    """log.md 应包含时间戳与审计字段."""
    exporter = OKFExporter()
    log = exporter._build_log_md("bundle_init", 3, "export")
    assert "## " in log  # 时间戳标题
    assert "- **action**: bundle_init" in log
    assert "- **concepts**: 3" in log
    assert "- **trigger**: export" in log


# ---------- 主流程 export ----------

def test_export_writes_okf_bundle_structure(tmp_path):
    """export 应产出 index.md + log.md + type 分组的 Concept 文件."""
    exp = OKFExporter()
    exp._load_graph_data = _fake_load_graph_data

    result = asyncio.run(exp.export(
        output_dir=tmp_path,
        dataset_name="test_ds",
        bundle_title="Test Bundle",
    ))

    assert result.index_created is True
    assert result.log_created is True
    assert result.concepts_exported == 3
    assert result.mode == "graph"

    # 文件结构
    assert (tmp_path / "index.md").exists()
    assert (tmp_path / "log.md").exists()
    assert (tmp_path / "person" / "Alice.md").exists()
    assert (tmp_path / "company" / "TechCorp.md").exists()

    index = (tmp_path / "index.md").read_text(encoding="utf-8")
    assert "## person" in index
    assert "[Alice](person/Alice)" in index


async def _fake_load_graph_data(dataset_id=None, dataset_name=None):
    """假的图数据源（无 Cognee 依赖）."""
    nodes = [
        _node("a", "Alice", "person", "A software engineer"),
        _node("b", "Bob", "person", "A product manager"),
        _node("t", "TechCorp", "company", "A technology company"),
    ]
    edges = [
        _edge("a", "works_at", "t"),
        _edge("a", "knows", "b"),
    ]
    return nodes, edges


def test_export_dataset_to_okf_convenience(tmp_path):
    """便捷函数 export_dataset_to_okf 应可用."""
    result = asyncio.run(export_dataset_to_okf(
        output_dir=tmp_path / "bundle",
        dataset_name="demo",
    ))
    # 无 Cognee 环境下走 dataset 回退，未落库则可能无数据，但不抛异常
    assert hasattr(result, "to_dict")
    assert isinstance(result.to_dict(), dict)
