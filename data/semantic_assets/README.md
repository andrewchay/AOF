# hk4e 语义资产（canonical，2026-09-20 入库）

本目录是 AOF hk4e 域语义资产的**唯一事实源**（版本控制在此）。
工作区 `~/.gravitas/.../hk4e_meta/aof/` 下的同名文件是历史镜像，不再更新。

## 权威产物（canonical）
- `hk4e-complete.ttl` — 权威本体（134k triples；人工精修段：avatar instances / combat / pub#）
- `resources_full.json` — SemanticResource 资源卡全集（5,852：ObjectType 2,388 / RelationType 3,019 / Metric 284 / Playbook 21 / 其他 40）
- `hk4e-biz.ttl` / `biz_resources.json` — biz 线平行资产（biz_growth 域）

## 输入快照（fetch 缓存，重采集走 tools/hk4e_pipeline/fetch_all.py）
- `catalog.json`（hk4e 族 6,620 表 schema）/ `catalog_crossdb.json`（13 张跨域表）
- `table_list.json` / `table_list_biz_hk4e.json`

## 规格/归档件
- `aof_spec.hk4e*.json`（资产构建规格）、`combat_bridge.json`、`biz_ontology_summary.json`、`USAGE.md`

## 再生管线（tools/hk4e_pipeline/，README 有各阶段说明与重跑警告）
catalog 快照 → build_playbook_assets(_v2).py → build_playbook_scenes.py → build_metric_registry.py
→ resources_full.json → `tools/ingest_authoritative_graph.py --rebuild`（Kuzu）
→ `tools/build_resource_index.py build`（FTS5 索引，sqlite 不入库）
