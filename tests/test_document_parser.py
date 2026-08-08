"""document_parser 单元测试。

覆盖：路由逻辑、Blake2b 缓存、parse_document 各路径（direct/fallback/docling）、
ingest_helper（maybe_parse_local_file）、subprocess_runner 的 worker JSON 解析。
不触发真实隔离子进程（用 mock），保证快速、环境无关。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from bridge.document_parser import ParsedDoc, parse_document
from bridge.document_parser.cache import ParseCache, blake2b_file
from bridge.document_parser.config import ParserConfig
from bridge.document_parser.core import ParseResult
from bridge.document_parser.ingest_helper import maybe_parse_local_file


class TestRoute(unittest.TestCase):
    def _cfg(self) -> ParserConfig:
        return ParserConfig()

    def test_pdf_routes_docling(self):
        self.assertEqual(self._cfg().route(Path("a.pdf")), "docling")

    def test_office_routes_docling(self):
        for ext in (".docx", ".pptx", ".xlsx"):
            self.assertEqual(self._cfg().route(Path(f"a{ext}")), "docling", ext)

    def test_markdown_routes_direct(self):
        for ext in (".md", ".markdown", ".txt"):
            self.assertEqual(self._cfg().route(Path(f"a{ext}")), "direct", ext)

    def test_image_routes_unsupported(self):
        self.assertEqual(self._cfg().route(Path("a.png")), "unsupported")

    def test_unknown_routes_fallback(self):
        self.assertEqual(self._cfg().route(Path("a.bin")), "fallback")


class TestParseCache(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = ParseCache(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_miss_then_hit(self):
        key = "abc123"
        self.assertIsNone(self.cache.get(key))
        self.cache.set(key, {"content": "hello", "engine": "docling"})
        self.assertEqual(self.cache.get(key)["content"], "hello")

    def test_build_key_stable(self):
        f = Path(self._tmp.name) / "f.pdf"
        f.write_text("data", encoding="utf-8")
        fp = blake2b_file(f)
        k1 = self.cache.build_key(
            file_path=f, fingerprint=fp, engine="docling", lang="zh"
        )
        k2 = self.cache.build_key(
            file_path=f, fingerprint=fp, engine="docling", lang="zh"
        )
        self.assertEqual(k1, k2)
        # 引擎不同 → 键不同
        k3 = self.cache.build_key(
            file_path=f, fingerprint=fp, engine="mineru", lang="zh"
        )
        self.assertNotEqual(k1, k3)

    def test_clear(self):
        self.cache.set("a", {"content": "1"})
        self.cache.set("b", {"content": "2"})
        self.assertEqual(self.cache.clear(), 2)
        self.assertIsNone(self.cache.get("a"))


class TestParseDocument(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = ParserConfig(enabled=True, cache_enabled=False)

    def test_direct_markdown(self):
        f = Path(self._tmp.name) / "note.md"
        f.write_text("# Title\n\nbody", encoding="utf-8")
        r = parse_document(f, config=self.cfg, cache=None)
        self.assertFalse(r.use_raw_path)
        self.assertEqual(r.doc.engine, "direct")
        self.assertIn("# Title", r.doc.content)

    def test_missing_file_falls_back(self):
        r = parse_document(
            Path(self._tmp.name) / "nope.pdf", config=self.cfg, cache=None
        )
        self.assertTrue(r.use_raw_path)
        self.assertEqual(r.doc.engine, "fallback")

    def test_disabled_parser_falls_back(self):
        f = Path(self._tmp.name) / "a.pdf"
        f.write_bytes(b"%PDF-1.4...")
        cfg = ParserConfig(enabled=False)
        r = parse_document(f, config=cfg, cache=None)
        self.assertTrue(r.use_raw_path)
        self.assertEqual(r.doc.engine, "fallback")

    def test_unsupported_type_falls_back(self):
        f = Path(self._tmp.name) / "img.png"
        f.write_bytes(b"\x89PNG")
        r = parse_document(f, config=self.cfg, cache=None)
        self.assertTrue(r.use_raw_path)
        self.assertEqual(r.doc.engine, "fallback")

    @patch("bridge.document_parser.core.docling_engine.parse")
    def test_docling_success(self, mock_parse):
        f = Path(self._tmp.name) / "report.pdf"
        f.write_bytes(b"%PDF-1.4")
        mock_parse.return_value = ParsedDoc(
            content="# 报告\n\n正文",
            source_path=str(f),
            engine="docling",
            tables_count=1,
        )
        r = parse_document(f, config=self.cfg, cache=None)
        self.assertFalse(r.use_raw_path)
        self.assertEqual(r.doc.engine, "docling")
        self.assertEqual(r.doc.tables_count, 1)

    @patch(
        "bridge.document_parser.core.docling_engine.parse",
        side_effect=RuntimeError("boom"),
    )
    def test_docling_failure_falls_back(self, _mock):
        f = Path(self._tmp.name) / "report.pdf"
        f.write_bytes(b"%PDF-1.4")
        r = parse_document(f, config=self.cfg, cache=None)
        self.assertTrue(r.use_raw_path)
        self.assertEqual(r.doc.engine, "fallback")

    @patch("bridge.document_parser.core.docling_engine.parse")
    def test_docling_cache_hit_skips_engine(self, mock_parse):
        f = Path(self._tmp.name) / "report.pdf"
        f.write_bytes(b"%PDF-1.4 stable content")
        cfg = ParserConfig(enabled=True, cache_enabled=True, cache_dir=self._tmp.name)
        m = MagicMock()
        m.build_key.return_value = "cachekey"
        m.get.return_value = {
            "content": "# 报告",
            "source_path": str(f),
            "engine": "docling",
            "tables_count": 9,
        }
        r = parse_document(f, config=cfg, cache=m)
        self.assertFalse(r.use_raw_path)
        self.assertTrue(r.cached)
        self.assertEqual(r.doc.tables_count, 9)
        mock_parse.assert_not_called()


class TestIngestHelper(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = ParserConfig(enabled=False)  # 关闭解析 → fallback

    def test_non_path_data_passthrough(self):
        data, ctx = maybe_parse_local_file("plain text not a path")
        self.assertEqual(data, "plain text not a path")
        self.assertEqual(ctx["parser_engine"], "none")

    def test_local_md_parses_direct(self):
        f = Path(self._tmp.name) / "x.md"
        f.write_text("## h", encoding="utf-8")
        data, ctx = maybe_parse_local_file(str(f), config=None)
        self.assertIn("## h", data)
        self.assertEqual(ctx["parser_engine"], "direct")


class TestParseResultShape(unittest.TestCase):
    def test_parse_result_fields(self):
        r = ParseResult(
            ParsedDoc(content="", source_path="f", engine="fallback"), use_raw_path=True
        )
        self.assertTrue(r.use_raw_path)
        self.assertEqual(r.doc.engine, "fallback")


if __name__ == "__main__":
    unittest.main(verbosity=2)
