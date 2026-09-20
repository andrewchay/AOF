#!/usr/bin/env python3
"""
把 hk4e 元数据 + 本体成果接入 AOF 体系。

产出（写入 ~/.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta/aof/）：
1. hk4e.ttl            —— OWL/Turtle 本体（对齐 bridge/database_schema_extractor.Ontology 模式）
2. resources.json      —— 符合 ResourceKind 的 SemanticResource 集合（用 AOF 的 SemanticResource.create 构造）
3. normalized.txt/.jsonl —— 供 aof_add.py --data-path 摄入的归一化数据
"""
import json
import os
import sys
from pathlib import Path

AOF_ROOT = Path("/Users/chaihao/LLM/AOF")
sys.path.insert(0, str(AOF_ROOT))

BASE = Path(os.path.expanduser(
    "~/.gravitas/agent-workspaces/aof/workspace-files/.context/hk4e_meta"))
OUT = BASE / "aof"
OUT.mkdir(parents=True, exist_ok=True)

# 复用 AOF 原生类
from bridge.semantic_core.models import ResourceKind, SemanticResource  # noqa: E402
from bridge.semantic_core.adapters.base import AdapterContext  # noqa: E402

TENANT = "mihoyo"
DOMAIN = "hk4e"
OWNER = "hao.chai"
ns = "http://hk4e.mihoyo.com/ontology#"
ctx = AdapterContext(tenant=TENANT, domain=DOMAIN, owner=OWNER)

# catalog.json 由 fetch_all.py 重建，顶层字段为 table_count（兼容旧的 total_tables）
def _catalog_total(c):
    return c.get("total_tables") or c.get("table_count") or sum(
        v["table_count"] for v in c["databases"].values())


def _db_tables(catalog, db):
    """兼容两种 catalog 形态：databases[db].tables 或顶层扁平 tables 列表。"""
    if "tables" in catalog["databases"].get(db, {}):
        return catalog["databases"][db]["tables"]
    return [t for t in catalog.get("tables", []) if t["database"] == db]


# ============ 读入我已提取的本体 ============
ent = json.loads((BASE / "ontology" / "entities.json").read_text())
rel = json.loads((BASE / "ontology" / "relations.json").read_text())
dom = json.loads((BASE / "ontology" / "domains.json").read_text())
catalog = json.loads((BASE / "catalog.json").read_text())

entities = ent["entities"]
relations = rel["relations"]
domains = dom["domains"]

# ============ 1. 生成 Turtle 本体 ============
# 类型映射：SQL → XSD（对齐 database_schema_extractor.SQL_TO_XSD_TYPE）
SQL2XSD = {"int": "integer", "bigint": "long", "smallint": "short", "string": "string",
           "double": "double", "float": "float", "decimal": "decimal", "boolean": "boolean",
           "date": "date", "timestamp": "dateTime"}

ttl = [
    "@prefix owl:  <http://www.w3.org/2002/07/owl#> .",
    "@prefix rdf:  <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .",
    "@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .",
    "@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .",
    f"@prefix hk4e: <{ns}> .",
    "",
    f"<{ns.rstrip('#')}> a owl:Ontology ;",
    '    rdfs:label "Genshin Impact (hk4e) Data Ontology"@en ;',
    '    rdfs:label "原神数据本体"@zh ;',
    '    rdfs:comment "Extracted from CN warehouse metadata snapshot 2026-09-17 (1940 tables)."@en .',
    "",
]

# 域 → owl:Class（顶层）
for dname, d in domains.items():
    ttl += [
        f"hk4e:{dname} a owl:Class ;",
        f'    rdfs:label "{d["cn"]}"@zh ;',
        f'    rdfs:label "{dname}"@en ;',
        f'    rdfs:comment "本体域: {d["cn"]}"@zh .',
        "",
    ]

