#!/usr/bin/env python3
"""
将 describe_table 的返回结果批量落盘为 JSON 文件。
用法: echo '<json>' | python3 save_meta.py
或: python3 save_meta.py '<json>'

输入是 describe_table 的 {"tables": [...]} 结构。
"""
import json
import os
import sys

OUTPUT_DIR = os.path.expanduser(
    "~/.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta"
)


def save(data):
    tables = data.get("tables", [])
    saved = []
    for t in tables:
        full_name = t["table_name"]  # e.g. "dim_hk4e.some_table"
        db, table = full_name.split(".", 1)
        db_dir = os.path.join(OUTPUT_DIR, db)
        os.makedirs(db_dir, exist_ok=True)

        cols = []
        parts = []
        for c in t.get("columns", []):
            ci = {
                "name": c["column_name"],
                "type": c["data_type"],
                "comment": c.get("comment", ""),
                "is_partition": c.get("partition", False),
            }
            cols.append(ci)
            if ci["is_partition"]:
                parts.append(ci["name"])

        meta = {
            "database": db,
            "table": table,
            "full_name": full_name,
            "column_count": len(cols),
            "partition_columns": parts,
            "columns": cols,
        }
        fp = os.path.join(db_dir, f"{table}.json")
        with open(fp, "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        saved.append(full_name)
    return saved


if __name__ == "__main__":
    if len(sys.argv) > 1:
        raw = sys.argv[1]
    else:
        raw = sys.stdin.read()
    data = json.loads(raw)
    saved = save(data)
    print(f"saved {len(saved)} tables")
    for s in saved:
        print(" ", s)
