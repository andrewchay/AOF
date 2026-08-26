"""AOF ontology_adapter bridge.

Thin adapter only: build Cognee ontology config from AOF ontology settings.
"""

from .adapter import apply_ontology, build_cognee_ontology_config

__all__ = ["apply_ontology", "build_cognee_ontology_config"]
