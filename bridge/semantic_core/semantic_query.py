# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Typed semantic intents and deterministic SQL compilation."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from .canonical import canonical_data, canonical_json, content_digest
from .models import ResourceKind, SemanticResource, validate_resource_id


class SemanticQueryCompileError(ValueError):
    """Raised when a semantic intent cannot be compiled without guessing."""


def _non_empty(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise SemanticQueryCompileError(f"{field} must be a non-empty string")
    return value.strip()


def _resource_ids(values: Iterable[str], field: str) -> tuple[str, ...]:
    normalized = []
    for value in values:
        resource_id = _non_empty(value, field)
        validate_resource_id(resource_id)
        normalized.append(resource_id)
    return tuple(sorted(set(normalized)))


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class IntentFilter:
    dimension: str
    operator: str
    value: Any

    @classmethod
    def create(cls, *, dimension: str, operator: str, value: Any) -> "IntentFilter":
        dimension_id = _non_empty(dimension, "filter dimension")
        validate_resource_id(dimension_id)
        normalized_operator = _non_empty(operator, "filter operator").lower()
        if normalized_operator not in {"eq", "ne", "gt", "gte", "lt", "lte", "in"}:
            raise SemanticQueryCompileError(
                f"unsupported semantic filter operator: {normalized_operator}"
            )
        normalized_value = canonical_data(value)
        if normalized_operator == "in" and (
            not isinstance(normalized_value, list) or not normalized_value
        ):
            raise SemanticQueryCompileError("semantic IN filter requires a non-empty list")
        return cls(dimension_id, normalized_operator, _freeze(normalized_value))

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "operator": self.operator,
            "value": canonical_data(self.value),
        }


@dataclass(frozen=True)
class SemanticIntent:
    metrics: tuple[str, ...]
    dimensions: tuple[str, ...]
    filters: tuple[IntentFilter, ...]
    limit: int | None
    purpose: str
    intent_digest: str

    @classmethod
    def create(
        cls,
        *,
        metrics: Iterable[str],
        dimensions: Iterable[str] = (),
        filters: Iterable[IntentFilter] = (),
        limit: int | None = None,
        purpose: str,
    ) -> "SemanticIntent":
        metric_ids = _resource_ids(metrics, "metrics")
        if not metric_ids:
            raise SemanticQueryCompileError("metrics must contain at least one resource ID")
        dimension_ids = _resource_ids(dimensions, "dimensions")
        if limit is not None and (
            isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
        ):
            raise SemanticQueryCompileError("limit must be a positive integer")
        filter_values = tuple(filters)
        if not all(isinstance(item, IntentFilter) for item in filter_values):
            raise SemanticQueryCompileError("filters must contain IntentFilter values")
        normalized_filters = tuple(
            sorted(
                filter_values,
                key=lambda item: (
                    item.dimension,
                    item.operator,
                    canonical_json(item.value),
                ),
            )
        )
        payload = {
            "api_version": "aof.semantic-intent/v1",
            "metrics": list(metric_ids),
            "dimensions": list(dimension_ids),
            "filters": [item.to_dict() for item in normalized_filters],
            "limit": limit,
            "purpose": _non_empty(purpose, "purpose"),
        }
        return cls(
            metrics=metric_ids,
            dimensions=dimension_ids,
            filters=normalized_filters,
            limit=limit,
            purpose=payload["purpose"],
            intent_digest=content_digest(payload),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "SemanticIntent":
        if value.get("api_version") != "aof.semantic-intent/v1":
            raise SemanticQueryCompileError("unsupported semantic intent api_version")
        raw_filters = value.get("filters", ())
        if not isinstance(raw_filters, list | tuple):
            raise SemanticQueryCompileError("filters must be a list")
        filters = []
        for item in raw_filters:
            if not isinstance(item, Mapping):
                raise SemanticQueryCompileError("filters must contain semantic objects")
            try:
                filters.append(
                    IntentFilter.create(
                        dimension=item["dimension"],
                        operator=item["operator"],
                        value=item.get("value"),
                    )
                )
            except KeyError as exc:
                raise SemanticQueryCompileError("semantic filter is incomplete") from exc
        metrics = value.get("metrics", ())
        dimensions = value.get("dimensions", ())
        if not isinstance(metrics, list | tuple) or not isinstance(
            dimensions, list | tuple
        ):
            raise SemanticQueryCompileError("metrics and dimensions must be lists")
        intent = cls.create(
            metrics=metrics,
            dimensions=dimensions,
            filters=filters,
            limit=value.get("limit"),
            purpose=value.get("purpose", ""),
        )
        if value.get("intent_digest") != intent.intent_digest:
            raise SemanticQueryCompileError(
                "intent_digest does not match canonical semantic intent"
            )
        return intent

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.semantic-intent/v1",
            "metrics": list(self.metrics),
            "dimensions": list(self.dimensions),
            "filters": [item.to_dict() for item in self.filters],
            "limit": self.limit,
            "purpose": self.purpose,
            "intent_digest": self.intent_digest,
        }


