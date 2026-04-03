"""Integration tests for FastAPI service layer.

Note: These tests require pytest and fastapi dependencies.
Run with: python -m pytest tests/test_api_integration.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch

# Skip all tests if pytest is not available
try:
    import pytest
    from fastapi.testclient import TestClient
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False
    # Create dummy classes to prevent import errors
    class TestClient:
        pass
    pytest = None

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if HAS_DEPS:
    from services.semantic_middle_layer_api.app import app

# Add project root to path for imports
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.semantic_middle_layer_api.app import app


@pytest.fixture
def client(clean_api_state: Path) -> TestClient:
    """Provide a TestClient for the FastAPI app."""
    return TestClient(app)


class TestHealthEndpoints:
    """Tests for health check endpoints."""

    def test_healthz(self, client: TestClient) -> None:
        """Test health check endpoint."""
        response = client.get("/healthz")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"


class TestIngestEndpoints:
    """Tests for data ingestion endpoints."""

    def test_ingest_docs_success(self, client: TestClient, temp_dir: Path) -> None:
        """Test ingesting documents."""
        # Create a test docs directory
        docs_dir = temp_dir / "test_docs"
        docs_dir.mkdir()
        (docs_dir / "test.txt").write_text("Test content", encoding="utf-8")
        
        response = client.post(
            "/v1/ingest/docs",
            json={"topic": "test_topic", "docs_uri": str(docs_dir)}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["topic"] == "test_topic"
        assert "state" in data

    def test_ingest_docs_not_exist(self, client: TestClient) -> None:
        """Test ingesting non-existent docs."""
        response = client.post(
            "/v1/ingest/docs",
            json={"topic": "test_topic", "docs_uri": "/path/not/exist"}
        )
        assert response.status_code == 400
        assert "not exists" in response.json()["detail"]

    def test_ingest_metadata(self, client: TestClient) -> None:
        """Test ingesting metadata."""
        metadata = {"version": "1.0", "source": "test"}
        response = client.post(
            "/v1/ingest/metadata",
            json={"topic": "test_topic", "metadata": metadata}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["topic"] == "test_topic"
        assert "metadata_json" in data

    def test_ingest_feedback(self, client: TestClient) -> None:
        """Test ingesting feedback patches."""
        patches = [
            {"action": "add", "term": "test_term", "type": "class"},
            {"action": "modify", "term": "other_term", "mapping": "NewMapping"}
        ]
        response = client.post(
            "/v1/ingest/feedback",
            json={"topic": "test_topic", "patches": patches}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["topic"] == "test_topic"
        assert "feedback_jsonl" in data


class TestBuildEndpoints:
    """Tests for build endpoints."""

    @patch("services.semantic_middle_layer_api.app._run_middle_layer")
    def test_build_topic_success(self, mock_run: Any, client: TestClient, temp_dir: Path) -> None:
        """Test building topic with mocked middle layer."""
        # Create mock manifest
        manifest = temp_dir / "middle_layer_manifest_test_topic_20240101_120000.json"
        manifest.parent.mkdir(parents=True, exist_ok=True)
        manifest.write_text(json.dumps({
            "topic": "test_topic",
            "artifacts": {"ontology_file": "test.owl"}
        }), encoding="utf-8")
        mock_run.return_value = manifest
        
        # First ingest docs
        docs_dir = temp_dir / "docs"
        docs_dir.mkdir()
        (docs_dir / "test.txt").write_text("content", encoding="utf-8")
        client.post(
            "/v1/ingest/docs",
            json={"topic": "test_topic", "docs_uri": str(docs_dir)}
        )
        
        # Then build
        response = client.post(
            "/v1/build/topic",
            json={"topic": "test_topic", "max_iterations": 2, "skip_align": True}
        )
        assert response.status_code == 200
        data = response.json()
        assert "run_id" in data
        assert "manifest" in data
        assert data["topic"] == "test_topic"

    def test_build_topic_no_docs(self, client: TestClient) -> None:
        """Test building topic without ingesting docs first."""
        response = client.post(
            "/v1/build/topic",
            json={"topic": "new_topic", "max_iterations": 2}
        )
        assert response.status_code == 400
        assert "docs_uri not set" in response.json()["detail"]


class TestArtifactEndpoints:
    """Tests for artifact retrieval endpoints."""

    def test_get_artifacts_not_found(self, client: TestClient) -> None:
        """Test getting non-existent artifacts."""
        response = client.get("/v1/artifacts/nonexistent_run_id")
        assert response.status_code == 404


class TestSemanticEndpoints:
    """Tests for semantic layer endpoints."""

    def test_semantic_retrieve_no_manifest(self, client: TestClient) -> None:
        """Test semantic retrieve without manifest."""
        response = client.post(
            "/v1/semantic/retrieve",
            json={"topic": "unknown_topic", "query": "test"}
        )
        assert response.status_code == 404
        assert "no manifest" in response.json()["detail"]

    def test_semantic_compile_no_manifest(self, client: TestClient) -> None:
        """Test semantic compile without manifest."""
        response = client.post(
            "/v1/semantic/compile",
            json={"topic": "unknown_topic", "intent": "get users"}
        )
        assert response.status_code == 404

    def test_semantic_evaluate(self, client: TestClient) -> None:
        """Test semantic evaluate endpoint."""
        response = client.post(
            "/v1/semantic/evaluate",
            json={"topic": "test_topic", "candidate": "SELECT * FROM users"}
        )
        assert response.status_code == 200
        data = response.json()
        assert data["topic"] == "test_topic"
        assert "score" in data
        assert "risks" in data
        # risks is now a list of dicts with 'type' field
        risk_types = [r.get("type") for r in data["risks"]]
        assert "select_star" in risk_types

    def test_semantic_evaluate_good_query(self, client: TestClient) -> None:
        """Test semantic evaluate with good query."""
        response = client.post(
            "/v1/semantic/evaluate",
            json={"topic": "test_topic", "candidate": "SELECT id FROM users WHERE id = 1"}
        )
        assert response.status_code == 200
        data = response.json()
        # Good queries should have high score (>0.8)
        assert data["score"] > 0.8
        # Good query should have no risks
        assert len(data["risks"]) == 0


class TestExportEndpoints:
    """Tests for export endpoints."""

    def test_export_owl_no_manifest(self, client: TestClient) -> None:
        """Test export OWL without manifest."""
        response = client.get("/v1/export/owl?topic=unknown")
        assert response.status_code == 404

    def test_export_mapping_no_manifest(self, client: TestClient) -> None:
        """Test export mapping without manifest."""
        response = client.get("/v1/export/mapping?topic=unknown")
        assert response.status_code == 404

    def test_export_regression_no_manifest(self, client: TestClient) -> None:
        """Test export regression without manifest."""
        response = client.get("/v1/export/regression?topic=unknown")
        assert response.status_code == 404
