# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Integration tests for data adapter module."""

from __future__ import annotations

import json
import sys
from pathlib import Path


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.data_adapter.normalize_for_aof import main as normalize_main


class TestDataAdapterSQL:
    """Tests for SQL data adaptation."""

    def test_normalize_sql_insert(self, temp_dir: Path, sample_sql_data: str) -> None:
        """Test normalizing SQL INSERT statements."""
        input_file = temp_dir / "input.sql"
        output_txt = temp_dir / "output.txt"
        output_jsonl = temp_dir / "output.jsonl"
        
        input_file.write_text(sample_sql_data, encoding="utf-8")
        
        # Mock sys.argv
        import sys
        original_argv = sys.argv
        try:
            sys.argv = [
                "normalize_for_aof.py",
                "--input", str(input_file),
                "--output-txt", str(output_txt),
                "--output-jsonl", str(output_jsonl),
                "--table", "users"
            ]
            normalize_main()
        finally:
            sys.argv = original_argv
        
        # Check outputs
        assert output_txt.exists()
        assert output_jsonl.exists()
        
        txt_content = output_txt.read_text(encoding="utf-8")
        assert "users" in txt_content
        assert "Alice" in txt_content
        
        jsonl_lines = output_jsonl.read_text(encoding="utf-8").strip().split("\n")
        assert len(jsonl_lines) > 0
        data = json.loads(jsonl_lines[0])
        # Check structure: data contains "table" at top level, "_table" in "raw"
        assert data["table"] == "users"
        assert data["raw"]["_table"] == "users"
        assert data["raw"]["_kind"] == "insert"

    def test_normalize_sql_empty(self, temp_dir: Path) -> None:
        """Test normalizing empty SQL file."""
        input_file = temp_dir / "empty.sql"
        output_txt = temp_dir / "output.txt"
        
        input_file.write_text("", encoding="utf-8")
        
        import sys
        original_argv = sys.argv
        try:
            sys.argv = [
                "normalize_for_aof.py",
                "--input", str(input_file),
                "--output-txt", str(output_txt),
            ]
            normalize_main()
        finally:
            sys.argv = original_argv
        
        assert output_txt.exists()
        assert output_txt.read_text(encoding="utf-8") == ""


class TestDataAdapterCSV:
    """Tests for CSV data adaptation."""

    def test_normalize_csv(self, temp_dir: Path, sample_csv_data: str) -> None:
        """Test normalizing CSV data."""
        input_file = temp_dir / "input.csv"
        output_txt = temp_dir / "output.txt"
        output_jsonl = temp_dir / "output.jsonl"
        
        input_file.write_text(sample_csv_data, encoding="utf-8")
        
        import sys
        original_argv = sys.argv
        try:
            sys.argv = [
                "normalize_for_aof.py",
                "--input", str(input_file),
                "--output-txt", str(output_txt),
                "--output-jsonl", str(output_jsonl),
            ]
            normalize_main()
        finally:
            sys.argv = original_argv
        
        assert output_txt.exists()
        txt_content = output_txt.read_text(encoding="utf-8")
        assert "Alice" in txt_content
        assert "Bob" in txt_content


class TestDataAdapterJSON:
    """Tests for JSON data adaptation."""

    def test_normalize_json(self, temp_dir: Path) -> None:
        """Test normalizing JSON data."""
        input_file = temp_dir / "input.json"
        output_txt = temp_dir / "output.txt"
        
        data = [
            {"id": 1, "name": "Product A", "price": 100},
            {"id": 2, "name": "Product B", "price": 200}
        ]
        input_file.write_text(json.dumps(data), encoding="utf-8")
        
        import sys
        original_argv = sys.argv
        try:
            sys.argv = [
                "normalize_for_aof.py",
                "--input", str(input_file),
                "--output-txt", str(output_txt),
            ]
            normalize_main()
        finally:
            sys.argv = original_argv
        
        assert output_txt.exists()
        txt_content = output_txt.read_text(encoding="utf-8")
        assert "Product A" in txt_content
        assert "Product B" in txt_content
