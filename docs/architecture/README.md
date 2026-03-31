# AOF 架构总览（主入口）

本文档是 `docs/architecture/` 的统一入口，包含：

1. 主架构图（系统级）
2. 核心能力边界
3. 术语表（Glossary）
4. 分文档导航

最后更新：`2026-03-31`

## 1. 主架构图

```text
上游输入
  ├─ 文档（SQL/Markdown/报告）
  ├─ 元数据（表结构/字段）
  └─ 反馈（jsonl patches）
            │
            ▼
services/semantic_middle_layer_api (FastAPI 编排层)
            │
            ▼
bridge/ (能力适配层)
  ├─ 规格与本体: spec_mapper / ontology_adapter
  ├─ 摄取同步: incremental / url / s3 / batch / sync
  ├─ 检索反馈: enhanced_search / cypher / memify
  ├─ 数据与图能力: dataset / visualize / analytics
  └─ 保障能力: preflight / quality_gate / errors
            │
            ▼
Cognee 引擎层（add / cognify / search / graph）
            │
            ▼
中间层产物（data/middle_layer/<topic>/）
  ├─ mapping/
  ├─ regression/
  └─ artifacts/（manifest, skills suggestions）
            │
            ▼
下游消费（Text-to-SQL / BI Copilot / 指标治理 / 查询审计）
```

## 2. 核心边界

- API 层负责协议与编排，不承载复杂业务规则
- Bridge 层负责能力适配和流程组装
- Cognee 层负责图谱引擎核心能力
- `data/middle_layer` 是统一产物契约

## 3. 术语表（Glossary）

| 术语 | 定义 |
|---|---|
| AOF | Agentic Ontology Factory，语义中间层工程 |
| Semantic Middle Layer | 连接上游数据与下游智能应用的语义抽象层 |
| Topic | 一次中间层构建的业务主题命名空间 |
| Mapping Library | 下游生成 SQL/解释语义时使用的映射库 |
| Regression Library | 下游回归门禁样例库 |
| Manifest | 一次构建的产物清单与索引文件 |
| Feedback Loop | 反馈回流并转化为本体/映射改进建议的闭环 |
| Quality Gate | 构建前后质量检查门禁 |
| Bridge Layer | AOF 与 Cognee 之间的适配编排层 |
| Cognify | Cognee 的图谱构建流程入口 |

## 4. 文档导航

- 桥接层详细设计：[bridge-design.md](/Users/chaihao/LLM/AOF/docs/architecture/bridge-design.md)
- AOF 与 Cognee 集成：[cognee-integration.md](/Users/chaihao/LLM/AOF/docs/architecture/cognee-integration.md)
- 能力覆盖与差距快照：[cognee-capabilities-gap-analysis.md](/Users/chaihao/LLM/AOF/docs/architecture/cognee-capabilities-gap-analysis.md)
- 历史新增功能总结（阶段性）：[new-features-summary.md](/Users/chaihao/LLM/AOF/docs/architecture/new-features-summary.md)

## 5. 维护约定

1. 架构变更先更新本文档主图与术语，再更新分文档细节
2. 路由与模块统计值以代码为准（`app.py` + `bridge/`）
3. 历史文档可保留，但需标注“阶段性”避免误读
