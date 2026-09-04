"""document_parser 阶段 3 API 端点测试。

覆盖 /v1/documents/parse 端点的同步解析（direct / 引擎就绪）、错误路径、
parser/md 直读、异步提交。用 md 直读避免隔离子进程；引擎相关用 mock。
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

try:
    import pytest
    from fastapi.testclient import TestClient

    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False
    pytest = None
    TestClient = None  # type: ignore

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if HAS_DEPS:
    from services.semantic_middle_layer_api.app import app

pytestmark = [pytest.mark.integration]


@pytest.fixture
def client(clean_api_state: Path) -> TestClient:
    """Provide a TestClient for the FastAPI app."""
    return TestClient(app)


@pytest.mark.skipif(not HAS_DEPS, reason="requires fastapi/testclient")
class TestDocumentParseEndpoint:
    @pytest.fixture(autouse=True)
    def _allow_temp_dir(self, temp_dir: Path, monkeypatch: pytest.MonkeyPatch):
        """把 pytest 临时目录加入解析白名单（测试环境模拟外部允许根）。"""
        monkeypatch.setenv("AOF_PARSER_ALLOW_ROOTS", str(temp_dir))

    def test_sync_parse_markdown(self, client: TestClient, temp_dir: Path):
        """md 文件直读 → direct 引擎，content 非空。"""
        f = temp_dir / "note.md"
        f.write_text("# 标题\n\n正文", encoding="utf-8")
        r = client.post("/v1/documents/parse", json={"path": str(f)})
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["engine"] == "direct"
        assert body["use_raw_path"] is False
        assert "# 标题" in body["content"]

    def test_parse_nonexistent_returns_400(self, client: TestClient):
        r = client.post("/v1/documents/parse", json={"path": "/no/such/file.pdf"})
        assert r.status_code == 400

    @patch("bridge.document_parser.parse_document")
    def test_sync_parse_engine_ok(self, mock_parse, client: TestClient, temp_dir: Path):
        """mock 引擎成功 → 返回 docling 结果。"""
        f = temp_dir / "report.pdf"
        f.write_bytes(b"%PDF-fake")
        from bridge.document_parser.core import ParseResult
        from bridge.document_parser.engine_base import ParsedDoc

        doc = ParsedDoc(
            content="# 报告",
            source_path=str(f),
            engine="docling",
            tables_count=2,
            pages=3,
        )
        mock_parse.return_value = ParseResult(doc, use_raw_path=False, cached=False)
        r = client.post("/v1/documents/parse", json={"path": str(f)})
        assert r.status_code == 200
        body = r.json()
        assert body["engine"] == "docling"
        assert body["tables_count"] == 2
        assert body["content"] == "# 报告"

    def test_async_submit_returns_task(self, client: TestClient, temp_dir: Path):
        """async 模式返回 task_id。"""
        f = temp_dir / "doc.pdf"
        f.write_bytes(b"%PDF-fake")
        with patch("services.semantic_middle_layer_api.app._get_parse_queue") as mq:
            queue = type("Q", (), {})()
            queue.submit_parse = __import__("unittest").mock.AsyncMock(
                return_value="task-abc"
            )
            mq.return_value = queue
            r = client.post("/v1/documents/parse", json={"path": str(f), "async": True})
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"
        assert r.json()["task_id"] == "task-abc"

    def test_alias_async_field(self, client: TestClient, temp_dir: Path):
        """Pydantic async 别名（alias='async'）工作。"""
        f = temp_dir / "x.md"
        f.write_text("hi", encoding="utf-8")
        # 同步路径不受 async 参数影响；验证请求能正常处理（不因 alias 报错）
        r = client.post("/v1/documents/parse", json={"path": str(f), "async": False})
        assert r.status_code == 200
        assert r.json()["engine"] == "direct"
