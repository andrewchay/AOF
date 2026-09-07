# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Map AOF spec to Cognee parameters.

Upstream target:
- /Users/chaihao/LLM/cognee/cognee/api/v1/cognify/cognify.py
- /Users/chaihao/LLM/cognee/cognee/modules/run_custom_pipeline/run_custom_pipeline.py
"""

from __future__ import annotations


def map_aof_spec_to_cognee(spec: dict) -> dict:
    """Return a minimal Cognee-compatible argument dict.

    This function is intentionally thin. It only maps fields and defaults.
    """
    runtime = spec.get("runtime", {})
    return {
        "datasets": spec.get("dataset") or spec.get("datasets"),
        "run_in_background": runtime.get("run_in_background", False),
        "incremental_loading": runtime.get("incremental_loading", True),
        "data_per_batch": runtime.get("data_per_batch", 20),
    }
