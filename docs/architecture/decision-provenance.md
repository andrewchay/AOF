# 决策级溯源（PROV-O 风格）

## 目标与边界

决策溯源回答“Agent 为何作出此结论、依据何证据、承接哪些先例、影响什么”，而 `bridge.audit` 继续记录访问与操作。两者互补，不能互相替代。

每条 `Decision` 是一个 Activity：由 `Agent` 执行，`used` 一组不可变 `Entity` 证据，`wasInformedBy` 其前序 Decision，`generated` 输出实体。账本写入 `data/audit/decision_provenance.jsonl`（可由 `AOF_DECISION_PROVENANCE_FILE` 覆盖）；每条记录的 SHA-256 覆盖内容和前序哈希。

## API 闭环

1. `POST /v1/decisions`：记录决策。父决策必须已存在，拒绝自环。
2. `GET /v1/decisions/{id}/causal-chain`：按 ancestors 或 descendants BFS 追溯。
3. `POST /v1/decisions/precedents/search`：按 `decision_type` 和标签重叠检索先例。
4. `POST /v1/decisions/impact`：从下游决策链汇总被影响的输出实体。
5. `GET /v1/decisions/{id}/audit-trail`：输出 PROV-O 风格 JSON-LD、完整性验证和合规摘要。

## 后续治理层

在数据模型稳定后再引入 SHACL（字段/关系约束）和 OWL（概念对齐）；冲突必须产出审查实体，不能覆盖原始决策。确定性推理应在版本化规则集上运行，并把规则版本、输入快照和推理结果重新写为可审计 Entity。
