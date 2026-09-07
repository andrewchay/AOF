# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
from __future__ import annotations

import sqlite3

import pytest

from bridge.semantic_core import (
    AgenticRequest,
    AgenticSummaryValidator,
    AgenticSystemError,
    AgenticSystemService,
    CapabilityResult,
    OntologyIntentRouter,
    QueryRequest,
    SqliteAgenticRunRepository,
)
from tests.test_trusted_query_execution import _trusted_query_runtime


def request(**overrides):
    values = {
        "run_id": "agentic-001",
        "tenant_id": "acme",
        "session_id": "finance-review",
        "query": "收入指标由哪些业务实体影响，如何处理异常？",
        "purpose": "weekly-review",
        "channel": "production",
        "release_id": "finance@1",
        "release_digest": "sha256:release",
        "policy_resource_id": "aof://acme/finance/policy/query",
        "allowed_capabilities": [
            "graph_search",
            "skill_search",
            "semantic_sql",
            "rag_retrieve",
        ],
        "max_steps": 4,
    }
    values.update(overrides)
    return AgenticRequest.create(**values)


def executor(capability, context):
    return {
        "output": {"summary": f"{capability} answered {context['purpose']}"},
        "evidence": [{
            "release_id": context["release_id"],
            "release_digest": context["release_digest"],
            "source": f"artifact:{capability}",
        }],
    }


def test_router_selects_multiple_ontology_capabilities_with_reason_codes():
    route = OntologyIntentRouter().route(request())
    assert route.capabilities == ("semantic_sql", "graph_search", "skill_search")
    assert route.fallback is None
    assert len(route.rationale_codes) == 3


def test_router_falls_back_to_rag_but_not_llm_without_explicit_policy():
    routed = OntologyIntentRouter().route(
        request(query="请解释这段资料", allowed_capabilities=["rag_retrieve"])
    )
    assert routed.capabilities == ("rag_retrieve",)
    assert routed.fallback == "rag_retrieve"

    with pytest.raises(AgenticSystemError, match="no approved fallback"):
        OntologyIntentRouter().route(
            request(
                query="请解释这段资料",
                allowed_capabilities=["llm_fallback"],
                allow_rag_fallback=False,
                allow_llm_fallback=False,
            )
        )


def test_agentic_run_persists_plan_evidence_summary_and_tenant_memory(tmp_path):
    repository = SqliteAgenticRunRepository(tmp_path / "agentic.sqlite3")
    service = AgenticSystemService(repository, executor=executor)

    run = service.run(request(), actor="analyst:alice")

    assert run["status"] == "succeeded"
    assert run["plan"]["release_digest"] == "sha256:release"
    assert len(run["results"]) == 3
    assert all(result["evidence"] for result in run["results"])
    assert all(claim["citations"] for claim in run["summary"]["claims"])
    assert service.evaluate("agentic-001", tenant_id="acme")["status"] == "passed"
    assert [item["role"] for item in repository.memory(
        tenant_id="acme", session_id="finance-review"
    )] == ["user", "assistant"]
    assert repository.memory(tenant_id="other", session_id="finance-review") == []


def test_capability_without_evidence_is_blocked(tmp_path):
    service = AgenticSystemService(
        SqliteAgenticRunRepository(tmp_path / "agentic.sqlite3"),
        executor=lambda capability, context: {"output": {"summary": "unsupported"}},
    )
    with pytest.raises(AgenticSystemError, match="returned no evidence"):
        service.run(request(query="多少收入", max_steps=1), actor="analyst:alice")


def test_malformed_evidence_and_unsupported_claim_are_rejected():
    with pytest.raises(AgenticSystemError, match='identify a source'):
        CapabilityResult.create(result_id='bad', capability='graph_search',
            value={'output': {'summary': 'invented'}, 'evidence': [{}]})
    result = CapabilityResult.create(result_id='source', capability='graph_search',
        value={'output': {'summary': 'Supported text'}, 'evidence': [{'id': 'edge'}]})
    with pytest.raises(AgenticSystemError, match='not supported'):
        AgenticSummaryValidator().validate({'claims': [{'text': 'Invented text', 'citations': ['source']}]}, [result])


def test_same_run_id_in_distinct_tenants_is_independent(tmp_path):
    service = AgenticSystemService(SqliteAgenticRunRepository(tmp_path / 'runs.sqlite3'), executor=executor)
    service.run(request(query='收入是多少'), actor='alice')
    service.run(request(query='收入是多少', tenant_id='other'), actor='bob')
    assert service.repository.get('agentic-001', tenant_id='other')['actor'] == 'bob'