# 实体 → owl:Class，subClassOf 其域
for ename, e in entities.items():
    ttl += [
        f"hk4e:{ename} a owl:Class ;",
        f'    rdfs:label "{e["cn"]}"@zh ;',
        f'    rdfs:label "{ename}"@en ;',
        f"    rdfs:subClassOf hk4e:{e['domain']} ;",
        f'    rdfs:comment "Domain={e["domain"]}; PK={e["pk"]}:{e["pk_type"]}; sources={", ".join(e.get("source_tables",[]))}"@en .',
        "",
    ]
    # 主键 + 属性 → DatatypeProperty
    props = [(e["pk"], e["pk_type"], True)] + [(a, "string", False) for a in e.get("attrs", [])]
    for pname, ptype, is_pk in props:
        xsd = SQL2XSD.get(ptype, "string")
        triple = [
            f"hk4e:{ename}_{pname} a owl:DatatypeProperty ;",
            f"    rdfs:domain hk4e:{ename} ;",
            f"    rdfs:range xsd:{xsd} ;",
            f'    rdfs:label "{pname}"@en',
        ]
        if is_pk:
            triple[-1] += " ;"
            triple.append('    rdfs:comment "primary key"@en')
        triple[-1] += " ."
        ttl += triple + [""]

# 关系 → owl:ObjectProperty
import re as _re
def _legal(name: str) -> str:
    """把关系名转成合法 Turtle 本地名（仅保留字母数字下划线）。"""
    s = _re.sub(r"[^A-Za-z0-9_]", "_", name)
    return s or "REL"

for r in relations:
    f, t, ty = r["from"], r["to"], r["type"]
    if f == "Event":
        continue  # Event 非实体
    ty_legal = _legal(ty)
    ttl += [
        f"hk4e:{ty_legal} a owl:ObjectProperty ;",
        f"    rdfs:domain hk4e:{f} ;",
        f"    rdfs:range hk4e:{t} ;",
        f'    rdfs:label "{ty}"@en ;',
        f'    rdfs:comment "card={r["card"]}; via={", ".join(r["via"])}"@en .',
        "",
    ]

(OUT / "hk4e.ttl").write_text("\n".join(ttl), encoding="utf-8")

# ============ 2. 生成 SemanticResource 集合 ============
resources = []

# 2.1 本体资源
onto_content = (OUT / "hk4e.ttl").read_text()
import hashlib
onto_hash = f"sha256:{hashlib.sha256(onto_content.encode()).hexdigest()}"
resources.append(SemanticResource.create(
    resource_id=ctx.resource_id("ontology", "hk4e-data-ontology"),
    kind=ResourceKind.ONTOLOGY,
    name="hk4e-data-ontology",
    display_name="原神数据本体",
    domain=DOMAIN, owner=OWNER,
    description="由 CN 区 1940 张表元数据提取的原神数据本体（7域/28实体/29关系）",
    tags=("genshin", "hk4e", "ontology", "warehouse"),
    evidence=[{"evidence_id": f"meta:snapshot:2026-09-17:{onto_hash[:16]}",
               "source_uri": "mcp://andrew-mcp/cn/hk4e_meta",
               "content_hash": onto_hash}],
    spec={"format": "turtle", "content": onto_content,
          "entity_count": len(entities), "relation_count": len(relations)},
))

# 2.2 每个实体 → OBJECT_TYPE
for ename, e in entities.items():
    resources.append(SemanticResource.create(
        resource_id=ctx.resource_id("object-type", ename.lower()),
        kind=ResourceKind.OBJECT_TYPE,
        name=ename.lower(), display_name=e["cn"],
        domain=DOMAIN, owner=OWNER,
        description=f'{e["cn"]}（PK={e["pk"]}），来源: {", ".join(e.get("source_tables",[]))}',
        tags=(e["domain"].lower(), "entity"),
        spec={"entity": ename, "cn": e["cn"], "pk": e["pk"], "pk_type": e["pk_type"],
              "attrs": e.get("attrs", []), "source_tables": e.get("source_tables", [])},
    ))

# 2.3 每个关系 → RELATION_TYPE
for i, r in enumerate(relations):
    if r["from"] == "Event":
        continue
    resources.append(SemanticResource.create(
        resource_id=ctx.resource_id("relation-type", f'{r["from"].lower()}-{r["type"].lower()}-{r["to"].lower()}'),
        kind=ResourceKind.RELATION_TYPE,
        name=f'{r["from"].lower()}-{r["type"].lower()}-{r["to"].lower()}',
        display_name=f'{r["from"]} --{r["type"]}--> {r["to"]}',
        domain=DOMAIN, owner=OWNER,
        description=r.get("note", f'{r["from"]} → {r["to"]} via {", ".join(r["via"])}'),
        tags=("relation",),
        spec={"from": r["from"], "to": r["to"], "type": r["type"],
              "via": r["via"], "card": r["card"]},
    ))

