"""Production-oriented file, SQLite, API, and event source connectors."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .continuous_ingest import ContinuousIngestionError, KnowledgeSource, SourceBatch


def _confined_path(raw_path: object, allowed_roots: tuple[Path, ...]) -> Path:
    path = Path(str(raw_path or "")).resolve()
    if allowed_roots and not any(path.is_relative_to(root) for root in allowed_roots):
        raise ContinuousIngestionError("source path is outside configured ingestion roots")
    return path


class JsonlFileSourceConnector:
    def __init__(self, *, allowed_roots: Sequence[Path] = ()) -> None:
        self.allowed_roots = tuple(root.resolve() for root in allowed_roots)

    def fetch(self, source: KnowledgeSource, cursor: str | None) -> SourceBatch:
        path = _confined_path(source.config.get("path"), self.allowed_roots)
        if not path.is_file():
            raise ContinuousIngestionError(f"JSONL source file not found: {path}")
        raw = path.read_bytes()
        records = [
            json.loads(line)
            for line in raw.decode("utf-8").splitlines()
            if line.strip()
        ]
        digest = hashlib.sha256(raw).hexdigest()
        return SourceBatch.create(
            cursor_from=cursor,
            cursor_to=f"sha256:{digest}",
            records=records,
            source_snapshot={
                "path": str(path),
                "content_hash": f"sha256:{digest}",
                "size": len(raw),
            },
        )


class SqliteTableSourceConnector:
    _IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

    def __init__(self, *, allowed_roots: Sequence[Path] = ()) -> None:
        self.allowed_roots = tuple(root.resolve() for root in allowed_roots)

    def fetch(self, source: KnowledgeSource, cursor: str | None) -> SourceBatch:
        database = _confined_path(source.config.get("database"), self.allowed_roots)
        table = str(source.config.get("table", ""))
        if not database.is_file() or not self._IDENTIFIER.fullmatch(table):
            raise ContinuousIngestionError("SQLite source database or table is invalid")
        with sqlite3.connect(database) as connection:
            connection.row_factory = sqlite3.Row
            rows = [
                dict(row)
                for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            ]
            schema = [
                dict(row) for row in connection.execute(f'PRAGMA table_info("{table}")')
            ]
        content_hash = hashlib.sha256(
            json.dumps(
                {"records": rows, "schema": schema},
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        snapshot = {
            "database": str(database),
            "table": table,
            "content_hash": f"sha256:{content_hash}",
            "schema": schema,
        }
        return SourceBatch.create(
            cursor_from=cursor,
            cursor_to=f"sha256:{content_hash}",
            records=rows,
            source_snapshot=snapshot,
        )


class HttpJsonSourceConnector:
    def __init__(
        self, transport: Callable[[str, str | None], Mapping[str, Any]]
    ) -> None:
        self.transport = transport

    def fetch(self, source: KnowledgeSource, cursor: str | None) -> SourceBatch:
        endpoint = str(source.config.get("endpoint", ""))
        if not endpoint.startswith(("https://", "http://")):
            raise ContinuousIngestionError("API source endpoint must be HTTP(S)")
        response = self.transport(endpoint, cursor)
        records = response.get("records")
        if not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            raise ContinuousIngestionError("API source response records must be a list")
        return SourceBatch.create(
            cursor_from=cursor,
            cursor_to=str(
                response.get("next_cursor") or response.get("etag") or "complete"
            ),
            records=records,
            source_snapshot={
                "endpoint": endpoint,
                "etag": response.get("etag"),
                "response_digest": response.get("response_digest"),
            },
        )


class EventStreamSourceConnector:
    def __init__(
        self,
        reader: Callable[[str, str | None], tuple[Sequence[Mapping[str, Any]], str]],
    ) -> None:
        self.reader = reader

    def fetch(self, source: KnowledgeSource, cursor: str | None) -> SourceBatch:
        topic = str(source.config.get("topic", ""))
        if not topic:
            raise ContinuousIngestionError("event source topic is required")
        events, next_cursor = self.reader(topic, cursor)
        return SourceBatch.create(
            cursor_from=cursor,
            cursor_to=next_cursor,
            records=events,
            source_snapshot={
                "topic": topic,
                "offset_from": cursor,
                "offset_to": next_cursor,
                "event_count": len(events),
            },
        )
