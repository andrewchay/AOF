"""W05.02 — RabbitMQ transport for the outbox dispatcher.

Implements the EventTransport protocol (outbox_dispatch) against a real
RabbitMQ broker: events are published to a durable queue with persistent
delivery mode, preserving the dispatcher's append order. Connection
failures surface as TransportDownError so the dispatcher's bounded
retry/dead-letter logic engages (never silent loss).

Consumer side: RabbitMqConsumer.poll() yields delivered batches from the
queue for the Inbox dedup path.
"""

from __future__ import annotations

import json
import os
from typing import Any

from bridge.audit.outbox_dispatch import TransportDownError


class RabbitMqTransport:
    """Publish outbox events to a durable RabbitMQ queue."""

    def __init__(
        self,
        *,
        url: str | None = None,
        queue: str = "aof.audit.events",
        auto_declare: bool = True,
    ) -> None:
        import pika  # type: ignore[import-untyped]

        self.url = url or os.environ.get(
            "AOF_RABBITMQ_URL", "amqp://aof:aof-dev-only@127.0.0.1:5673/%2F"
        )
        self.queue = queue
        self._pika = pika
        self._connection: Any = None
        self._channel: Any = None
        if auto_declare:
            self._ensure_queue()

    def _ensure_queue(self) -> None:
        try:
            channel = self._channel_or_connect()
            channel.queue_declare(queue=self.queue, durable=True)
        except Exception as exc:
            raise TransportDownError(f"rabbitmq queue declare failed: {exc}") from exc

    def _channel_or_connect(self):
        if self._connection is None or self._connection.is_closed:
            try:
                self._connection = self._pika.BlockingConnection(
                    self._pika.URLParameters(self.url)
                )
                self._channel = self._connection.channel()
            except Exception as exc:
                raise TransportDownError(f"rabbitmq connection failed: {exc}") from exc
        return self._channel

    def deliver(self, events: list[dict[str, Any]]) -> None:
        channel = self._channel_or_connect()
        try:
            for event in events:
                channel.basic_publish(
                    exchange="",
                    routing_key=self.queue,
                    body=json.dumps(event, ensure_ascii=False).encode("utf-8"),
                    properties=self._pika.BasicProperties(
                        delivery_mode=2,  # persistent
                        content_type="application/json",
                    ),
                )
        except Exception as exc:
            # publish-side failures (channel closed, broker gone) are
            # transport-down: the dispatcher retries with a fresh connection
            self._connection = None
            self._channel = None
            raise TransportDownError(f"rabbitmq publish failed: {exc}") from exc

    def close(self) -> None:
        if self._connection and self._connection.is_open:
            self._connection.close()
        self._connection = None
        self._channel = None


class RabbitMqConsumer:
    """Basic-get batch consumer. Ack must happen on the SAME channel that
    received the delivery (RabbitMQ delivery tags are channel-scoped), so
    poll/ack share one explicit connection opened by ``__enter__``."""

    def __init__(self, *, url: str | None = None, queue: str = "aof.audit.events") -> None:
        import pika  # type: ignore[import-untyped]

        self.url = url or os.environ.get(
            "AOF_RABBITMQ_URL", "amqp://aof:aof-dev-only@127.0.0.1:5673/%2F"
        )
        self.queue = queue
        self._pika = pika
        self._connection: Any = None
        self._channel: Any = None

    def __enter__(self) -> "RabbitMqConsumer":
        self._connection = self._pika.BlockingConnection(self._pika.URLParameters(self.url))
        self._channel = self._connection.channel()
        self._channel.queue_declare(queue=self.queue, durable=True)
        return self

    def __exit__(self, *exc) -> None:
        if self._connection and self._connection.is_open:
            self._connection.close()
        self._connection = self._channel = None

    def poll(self, limit: int = 100) -> list[tuple[int, dict[str, Any]]]:
        """Fetch up to ``limit`` messages WITHOUT acking; returns
        (delivery_tag, event) pairs — ack via ``ack`` on this consumer."""
        batch: list[tuple[int, dict[str, Any]]] = []
        while len(batch) < limit:
            method, _properties, body = self._channel.basic_get(
                queue=self.queue, auto_ack=False
            )
            if method is None:
                break
            batch.append((method.delivery_tag, json.loads(body.decode("utf-8"))))
        return batch

    def ack(self, delivery_tags: list[int]) -> None:
        for tag in delivery_tags:
            self._channel.basic_ack(delivery_tag=tag)
