# hk4e 权威图谱与资源索引使用指南

> 生成：2026-09-18。所有命令/接口均经实际验证。
> 两条通道互补：**资源索引**找 AOF 资源卡片（语义描述），**权威图谱**做结构化查询与路径扩展。

## 一、资源检索索引（SQLite FTS5，离线，<30ms）

路径：`/Users/chaihao/LLM/AOF/data/aof_resource_index/resource_index.sqlite3`（5,470 资源）

```bash
cd /Users/chaihao/LLM/AOF
PY=.venv/bin/python

$PY tools/build_resource_index.py search "圣遗物" -k 10       # 单词
$PY tools/build_resource_index.py search "深渊 甘雨" -k 10    # 多词 AND，无命中自动 OR 兜底并标注
$PY tools/build_resource_index.py search "uid" --kind RelationType -k 10
$PY tools/build_resource_index.py stats                       # 统计
$PY tools/build_resource_index.py build                       # 全量重建（秒级）
```

```python
import sys; sys.path.insert(0, "/Users/chaihao/LLM/AOF")
from tools.build_resource_index import search
results, elapsed_ms, partial = search(
    "/Users/chaihao/LLM/AOF/data/aof_resource_index/resource_index.sqlite3",
    "TempleBattle", k=10)
# 字段: resource_id/kind/name/display_name/domain/description/tags/score
```

索引覆盖：display_name（中文名）/name/kind/domain/tags/description/ObjectType 的属性与来源表/RelationType 的 from--via-->to。
kind 可选：ObjectType(2,346) / RelationType(2,984) / ContextAssertion(131) / PhysicalDataset(6) / Ontology(2) / DataSource(1)。

## 二、权威图谱（Kuzu：30,289 节点 / 60,692 边）

路径：`/Users/chaihao/LLM/AOF/data/hk4e_ontology/cognee_graph_kuzu`

### 1) 路径召回（graph_retrieval）

```bash
GRAPH_DATABASE_PROVIDER=kuzu \
GRAPH_FILE_PATH=/Users/chaihao/LLM/AOF/data/hk4e_ontology \
/Users/chaihao/LLM/AOF/.venv/bin/python your_script.py
```

```python
import asyncio
from bridge.graph_retrieval import graph_path_retrieval

async def main():
    hits = await graph_path_retrieval(
        query="庙宇挑战 TempleBattle 的相关结构",  # 中英文均可
        dataset_name="hk4e",                       # 仅标签
        limit=6,
        cognee_root="/Users/chaihao/LLM/cognee",
    )
    for h in hits:
        print(f"[{h.hops}hop] {h.text}  score={h.score:.2f}")

asyncio.run(main())
```

### 2) 原生 Cypher（KuzuAdapter）

```python
import asyncio, sys
sys.path.insert(0, "/Users/chaihao/LLM/cognee")
from cognee.infrastructure.databases.graph.kuzu.adapter import KuzuAdapter

DB = "/Users/chaihao/LLM/AOF/data/hk4e_ontology/cognee_graph_kuzu"

async def main():
    ad = KuzuAdapter(db_path=DB)
    rows = await ad.query("""
        MATCH (a:Node)-[r:EDGE]->(b:Node)
        WHERE a.name = '圣遗物背包'
        RETURN a.name, r.relationship_name, b.name
    """)
    for row in rows or []: print(row)

asyncio.run(main())
```

### Schema 速查

| 项 | 说明 |
|---|---|
| 节点表 | `Node(id, name, type, properties)`；type ∈ EntityClass(2,547) / DatatypeProperty(24,620) / ObjectProperty(2,989) / Instance(131) / Ontology(2) |
| 边表 | `EDGE(relationship_name, …)`；类型：subClassOf / hasDomain / hasRange / instance_of / 具体属性名（TempleBattleRound_uid_rel、wieldsElement、belongsToRegion…） |
| 命名 | biz 对象属性节点 name 形如 `A--col-->B`；类节点 name 是中文 label，英文原名在 id（URI）里 → 按英文查用 `n.id CONTAINS 'xxx'` |
| 实例数据 | 131 个 Avatar 实例的元素/星级等在 `properties` JSON 的 `facts` 字段 |

### 3) 导出 Markdown

`exporters/markdown_exporter.py` 走同一 `get_graph_engine()`，设同样两个环境变量即可导出整图为 Markdown。

## 三、推荐组合（端到端）

1. 索引定位：`search "深渊"` → 得 resource_id 与资源卡片
2. 图谱取结构：实体中文名查 1-hop（外键/所属域/快照族）
3. 一步到位：`graph_path_retrieval(query="深渊 SpiralAbyss 玩家关系")`

## 四、边界与维护

- ⚠️ 向量/hybrid/RAG 检索对 hk4e **无效**（权威库刻意 0-LLM、无 embedding）；文本检索一律走 FTS5 索引
- 「甘雨」等中文角色名 0 命中是语料边界（Avatar 名 unresolved，no-guess 策略）；拿到官方映射表后增量补入
- 更新流程：重生成 TTL/resources → `tools/ingest_authoritative_graph.py --rebuild` → `tools/build_resource_index.py build`
- 背景与决策记录见同目录 note.md「摄入架构切换」条目
