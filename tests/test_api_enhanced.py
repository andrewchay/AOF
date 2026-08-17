"""Tests for enhanced FastAPI service."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'services' / 'semantic_middle_layer_api'))

from app_enhanced import (
    app,
    _calculate_relevance,
    _build_sql_context,
    _generate_sql_template,
)


try:
    from fastapi.testclient import TestClient
    HAS_FASTAPI = True
    client = TestClient(app)
except ImportError:
    HAS_FASTAPI = False
    client = None


@pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")
class TestEnhancedRetrieve:
    """Tests for enhanced semantic retrieval."""

    def test_retrieve_endpoint_structure(self) -> None:
        """Test retrieve endpoint accepts new parameters."""
        # Just test the endpoint accepts the request
        # (Will fail with 404 because no manifest exists)
        response = client.post(
            '/v1/semantic/retrieve',
            json={'topic': 'test', 'query': 'users', 'top_k': 5, 'use_semantic': True}
        )
        # Expect 404 because no manifest
        assert response.status_code == 404


@pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")
class TestEnhancedCompile:
    """Tests for enhanced SQL compilation."""

    def test_compile_endpoint_structure(self) -> None:
        """Legacy compilation cannot bypass the trusted query boundary."""
        response = client.post(
            '/v1/semantic/compile',
            json={
                'topic': 'test',
                'intent': 'get active users',
                'target': 'sql',
                'context': {'filters': ['date_range']}
            }
        )
        assert response.status_code == 410
        detail = response.json()['detail']
        assert detail['code'] == 'legacy_semantic_compile_retired'
        assert detail['replacement'] == '/v1/semantic/query'


@pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")
class TestEnhancedEvaluate:
    """Tests for enhanced query evaluation."""

    def test_evaluate_with_criteria(self) -> None:
        """Test evaluate endpoint with criteria parameter."""
        response = client.post(
            '/v1/semantic/evaluate',
            json={
                'topic': 'test',
                'candidate': 'SELECT * FROM users',
                'criteria': ['syntax', 'performance']
            }
        )
        # Should succeed without manifest (rule-based evaluation)
        assert response.status_code == 200
        data = response.json()
        assert 'score' in data
        assert 'risks' in data
        assert 'suggestions' in data
        # Check for SELECT * risk
        risk_types = [r['type'] for r in data['risks']]
        assert 'select_star' in risk_types

    def test_evaluate_good_query(self) -> None:
        """Test evaluate with good query."""
        response = client.post(
            '/v1/semantic/evaluate',
            json={
                'topic': 'test',
                'candidate': 'SELECT id, name FROM users WHERE id = 1 LIMIT 10',
                'criteria': ['syntax']
            }
        )
        assert response.status_code == 200
        data = response.json()
        # Good queries should have high scores (>0.8)
        assert data['score'] > 0.8
        # Should have minimal risks
        assert len(data['risks']) == 0


class TestUtilityFunctions:
    """Tests for utility functions."""

    def test_calculate_relevance_exact_match(self) -> None:
        """Test relevance calculation with exact match."""
        data = {'name': 'user_account', 'description': 'User account table'}
        score = _calculate_relevance('user_account', 'user_account', data)
        assert score > 0.9

    def test_calculate_relevance_partial_match(self) -> None:
        """Test relevance calculation with partial match."""
        data = {'name': 'user_account', 'description': 'User account table'}
        score = _calculate_relevance('account', 'user_account', data)
        assert score > 0

    def test_calculate_relevance_no_match(self) -> None:
        """Test relevance calculation with no match."""
        data = {'name': 'products', 'description': 'Product catalog'}
        score = _calculate_relevance('users', 'products', data)
        assert score == 0.0

    def test_build_sql_context_empty(self) -> None:
        """Test SQL context building with empty library."""
        library = {'terms': {}, 'metrics': {}, 'sql_patterns': []}
        context = _build_sql_context(library, 'get users')
        assert 'tables' in context
        assert 'metrics' in context

    def test_build_sql_context_with_data(self) -> None:
        """Test SQL context building with data."""
        library = {
            'terms': {
                'users': {'name': 'users', 'fields': ['id', 'name']}
            },
            'metrics': {
                'user_count': {'name': 'user_count', 'sql_formula': 'COUNT(*)'}
            },
            'sql_patterns': []
        }
        context = _build_sql_context(library, 'get users')
        assert len(context['tables']) > 0

    def test_generate_sql_template_basic(self) -> None:
        """Test template SQL generation."""
        context = {
            'tables': [{'name': 'users', 'fields': ['id', 'name']}],
            'metrics': []
        }
        sql = _generate_sql_template('get users', context)
        assert 'SELECT' in sql
        assert 'FROM users' in sql

    def test_generate_sql_template_with_metric(self) -> None:
        """Test template SQL generation with metric."""
        context = {
            'tables': [{'name': 'users', 'fields': ['id']}],
            'metrics': [{'name': 'total', 'sql': 'COUNT(*)'}]
        }
        sql = _generate_sql_template('count users', context)
        assert 'COUNT(*)' in sql


@pytest.mark.skipif(not HAS_FASTAPI, reason="FastAPI not installed")
class TestHealthEndpoint:
    """Tests for health endpoint."""

    def test_healthz(self) -> None:
        """Test health check endpoint."""
        response = client.get('/healthz')
        assert response.status_code == 200
        data = response.json()
        assert data['status'] == 'ok'
        assert 'version' in data
        assert 'llm_configured' in data
