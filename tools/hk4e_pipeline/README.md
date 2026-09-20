# hk4e 语义资产再生管线（2026-09-20 入库）

所有脚本读写 `data/semantic_assets/`（路径经 `__file__` 推导，仓库内自洽）。
外部依赖：playbook 仓（/Users/chaihao/LLM/brando-playbook-game-growth/playbooks，绝对路径）；
fetch_all.py 需工作区 mcp.json 里的 andrew-mcp token（运行时读取，不入库）。

## 活管线（可重跑，全量刷新语义）
| 脚本 | 作用 |
|---|---|
| fetch_all.py | andrew-mcp HTTP 拉取表 schema → catalog*.json（需数据网权限） |
| build_playbook_assets.py / _v2.py | playbook 需求表 → ObjectType/RelationType 卡（步1 两批） |
| build_playbook_scenes.py | playbook 20 场景 → Playbook 场景卡（步2，全量刷新） |
| build_metric_registry.py | 口径.md 逐条 → Metric 卡（步3，全量刷新） |

## 历史一次性脚本（⚠️ 原样归档，勿盲目重跑）
gen_ontology.py / merge_ontology.py / build_aof_assets.py 生成的是**骨架**；
`hk4e-complete.ttl` 含人工精修段（avatar instances / combat / pub#），重跑会覆盖精修成果。
extract/build_avatar_instances.py、patch_avatar_instance_schema.py、patch_combat.py、
analyze_keys.py、save_meta.py、render_graph.py、prepare_cognify_chunks.py 同属历史阶段，
部分输入（ontology/、instances/、shards）留在工作区未入库，路径常量仍指向旧位置。

## 验收基线（改动后必跑）
```
python tools/hk4e_pipeline/build_playbook_scenes.py      # 20+1 卡
python tools/hk4e_pipeline/build_metric_registry.py      # 284 卡
python tools/build_resource_index.py build               # resources=5852
python -m pytest tests/test_aof_semantic_query.py -q -o addopts=''   # 11 passed
```
