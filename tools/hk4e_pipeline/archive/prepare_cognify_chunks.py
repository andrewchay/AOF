#!/usr/bin/env python3
"""将 hk4e JSONL 元数据分割为适合 Cognee 图谱抽取的小型 Markdown 文档。"""
from __future__ import annotations

import json
import re
from pathlib import Path

BASE = Path.home() / ".gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta/aof"
SOURCE = BASE / "normalized.jsonl"
OUT = BASE / "cognify_schema_chunks_stage35"
MAX_COLUMNS = 32


def safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for old in OUT.glob("*.md"):
        old.unlink()

    total_tables = total_chunks = 0
    for line in SOURCE.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        table = item["table"]
        database = item["database"]
        columns = item.get("columns", [])
        partitions = set(item.get("partition_columns", []))
        total_tables += 1

        for start in range(0, len(columns), MAX_COLUMNS):
            part = columns[start : start + MAX_COLUMNS]
            part_no = start // MAX_COLUMNS + 1
            lines = [
                f"# Hive schema: {table}",
                "",
                f"- Database: `{database}`",
                f"- Table: `{table}`",
                f"- Column group: {part_no} (columns {start + 1}-{start + len(part)} of {len(columns)})",
                f"- Partition columns: {', '.join(item.get('partition_columns', [])) or 'none'}",
                "",
                "## Columns",
            ]
            for col in part:
                name = col["name"]
                typ = col.get("type", "string")
                comment = col.get("comment") or ""
                suffix = "; partition key" if name in partitions or col.get("is_partition") else ""
                lines.append(f"- `{name}`: `{typ}`{suffix}" + (f" — {comment}" if comment else ""))
            out = OUT / f"{safe_name(table)}__cols_{part_no:03d}.md"
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
            total_chunks += 1

    # Cognee 的 add 对海量独立文件会逐个启动子任务，吞吐很差。把上面的有界 schema
    # 段落按明确分隔符聚合为一个 Markdown，由 cognify 的 chunk_size 再安全切块。
    aggregate = BASE / "cognify_schema_stage35.md"
    parts = []
    for doc in sorted(OUT.glob("*.md")):
        parts.append(doc.read_text(encoding="utf-8").rstrip())
    aggregate.write_text("\n\n---\n\n".join(parts) + "\n", encoding="utf-8")

    manifest = {
        "source": str(SOURCE),
        "max_columns_per_document": MAX_COLUMNS,
        "tables": total_tables,
        "schema_sections": total_chunks,
        "aggregate_file": str(aggregate),
    }
    (OUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, ensure_ascii=False))


if __name__ == "__main__":
    main()
