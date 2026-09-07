# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""AOF quality_gate bridge.

Thin adapter only: execute copied lint scripts as quality gates.
"""

from .gate import quality_gate_commands

__all__ = ["quality_gate_commands"]
