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
