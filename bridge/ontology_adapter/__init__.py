# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""AOF ontology_adapter bridge.

Thin adapter only: build Cognee ontology config from AOF ontology settings.
"""

from .adapter import apply_ontology, build_cognee_ontology_config

__all__ = ["apply_ontology", "build_cognee_ontology_config"]
