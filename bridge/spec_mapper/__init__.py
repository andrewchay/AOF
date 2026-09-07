# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""AOF spec_mapper bridge.

Thin adapter only: map AOF run spec to Cognee call args.
No extraction logic is implemented here.
"""

from .mapper import map_aof_spec_to_cognee

__all__ = ["map_aof_spec_to_cognee"]