@dataclass(frozen=True)
class SemanticSqlPlan:
    intent_digest: str
    dialect: str
    sql: str
    parameter_values: tuple[Any, ...]
    resource_revisions: Mapping[str, str]
    plan_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.semantic-sql-plan/v1",
            "intent_digest": self.intent_digest,
            "dialect": self.dialect,
            "sql": self.sql,
            "parameter_values": list(self.parameter_values),
            "resource_revisions": canonical_data(self.resource_revisions),
            "plan_digest": self.plan_digest,
        }


class SemanticSqlCompiler:
    """Compile resource-ID-only intents without allowing physical-name invention."""

    _IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_$]*$")
    _AGGREGATIONS = {
        "sum": "SUM",
        "count": "COUNT",
        "avg": "AVG",
        "min": "MIN",
        "max": "MAX",
        "count_distinct": "COUNT_DISTINCT",
    }
    _FILTER_OPERATORS = {
        "eq": "=",
        "ne": "<>",
        "gt": ">",
        "gte": ">=",
        "lt": "<",
        "lte": "<=",
    }

    def __init__(self, resources: Iterable[SemanticResource]) -> None:
        values = tuple(resources)
        self.resources = {item.resource_id: item for item in values}
        if len(self.resources) != len(values):
            raise SemanticQueryCompileError("semantic resources contain duplicate IDs")

    def compile(self, intent: SemanticIntent, *, dialect: str = "ansi") -> SemanticSqlPlan:
        if dialect != "ansi":
            raise SemanticQueryCompileError(f"unsupported SQL dialect: {dialect}")
        metrics = tuple(
            self._resource(resource_id, ResourceKind.METRIC) for resource_id in intent.metrics
        )
        dimensions = tuple(
            self._resource(resource_id, ResourceKind.DIMENSION)
            for resource_id in intent.dimensions
        )
        filter_dimensions = tuple(
            self._resource(item.dimension, ResourceKind.DIMENSION)
            for item in intent.filters
        )
        selected = (*metrics, *dimensions, *filter_dimensions)
        dataset_ids = {
            dependency
            for resource in selected
            for dependency in resource.depends_on
            if self.resources.get(dependency, None)
            and self.resources[dependency].kind is ResourceKind.PHYSICAL_DATASET
        }
        if len(dataset_ids) != 1:
            raise SemanticQueryCompileError(
                "semantic SQL compilation requires exactly one shared PhysicalDataset"
            )
        dataset = self._resource(next(iter(dataset_ids)), ResourceKind.PHYSICAL_DATASET)
        physical_name = _non_empty(dataset.spec.get("physical_name"), "dataset physical_name")
        projections = [self._dimension_projection(item) for item in dimensions]
        projections.extend(self._metric_projection(item) for item in metrics)
        sql = f"SELECT {', '.join(projections)} FROM {self._qualified(physical_name)}"
        predicates, parameters = self._predicates(intent.filters, filter_dimensions)
        if predicates:
            sql += " WHERE " + " AND ".join(predicates)
        if dimensions:
            grouping = ", ".join(
                self._identifier(self._dimension_field(item)) for item in dimensions
            )
            sql += f" GROUP BY {grouping} ORDER BY {grouping}"
        if intent.limit is not None:
            sql += f" LIMIT {intent.limit}"
        used_by_id = {
            item.resource_id: item
            for item in (dataset, *metrics, *dimensions, *filter_dimensions)
        }
        revisions = {
            item.resource_id: item.revision_id
            for item in sorted(used_by_id.values(), key=lambda resource: resource.resource_id)
        }
        payload = {
            "api_version": "aof.semantic-sql-plan/v1",
            "intent_digest": intent.intent_digest,
            "dialect": dialect,
            "sql": sql,
            "parameter_values": canonical_data(parameters),
            "resource_revisions": revisions,
        }
        return SemanticSqlPlan(
            intent_digest=intent.intent_digest,
            dialect=dialect,
            sql=sql,
            parameter_values=tuple(parameters),
            resource_revisions=MappingProxyType(revisions),
            plan_digest=content_digest(payload),
        )

    def _resource(self, resource_id: str, kind: ResourceKind) -> SemanticResource:
        resource = self.resources.get(resource_id)
        if resource is None:
            raise SemanticQueryCompileError(f"semantic resource is not published: {resource_id}")
        if resource.kind is not kind:
            raise SemanticQueryCompileError(
                f"semantic resource must be {kind.value}: {resource_id}"
            )
        return resource

    def _metric_projection(self, resource: SemanticResource) -> str:
        aggregation = _non_empty(resource.spec.get("aggregation"), "metric aggregation")
        function = self._AGGREGATIONS.get(aggregation.lower())
        if function is None:
            raise SemanticQueryCompileError(f"unsupported metric aggregation: {aggregation}")
        raw_measure = _non_empty(resource.spec.get("measure"), "metric measure")
        if raw_measure == "*":
            if function != "COUNT":
                raise SemanticQueryCompileError(
                    "wildcard metric measure is only valid for count aggregation"
                )
            measure = "*"
        else:
            measure = self._identifier(raw_measure)
        alias = self._identifier(resource.name.replace("-", "_"))
        if function == "COUNT_DISTINCT":
            return f"COUNT(DISTINCT {measure}) AS {alias}"
        return f"{function}({measure}) AS {alias}"

    def _dimension_projection(self, resource: SemanticResource) -> str:
        field = self._identifier(self._dimension_field(resource))
        return f"{field} AS {field}"

    def _predicates(
        self,
        filters: tuple[IntentFilter, ...],
        resources: tuple[SemanticResource, ...],
    ) -> tuple[list[str], list[Any]]:
        predicates: list[str] = []
        parameters: list[Any] = []
        for item, resource in zip(filters, resources, strict=True):
            field = self._identifier(self._dimension_field(resource))
            if item.operator == "in":
                placeholders = []
                for value in item.value:
                    parameters.append(value)
                    placeholders.append(f":p{len(parameters)}")
                predicates.append(f"{field} IN ({', '.join(placeholders)})")
            else:
                parameters.append(item.value)
                predicates.append(
                    f"{field} {self._FILTER_OPERATORS[item.operator]} :p{len(parameters)}"
                )
        return predicates, parameters

    @staticmethod
    def _dimension_field(resource: SemanticResource) -> str:
        return _non_empty(resource.spec.get("field"), "dimension field")

    def _qualified(self, value: str) -> str:
        return ".".join(self._identifier(part) for part in value.split("."))

    def _identifier(self, value: str) -> str:
        if not self._IDENTIFIER.fullmatch(value):
            raise SemanticQueryCompileError(f"unsafe SQL identifier: {value}")
        return f'"{value}"'