# 2.4 每个库 → PHYSICAL_DATASET
for db, info in catalog["databases"].items():
    resources.append(SemanticResource.create(
        resource_id=ctx.resource_id("physical-dataset", db.replace("_", "-")),
        kind=ResourceKind.PHYSICAL_DATASET,
        name=db.replace("_", "-"), display_name=db,
        domain=DOMAIN, owner=OWNER,
        description=f'{db} 物理数据集，{info["table_count"]} 张表',
        tags=("physical", db.split("_")[0]),
        spec={"database": db, "table_count": info["table_count"],
              "region": "cn", "system": "hive-spark"},
    ))

# 2.5 DATA_SOURCE
resources.append(SemanticResource.create(
    resource_id=ctx.resource_id("data-source", "mihoyo-cn-hk4e"),
    kind=ResourceKind.DATA_SOURCE,
    name="mihoyo-cn-hk4e", display_name="米哈游 CN 数据仓 hk4e",
    domain=DOMAIN, owner=OWNER,
    description="CN 区数据仓库中的原神(hk4e)数据，通过 andrew-mcp 访问",
    tags=("datasource", "mcp"),
    spec={"mcp_server": "andrew-mcp", "region": "cn",
          "databases": list(catalog["databases"].keys()),
          "total_tables": _catalog_total(catalog)},
))

# 写 resources.json
def _dump(res):
    return {
        "resource_id": res.resource_id, "kind": res.kind.value, "name": res.name,
        "domain": res.domain, "owner": res.owner, "display_name": res.display_name,
        "description": res.description, "tags": list(res.tags),
        "depends_on": list(res.depends_on),
        "evidence": [dict(e) for e in res.evidence],
        "spec": dict(res.spec), "revision_id": res.revision_id,
        "schema_version": res.schema_version,
    }

# 让实体/关系依赖本体
onto_id = ctx.resource_id("ontology", "hk4e-data-ontology")
dep_map = {}
for res in resources:
    if res.kind in (ResourceKind.OBJECT_TYPE, ResourceKind.RELATION_TYPE):
        dep_map[res.resource_id] = [onto_id]

final = []
for res in resources:
    d = _dump(res)
    if res.resource_id in dep_map:
        # 重建带依赖的 resource
        rebuilt = SemanticResource.create(
            resource_id=res.resource_id, kind=res.kind, name=res.name,
            domain=res.domain, owner=res.owner, display_name=res.display_name,
            description=res.description, tags=res.tags, depends_on=dep_map[res.resource_id],
            evidence=res.evidence, spec=res.spec,
        )
        d = _dump(rebuilt)
    final.append(d)

(OUT / "resources.json").write_text(
    json.dumps({"schema_version": "aof.semantic/v1", "resources": final},
               ensure_ascii=False, indent=2), encoding="utf-8")

# ============ 3. 归一化数据（供 aof_add 摄入）============
lines_txt, lines_jsonl = [], []
for db, info in catalog["databases"].items():
    for t in _db_tables(catalog, db):
        cols = "、".join(f'{c["name"]}({c["type"]})' for c in t["columns"])
        txt = (f"表 {t['full_name']}（库 {db}，{t['column_count']} 列，"
               f"分区 {','.join(t['partition_columns']) or '无'}）。字段：{cols}")
        lines_txt.append(txt)
        lines_jsonl.append(json.dumps({
            "table": t["full_name"], "database": db,
            "column_count": t["column_count"],
            "partition_columns": t["partition_columns"],
            "columns": t["columns"],
        }, ensure_ascii=False))

(OUT / "normalized.txt").write_text("\n".join(lines_txt), encoding="utf-8")
(OUT / "normalized.jsonl").write_text("\n".join(lines_jsonl), encoding="utf-8")

# ============ 汇总 ============
kinds = {}
for r in final:
    kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1

print("=== AOF 接入产物 ===")
print(f"hk4e.ttl        : {len(ttl)} 行")
print(f"resources.json  : {len(final)} 个 SemanticResource")
for k, v in sorted(kinds.items()):
    print(f"    {k:20s} {v}")
print(f"normalized.txt  : {len(lines_txt)} 行")
print(f"normalized.jsonl: {len(lines_jsonl)} 行")
print(f"输出目录: {OUT}")
