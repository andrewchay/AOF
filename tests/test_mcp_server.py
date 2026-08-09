"""MCP Server - OKF 知识消费工具测试."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from unittest.mock import patch

from mcp_server import build_server


def _write_bundle(root: Path) -> Path:
    """构造一个可测试的临时 OKF 知识包."""
    (root / "person").mkdir(parents=True, exist_ok=True)
    (root / "company").mkdir(parents=True, exist_ok=True)

    (root / "index.md").write_text(
        "---\ntype: bundle_index\ntitle: Test\n---\n\n## person\n\n- [Alice](person/alice) — A software engineer\n",
        encoding="utf-8",
    )
    (root / "log.md").write_text(
        "## 2026-01-01T00:00:00Z\n\n- **action**: bundle_init\n",
        encoding="utf-8",
    )
    (root / "person" / "alice.md").write_text(
        '---\ntype: person\ntitle: Alice\ndescription: A software engineer\ntags: ["eng", "person"]\n'
        "---\n\nA software engineer\n\n## Relations (out)\n- works_at: [TechCorp](company/techcorp)\n",
        encoding="utf-8",
    )
    (root / "company" / "techcorp.md").write_text(
        '---\ntype: company\ntitle: TechCorp\ndescription: A tech company\ntags: ["company"]\n---\n\nA tech company\n',
        encoding="utf-8",
    )
    return root


def _tool(server, name):
    return server.tools[name]


def test_okf_index_reads_progressive_entry(tmp_path):
    bundle = _write_bundle(tmp_path)
    server = build_server()
    result = asyncio.run(
        _tool(server, "aof_okf_index").handler({"bundle_dir": str(bundle)})
    )
    assert result["exists"] is True
    assert "[Alice](person/alice)" in result["index_md"]


def test_okf_index_missing_bundle(tmp_path):
    server = build_server()
    result = asyncio.run(
        _tool(server, "aof_okf_index").handler({"bundle_dir": str(tmp_path)})
    )
    assert result["exists"] is False
    assert "index.md" in result["error"]


def test_okf_get_concept_reads_full(tmp_path):
    bundle = _write_bundle(tmp_path)
    server = build_server()
    result = asyncio.run(
        _tool(server, "aof_okf_get_concept").handler(
            {"bundle_dir": str(bundle), "path": "person/alice.md"}
        )
    )
    assert result["exists"] is True
    assert "title: Alice" in result["concept"]
    assert "works_at" in result["concept"]


def test_okf_get_concept_accepts_no_suffix(tmp_path):
    """index/search 返回的路径无 .md 后缀，get_concept 应能兼容读取."""
    bundle = _write_bundle(tmp_path)
    server = build_server()
    result = asyncio.run(
        _tool(server, "aof_okf_get_concept").handler(
            {"bundle_dir": str(bundle), "path": "person/alice"}
        )
    )
    assert result["exists"] is True
    assert "title: Alice" in result["concept"]


def test_okf_get_concept_rejects_path_traversal(tmp_path):
    bundle = _write_bundle(tmp_path)
    server = build_server()
    # 尝试目录穿越（应被拒绝）
    result = asyncio.run(
        _tool(server, "aof_okf_get_concept").handler(
            {"bundle_dir": str(bundle), "path": "../index.md"}
        )
    )
    assert result["exists"] is False
    assert "路径非法" in result["error"]


def test_okf_search_concepts_by_type_and_query(tmp_path):
    bundle = _write_bundle(tmp_path)
    server = build_server()

    by_type = asyncio.run(
        _tool(server, "aof_okf_search_concepts").handler(
            {"bundle_dir": str(bundle), "type": "person"}
        )
    )
    assert by_type["count"] == 1
    assert by_type["results"][0]["title"] == "Alice"

    by_query = asyncio.run(
        _tool(server, "aof_okf_search_concepts").handler(
            {"bundle_dir": str(bundle), "query": "tech"}
        )
    )
    # "tech" 命中 TechCorp 的 title/正文，同时也可能命中 Alice 正文里出现的 TechCorp 关联
    titles = [r["title"] for r in by_query["results"]]
    assert "TechCorp" in titles
    # 按 title 精确命中应排在前面
    assert titles[0] == "TechCorp"


def test_okf_search_concepts_no_match(tmp_path):
    bundle = _write_bundle(tmp_path)
    server = build_server()
    result = asyncio.run(
        _tool(server, "aof_okf_search_concepts").handler(
            {"bundle_dir": str(bundle), "query": "zzz_nonexistent"}
        )
    )
    assert result["count"] == 0
    assert result["results"] == []


def test_okf_lint_report(tmp_path):
    bundle = _write_bundle(tmp_path)
    server = build_server()
    report = asyncio.run(
        _tool(server, "aof_okf_lint").handler({"bundle_dir": str(bundle)})
    )
    # alice 链接 TechCorp，techcorp 存在 → 无断链，报告无 error
    assert report["concepts_scanned"] == 4
    assert report["error_count"] == 0
    assert "issues" in report


def test_default_bundle_dir_from_env(tmp_path, monkeypatch):
    """未显式传 bundle_dir 时，应从 AOF_OKF_DIR 解析默认知识包目录."""
    monkeypatch.setattr(
        "mcp_server.os.environ", {**os.environ, "AOF_OKF_DIR": str(tmp_path)}
    )
    from mcp_server import _bundle_dir

    assert _bundle_dir({}) == Path(str(tmp_path))


def test_default_bundle_dir_fallback(tmp_path, monkeypatch):
    """既无 bundle_dir 也无环境变量时，应回退到 ./okf_bundle (PROJECT_ROOT)."""
    monkeypatch.setattr("mcp_server.os.environ", {})
    monkeypatch.setattr("mcp_server.PROJECT_ROOT", tmp_path)
    from mcp_server import _bundle_dir

    assert _bundle_dir({}) == tmp_path / "okf_bundle"


# ---------------------------------------------------------------------------
# aof_document_parse（阶段 3）
# ---------------------------------------------------------------------------


def test_document_parse_tool_registered():
    server = build_server()
    assert "aof_document_parse" in server.tools


def test_document_parse_markdown_direct(tmp_path, monkeypatch):
    """md 文件 → direct 引擎，返回干净 markdown。"""
    monkeypatch.setenv("AOF_PARSER_ALLOW_ROOTS", str(tmp_path))
    f = tmp_path / "note.md"
    f.write_text("# 标题\n\n正文", encoding="utf-8")
    server = build_server()
    result = asyncio.run(_tool(server, "aof_document_parse").handler({"path": str(f)}))
    assert result["ok"] is True
    assert result["engine"] == "direct"
    assert result["use_raw_path"] is False
    assert "# 标题" in result["content"]


def test_document_parse_rejects_sensitive_path(monkeypatch):
    """敏感路径应被拒（防任意文件读取）。"""
    server = build_server()
    result = asyncio.run(
        _tool(server, "aof_document_parse").handler({"path": "/etc/passwd"})
    )
    assert result["ok"] is False
    assert "error" in result
    assert "根目录" in result["error"]


def test_document_parse_missing_path():
    """path 缺失 → 返回 error。"""
    server = build_server()
    result = asyncio.run(_tool(server, "aof_document_parse").handler({}))
    assert result["ok"] is False
    assert "error" in result


@patch("bridge.document_parser.parse_document")
def test_document_parse_engine_ok(mock_parse, tmp_path, monkeypatch):
    """mock 引擎成功 → 返回 docling 结果。"""
    monkeypatch.setenv("AOF_PARSER_ALLOW_ROOTS", str(tmp_path))
    f = tmp_path / "report.pdf"
    f.write_bytes(b"%PDF-fake")
    from bridge.document_parser.core import ParseResult
    from bridge.document_parser.engine_base import ParsedDoc

    mock_parse.return_value = ParseResult(
        ParsedDoc(
            content="# 报告", source_path=str(f), engine="docling", tables_count=3
        ),
        use_raw_path=False,
    )
    server = build_server()
    result = asyncio.run(_tool(server, "aof_document_parse").handler({"path": str(f)}))
    assert result["ok"] is True
    assert result["engine"] == "docling"
    assert result["tables_count"] == 3
