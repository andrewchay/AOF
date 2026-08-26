"""Durable quarantine ingress for consented ContextPacket values."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from bridge.decision_provenance import DecisionProvenanceStore
from bridge.semantic_core.canonical import canonical_json

from .contracts import ContextExchangeError, ContextPacket


@dataclass(frozen=True)
class QuarantinedPacket:
    packet: ContextPacket
    received_at: str
    receipt_decision_id: str


@dataclass(frozen=True)
class ContextPublication:
    packet_id: str
    source_space: str
    target_space: str
    visibility: str
    release_id: str
    release_digest: str
    publish_decision_id: str


class SqliteContextPacketRepository:
    """Tenant-isolated, immutable packets that are never a query source."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS quarantined_context_packets (
                    tenant_id TEXT NOT NULL,
                    packet_id TEXT NOT NULL,
                    packet_digest TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    receipt_decision_id TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, packet_id)
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS context_publications (
                    tenant_id TEXT NOT NULL,
                    packet_id TEXT NOT NULL,
                    visibility TEXT NOT NULL,
                    source_space TEXT NOT NULL,
                    target_space TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    release_digest TEXT NOT NULL,
                    publish_decision_id TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, packet_id, visibility)
                )
                """
            )

    def put(self, value: QuarantinedPacket) -> QuarantinedPacket:
        packet = value.packet
        payload = canonical_json(packet.to_dict())
        with self._connect() as connection:
            current = connection.execute(
                "SELECT packet_digest, payload, received_at, receipt_decision_id "
                "FROM quarantined_context_packets WHERE tenant_id = ? AND packet_id = ?",
                (packet.target_space.tenant_id, packet.packet_id),
            ).fetchone()
            if current is not None:
                restored = self._row_to_value(current)
                if restored.packet.packet_digest != packet.packet_digest:
                    raise ContextExchangeError("quarantined packet cannot be overwritten")
                return restored
            connection.execute(
                "INSERT INTO quarantined_context_packets "
                "(tenant_id, packet_id, packet_digest, payload, received_at, receipt_decision_id) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (packet.target_space.tenant_id, packet.packet_id, packet.packet_digest, payload, value.received_at, value.receipt_decision_id),
            )
        return value

    def get(self, packet_id: str, *, tenant_id: str) -> QuarantinedPacket | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT packet_digest, payload, received_at, receipt_decision_id "
                "FROM quarantined_context_packets WHERE tenant_id = ? AND packet_id = ?",
                (tenant_id, packet_id),
            ).fetchone()
        return self._row_to_value(row) if row is not None else None

    def list_for_review(self, *, tenant_id: str) -> tuple[QuarantinedPacket, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT packet_digest, payload, received_at, receipt_decision_id "
                "FROM quarantined_context_packets WHERE tenant_id = ? ORDER BY received_at, packet_id",
                (tenant_id,),
            ).fetchall()
        return tuple(self._row_to_value(row) for row in rows)

    def put_publication(self, value: ContextPublication, *, tenant_id: str) -> ContextPublication:
        with self._connect() as connection:
            current = connection.execute(
                "SELECT source_space, target_space, release_id, release_digest, publish_decision_id "
                "FROM context_publications WHERE tenant_id = ? AND packet_id = ? AND visibility = ?",
                (tenant_id, value.packet_id, value.visibility),
            ).fetchone()
            if current is not None:
                restored = ContextPublication(value.packet_id, current[0], current[1], value.visibility, current[2], current[3], current[4])
                if restored != value:
                    raise ContextExchangeError("context publication cannot be overwritten")
                return restored
            connection.execute(
                "INSERT INTO context_publications "
                "(tenant_id, packet_id, visibility, source_space, target_space, release_id, release_digest, publish_decision_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (tenant_id, value.packet_id, value.visibility, value.source_space, value.target_space, value.release_id, value.release_digest, value.publish_decision_id),
            )
        return value

    def get_publication(self, packet_id: str, *, tenant_id: str, visibility: str) -> ContextPublication | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT source_space, target_space, release_id, release_digest, publish_decision_id "
                "FROM context_publications WHERE tenant_id = ? AND packet_id = ? AND visibility = ?",
                (tenant_id, packet_id, visibility),
            ).fetchone()
        if row is None:
            return None
        return ContextPublication(packet_id, row[0], row[1], visibility, row[2], row[3], row[4])

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    @staticmethod
    def _row_to_value(row: tuple[str, str, str, str]) -> QuarantinedPacket:
        packet = ContextPacket.from_dict(json.loads(row[1]))
        if packet.packet_digest != row[0]:
            raise ContextExchangeError("stored quarantined packet integrity check failed")
        return QuarantinedPacket(packet=packet, received_at=row[2], receipt_decision_id=row[3])


class ContextGateway:
    """Records an immutable consent receipt before later validation and promotion."""

    def __init__(self, *, repository: SqliteContextPacketRepository, decision_store: DecisionProvenanceStore) -> None:
        self.repository = repository
        self.decision_store = decision_store

    def receive(self, packet: ContextPacket, *, tenant_id: str, actor: str, rationale: str) -> QuarantinedPacket:
        if packet.target_space.tenant_id != tenant_id:
            raise ContextExchangeError("packet tenant does not match receiving tenant")
        current = self.repository.get(packet.packet_id, tenant_id=tenant_id)
        if current is not None:
            if current.packet.packet_digest != packet.packet_digest:
                raise ContextExchangeError("quarantined packet cannot be overwritten")
            return current
        decision = self.decision_store.record(
            agent_id=actor,
            decision_type="context_packet_quarantined",
            conclusion="accepted into shared-draft quarantine",
            rationale=rationale,
            evidence=[
                {
                    "id": evidence.evidence_id,
                    "type": "context_assertion_evidence",
                    "uri": evidence.source_ref,
                    "content_hash": evidence.content_hash,
                    "metadata": {"disclosure": evidence.disclosure, "observed_at": evidence.observed_at},
                }
                for assertion in packet.assertions
                for evidence in assertion.evidence
            ],
            policies=["policy:context-exchange-v1", "policy:quarantine-default-deny"],
            tags=["context-exchange", "quarantine", packet.producer],
            tenant_id=tenant_id,
            output_entities=[{"id": packet.packet_id, "type": "context_packet", "content_hash": packet.packet_digest}],
            metadata={"consent_decision_id": packet.consent_decision_id, "consent_expires_at": packet.consent_expires_at, "target_space": packet.target_space.to_dict()},
        )
        return self.repository.put(
            QuarantinedPacket(
                packet=packet,
                received_at=datetime.now(timezone.utc).isoformat(),
                receipt_decision_id=decision["decision"]["id"],
            )
        )
