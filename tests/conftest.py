"""Pytest configuration and shared fixtures for AOF tests."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Generator

import pytest


@pytest.fixture
def temp_dir() -> Generator[Path, None, None]:
    """Provide a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmp:
        yield Path(tmp)


@pytest.fixture
def sample_spec(temp_dir: Path) -> dict[str, Any]:
    """Provide a sample AOF spec for testing."""
    return {
        "project_root": str(temp_dir),
        "dataset": "test_dataset",
        "runtime": {
            "run_in_background": False,
            "incremental_loading": True,
            "data_per_batch": 10,
            "retries": 0,
            "backoff_seconds": 1.0,
        },
        "ontology": {
            "file": str(temp_dir / "test_ontology.owl"),
            "matching_cutoff": 0.8,
        },
        "cognee": {
            "root": "/tmp/cognee"
        }
    }


@pytest.fixture
def sample_sql_data() -> str:
    """Provide sample SQL data for testing."""
    return """
CREATE TABLE users (
    id INT PRIMARY KEY,
    name VARCHAR(100),
    email VARCHAR(100)
);

INSERT INTO users (id, name, email) VALUES
(1, 'Alice', 'alice@example.com'),
(2, 'Bob', 'bob@example.com');
"""


@pytest.fixture
def sample_csv_data() -> str:
    """Provide sample CSV data for testing."""
    return """id,name,email
1,Alice,alice@example.com
2,Bob,bob@example.com
"""


@pytest.fixture
def mock_cognee_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set up mock environment for cognee tests."""
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    monkeypatch.setenv("LLM_PROVIDER", "custom")
    monkeypatch.setenv("LLM_MODEL", "test/model")
    monkeypatch.setenv("LLM_ENDPOINT", "http://localhost:8080/v1")


@pytest.fixture
def clean_api_state(temp_dir: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Set up clean API state directory."""
    api_data = temp_dir / "api_runs"
    api_data.mkdir(parents=True, exist_ok=True)
    
    # Create empty state files
    (api_data / "topic_state.json").write_text("{}", encoding="utf-8")
    (api_data / "run_index.json").write_text("{}", encoding="utf-8")
    
    monkeypatch.setenv("AOF_ROOT", str(temp_dir))
    return api_data
