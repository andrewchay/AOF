#!/usr/bin/env python3
"""键与外键线索分析 → keys_analysis.md"""
import json, glob, os
from collections import Counter, defaultdict

BASE = os.path.expanduser("~/.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta")
files = glob.glob(os.path.join(BASE, "*", "[!_]*.json"))

id_cols = Counter(); id_types = defaultdict(Counter)
core = Counter(); has_core = Counter()
part = Counter(); total = 0
CORE = ["time","action_id","action_name","sub_action_id","sub_action_name","region_name",
        "game_version","uid","level","vip_point","vip_level","account_type","tag",
        "trans_no","coin_1","coin_2","coin_3","ext_head","c_body","ext_body","uuid"]

for fp in files:
    m = json.load(open(fp))
    if "columns" not in m:
        continue
    total += 1
    names = {c["name"] for c in m["columns"]}
    part[tuple(m["partition_columns"])] += 1
    if "uid" in names and "action_id" in names:  # 事件表判定
        for k in CORE:
            if k in names: has_core[k]+=1
    for c in m["columns"]:
        n = c["name"]
        if n.endswith("_id") or n in ("uid","aid","id","uuid","guid","trans_no","version","version_num","logdate","logregion","loghour"):
            id_cols[n]+=1; id_types[n][c["type"]]+=1

lines = ["# hk4e 键与外键线索分析", "", f"分析 {total} 张表", "", "## 高频键列 Top50", ""]
lines.append("| 列名 | 出现表数 | 类型分布 |")
lines.append("|---|---|---|")
for name, cnt in id_cols.most_common(50):
    types = ", ".join(f"{t}×{n}" for t,n in id_types[name].most_common())
    lines.append(f"| `{name}` | {cnt} | {types} |")

lines += ["", "## 事件表核心字段骨架（uid+action_id 同时存在的表）", "",
          "| 字段 | 表数 |", "|---|---|"]
for k,c in has_core.most_common():
    lines.append(f"| `{k}` | {c} |")

lines += ["", "## 分区组合 Top20", "", "| 分区 | 表数 |", "|---|---|"]
for p,c in part.most_common(20):
    ps = ", ".join(p) if p else "(无分区)"
    lines.append(f"| `{ps}` | {c} |")

out = os.path.join(BASE, "analysis", "keys_analysis.md")
os.makedirs(os.path.dirname(out), exist_ok=True)
open(out,"w",encoding="utf-8").write("\n".join(lines))
print(f"written {out}")
print(f"总表 {total}, 事件表(uid+action_id) {sum(1 for fp in files if 'uid' in {c['name'] for c in json.load(open(fp))['columns']} and 'action_id' in {c['name'] for c in json.load(open(fp))['columns']})}")
