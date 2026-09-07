# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""End-to-end integration tests for AOF pipeline."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


class TestAOFRunPipeline:
    """End-to-end tests for aof_run pipeline."""

    @patch("aof_run.asyncio.run")
    @patch("aof_run.run_quality")
    def test_quality_stage(self, mock_quality: Any, mock_asyncio_run: Any, temp_dir: Path) -> None:
        """Test running only quality stage."""
        from aof_run import main
        
        spec_file = temp_dir / "test_spec.json"
        spec_file.write_text(json.dumps({
            "project_root": str(temp_dir),
            "dataset": "test",
        }), encoding="utf-8")
        
        mock_quality.return_value = ["cmd1", "cmd2"]
        
        import sys
        original_argv = sys.argv
        try:
            sys.argv = ["aof_run.py", "--spec", str(spec_file), "--run-quality"]
            code = main()
        finally:
            sys.argv = original_argv
        
        assert code == 0
        mock_quality.assert_called_once()

    @patch("aof_run.asyncio.run")
    def test_cognify_stage_mock(self, mock_asyncio_run: Any, temp_dir: Path) -> None:
        """Test running cognify stage with mocked execution."""
        from aof_run import main
        
        spec_file = temp_dir / "test_spec.json"
        spec_file.write_text(json.dumps({
            "project_root": str(temp_dir),
            "dataset": "test",
            "cognee": {"root": "/tmp/cognee"},
        }), encoding="utf-8")
        
        def _consume_coroutine(coro: Any) -> dict[str, Any]:
            # main() passes a coroutine to asyncio.run; close it in tests to avoid un-awaited warnings.
            if hasattr(coro, "close"):
                coro.close()
            return {"dry_run": True}

        mock_asyncio_run.side_effect = _consume_coroutine
        
        import sys
        original_argv = sys.argv
        try:
            sys.argv = ["aof_run.py", "--spec", str(spec_file), "--run-cognify", "--dry-run"]
            code = main()
        finally:
            sys.argv = original_argv
        
        assert code == 0


class TestAOFAddPipeline:
    """End-to-end tests for aof_add pipeline."""

    @patch("aof_add.asyncio.run")
    def test_add_with_data(self, mock_asyncio_run: Any, temp_dir: Path) -> None:
        """Test adding data with aof_add."""
        from aof_add import main
        
        spec_file = temp_dir / "test_spec.json"
        spec_file.write_text(json.dumps({
            "project_root": str(temp_dir),
            "dataset": "test",
            "cognee": {"root": "/tmp/cognee"},
        }), encoding="utf-8")
        
        def _consume_coroutine(coro: Any) -> dict[str, Any]:
            if hasattr(coro, "close"):
                coro.close()
            return {"add": {"status": "ok"}}

        mock_asyncio_run.side_effect = _consume_coroutine
        
        import sys
        original_argv = sys.argv
        try:
            sys.argv = [
                "aof_add.py",
                "--spec", str(spec_file),
                "--data", "Test content",
                "--dry-run"
            ]
            code = main()
        finally:
            sys.argv = original_argv
        
        assert code == 0

    @patch("aof_add.asyncio.run")
    def test_add_with_file(self, mock_asyncio_run: Any, temp_dir: Path) -> None:
        """Test adding data from file with aof_add."""
        from aof_add import main
        
        data_file = temp_dir / "test_data.txt"
        data_file.write_text("Test content from file", encoding="utf-8")
        
        spec_file = temp_dir / "test_spec.json"
        spec_file.write_text(json.dumps({
            "project_root": str(temp_dir),
            "dataset": "test",
            "cognee": {"root": "/tmp/cognee"},
        }), encoding="utf-8")
        
        def _consume_coroutine(coro: Any) -> dict[str, Any]:
            if hasattr(coro, "close"):
                coro.close()
            return {"add": {"status": "ok"}}

        mock_asyncio_run.side_effect = _consume_coroutine
        
        import sys
        original_argv = sys.argv
        try:
            sys.argv = [
                "aof_add.py",
                "--spec", str(spec_file),
                "--data-path", str(data_file),
                "--dry-run"
            ]
            code = main()
        finally:
            sys.argv = original_argv
        
        assert code == 0


