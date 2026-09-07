# Copyright (C) 2026 Andrewchay
# Use of this software is governed by the Business Source License
# included in the LICENSE file of this repository.
#
# As of the Change Date specified in that file, in accordance with
# the Business Source License, use of this software will be governed
# by the Apache License, Version 2.0.
"""File, database, API, and event fixtures produce replayable source batches."""

import json
import sqlite3

import pytest

from bridge.semantic_core import KnowledgeSource
from bridge.semantic_core.source_connectors import (
    EventStreamSourceConnector,
    HttpJsonSourceConnector,
    JsonlFileSourceConnector,
    SqliteTableSourceConnector,
)


def _source(source_type, config):
    return KnowledgeSource.create(
        source_id=f"source-{source_type}",
        tenant_id="acme",
        source_type=source_type,
        owner="data-platform",
        config=config,
    )


def test_enterprise_connectors_bind_records_to_source_snapshots(tmp_path) -> None:
    jsonl = tmp_path / "customers.jsonl"
    jsonl.write_text(
        "\n".join(
            json.dumps(item)
            for item in [{"id": "c1", "name": "Alice"}, {"id": "c2", "name": "Bob"}]
        )
        + "\n",
        encoding="utf-8",
    )
    database = tmp_path / "orders.sqlite3"
    with sqlite3.connect(database) as db:
        db.execute("CREATE TABLE orders (id TEXT PRIMARY KEY, amount INTEGER)")
        db.execute("INSERT INTO orders VALUES ('o1', 42)")

    file_batch = JsonlFileSourceConnector().fetch(
        _source("jsonl", {"path": str(jsonl)}), None
    )
    sql_batch = SqliteTableSourceConnector().fetch(
        _source("sqlite", {"database": str(database), "table": "orders"}), None
    )
    api_batch = HttpJsonSourceConnector(
        lambda endpoint, cursor: {
            "records": [{"id": "a1"}],
            "next_cursor": "page:2",
            "etag": "v1",
            "response_digest": "sha256:api",
        }
    ).fetch(_source("api", {"endpoint": "https://api.example.test/assets"}), None)
    event_batch = EventStreamSourceConnector(
        lambda topic, cursor: ([{"id": "e1", "type": "asset.changed"}], "offset:1")
    ).fetch(_source("events", {"topic": "assets"}), None)

    assert file_batch.cursor_to.startswith("sha256:") and len(file_batch.records) == 2
    assert sql_batch.records[0] == {"id": "o1", "amount": 42}
    assert sql_batch.source_snapshot["schema"][0]["name"] == "id"
    assert api_batch.cursor_to == "page:2" and api_batch.source_snapshot["etag"] == "v1"
    assert (
        event_batch.cursor_to == "offset:1"
        and event_batch.source_snapshot["event_count"] == 1
    )


def test_file_connectors_enforce_deployment_path_boundaries(tmp_path) -> None:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "private.jsonl"
    outside.write_text('{"id":"secret"}\n', encoding="utf-8")

    with pytest.raises(Exception, match="outside configured ingestion roots"):
        JsonlFileSourceConnector(allowed_roots=[allowed]).fetch(
            _source("jsonl", {"path": str(outside)}), None
        )
