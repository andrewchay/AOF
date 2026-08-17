"""Built-in validators for governed Semantic IR proposals."""

from .ontology import ontology_release_validator
from .query_regression import semantic_query_regression_validator

__all__ = ["ontology_release_validator", "semantic_query_regression_validator"]
