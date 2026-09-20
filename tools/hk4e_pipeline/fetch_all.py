#!/usr/bin/env python3
"""
全自动拉取 hk4e 表元数据并落盘为 JSON。
直连 andrew-mcp HTTP 接口（JSON-RPC over HTTP）。
"""
import json
import os
import sys
import time
import urllib.request
import urllib.error

MCP_URL = "https://data-service-prod-office.mihoyo.com/unifie-data-mcp-server/mcp/message"
MCP_CONF = os.path.expanduser("~/.gravitas/agent-workspaces/aof/mcp.json")
OUTPUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data", "semantic_assets",
)
REGION = "cn"
BATCH = 5          # describe_table 每批最多 5 张表
RETRY = 3
SLEEP = 0.2

with open(MCP_CONF) as f:
    TOKEN = json.load(f)["servers"]["andrew-mcp"]["headers"]["Authorization"]


def rpc(method, params, req_id=1):
    body = json.dumps(
        {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    ).encode()
    req = urllib.request.Request(
        MCP_URL,
        data=body,
        headers={
            "Authorization": TOKEN,
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read().decode()
    # SSE 可能带 data: 前缀
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(f"unexpected response: {raw[:300]}")


def call_tool(name, args):
    r = rpc("tools/call", {"name": name, "arguments": args})
    if "error" in r:
        raise RuntimeError(f"rpc error: {r['error']}")
    content = r["result"]["content"]
    text = content[0]["text"]
    if r["result"].get("isError"):
        raise RuntimeError(f"tool error: {text[:300]}")
    return json.loads(text)


DEFAULT_DATABASES = ["dwd_hk4e", "dws_hk4e", "ads_hk4e", "dim_hk4e", "dwb_hk4e"]


def get_all_tables(dbs):
    """通过 SHOW TABLES 获取指定数据库的完整表名清单。"""
    result = {}
    for db in dbs:
        try:
            r = call_tool("query_data", {"region": REGION, "sql": f"SHOW TABLES IN {db}"})
            rows = r.get("rows", [])
            result[db] = [row[1] for row in rows]
            print(f"  {db}: {len(result[db])} tables", flush=True)
        except Exception as e:
            print(f"  {db}: ERROR {e}", flush=True)
            result[db] = []
    return result


def save(data):
    n = 0
    for t in data.get("tables", []):
        full = t["table_name"]
        db, table = full.split(".", 1)
        d = os.path.join(OUTPUT_DIR, db)
        os.makedirs(d, exist_ok=True)
        cols, parts = [], []
        for c in t.get("columns", []):
            parts.append(c["column_name"]) if c.get("partition") else None
            cols.append({
                "name": c["column_name"],
                "type": c["data_type"],
                "comment": c.get("comment", ""),
                "is_partition": c.get("partition", False),
            })
        meta = {
            "database": db, "table": table, "full_name": full,
            "column_count": len(cols), "partition_columns": parts, "columns": cols,
        }
        with open(os.path.join(d, f"{table}.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        n += 1
    return n


def rebuild_catalog(tables_map):
    """按 table_list 重建聚合 catalog，避免目录中的临时 JSON 混入。"""
    catalog = {"region": REGION, "databases": {}, "tables": []}
    missing = []
    for db, names in tables_map.items():
        catalog["databases"][db] = {"table_count": len(names)}
        for table in names:
            short = table.split(".", 1)[-1]
            path = os.path.join(OUTPUT_DIR, db, f"{short}.json")
            if not os.path.exists(path):
                missing.append(f"{db}.{short}")
                continue
            with open(path, encoding="utf-8") as f:
                catalog["tables"].append(json.load(f))
    catalog["table_count"] = len(catalog["tables"])
    catalog["missing_count"] = len(missing)
    catalog["missing_tables"] = missing
    with open(os.path.join(OUTPUT_DIR, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    return catalog


def main():
    requested_dbs = sys.argv[1:] or DEFAULT_DATABASES
    print("== 1. 获取表清单 ==")
    tl_path = os.path.join(OUTPUT_DIR, "table_list.json")
    tables_map = {}
    if os.path.exists(tl_path):
        with open(tl_path) as f:
            tables_map = json.load(f)
        print("  (读取现有 table_list.json，将增量合并请求数据库)")

    # 命令行显式传入的库始终刷新；默认运行仅补拉缓存缺失库。
    refresh_dbs = requested_dbs if len(sys.argv) > 1 else [db for db in requested_dbs if db not in tables_map]
    if refresh_dbs:
        tables_map.update(get_all_tables(refresh_dbs))
    with open(tl_path, "w", encoding="utf-8") as f:
        json.dump(tables_map, f, ensure_ascii=False, indent=2)

    all_tables = []
    for db, tbls in tables_map.items():
        for t in tbls:
            # 表名可能已含库前缀；统一成 db.table
            if t.startswith(db + "."):
                all_tables.append(t)
            else:
                all_tables.append(f"{db}.{t}")

    print(f"== 2. 共 {len(all_tables)} 张表，开始拉取 ==")
    done, fail = 0, []
    # 跳过已存在的
    todo = []
    for full in all_tables:
        db, table = full.split(".", 1)
        if not os.path.exists(os.path.join(OUTPUT_DIR, db, f"{table}.json")):
            todo.append(full)
    print(f"   待拉取 {len(todo)} 张（已跳过 {len(all_tables)-len(todo)} 张）")

    for i in range(0, len(todo), BATCH):
        batch = todo[i:i + BATCH]
        for attempt in range(RETRY):
            try:
                data = call_tool("describe_table", {"region": REGION, "table_names": batch})
                n = save(data)
                done += n
                print(f"  [{i+n}/{len(todo)}] saved {n}", flush=True)
                break
            except Exception as e:
                if attempt == RETRY - 1:
                    fail.extend(batch)
                    print(f"  FAIL {batch}: {e}", flush=True)
                else:
                    time.sleep(1.5 * (attempt + 1))
        time.sleep(SLEEP)

    print(f"\n== 完成：成功 {done}，失败 {len(fail)} ==")
    if fail:
        with open(os.path.join(OUTPUT_DIR, "failed.json"), "w") as f:
            json.dump(fail, f, ensure_ascii=False, indent=2)
    catalog = rebuild_catalog(tables_map)
    print(
        f"== catalog 已重建：{catalog['table_count']} 张表，"
        f"缺失 {catalog['missing_count']} 张 =="
    )


if __name__ == "__main__":
    main()