def test_router_never_silently_drops_a_disallowed_part_of_question():
    with pytest.raises(AgenticSystemError, match='not allowed'):
        OntologyIntentRouter().route(request(allowed_capabilities=['semantic_sql']))


def test_next_turn_consumes_bounded_tenant_session_memory(tmp_path):
    observed = []

    def memory_aware_executor(capability, context):
        observed.append((len(context["memory"]), context["memory_digest"]))
        return executor(capability, context)

    service = AgenticSystemService(
        SqliteAgenticRunRepository(tmp_path / "agentic.sqlite3"),
        executor=memory_aware_executor,
    )
    first = service.run(
        request(query="收入是多少", max_steps=1), actor="analyst:alice"
    )
    second = service.run(
        request(run_id="agentic-002", query="收入趋势", max_steps=1),
        actor="analyst:alice",
    )

    assert observed[0][0] == 0
    assert observed[1][0] == 2
    assert first["plan"]["memory_count"] == 0
    assert second["plan"]["memory_count"] == 2
    assert second["plan"]["memory_digest"] == observed[1][1]


def test_summary_rejects_unknown_or_missing_citations():
    result = CapabilityResult.create(
        result_id="result-1",
        capability="graph_search",
        value={"output": {"summary": "A relates to B"}, "evidence": [{"id": "edge-1"}]},
    )
    validator = AgenticSummaryValidator()
    with pytest.raises(AgenticSystemError, match="requires citations"):
        validator.validate({"claims": [{"text": "unsupported", "citations": []}]}, [result])
    with pytest.raises(AgenticSystemError, match="unknown capability result"):
        validator.validate({"claims": [{"text": "unsupported", "citations": ["missing"]}]}, [result])


def test_replay_is_deterministic_for_same_release_and_tool_receipts(tmp_path):
    repository = SqliteAgenticRunRepository(tmp_path / "agentic.sqlite3")
    service = AgenticSystemService(repository, executor=executor)
    service.run(request(query="收入是多少", max_steps=1), actor="analyst:alice")

    replay = service.replay(
        "agentic-001", run_id="agentic-002", tenant_id="acme", actor="analyst:bob"
    )

    assert replay["reproducible"] is True


def test_repository_fails_closed_on_tampered_run(tmp_path):
    database = tmp_path / "agentic.sqlite3"
    repository = SqliteAgenticRunRepository(database)
    service = AgenticSystemService(repository, executor=executor)
    service.run(request(query="收入是多少", max_steps=1), actor="analyst:alice")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE agentic_runs SET payload = ? WHERE run_id = ?",
            ('{"run_id":"agentic-001"}', "agentic-001"),
        )
    with pytest.raises(AgenticSystemError, match="digest mismatch"):
        repository.get("agentic-001", tenant_id="acme")


def test_agentic_vector_route_consumes_the_release_pinned_query_runtime(tmp_path):
    resolver, trusted_executor = _trusted_query_runtime(tmp_path)
    snapshot = resolver.plan(
        QueryRequest.create(
            channel="production",
            capability="semantic_search",
            query="customer",
            purpose="customer-support",
        ),
        tenant_id="acme",
    )

    def trusted_capability(capability, context):
        assert capability == "vector_search"
        plan = resolver.plan(
            QueryRequest.create(
                channel=context["channel"],
                capability="semantic_search",
                query=context["query"],
                purpose=context["purpose"],
            ),
            tenant_id=context["tenant_id"],
        )
        result = trusted_executor.execute(plan)
        assert result.release_digest == context["release_digest"]
        return {
            "output": {"summary": f"found {result.data['count']} published concepts"},
            "evidence": list(result.evidence),
        }

    service = AgenticSystemService(
        SqliteAgenticRunRepository(tmp_path / "agentic.sqlite3"),
        executor=trusted_capability,
    )
    run = service.run(
        request(
            query="find similar customer concepts",
            purpose="customer-support",
            release_id=snapshot.release_id,
            release_digest=snapshot.release_digest,
            allowed_capabilities=["vector_search"],
            max_steps=1,
        ),
        actor="analyst:alice",
    )

    assert run["route"]["capabilities"] == ["vector_search"]
    assert run["results"][0]["evidence"][0]["target"] == "rag"
    assert service.evaluate("agentic-001", tenant_id="acme")["status"] == "passed"
