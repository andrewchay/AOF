# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
import unittest
from pathlib import Path
from unittest.mock import patch

from bridge.preflight import preflight_checks_for_add, preflight_checks_for_cognify


class TestPreflight(unittest.TestCase):
    def test_missing_paths_for_cognify(self):
        spec = {
            "cognee": {"root": "/path/not/exist"},
            "ontology": {"file": "/path/not/exist.owl"},
        }
        issues = preflight_checks_for_cognify(spec, require_api_key=False)
        self.assertEqual(len(issues), 2)

    def test_missing_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            issues = preflight_checks_for_cognify({}, require_api_key=True)
        self.assertTrue(any("LLM_API_KEY" in x for x in issues))

    def test_add_data_path_missing(self):
        issues = preflight_checks_for_add({}, data_paths=["/path/not/exist"], require_api_key=False)
        self.assertEqual(len(issues), 1)

    def test_add_data_path_ok(self):
        p = str(Path("/tmp"))
        issues = preflight_checks_for_add({}, data_paths=[p], require_api_key=False)
        self.assertEqual(issues, [])


if __name__ == "__main__":
    unittest.main()
