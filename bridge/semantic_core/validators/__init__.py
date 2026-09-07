# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Built-in validators for governed Semantic IR proposals."""

from .ontology import ontology_release_validator
from .query_regression import semantic_query_regression_validator

__all__ = ["ontology_release_validator", "semantic_query_regression_validator"]
