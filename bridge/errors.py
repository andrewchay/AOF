# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""AOF bridge error taxonomy."""

from __future__ import annotations


class AOFBridgeError(Exception):
    """Base error for all bridge-related failures."""


class PreflightError(AOFBridgeError):
    """Raised when preflight checks fail."""


class CogneeImportError(AOFBridgeError):
    """Raised when Cognee cannot be imported from configured location."""


class OntologyConfigError(AOFBridgeError):
    """Raised when ontology configuration is invalid."""


class AddExecutionError(AOFBridgeError):
    """Raised when Cognee add execution fails."""


class CognifyExecutionError(AOFBridgeError):
    """Raised when Cognee cognify execution fails."""
