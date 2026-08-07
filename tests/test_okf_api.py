"""OKF FastAPI REST 端点测试."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


def _write_bundle(root: Path) -> None:
    (root / "person").mkdir(parents=True, exist_ok=True)
    (root / "company").mkdir(parents=True, exist_ok=True)
    (root / "index.md").write_text(
        "---\ntype: bundle_index\ntitle: TestBundle\n---\n\n## person\n\n- [Alice](person/alice) — A software engineer\n",
        encoding="utf-8",
    )
    (root / "log.md").write_text("## 2026-01-01T00:00:00Z\n\n- action: init\n", encoding="utf-8")
    (root / "person" / "alice.md").write_text(
        "---\ntype: person\ntitle: Alice\ndescription: A software engineer\ntags: [\"eng\"]\n"
        "---\n\nA software engineer\n",
        encoding="utf-8",
    )
    (root / "company" / "techcorp.md").write_text(
        "---\ntype: company\ntitle: TechCorp\ndescription: A tech company\ntags: [\"company\"]\n---\n\nA tech company\n",
        encoding="utf-8",
    )


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("okf_root")
    _write_bundle(root / "demo")
    import importlib
    import sys

    # 加载 app 模块（AOF_OKF_DIR 指向临时根）
    import os
    os.environ["AOF_OKF_DIR"] = str(root)
    sys.path.insert(0, "services/semantic_middle_layer_api")
    app_mod = importlib.import_module("app")
    importlib.reload(app_mod)
    # okf_root 读取 env，逐次加载时拿到真实目录
    return TestClient(app_mod.app)


def test_list_bundles(client):
    r = client.get("/v1/okf/bundles")
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["bundles"][0]["name"] == "demo"


def test_bundle_index(client):
    r = client.get("/v1/okf/bundles/demo/index")
    assert r.status_code == 200
    assert r.json()["exists"] is True
    assert "[Alice](person/alice)" in r.json()["index_md"]


def test_bundle_index_missing(client):
    r = client.get("/v1/okf/bundles/nonexistent/index")
    assert r.status_code == 200
    assert r.json()["exists"] is False


def test_search_concepts(client):
    r = client.get("/v1/okf/bundles/demo/search", params={"type": "person"})
    assert r.status_code == 200
    assert r.json()["count"] == 1
    assert r.json()["results"][0]["title"] == "Alice"


def test_get_concept(client):
    r = client.get("/v1/okf/bundles/demo/concept", params={"path": "person/alice.md"})
    assert r.status_code == 200
    assert r.json()["exists"] is True
    assert "title: Alice" in r.json()["concept"]


def test_get_concept_path_traversal_rejected(client):
    r = client.get("/v1/okf/bundles/demo/concept", params={"path": "../index.md"})
    assert r.status_code == 200
    assert r.json()["exists"] is False


def test_lint_bundle(client):
    r = client.post("/v1/okf/bundles/demo/lint")
    assert r.status_code == 200
    assert r.json()["error_count"] == 0
