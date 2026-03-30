"""Build Cognee ontology config from AOF settings.

Upstream target:
- /Users/chaihao/LLM/cognee/cognee/modules/ontology/rdf_xml/RDFLibOntologyResolver.py
- /Users/chaihao/LLM/cognee/cognee/modules/ontology/matching_strategies.py
"""

from __future__ import annotations


def build_cognee_ontology_config(
    ontology_file: str,
    resolver_cls,
    matching_strategy=None,
) -> dict:
    """Return Cognee Config shape for cognify(config=...)."""
    resolver = resolver_cls(ontology_file=ontology_file, matching_strategy=matching_strategy)
    return {"ontology_config": {"ontology_resolver": resolver}}
