# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
import unittest

from bridge.quality_gate.gate import quality_gate_commands


class TestQualityGate(unittest.TestCase):
    def test_commands(self):
        cmds = quality_gate_commands("/tmp/aof")
        # Should return unified gate + L1-L3 individual commands
        self.assertGreaterEqual(len(cmds), 1)
        self.assertIn("run_all_lints.py", cmds[0])

    def test_unified_gate_first(self):
        """Unified quality gate should be the first command."""
        cmds = quality_gate_commands("/tmp/aof")
        self.assertIn("--quick", cmds[0])


if __name__ == "__main__":
    unittest.main()
