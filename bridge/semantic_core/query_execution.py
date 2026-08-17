"""Unified deterministic execution for trusted semantic query plans."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from rdflib import Graph

from bridge.ontology_governance import DatalogEngine, SparqlService

from .canonical import canonical_data, canonical_json, content_digest
from .query_plans import (
    QueryCapability,
    QueryPlan,
    QueryRequest,
    TrustedQueryError,
    TrustedSnapshotResolver,
)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list | tuple):
        return tuple(_freeze(item) for item in value)
    return value


@dataclass(frozen=True)
class QueryResult:
    status: str
    tenant_id: str
    capability: QueryCapability
    request_digest: str
    plan_digest: str
    run_id: str
    run_digest: str
    release_id: str
    release_digest: str
    data: Mapping[str, Any]
    evidence: tuple[Mapping[str, Any], ...]
    result_digest: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.query-result/v1",
            "status": self.status,
            "tenant_id": self.tenant_id,
            "capability": self.capability.value,
            "request_digest": self.request_digest,
            "plan_digest": self.plan_digest,
            "run_id": self.run_id,
            "run_digest": self.run_digest,
            "release_id": self.release_id,
            "release_digest": self.release_digest,
            "data": canonical_data(self.data),
            "evidence": [canonical_data(item) for item in self.evidence],
            "result_digest": self.result_digest,
        }


class QueryExecutor:
    """Execute only plans that still resolve to the same verified channel snapshot."""

    def __init__(self, resolver: TrustedSnapshotResolver) -> None:
        self.resolver = resolver

    def execute(self, plan: QueryPlan) -> QueryResult:
        if not plan.verify():
            raise TrustedQueryError("query plan digest mismatch")
        request = QueryRequest.create(
            channel=plan.channel,
            capability=plan.capability,
            query=plan.query,
            purpose=plan.purpose,
            parameters=plan.parameters,
        )
        if request.request_digest != plan.request_digest:
            raise TrustedQueryError("query plan request digest mismatch")
        current = self.resolver.plan(request, tenant_id=plan.tenant_id)
        if current.plan_digest != plan.plan_digest:
            raise TrustedQueryError("query plan no longer matches the trusted channel snapshot")
        if plan.capability is QueryCapability.SEMANTIC_SEARCH:
            data = self._semantic_search(plan)
        elif plan.capability is QueryCapability.DATALOG:
            data = self._datalog(plan)
        elif plan.capability is QueryCapability.SPARQL:
            data = self._sparql(plan)
        elif plan.capability is QueryCapability.QUERY_TEMPLATE:
            data = self._query_template(plan)
        else:
            raise TrustedQueryError(
                f"no unified executor is available for capability: {plan.capability.value}"
            )
        evidence = tuple(
            {
                "evidence_id": f"artifact:{artifact.target}:{artifact.content_hash}",
                "type": "compiled_artifact",
                "target": artifact.target,
                "compiler": artifact.compiler,
                "content_hash": artifact.content_hash,
                "release_digest": artifact.release_digest,
            }
            for artifact in plan.artifacts
        )
        payload = {
            "api_version": "aof.query-result/v1",
            "status": "succeeded",
            "tenant_id": plan.tenant_id,
            "capability": plan.capability.value,
            "request_digest": plan.request_digest,
            "plan_digest": plan.plan_digest,
            "run_id": plan.run_id,
            "run_digest": plan.run_digest,
            "release_id": plan.release_id,
            "release_digest": plan.release_digest,
            "data": canonical_data(data),
            "evidence": [canonical_data(item) for item in evidence],
        }
        return QueryResult(
            status="succeeded",
            tenant_id=plan.tenant_id,
            capability=plan.capability,
            request_digest=plan.request_digest,
            plan_digest=plan.plan_digest,
            run_id=plan.run_id,
            run_digest=plan.run_digest,
            release_id=plan.release_id,
            release_digest=plan.release_digest,
            data=_freeze(data),
            evidence=tuple(_freeze(item) for item in evidence),
            result_digest=content_digest(payload),
        )

    def _semantic_search(self, plan: QueryPlan) -> dict[str, Any]:
        payload = self._artifact_payload(plan, "rag")
        raw_limit = plan.parameters.get("limit", 10)
        if isinstance(raw_limit, bool) or not isinstance(raw_limit, int) or raw_limit < 1:
            raise TrustedQueryError("semantic_search limit must be a positive integer")
        needle = plan.query.casefold()
        hits = []
        for resource in payload.get("resources", []):
            if needle in canonical_json(resource).casefold():
                hits.append(
                    {
                        "resource_id": resource["resource_id"],
                        "revision_id": resource["revision_id"],
                        "kind": resource["kind"],
                        "name": resource["name"],
                    }
                )
        hits.sort(key=lambda item: item["resource_id"])
        selected = hits[:raw_limit]
        return {"query": plan.query, "hits": selected, "count": len(selected)}

    def _datalog(self, plan: QueryPlan) -> dict[str, Any]:
        payload = self._artifact_payload(plan, "datalog")
        raw_facts = plan.parameters.get("facts", ())
        if not isinstance(raw_facts, list | tuple):
            raise TrustedQueryError("datalog facts must be a list")
        facts = []
        for item in raw_facts:
            if not isinstance(item, Mapping):
                raise TrustedQueryError("datalog facts must contain semantic objects")
            predicate = item.get("predicate")
            terms = item.get("terms", ())
            if not isinstance(predicate, str) or not predicate.strip():
                raise TrustedQueryError("datalog fact predicate must be a non-empty string")
            if not isinstance(terms, list | tuple):
                raise TrustedQueryError("datalog fact terms must be a list")
            facts.append((predicate.strip(), [str(term) for term in terms]))
        derived = []
        for ruleset in payload.get("rulesets", []):
            result = DatalogEngine(
                ruleset["program"], ruleset_id=ruleset["resource_id"]
            ).run(facts)
            derived.extend(
                item for item in result["derived_facts"] if item["predicate"] == plan.query
            )
        derived.sort(key=lambda item: (item["predicate"], tuple(item["terms"])))
        return {
            "predicate": plan.query,
            "derived_count": len(derived),
            "derived_facts": derived,
        }

    def _sparql(self, plan: QueryPlan) -> dict[str, Any]:
        payload = self._artifact_payload(plan, "owl")
        graph = Graph()
        formats = {"ttl": "turtle", "rdfxml": "xml", "jsonld": "json-ld"}
        for resource in payload.get("resources", []):
            spec = resource.get("spec", {})
            raw_format = str(spec.get("format", "turtle")).lower()
            graph.parse(
                data=spec.get("content", ""),
                format=formats.get(raw_format, raw_format),
            )
        service = SparqlService(
            graph.serialize(format="turtle"),
            snapshot_id=f"release:{plan.release_digest}",
        )
        return service.query(plan.query)

    def _query_template(self, plan: QueryPlan) -> dict[str, Any]:
        semantic = self._artifact_payload(plan, "semantic-json")
        catalog = self._artifact_payload(plan, "mcp")
        template = next(
            (
                item
                for item in semantic.get("resources", [])
                if item.get("kind") == "QueryTemplate"
                and (item.get("resource_id") == plan.query or item.get("name") == plan.query)
            ),
            None,
        )
        if template is None:
            raise TrustedQueryError(f"governed query template not found: {plan.query}")
        tool = next(
            (
                item
                for item in catalog.get("mcp_tools", [])
                if item.get("resource_id") == template["resource_id"]
                and item.get("revision_id") == template["revision_id"]
            ),
            None,
        )
        if tool is None:
            raise TrustedQueryError("query template is absent from the compiled MCP catalog")
        spec = template.get("spec", {})
        legacy = spec.get("legacy_record", {})
        source = spec.get("template") or legacy.get("sql_template")
        if not isinstance(source, str) or not source.strip():
            raise TrustedQueryError("query template has no deterministic template source")
        required = spec.get("required_parameters", legacy.get("required_slots", ()))
        if not isinstance(required, list | tuple):
            raise TrustedQueryError("query template required_parameters must be a list")
        missing = sorted(
            str(name) for name in required if str(name) not in plan.parameters
        )
        if missing:
            raise TrustedQueryError(
                f"query template is missing required parameters: {', '.join(missing)}"
            )
        rendered = source
        for name in sorted(plan.parameters):
            rendered = rendered.replace(f"${{{name}}}", str(plan.parameters[name]))
        unresolved = sorted(set(re.findall(r"\$\{([^}]+)\}", rendered)))
        if unresolved:
            raise TrustedQueryError(
                f"query template has unresolved parameters: {', '.join(unresolved)}"
            )
        return {
            "template_resource_id": template["resource_id"],
            "template_revision": template["revision_id"],
            "mcp_tool": tool["name"],
            "rendered_query": rendered,
            "executed": False,
        }

    def _artifact_payload(self, plan: QueryPlan, target: str) -> dict[str, Any]:
        artifact = next((item for item in plan.artifacts if item.target == target), None)
        if artifact is None:
            raise TrustedQueryError(f"query plan does not contain artifact target: {target}")
        path = (
            self.resolver.repository.root
            / "artifacts"
            / plan.run_id
            / target
            / artifact.uri
        )
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_digest = payload.get("source_release_digest")
        if source_digest is None and isinstance(payload.get("source_release"), Mapping):
            source_digest = payload["source_release"].get("release_digest")
        if source_digest != plan.release_digest:
            raise TrustedQueryError(f"runtime artifact source release mismatch: {target}")
        return payload
