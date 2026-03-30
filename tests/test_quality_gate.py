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
