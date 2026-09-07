# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""Persistent domain events and deterministic incremental action-rule subscriptions."""

from __future__ import annotations
from bridge.persistence.sqlite_support import managed_sqlite_connection

import json
import hashlib
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Any

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.ontology_governance import DatalogEngine

from .canonical import canonical_data, canonical_json, content_digest
from .compilers import CompilationRunRepository


class EventRuleError(ValueError):
    """Raised when an event or incremental subscription is unsafe."""


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventRuleError(f"{field} must be a non-empty string")
    return value.strip()


def _strings(value: Any, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise EventRuleError(f"{field} must be a list")
    result = tuple(sorted({_text(item, field) for item in value}))
    if not result:
        raise EventRuleError(f"{field} must be non-empty")
    return result


def _instant(value: Any, field: str) -> str:
    raw = _text(value, field).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise EventRuleError(f"{field} must be an ISO-8601 instant") from exc
    if parsed.tzinfo is None:
        raise EventRuleError(f"{field} must include a timezone")
    return parsed.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class DomainEvent:
    event_id: str
    tenant_id: str
    event_type: str
    occurred_at: str
    facts: tuple[Mapping[str, Any], ...]
    source: Mapping[str, Any]
    event_digest: str

    @classmethod
    def create(
        cls,
        *,
        event_id: str,
        tenant_id: str,
        event_type: str,
        occurred_at: str,
        facts: Sequence[Mapping[str, Any]],
        source: Mapping[str, Any],
    ) -> "DomainEvent":
        normalized_facts = []
        if not isinstance(facts, Sequence) or not facts:
            raise EventRuleError("event facts must be a non-empty list")
        for fact in facts:
            if not isinstance(fact, Mapping):
                raise EventRuleError("event fact must be a semantic object")
            normalized_facts.append(
                {
                    "predicate": _text(fact.get("predicate"), "fact predicate"),
                    "terms": list(_strings(fact.get("terms"), "fact terms")),
                }
            )
        normalized_facts.sort(key=lambda item: (item["predicate"], item["terms"]))
        if not isinstance(source, Mapping) or not source:
            raise EventRuleError("event source must be a non-empty semantic object")
        payload = {
            "api_version": "aof.domain-event/v1",
            "event_id": _text(event_id, "event_id"),
            "tenant_id": _text(tenant_id, "tenant_id"),
            "event_type": _text(event_type, "event_type"),
            "occurred_at": _instant(occurred_at, "occurred_at"),
            "facts": normalized_facts,
            "source": canonical_data(source),
        }
        return cls(
            event_id=payload["event_id"],
            tenant_id=payload["tenant_id"],
            event_type=payload["event_type"],
            occurred_at=payload["occurred_at"],
            facts=tuple(MappingProxyType(item) for item in normalized_facts),
            source=MappingProxyType(payload["source"]),
            event_digest=content_digest(payload),
        )

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "DomainEvent":
        event = cls.create(
            event_id=value.get("event_id"),
            tenant_id=value.get("tenant_id"),
            event_type=value.get("event_type"),
            occurred_at=value.get("occurred_at"),
            facts=value.get("facts", ()),
            source=value.get("source", {}),
        )
        if value.get("event_digest") != event.event_digest:
            raise EventRuleError("domain event digest mismatch")
        return event

    def to_dict(self) -> dict[str, Any]:
        return {
            "api_version": "aof.domain-event/v1",
            "event_id": self.event_id,
            "tenant_id": self.tenant_id,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "facts": canonical_data(self.facts),
            "source": canonical_data(self.source),
            "event_digest": self.event_digest,
        }


@dataclass(frozen=True)
class EventRuleSubscription:
    subscription_id: str
    tenant_id: str
    release_id: str
    release_digest: str
    event_types: tuple[str, ...]
    program: str
    trigger_predicate: str
    action_type_id: str
    purpose: str
    policy_resource_id: str
    ruleset_version: str
    subscription_digest: str

    @classmethod
    def create(
        cls,
        *,
        subscription_id: str,
        tenant_id: str,
        release_id: str,
        release_digest: str,
        event_types: Sequence[str],
        program: str,
        trigger_predicate: str,
        action_type_id: str,
        purpose: str,
        policy_resource_id: str,
    ) -> "EventRuleSubscription":
        ruleset_id = _text(subscription_id, "subscription_id")
        engine = DatalogEngine(_text(program, "program"), ruleset_id=ruleset_id)
        payload = {
            "api_version": "aof.event-rule-subscription/v1",
            "subscription_id": ruleset_id,
            "tenant_id": _text(tenant_id, "tenant_id"),
            "release_id": _text(release_id, "release_id"),
            "release_digest": _text(release_digest, "release_digest"),
            "event_types": list(_strings(event_types, "event_types")),
            "program": engine.program,
            "trigger_predicate": _text(trigger_predicate, "trigger_predicate"),
            "action_type_id": _text(action_type_id, "action_type_id"),
            "purpose": _text(purpose, "purpose"),
            "policy_resource_id": _text(policy_resource_id, "policy_resource_id"),
            "ruleset_version": engine.ruleset_version,
        }
        return cls(
            subscription_id=payload["subscription_id"],
            tenant_id=payload["tenant_id"],
            release_id=payload["release_id"],
            release_digest=payload["release_digest"],
            event_types=tuple(payload["event_types"]),
            program=payload["program"],
            trigger_predicate=payload["trigger_predicate"],
            action_type_id=payload["action_type_id"],
            purpose=payload["purpose"],
            policy_resource_id=payload["policy_resource_id"],
            ruleset_version=payload["ruleset_version"],
            subscription_digest=content_digest(payload),
        )


class PublishedEventSubscriptionResolver:
    """Resolve event rules and action targets from one verified channel Release."""

    def __init__(self, repository: CompilationRunRepository) -> None:
        self.repository = repository

    def resolve(
        self, *, channel: str, tenant_id: str, ruleset_resource_id: str
    ) -> EventRuleSubscription:
        pointer = self.repository.get_channel(_text(channel, "channel"))
        if pointer is None:
            raise EventRuleError(f"event subscription channel not found: {channel}")
        pointer_payload = {
            key: value for key, value in pointer.items() if key != "pointer_digest"
        }
        if pointer.get("pointer_digest") != content_digest(pointer_payload):
            raise EventRuleError("event subscription channel pointer digest mismatch")
        run = self.repository.get(str(pointer["run_id"]))
        if run is None or not run.reproducible:
            raise EventRuleError("event subscription requires a reproducible run")
        if run.tenant_id != _text(tenant_id, "tenant_id"):
            raise EventRuleError("event subscription tenant does not match run tenant")
        artifacts = {str(item["target"]): item for item in run.artifacts}
        missing = sorted({"actions", "datalog"} - set(artifacts))
        if missing:
            raise EventRuleError(
                f"event subscription requires missing artifacts: {', '.join(missing)}"
            )
        rules_payload = self._artifact(run.run_id, artifacts["datalog"], run.release_digest)
        action_payload = self._artifact(run.run_id, artifacts["actions"], run.release_digest)
        ruleset_id = _text(ruleset_resource_id, "ruleset_resource_id")
        ruleset = next(
            (
                item
                for item in rules_payload.get("rulesets", ())
                if item.get("resource_id") == ruleset_id
            ),
            None,
        )
        if ruleset is None:
            raise EventRuleError(f"event ruleset is not published: {ruleset_id}")
        subscription = ruleset.get("event_subscription")
        if not isinstance(subscription, Mapping):
            raise EventRuleError("published ruleset has no event_subscription contract")
        action_id = _text(subscription.get("action_type_id"), "action_type_id")
        if not any(
            item.get("resource_id") == action_id
            for item in action_payload.get("action_types", ())
        ):
            raise EventRuleError(
                "event subscription action type is not in the same release"
            )
        return EventRuleSubscription.create(
            subscription_id=ruleset_id,
            tenant_id=tenant_id,
            release_id=run.release_id,
            release_digest=run.release_digest,
            event_types=subscription.get("event_types", ()),
            program=_text(ruleset.get("program"), "ruleset program"),
            trigger_predicate=subscription.get("trigger_predicate"),
            action_type_id=action_id,
            purpose=subscription.get("purpose"),
            policy_resource_id=subscription.get("policy_resource_id"),
        )

    def _artifact(
        self, run_id: str, artifact: Mapping[str, Any], release_digest: str
    ) -> dict[str, Any]:
        path = (
            Path(self.repository.root)
            / "artifacts"
            / run_id
            / str(artifact["target"])
            / str(artifact["uri"])
        )
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise EventRuleError(f"event artifact is missing: {path.name}") from exc
        if f"sha256:{hashlib.sha256(raw).hexdigest()}" != artifact.get("content_hash"):
            raise EventRuleError("event artifact content hash mismatch")
        payload = json.loads(raw)
        source_digest = payload.get("source_release_digest")
        if source_digest is None and isinstance(payload.get("source_release"), Mapping):
            source_digest = payload["source_release"].get("release_digest")
        if source_digest != release_digest:
            raise EventRuleError("event artifact release digest mismatch")
        return payload


class IncrementalActionRuleRuntime:
    """Persist asserted facts and emit only newly derived action triggers."""

    def __init__(
        self, path: str | Path, *, decision_store: DecisionProvenanceStore
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.decision_store = decision_store
        with self._connection() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS domain_events (
                    event_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    event_digest TEXT NOT NULL, payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subscription_state (
                    subscription_id TEXT PRIMARY KEY, subscription_digest TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS subscription_facts (
                    subscription_id TEXT NOT NULL, predicate TEXT NOT NULL,
                    terms_json TEXT NOT NULL,
                    PRIMARY KEY (subscription_id, predicate, terms_json)
                );
                CREATE TABLE IF NOT EXISTS subscription_derived (
                    subscription_id TEXT NOT NULL, predicate TEXT NOT NULL,
                    terms_json TEXT NOT NULL,
                    PRIMARY KEY (subscription_id, predicate, terms_json)
                );
                CREATE TABLE IF NOT EXISTS action_triggers (
                    trigger_id TEXT PRIMARY KEY, trigger_digest TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS processed_events (
                    subscription_id TEXT NOT NULL, event_id TEXT NOT NULL,
                    result_digest TEXT NOT NULL, payload TEXT NOT NULL,
                    PRIMARY KEY (subscription_id, event_id)
                );
                PRAGMA user_version=1;
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=30)
    def _connection(self):
        """Transaction + close context manager (W09.01: no leaked connections)."""
        return managed_sqlite_connection(self._connect)


    def process(
        self, subscription: EventRuleSubscription, event: DomainEvent
    ) -> dict[str, Any]:
        if event.tenant_id != subscription.tenant_id:
            raise EventRuleError("event tenant does not match subscription tenant")
        if event.event_type not in subscription.event_types:
            raise EventRuleError("event type is not accepted by subscription")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing_event = connection.execute(
                "SELECT event_digest FROM domain_events WHERE event_id = ?",
                (event.event_id,),
            ).fetchone()
            if existing_event is not None and existing_event[0] != event.event_digest:
                raise EventRuleError("event_id cannot be overwritten")
            if existing_event is None:
                connection.execute(
                    "INSERT INTO domain_events VALUES (?, ?, ?, ?)",
                    (
                        event.event_id,
                        event.tenant_id,
                        event.event_digest,
                        canonical_json(event.to_dict()),
                    ),
                )
            processed = connection.execute(
                "SELECT payload FROM processed_events "
                "WHERE subscription_id = ? AND event_id = ?",
                (subscription.subscription_id, event.event_id),
            ).fetchone()
            if processed is not None:
                connection.commit()
                return json.loads(processed[0])
            state = connection.execute(
                "SELECT subscription_digest FROM subscription_state "
                "WHERE subscription_id = ?",
                (subscription.subscription_id,),
            ).fetchone()
            if state is not None and state[0] != subscription.subscription_digest:
                raise EventRuleError("subscription_id cannot change its release or rules")
            if state is None:
                connection.execute(
                    "INSERT INTO subscription_state VALUES (?, ?)",
                    (subscription.subscription_id, subscription.subscription_digest),
                )
            for fact in event.facts:
                connection.execute(
                    "INSERT OR IGNORE INTO subscription_facts VALUES (?, ?, ?)",
                    (
                        subscription.subscription_id,
                        fact["predicate"],
                        canonical_json(fact["terms"]),
                    ),
                )
            asserted_rows = connection.execute(
                "SELECT predicate, terms_json FROM subscription_facts "
                "WHERE subscription_id = ? ORDER BY predicate, terms_json",
                (subscription.subscription_id,),
            ).fetchall()
            inference = DatalogEngine(
                subscription.program, ruleset_id=subscription.subscription_id
            ).run((predicate, json.loads(terms)) for predicate, terms in asserted_rows)
            new_derived = []
            for fact in inference["derived_facts"]:
                terms_json = canonical_json(fact["terms"])
                inserted = connection.execute(
                    "INSERT OR IGNORE INTO subscription_derived VALUES (?, ?, ?)",
                    (subscription.subscription_id, fact["predicate"], terms_json),
                ).rowcount
                if inserted and fact["predicate"] == subscription.trigger_predicate:
                    new_derived.append(fact)
            trigger_bases = []
            for fact in new_derived:
                base = {
                    "api_version": "aof.action-trigger/v1",
                    "tenant_id": event.tenant_id,
                    "subscription_id": subscription.subscription_id,
                    "subscription_digest": subscription.subscription_digest,
                    "ruleset_version": subscription.ruleset_version,
                    "release_id": subscription.release_id,
                    "release_digest": subscription.release_digest,
                    "event_id": event.event_id,
                    "event_digest": event.event_digest,
                    "derived_fact": canonical_data(fact),
                    "action_type_id": subscription.action_type_id,
                    "object_ids": list(fact["terms"]),
                    "purpose": subscription.purpose,
                    "policy_resource_id": subscription.policy_resource_id,
                }
                trigger_bases.append(
                    {**base, "trigger_id": "trigger-" + content_digest(base).split(":", 1)[1]}
                )
            decision_id = self.decision_store.record(
                agent_id="engine:incremental-datalog",
                decision_type="event_rule_evaluated",
                conclusion=f"emitted {len(trigger_bases)} action trigger(s)",
                rationale="A release-pinned Datalog subscription reached a deterministic fixed point.",
                evidence=[
                    {
                        "id": event.event_id,
                        "type": "domain_event",
                        "content_hash": event.event_digest,
                    },
                    {
                        "id": subscription.subscription_id,
                        "type": "event_rule_subscription",
                        "content_hash": subscription.subscription_digest,
                    },
                ],
                output_entities=[
                    {"id": item["trigger_id"], "type": "action_trigger"}
                    for item in trigger_bases
                ],
                policies=[subscription.policy_resource_id],
                tenant_id=event.tenant_id,
                metadata={
                    "release_id": subscription.release_id,
                    "release_digest": subscription.release_digest,
                    "inference_result_hash": inference["result_hash"],
                },
            )["decision"]["id"]
            triggers = []
            for base in trigger_bases:
                payload = {**base, "decision_id": decision_id}
                trigger = {**payload, "trigger_digest": content_digest(payload)}
                connection.execute(
                    "INSERT INTO action_triggers VALUES (?, ?, ?)",
                    (
                        trigger["trigger_id"],
                        trigger["trigger_digest"],
                        canonical_json(trigger),
                    ),
                )
                triggers.append(trigger)
            result_payload = {
                "api_version": "aof.event-rule-result/v1",
                "subscription_id": subscription.subscription_id,
                "subscription_digest": subscription.subscription_digest,
                "event_id": event.event_id,
                "event_digest": event.event_digest,
                "inference_result_hash": inference["result_hash"],
                "decision_id": decision_id,
                "triggers": triggers,
            }
            result = {
                **result_payload,
                "result_digest": content_digest(result_payload),
            }
            connection.execute(
                "INSERT INTO processed_events VALUES (?, ?, ?, ?)",
                (
                    subscription.subscription_id,
                    event.event_id,
                    result["result_digest"],
                    canonical_json(result),
                ),
            )
            connection.commit()
            return result
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def verify_all(self) -> dict[str, Any]:
        errors = []
        with self._connection() as connection:
            events = connection.execute(
                "SELECT event_id, event_digest, payload FROM domain_events"
            ).fetchall()
            processed = connection.execute(
                "SELECT subscription_id, event_id, result_digest, payload FROM processed_events"
            ).fetchall()
            triggers = connection.execute(
                "SELECT trigger_id, trigger_digest, payload FROM action_triggers"
            ).fetchall()
            integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            errors.append(f"sqlite integrity check failed: {integrity}")
        for event_id, digest, payload in events:
            try:
                event = DomainEvent.from_dict(json.loads(payload))
                if event.event_id != event_id or event.event_digest != digest:
                    raise EventRuleError("indexed event identity mismatch")
            except Exception as exc:
                errors.append(f"event/{event_id}: {exc}")
        for subscription_id, event_id, digest, payload in processed:
            try:
                value = json.loads(payload)
                raw = {key: item for key, item in value.items() if key != "result_digest"}
                if value.get("result_digest") != digest or digest != content_digest(raw):
                    raise EventRuleError("processed result digest mismatch")
            except Exception as exc:
                errors.append(f"processed/{subscription_id}/{event_id}: {exc}")
        for trigger_id, digest, payload in triggers:
            try:
                value = json.loads(payload)
                raw = {key: item for key, item in value.items() if key != "trigger_digest"}
                if (
                    value.get("trigger_id") != trigger_id
                    or value.get("trigger_digest") != digest
                    or digest != content_digest(raw)
                ):
                    raise EventRuleError("action trigger digest mismatch")
            except Exception as exc:
                errors.append(f"trigger/{trigger_id}: {exc}")
        return {
            "valid": not errors,
            "event_count": len(events),
            "processed_count": len(processed),
            "trigger_count": len(triggers),
            "errors": errors,
        }
