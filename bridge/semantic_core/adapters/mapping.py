"""Convert legacy middle-layer YAML mappings into dependency-closed Semantic IR."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Iterable

from ..models import ResourceKind, SemanticResource
from .base import AdapterContext, safe_segment


def _rows(value: Any, *, key_field: str) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [dict(item) for item in value if isinstance(item, Mapping)]
    if isinstance(value, Mapping):
        rows = []
        for key, item in value.items():
            row = dict(item) if isinstance(item, Mapping) else {"value": item}
            row.setdefault(key_field, str(key))
            rows.append(row)
        return rows
    return []


def _table_from_field(value: str) -> str | None:
    parts = value.rsplit(".", 1)
    return parts[0] if len(parts) == 2 and parts[0] else None


def adapt_mapping_library(
    library: Mapping[str, Any], *, context: AdapterContext
) -> tuple[SemanticResource, ...]:
    """Adapt both list-shaped generated YAML and dict-shaped API mapping libraries."""

    metrics = _rows(library.get("metrics", []), key_field="metric_id")
    dimensions = _rows(library.get("dimensions", []), key_field="business_dim")
    terms = _rows(library.get("terms", []), key_field="term")
    patterns = _rows(library.get("sql_patterns", []), key_field="pattern_id")
    datasets: dict[str, SemanticResource] = {}
    resources: list[SemanticResource] = []
    metric_ids: dict[str, str] = {}

    def ensure_dataset(table_name: str) -> SemanticResource:
        resource_id = context.resource_id("physical-dataset", table_name)
        if resource_id not in datasets:
            datasets[resource_id] = SemanticResource.create(
                resource_id=resource_id,
                kind=ResourceKind.PHYSICAL_DATASET,
                name=safe_segment(table_name),
                display_name=table_name,
                domain=context.domain,
                owner=context.owner,
                evidence=[{"evidence_id": f"legacy:mapping:dataset:{table_name}"}],
                spec={"physical_name": table_name, "legacy_source": "mapping_library"},
            )
        return datasets[resource_id]

    for row in metrics:
        metric_name = str(row.get("metric_id") or row.get("name") or "").strip()
        if not metric_name:
            continue
        base_table = str(row.get("base_table") or "").strip()
        dependencies = [ensure_dataset(base_table).resource_id] if base_table else []
        resource_id = context.resource_id("metric", metric_name)
        metric_ids[metric_name] = resource_id
        resources.append(
            SemanticResource.create(
                resource_id=resource_id,
                kind=ResourceKind.METRIC,
                name=safe_segment(metric_name),
                display_name=str(row.get("cn_name") or metric_name),
                domain=context.domain,
                owner=context.owner,
                depends_on=dependencies,
                evidence=[{"evidence_id": f"legacy:mapping:metric:{metric_name}"}],
                spec={"legacy_record": row, "legacy_source": "metric_catalog"},
            )
        )

    for row in dimensions:
        dimension_name = str(row.get("business_dim") or row.get("name") or "").strip()
        if not dimension_name:
            continue
        physical_field = str(row.get("physical_field") or "").strip()
        table_name = _table_from_field(physical_field)
        dependencies = [ensure_dataset(table_name).resource_id] if table_name else []
        resources.append(
            SemanticResource.create(
                resource_id=context.resource_id("dimension", dimension_name),
                kind=ResourceKind.DIMENSION,
                name=safe_segment(dimension_name),
                display_name=str(row.get("display_name") or dimension_name),
                domain=context.domain,
                owner=context.owner,
                depends_on=dependencies,
                evidence=[{"evidence_id": f"legacy:mapping:dimension:{dimension_name}"}],
                spec={"legacy_record": row, "legacy_source": "dimension_mapping"},
            )
        )

    for row in terms:
        term = str(row.get("term") or row.get("name") or "").strip()
        if not term:
            continue
        canonical_metric = str(row.get("canonical_metric") or "").strip()
        dependencies = [metric_ids[canonical_metric]] if canonical_metric in metric_ids else []
        resources.append(
            SemanticResource.create(
                resource_id=context.resource_id("concept", term),
                kind=ResourceKind.CONCEPT,
                name=safe_segment(term),
                display_name=term,
                domain=context.domain,
                owner=context.owner,
                depends_on=dependencies,
                evidence=[{"evidence_id": f"legacy:mapping:term:{safe_segment(term)}"}],
                spec={
                    "legacy_record": row,
                    "legacy_source": "term_mapping",
                    "unresolved_canonical_metric": canonical_metric if canonical_metric and not dependencies else None,
                },
            )
        )

    for row in patterns:
        pattern_id = str(row.get("pattern_id") or row.get("name") or "").strip()
        if not pattern_id:
            continue
        resources.append(
            SemanticResource.create(
                resource_id=context.resource_id("query-template", pattern_id),
                kind=ResourceKind.QUERY_TEMPLATE,
                name=safe_segment(pattern_id),
                display_name=str(row.get("intent") or pattern_id),
                domain=context.domain,
                owner=context.owner,
                evidence=[{"evidence_id": f"legacy:mapping:query-template:{pattern_id}"}],
                spec={"legacy_record": row, "legacy_source": "sql_pattern_library"},
            )
        )

    combined: Iterable[SemanticResource] = [*datasets.values(), *resources]
    return tuple(sorted(combined, key=lambda resource: resource.resource_id))
