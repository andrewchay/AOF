"""W05.02/W02.03 — Real-infrastructure adapters verified against the
running docker compose stack (RabbitMQ 4, OpenBao 2.2).

These tests SKIP cleanly when the stack is down; CI with the stack
always runs them.
"""

from __future__ import annotations

import socket
import time

import pytest

from bridge.audit.outbox_dispatch import Inbox, OutboxDispatcher, TransportDownError


def _rabbit_up() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 5673), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _openbao_up() -> bool:
    try:
        s = socket.create_connection(("127.0.0.1", 8200), timeout=1)
        s.close()
        return True
    except OSError:
        return False


def _event(n: int) -> dict:
    return {"event_id": f"rmq-evt-{n:04d}", "topic": "audit", "seq": n}


# ---------------------------------------------------------------------------
# W05.02 — RabbitMQ transport (real broker)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _rabbit_up(), reason="RabbitMQ not running")
def test_rabbitmq_transport_delivers_in_order(tmp_path):
    from bridge.audit.rabbitmq_transport import RabbitMqConsumer, RabbitMqTransport

    queue = f"aof.test.{int(time.time())}"  # unique queue per test run
    transport = RabbitMqTransport(url="amqp://aof:aof-dev-only@127.0.0.1:5673/%2F", queue=queue)
    events = [_event(i) for i in range(5)]
    transport.deliver(events)
    transport.close()

    with RabbitMqConsumer(url="amqp://aof:aof-dev-only@127.0.0.1:5673/%2F", queue=queue) as consumer:
        batch = consumer.poll(limit=10)
        assert len(batch) == 5
        # append order preserved across delivery
        assert [e["seq"] for _, e in batch] == list(range(5))


@pytest.mark.skipif(not _rabbit_up(), reason="RabbitMQ not running")
def test_rabbitmq_down_raises_transport_down(tmp_path, monkeypatch):
    from bridge.audit.rabbitmq_transport import RabbitMqTransport

    transport = RabbitMqTransport(
        url="amqp://aof:aof-dev-only@127.0.0.1:5999/%2F",  # nothing listens
        auto_declare=False,
    )
    with pytest.raises(TransportDownError):
        transport.deliver([_event(1)])


@pytest.mark.skipif(not _rabbit_up(), reason="RabbitMQ not running")
def test_dispatcher_plus_rabbitmq_plus_inbox(tmp_path):
    """Full W05.02 chain over the real broker: outbox -> RabbitMQ -> inbox."""
    import json

    from bridge.audit.logger import FileOutbox
    from bridge.audit.rabbitmq_transport import RabbitMqConsumer, RabbitMqTransport

    queue = f"aof.test.chain.{int(time.time())}"
    outbox_dir = tmp_path / "outbox"
    outbox = FileOutbox(str(outbox_dir))
    events = [_event(i) for i in range(4)]
    lines = "".join(json.dumps(e) + "\n" for e in events)
    (outbox.outbox_dir / "audit_outbox_20260907_rmq.jsonl").write_text(lines, encoding="utf-8")

    transport = RabbitMqTransport(
        url="amqp://aof:aof-dev-only@127.0.0.1:5673/%2F", queue=queue
    )
    dispatcher = OutboxDispatcher(outbox, transport=transport, inbox=Inbox(tmp_path / "inbox.sqlite"))
    report = dispatcher.dispatch_pending()
    assert report.events_dispatched == 4
    assert report.files_marked == 1

    # consume from the real broker with inbox dedup (same-channel ack)
    inbox = Inbox(tmp_path / "consumer-inbox.sqlite")
    processed = duplicates = 0

    with RabbitMqConsumer(url="amqp://aof:aof-dev-only@127.0.0.1:5673/%2F", queue=queue) as consumer:
        batch = consumer.poll(limit=10)
        for tag, event in batch:
            if inbox.accept(event["event_id"]):
                processed += 1
            else:
                duplicates += 1
        consumer.ack([tag for tag, _ in batch])
    assert processed == 4 and duplicates == 0

    # redeliver the same events (at-least-once): all deduped
    transport.deliver(events)
    with RabbitMqConsumer(url="amqp://aof:aof-dev-only@127.0.0.1:5673/%2F", queue=queue) as consumer2:
        batch2 = consumer2.poll(limit=10)
        for tag, event in batch2:
            if inbox.accept(event["event_id"]):
                processed += 1
            else:
                duplicates += 1
        consumer2.ack([tag for tag, _ in batch2])
    assert duplicates == 4
    assert processed == 4, "consumer effect stays exactly-once"


# ---------------------------------------------------------------------------
# W02.03 — OpenBao transit anchor (real OpenBao)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _openbao_up(), reason="OpenBao not running")
def test_openbao_transit_sign_and_verify():
    from bridge.semantic_core.openbao_signer import OpenBaoTransitClient

    client = OpenBaoTransitClient()
    payload = b"checkpoint|tenant-a|5|sha256:head5"
    signature = client.sign("aof-anchor", payload)
    assert signature.startswith("vault:v")
    assert client.verify("aof-anchor", payload, signature) is True
    # tampered payload fails verification
    assert client.verify("aof-anchor", b"checkpoint|tenant-a|5|sha256:TAMPERED", signature) is False


@pytest.mark.skipif(not _openbao_up(), reason="OpenBao not running")
def test_openbao_down_raises_signer_error(monkeypatch):
    from bridge.semantic_core.openbao_signer import OpenBaoSignerError, OpenBaoTransitClient

    client = OpenBaoTransitClient(addr="http://127.0.0.1:5999")
    with pytest.raises(OpenBaoSignerError, match="unreachable"):
        client.sign("aof-anchor", b"payload")


@pytest.mark.skipif(not _openbao_up(), reason="OpenBao not running")
def test_ledger_anchor_detects_chain_rewrite():
    """W02.03 核心威胁模型：DB 管理员重算链后，锚签名校验失败。"""
    from bridge.semantic_core.openbao_signer import LedgerAnchor, OpenBaoTransitClient

    anchor = LedgerAnchor(OpenBaoTransitClient())

    # 1. anchor the checkpoint of the honest chain
    anchored = anchor.anchor_checkpoint(
        tenant_id="tenant-a", sequence=5, head_hash="sha256:head5"
    )
    assert anchored["anchor_signature"].startswith("vault:v")

    # 2. honest chain: anchor verification passes
    assert anchor.verify_anchor(
        tenant_id="tenant-a", sequence=5, head_hash="sha256:head5",
        anchor_signature=anchored["anchor_signature"],
    ) is True

    # 3. admin rewrites the chain (same sequence, different head) —
    #    internal hashes may be self-consistent, but the ANCHOR check fails
    assert anchor.verify_anchor(
        tenant_id="tenant-a", sequence=5, head_hash="sha256:FORGED-HEAD",
        anchor_signature=anchored["anchor_signature"],
    ) is False
