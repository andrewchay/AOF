# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Compatibility adapters from existing AOF assets to Semantic IR."""

from .base import AdapterContext, AdapterError
from .mapping import adapt_mapping_library
from .okf import adapt_okf_bundle
from .ontology import adapt_ontology_release
from .ruleset import adapt_ruleset_release

__all__ = [
    "AdapterContext",
    "AdapterError",
    "adapt_mapping_library",
    "adapt_okf_bundle",
    "adapt_ontology_release",
    "adapt_ruleset_release",
]