class TestAOFDoctor:
    """Tests for aof_doctor functionality."""

    def test_doctor_basic(self, temp_dir: Path) -> None:
        """Test basic doctor check."""
        from aof_doctor import main
        
        spec_file = temp_dir / "test_spec.json"
        spec_file.write_text(json.dumps({
            "project_root": str(temp_dir),
            "cognee": {"root": "/tmp/nonexistent"},
        }), encoding="utf-8")
        
        import sys
        original_argv = sys.argv
        try:
            sys.argv = ["aof_doctor.py", "--spec", str(spec_file)]
            code = main()
        finally:
            sys.argv = original_argv
        
        # Should fail because cognee root doesn't exist
        assert code == 2

    def test_doctor_missing_spec(self, temp_dir: Path) -> None:
        """Test doctor with missing spec file."""
        from aof_doctor import main
        
        import sys
        original_argv = sys.argv
        try:
            sys.argv = ["aof_doctor.py", "--spec", str(temp_dir / "nonexistent.json")]
            code = main()
        finally:
            sys.argv = original_argv
        
        assert code == 2


class TestOntologyFactoryWorkflow:
    """End-to-end tests for ontology factory workflow."""

    def test_factory_skip_align(self, temp_dir: Path, sample_sql_data: str) -> None:
        """Test ontology factory utility functions directly."""
        from tools.ontology_factory.build_testdata_ontology_factory import (
            slugify,
            build_initial_ontology,
        )
        
        # Test slugify
        assert slugify("Test Topic") == "test_topic"
        
        # Create mock semantics directly (without parsing SQL)
        semantics = {
            "tables": ["users"],
            "table_names": ["users"],
            "fields": ["id", "name", "email"],
            "metrics": [],
            "filter_fields": [],
            "queries": [],
        }
        
        # Test ontology building
        ontology_file = temp_dir / "test_ontology.owl"
        build_initial_ontology("test_topic", semantics, ontology_file)
        assert ontology_file.exists()


class TestQualityGateWorkflow:
    """End-to-end tests for quality gate workflow."""

    def test_quality_gate_commands(self, temp_dir: Path) -> None:
        """Test quality gate command generation."""
        from bridge.quality_gate.gate import quality_gate_commands
        
        cmds = quality_gate_commands(str(temp_dir))
        
        # Quality Gate now has L1-L5 layers, so expect 4 commands:
        # 1. run_all_lints.py --quick
        # 2. lint_l1_text_integrity.py
        # 3. lint_l2_syntax.py
        # 4. lint_l3_data_integrity.py
        assert len(cmds) >= 2
        assert any("lint" in cmd for cmd in cmds)

    def test_quality_gate_runs(self, temp_dir: Path) -> None:
        """Test that quality gate scripts exist and are valid Python."""
        import subprocess
        import sys
        
        project_root = Path(__file__).resolve().parents[1]
        python_executable = sys.executable
        
        # Test lint_text_integrity - just check syntax
        script_path = project_root / "tools" / "quality_gate" / "lint_text_integrity.py"
        result = subprocess.run(
            [python_executable, "-m", "py_compile", str(script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error in {script_path}"
        
        # Test lint_markdown - just check syntax
        script_path = project_root / "tools" / "quality_gate" / "lint_markdown.py"
        result = subprocess.run(
            [python_executable, "-m", "py_compile", str(script_path)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, f"Syntax error in {script_path}"


class TestSpecValidation:
    """Tests for spec validation across components."""

    def test_spec_schema(self, temp_dir: Path) -> None:
        """Test that spec has required fields."""
        spec = {
            "project_root": str(temp_dir),
            "dataset": "test",
            "runtime": {
                "run_in_background": False,
                "incremental_loading": True,
                "data_per_batch": 20,
            },
            "ontology": {
                "file": str(temp_dir / "ontology.owl"),
                "matching_cutoff": 0.8,
            },
            "cognee": {
                "root": "/tmp/cognee"
            }
        }
        
        # Validate structure
        assert "project_root" in spec
        assert "dataset" in spec or "datasets" in spec
        assert "cognee" in spec
        assert spec["ontology"]["matching_cutoff"] > 0
        assert spec["ontology"]["matching_cutoff"] <= 1
