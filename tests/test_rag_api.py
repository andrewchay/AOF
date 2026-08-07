"""Integration tests for /v1/rag/retrieve (RAG 统一检索).

覆盖：
- 参数校验（缺失 query / 空 query / limit 越界 → 422）
- mock 内部 rag_retrieve 后验证返回结构与参数透传
- 对应 _rag_smoke_test.py 的 4 个用例（转为带断言的正式测试）

运行:
    python -m pytest tests/test_rag_api.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

try:
    import pytest
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
    from services.semantic_middle_layer_api.app import app
    client = TestClient(app)
except ImportError:
    HAS_FASTAPI = False
    client = None
    pytest = None


@pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")
class TestRagRetrieveValidation:
    """参数校验：进入路由前由 pydantic 拦截."""

    def test_missing_query_422(self) -> None:
        response = client.post("/v1/rag/retrieve", json={"dataset_name": "d"})
        assert response.status_code == 422

    def test_empty_query_422(self) -> None:
        response = client.post("/v1/rag/retrieve", json={"query": ""})
        assert response.status_code == 422

    def test_limit_out_of_range_422(self) -> None:
        for bad_limit in (0, 51):
            response = client.post("/v1/rag/retrieve", json={"query": "q", "limit": bad_limit})
            assert response.status_code == 422, f"limit={bad_limit} 应被拒绝"


@pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")
class TestRagRetrieveEndpoint:
    """mock 内部 rag_retrieve，验证路由行为."""

    @patch("exporters.rag_service.rag_retrieve")
    def test_retrieve_success_structure(self, mock_retrieve) -> None:
        from exporters.rag_service import RagResult

        mock_retrieve.return_value = RagResult(
            query="魔女会",
            results=[{
                "text": "魔女会是提瓦特的神秘组织",
                "type": "entity",
                "score": 0.0167,
                "source": "rrf",
                "slug": "mofa",
                "metadata": {"seed": "魔女会"},
                "provenance": {"route": "keyword", "dataset": "genshin_ultimate_kg"},
            }],
            route_counts={"keyword": 1, "vector": 0, "graph": 0},
            execution_time_ms=5,
        )
        response = client.post(
            "/v1/rag/retrieve",
            json={"query": "魔女会", "dataset_name": "genshin_ultimate_kg", "limit": 5},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["query"] == "魔女会"
        assert data["count"] == 1
        assert data["route_counts"] == {"keyword": 1, "vector": 0, "graph": 0}
        assert data["error"] is None
        r = data["results"][0]
        assert r["score"] > 0
        assert r["source"] == "rrf"
        assert r["provenance"]["route"] == "keyword"

    @patch("exporters.rag_service.rag_retrieve")
    def test_params_passthrough(self, mock_retrieve) -> None:
        from exporters.rag_service import RagResult

        mock_retrieve.return_value = RagResult(query="q")
        response = client.post(
            "/v1/rag/retrieve",
            json={
                "query": "q",
                "dataset_id": "d1",
                "dataset_name": "d2",
                "limit": 7,
                "expansion": True,
                "include_graph": False,
            },
        )
        assert response.status_code == 200
        _, kwargs = mock_retrieve.call_args
        assert kwargs["dataset_id"] == "d1"
        assert kwargs["dataset_name"] == "d2"
        assert kwargs["limit"] == 7
        assert kwargs["expansion"] is True
        assert kwargs["include_graph"] is False

    @patch("exporters.rag_service.rag_retrieve")
    def test_retrieve_empty_results(self, mock_retrieve) -> None:
        from exporters.rag_service import RagResult

        mock_retrieve.return_value = RagResult(
            query="不存在的内容", results=[], route_counts={"keyword": 0, "vector": 0, "graph": 0}
        )
        response = client.post("/v1/rag/retrieve", json={"query": "不存在的内容"})
        assert response.status_code == 200
        data = response.json()
        assert data["count"] == 0
        assert data["results"] == []
