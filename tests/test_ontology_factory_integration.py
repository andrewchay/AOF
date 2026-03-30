"""Integration tests for ontology factory module."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ontology_factory.build_testdata_ontology_factory import (
    slugify,
    concept_name,
    individual_name,
)


class TestOntologyFactoryUtils:
    """Tests for ontology factory utility functions."""

    def test_slugify_basic(self) -> None:
        """Test basic slugify functionality."""
        assert slugify("Hello World") == "hello_world"
        assert slugify("Test-Topic_123") == "test-topic_123"
        assert slugify("  spaced  ") == "spaced"

    def test_slugify_chinese(self) -> None:
        """Test slugify with Chinese characters."""
        assert slugify("中文测试") == "中文测试"
        assert slugify("测试-topic") == "测试-topic"

    def test_slugify_special_chars(self) -> None:
        """Test slugify with special characters."""
        assert slugify("a@b#c") == "a_b_c"
        assert slugify("test...file") == "test_file"

    def test_concept_name_basic(self) -> None:
        """Test concept name generation."""
        assert concept_name("user_account") == "UserAccount"
        assert concept_name("test_field") == "TestField"

    def test_concept_name_reserved(self) -> None:
        """Test concept name with reserved words."""
        assert concept_name("platform") == "Platform"
        assert concept_name("PLATFORM") == "Platform"
        assert concept_name("query") == "Query"
        assert concept_name("process") == "Process"

    def test_individual_name_basic(self) -> None:
        """Test individual name generation."""
        assert individual_name("User_123") == "user_123"
        assert individual_name("Test.Item") == "test.item"

    def test_individual_name_special(self) -> None:
        """Test individual name with special characters."""
        assert individual_name("Item @#$%") == "item"
        assert individual_name("  spaced  ") == "spaced"


class TestOntologyGeneration:
    """Tests for ontology generation."""

    def test_build_initial_ontology(self, temp_dir: Path) -> None:
        """Test building initial ontology from parsed semantics."""
        from tools.ontology_factory.build_testdata_ontology_factory import (
            build_initial_ontology,
        )
        
        topic = "test_topic"
        # Note: semantics structure matches what parse_sql_semantics returns
        semantics = {
            "tables": ["inventory"],
            "table_names": ["inventory"],
            "fields": ["sku", "name", "quantity"],
            "metrics": [],
            "filter_fields": [],
            "queries": [],
        }
        
        out_path = temp_dir / "test_ontology.owl"
        build_initial_ontology(topic, semantics, out_path)
        
        # Check file was created
        assert out_path.exists()
        
        # Check basic OWL structure
        owl_content = out_path.read_text(encoding="utf-8")
        assert "<?xml version='1.0' encoding='UTF-8'?>" in owl_content
        assert "<rdf:RDF" in owl_content
        assert "http://www.w3.org/1999/02/22-rdf-syntax-ns#" in owl_content
        assert "test_topic" in owl_content


class TestFeedbackPatching:
    """Tests for feedback patch application."""

    def test_apply_patch_add_class(self, temp_dir: Path) -> None:
        """Test applying ADD CLASS patch."""
        from tools.ontology_factory.apply_feedback_patches import _apply
        import xml.etree.ElementTree as ET
        
        # Create minimal ontology structure
        RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
        root = ET.Element(f"{{{RDF_NS}}}RDF")
        
        patch = {
            "action": "ADD",
            "entity_type": "CLASS",
            "name": "NewClass",
        }
        
        # Should not raise exception
        result = _apply(root, patch, dry_run=False)
        
        # Verify patch was applied (result should indicate success)
        assert result is not None


class TestAlignmentReport:
    """Tests for alignment report handling."""

    def test_analyze_patterns_empty(self) -> None:
        """Test analyzing empty report."""
        from tools.ontology_factory.analyze_alignment_patterns import analyze
        
        report = {"iterations": []}
        result = analyze(report)
        
        # Check expected structure
        assert "round_stats" in result
        assert "bucket_counts" in result
        assert "top_terms" in result
        assert len(result["round_stats"]) == 0
        assert len(result["top_terms"]) == 0

    def test_analyze_patterns_with_data(self) -> None:
        """Test analyzing report with iteration data."""
        from tools.ontology_factory.analyze_alignment_patterns import analyze
        
        report = {
            "topic": "test_topic",
            "status": "completed",
            "iterations": [
                {
                    "iteration": 1,
                    "matched_count": 5,
                    "unmatched_count": 3,
                    "unmatched": [
                        {"term": "user_id", "category": "individuals"},
                        {"term": "account", "category": "classes"},
                    ]
                }
            ]
        }
        result = analyze(report)
        
        assert result["topic"] == "test_topic"
        assert result["status"] == "completed"
        assert len(result["round_stats"]) == 1
        assert result["round_stats"][0]["matched_count"] == 5
        # Check top_terms contains the unmatched terms
        term_names = [t["term"] for t in result["top_terms"]]
        assert "user_id" in term_names


class TestExportFeedbackCandidates:
    """Tests for feedback candidate export."""

    def test_export_candidates(self, temp_dir: Path) -> None:
        """Test exporting feedback candidates."""
        from tools.ontology_factory.export_feedback_candidates import export_candidates
        import json
        
        # Create report file
        report = {
            "topic": "test_topic",
            "iterations": [
                {
                    "iteration": 1,
                    "matched_count": 5,
                    "unmatched_count": 2,
                    "unmatched": [
                        {"term": "missing_term", "category": "classes"},
                    ]
                }
            ]
        }
        
        report_file = temp_dir / "alignment_report_test.json"
        report_file.write_text(json.dumps(report), encoding="utf-8")
        
        output_jsonl = temp_dir / "candidates.jsonl"
        output_md = temp_dir / "candidates.md"
        
        export_candidates(report_file, output_jsonl, output_md, max_terms=100)
        
        assert output_jsonl.exists()
        assert output_md.exists()
        
        jsonl_content = output_jsonl.read_text(encoding="utf-8")
        assert "missing_term" in jsonl_content
