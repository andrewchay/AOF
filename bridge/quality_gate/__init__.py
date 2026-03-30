"""AOF quality_gate bridge.

Thin adapter only: execute copied lint scripts as quality gates.
"""

from .gate import quality_gate_commands

__all__ = ["quality_gate_commands"]
