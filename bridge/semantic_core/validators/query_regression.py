"""Deterministic regression contracts for governed semantic query releases."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ..canonical import canonical_json, content_digest
from ..governance import SemanticFinding
from ..models import ResourceKind, SemanticResource
from ..semantic_query import SemanticIntent, SemanticSqlCompiler


def semantic_query_regression_validator(
    resources: tuple[SemanticResource, ...],
) -> tuple[SemanticFinding, ...]:
    """Compile every QueryContract against the candidate release and block drift."""

    contracts = tuple(
        item for item in resources if item.kind is ResourceKind.QUERY_CONTRACT
    )
    if not contracts:
        return ()
    findings = []
    compiler = SemanticSqlCompiler(resources)
    for contract in contracts:
        contract_type = contract.spec.get("contract_type")
        if contract_type == "semantic_search":
            finding = _validate_semantic_search(contract, resources)
            if finding is not None:
                findings.append(finding)
            continue
        if contract_type != "semantic_sql":
            findings.append(
                _finding(
                    contract,
                    "unsupported_contract_type",
                    "QueryContract contract_type must be semantic_sql",
                    {"contract_type": contract.spec.get("contract_type")},
                )
            )
            continue
        raw_intent = contract.spec.get("intent")
        expected_sql = contract.spec.get("expected_sql")
        if not isinstance(raw_intent, Mapping) or not isinstance(expected_sql, str) or not expected_sql.strip():
            findings.append(
                _finding(
                    contract,
                    "invalid_query_contract",
                    "semantic_sql QueryContract requires intent and expected_sql",
                    {},
                )
            )
            continue
        try:
            plan = compiler.compile(SemanticIntent.from_dict(raw_intent))
        except Exception as exc:
            findings.append(
                _finding(
                    contract,
                    "semantic_query_compile_failed",
                    f"semantic query contract failed to compile: {exc}",
                    {"error_type": type(exc).__name__},
                )
            )
            continue
        if plan.sql != expected_sql.strip():
            findings.append(
                _finding(
                    contract,
                    "semantic_sql_regression",
                    "deterministic SQL no longer matches the governed contract",
                    {
                        "intent_digest": plan.intent_digest,
                        "expected_sql": expected_sql.strip(),
                        "actual_sql": plan.sql,
                        "actual_plan_digest": plan.plan_digest,
                    },
                )
            )
    return tuple(sorted(findings, key=lambda item: item.finding_id))


def _validate_semantic_search(
    contract: SemanticResource,
    resources: tuple[SemanticResource, ...],
) -> SemanticFinding | None:
    query = contract.spec.get("query")
    expected = contract.spec.get("expected_resource_ids")
    limit = contract.spec.get("limit", 10)
    if (
        not isinstance(query, str)
        or not query.strip()
        or not isinstance(expected, list | tuple)
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or limit < 1
    ):
        return _finding(
            contract,
            "invalid_query_contract",
            "semantic_search QueryContract requires query, expected_resource_ids, and positive limit",
            {},
        )
    searchable_kinds = {
        ResourceKind.RETRIEVAL_PROFILE,
        ResourceKind.CONCEPT,
        ResourceKind.METRIC,
        ResourceKind.DIMENSION,
        ResourceKind.LOGICAL_DATASET,
        ResourceKind.PHYSICAL_DATASET,
        ResourceKind.FIELD_BINDING,
        ResourceKind.RELATION_BINDING,
        ResourceKind.QUERY_TEMPLATE,
    }
    needle = query.casefold()
    actual = tuple(
        sorted(
            item.resource_id
            for item in resources
            if item.kind in searchable_kinds
            and needle in canonical_json(item.to_dict()).casefold()
        )[:limit]
    )
    normalized_expected = tuple(sorted({str(item) for item in expected}))
    if actual == normalized_expected:
        return None
    return _finding(
        contract,
        "semantic_search_regression",
        "semantic search results no longer match the governed contract",
        {
            "query": query,
            "expected_resource_ids": normalized_expected,
            "actual_resource_ids": actual,
        },
    )


def _finding(
    contract: SemanticResource,
    code: str,
    message: str,
    details: dict[str, Any],
) -> SemanticFinding:
    payload = {
        "validator": "semantic-query-regression",
        "contract_revision": contract.revision_id,
        "code": code,
        "message": message,
        "details": details,
    }
    digest = content_digest(payload).split(":", 1)[1][:20]
    return SemanticFinding(
        finding_id=f"finding:semantic-query-regression:{digest}",
        validator="semantic-query-regression",
        severity="blocking",
        message=message,
        resource_id=contract.resource_id,
        waiver_allowed=False,
        details={"code": code, **details},
    )
