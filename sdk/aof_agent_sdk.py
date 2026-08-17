#!/usr/bin/env python3
"""Minimal AOF Agent SDK for unified orchestration protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class AgentQuery:
    topic: str
    query: str
    intent: str = ''
    target: str = 'sql'
    top_k: int = 10
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class OrchestrationResult:
    retrieve: dict[str, Any]
    constrain: dict[str, Any]
    generate: dict[str, Any]
    validate: dict[str, Any]


class AOFClient:
    """
    Unified AOF protocol client:
    retrieve -> constrain -> generate -> validate.
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self._client = httpx.Client(base_url=self.base_url, timeout=timeout)

    def close(self) -> None:
        self._client.close()

    def retrieve(self, q: AgentQuery) -> dict[str, Any]:
        resp = self._client.post(
            '/v1/semantic/retrieve',
            json={'topic': q.topic, 'query': q.query, 'top_k': q.top_k, 'use_semantic': True},
        )
        resp.raise_for_status()
        return resp.json()

    def constrain(self, q: AgentQuery, retrieve: dict[str, Any]) -> dict[str, Any]:
        # Keep this structure stable for different downstream agent implementations.
        return {
            'topic': q.topic,
            'query': q.query,
            'intent': q.intent or q.query,
            'target': q.target,
            'retrieved_hits': retrieve.get('hits', []),
            'context': q.context,
        }

    def generate(self, constrained: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post(
            '/v1/semantic/compile',
            json={
                'topic': constrained['topic'],
                'intent': constrained['intent'],
                'target': constrained.get('target', 'sql'),
                'context': constrained.get('context', {}),
            },
        )
        resp.raise_for_status()
        return resp.json()

    def validate(self, topic: str, candidate_sql: str) -> dict[str, Any]:
        resp = self._client.post(
            '/v1/semantic/evaluate',
            json={'topic': topic, 'candidate': candidate_sql, 'criteria': ['syntax', 'performance', 'correctness']},
        )
        resp.raise_for_status()
        return resp.json()

    def record_decision(self, **decision: Any) -> dict[str, Any]:
        """Persist a PROV-O-inspired Agent Decision and its causal evidence."""
        resp = self._client.post('/v1/decisions', json=decision)
        resp.raise_for_status()
        return resp.json()

    def decision_audit_trail(self, decision_id: str) -> dict[str, Any]:
        """Retrieve the integrity-checked causal/compliance trace for a decision."""
        resp = self._client.get(f'/v1/decisions/{decision_id}/audit-trail')
        resp.raise_for_status()
        return resp.json()

    def find_decision_precedents(self, decision_type: str, tags: list[str] | None = None, **filters: Any) -> dict[str, Any]:
        resp = self._client.post('/v1/decisions/precedents/search', json={
            'decision_type': decision_type, 'tags': tags or [], **filters,
        })
        resp.raise_for_status()
        return resp.json()

    def create_ontology_draft(self, **draft: Any) -> dict[str, Any]:
        resp = self._client.post('/v1/ontology/drafts', json=draft)
        resp.raise_for_status()
        return resp.json()

    def validate_ontology_draft(self, draft_id: str, actor: str) -> dict[str, Any]:
        resp = self._client.post(f'/v1/ontology/drafts/{draft_id}/validate', json={'actor': actor})
        resp.raise_for_status()
        return resp.json()

    def waive_ontology_finding(self, draft_id: str, **waiver: Any) -> dict[str, Any]:
        resp = self._client.post(f'/v1/ontology/drafts/{draft_id}/waivers', json=waiver)
        resp.raise_for_status()
        return resp.json()

    def approve_ontology_draft(self, draft_id: str, **approval: Any) -> dict[str, Any]:
        resp = self._client.post(f'/v1/ontology/drafts/{draft_id}/approve', json=approval)
        resp.raise_for_status()
        return resp.json()

    def request_ontology_changes(self, draft_id: str, reviewer: str, rationale: str) -> dict[str, Any]:
        resp = self._client.post(f'/v1/ontology/drafts/{draft_id}/request-changes', json={'reviewer': reviewer, 'rationale': rationale})
        resp.raise_for_status()
        return resp.json()

    def publish_ontology_draft(self, draft_id: str, actor: str) -> dict[str, Any]:
        resp = self._client.post(f'/v1/ontology/drafts/{draft_id}/publish', json={'actor': actor})
        resp.raise_for_status()
        return resp.json()

    def run_datalog(self, program: str, facts: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        resp = self._client.post('/v1/reasoning/datalog/run', json={'program': program, 'facts': facts, **options})
        resp.raise_for_status()
        return resp.json()

    def publish_datalog_ruleset(self, ruleset_id: str, program: str, actor: str, description: str = '') -> dict[str, Any]:
        resp = self._client.post('/v1/reasoning/rulesets', json={'ruleset_id': ruleset_id, 'program': program, 'actor': actor, 'description': description})
        resp.raise_for_status()
        return resp.json()

    def run_datalog_ruleset(self, ruleset_id: str, version: str, facts: list[dict[str, Any]], **options: Any) -> dict[str, Any]:
        resp = self._client.post(f'/v1/reasoning/rulesets/{ruleset_id}/{version}/run', json={'facts': facts, **options})
        resp.raise_for_status()
        return resp.json()

    def query_ontology_sparql(self, ontology_id: str, version: str, query: str) -> dict[str, Any]:
        resp = self._client.post(f'/v1/ontology/releases/{ontology_id}/{version}/sparql', json={'query': query})
        resp.raise_for_status()
        return resp.json()

    def orchestrate(self, q: AgentQuery) -> OrchestrationResult:
        retrieved = self.retrieve(q)
        constrained = self.constrain(q, retrieved)
        generated = self.generate(constrained)
        sql = generated.get('generated_sql', '')
        validated = self.validate(q.topic, sql)
        return OrchestrationResult(
            retrieve=retrieved,
            constrain=constrained,
            generate=generated,
            validate=validated,
        )
