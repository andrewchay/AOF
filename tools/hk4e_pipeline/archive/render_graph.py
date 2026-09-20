#!/usr/bin/env python3
"""从 ontology JSON 渲染 Mermaid 图 + 生成可检索的图谱索引"""
import json, os

BASE = os.path.expanduser("~/.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta")
onto = os.path.join(BASE, "ontology")
ent = json.load(open(os.path.join(onto,"entities.json")))
rel = json.load(open(os.path.join(onto,"relations.json")))
dom = json.load(open(os.path.join(onto,"domains.json")))

entities = ent["entities"]; relations = rel["relations"]; domains = dom["domains"]

# 域 → 颜色
DCOLOR = {"Player":"#e3f2fd","GameEntity":"#e8f5e9","Combat":"#fff3e0",
          "Commerce":"#fce4ec","UGC":"#f3e5f5","Platform":"#e0f7fa","Time":"#fffde7"}

lines = ["```mermaid","graph LR"]
# 按域分组
for dname, d in domains.items():
    lines.append(f'  subgraph {dname}["{d["cn"]}"]')
    for e in d["entities"]:
        if e in entities:
            lines.append(f'    {e}["{entities[e]["cn"]}<br/>{entities[e]["pk"]}"]')
    lines.append("  end")
# 关系
for r in relations:
    f,t = r["from"], r["to"]
    if f in entities or f=="Event":
        lbl = r["type"]
        if f=="Event": continue  # Event 单独画
        lines.append(f'  {f} -->|{lbl}| {t}')
# Event 作为特殊节点
lines.append('  Event{{"事件(Event)"}}')
for r in relations:
    if r["from"]=="Event":
        lines.append(f'  Event -->|{r["type"]}| {r["to"]}')
lines.append("```")

# 域汇总表
md = ["# 原神本体关系图", "", "## 域与实体", "", "| 域 | 实体数 | 实体 |", "|---|---|---|"]
for dname,d in domains.items():
    els = "、".join(f'{entities[e]["cn"]}({e})' for e in d["entities"] if e in entities)
    md.append(f'| {d["cn"]} | {len(d["entities"])} | {els} |')

md += ["", "## 关系图", ""] + lines + ["", "## 关系清单", "", "| 源 | 关系 | 目标 | 连接键 | 基数 |", "|---|---|---|---|---|"]
for r in relations:
    md.append(f'| {r["from"]} | {r["type"]} | {r["to"]} | {", ".join(r["via"])} | {r["card"]} |')

open(os.path.join(onto,"graph.md"),"w",encoding="utf-8").write("\n".join(md))

# 生成可检索索引：列名 → 拥有该列的实体
col2ent = {}
for ename, e in entities.items():
    col2ent.setdefault(e["pk"], []).append(f'{ename}(pk)')
    for a in e.get("attrs",[]):
        col2ent.setdefault(a, []).append(ename)
json.dump(col2ent, open(os.path.join(onto,"column_index.json"),"w",encoding="utf-8"), ensure_ascii=False, indent=2)

print("graph.md written")
print(f"entities={len(entities)} relations={len(relations)}")
print(f"column_index: {len(col2ent)} distinct columns")
