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
