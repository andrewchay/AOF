# 本体治理与确定性推理

企业 Web 工作台、签名身份和运行验收见
[运行手册](../operations/ontology-workbench-runbook.md)。工作台复用本章状态机，不建立独立 CRUD 数据源或发布旁路。

## 能力边界

AOF 现在把语义资产拆成三类不可混淆的主档：

- OWL：类、属性、关系语义和可计算冲突。
- SHACL：发布前的闭世界数据契约。
- SKOS：业务术语、首选/替代标签、上下位、映射、弃用和替代关系。

治理实现位于 `bridge/ontology_governance/`。所有草稿必须经过 `draft → validation → conflict review / validated → approval → publish`；冲突审查可以记录豁免，也可以请求修改并产生新 revision。校验、请求修改、豁免、审批与发布分别进入决策溯源因果链。

## 发布门禁

当前内置 SHACL Core 确定性子集覆盖：

- `sh:targetClass`、`sh:targetNode`、`sh:targetSubjectsOf`、`sh:targetObjectsOf`
- `sh:minCount`、`sh:maxCount`
- `sh:datatype`、`sh:class`、`sh:nodeKind`
- `sh:minInclusive`、`sh:maxInclusive`、`sh:minLength`、`sh:maxLength`
- `sh:pattern`、`sh:in`

类匹配包含 `rdfs:subClassOf` 闭包。关系对象可用 `sh:class` 做跨实体约束。未支持的 SHACL 约束会生成阻断性 finding，不能被静默忽略。

附加冲突检查包括 OWL disjoint class、OWL functional property 多值、SKOS 每语言多首选标签、上下位循环，以及弃用概念缺少 `dcterms:isReplacedBy` / `skos:exactMatch`。

审查与豁免分别保存为追加式哈希链 JSONL。任一证据链被修改后，审批会被阻断。发布生成：

- `ontology_version`
- `shape_version`
- `skos_version`
- 相对上一版本的三元组变更集
- 审批与发布决策 ID
- 各源文件内容哈希

## 编辑器

Web 控制台 `/ontology` 提供 OWL、SHACL、SKOS 三栏编辑、结构化添加类/关系属性/术语、门禁报告、不可变豁免、请求修改、审批、发布和影响预览。发布版本不可覆盖；修改必须回到新草稿/新 revision。

## 确定性推理

`DatalogEngine` 执行安全、分层 Datalog：

- 拒绝不安全变量和经否定递归的不可分层程序。
- 按 strata 和固定点计算，输入顺序不改变结果。
- 每个派生事实携带 `fact_id`、`rule_id`、规则集内容哈希、变量绑定和输入事实。
- 每次运行自动计算事实快照哈希，并把规则版本、输入快照和派生事实集写入决策溯源。

`RuleSetRepository` 以内容寻址方式发布不可变规则集版本。生产调用应优先使用 `/v1/reasoning/rulesets/{id}/{version}/run`，临时诊断才使用 `/v1/reasoning/datalog/run`。

SPARQL 只查询已发布 OWL/SKOS 快照，禁止 SPARQL Update；响应包含 snapshot ID 和 query hash，用于审计取证。

Rete 明确保留为后续在线增量事件层。只有当规则规模、事件吞吐、事实撤回和延迟目标有真实基线后才引入，不能替代当前可复现的批量 Datalog 主路径。

## 关键 API

- `POST /v1/ontology/drafts`
- `POST /v1/ontology/drafts/{id}/validate`
- `POST /v1/ontology/drafts/{id}/waivers`
- `POST /v1/ontology/drafts/{id}/request-changes`
- `POST /v1/ontology/drafts/{id}/approve`
- `POST /v1/ontology/drafts/{id}/publish`
- `GET /v1/ontology/drafts/{id}/impact`
- `POST /v1/ontology/releases/{ontology_id}/{version}/sparql`
- `POST /v1/reasoning/rulesets`
- `POST /v1/reasoning/rulesets/{id}/{version}/run`
