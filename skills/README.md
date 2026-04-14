# AOF Skills - Agent Playbook

> 这是 AOF (Agentic Ontology Factory) 的 Agent 操作手册。
> 
> **核心原则**：AOF 不是黑盒 API，而是你的知识工程伙伴。这些技能文件告诉你
> **什么时候该做什么、怎么做、以及为什么这样做**。

## 何时读取这些文件

- 用户要求"把数据导入 AOF" → 读 `SKILL_INGEST.md`
- 用户要求"搜索知识图谱" / "查一下..." → 读 `SKILL_QUERY.md`
- 用户要求"分析这个图谱" / "看看节点关系" → 读 `SKILL_ANALYTICS.md`
- 用户要求"检查系统健康" / "清理旧数据" → 读 `SKILL_MAINTAIN.md`
- 用户要求"把图谱导出为人类可读格式" → 读 `SKILL_EXPORT.md`（本文档末尾）

## AOF 核心工作流（记住这个循环）

```
摄取 (Ingest) → 认知化 (Cognify) → 查询/分析 (Query/Analytics) → 维护 (Maintain)
       ↑                                                              ↓
       └────────────────── 持续迭代 ← 导出反馈 ←───────────────────────┘
```

## 快速参考：AOF 关键模块

| 模块 | 路径 | 用途 |
|------|------|------|
| `bridge/` | 业务逻辑层 | 数据摄取、同步、搜索、分析 |
| `exporters/` | 导出层 | Markdown 导出、人类可读化 |
| `services/` | API 服务 | REST API、FastAPI 端点 |
| `sdk/` | SDK | Python SDK 封装 |

## Skill 清单

### SKILL_INGEST.md - 数据摄取大师
如何正确地把文档、数据库、URL、S3 等数据导入 AOF。包括幂等性检查、增量更新、错误处理。

### SKILL_QUERY.md - 查询与检索
如何选择搜索类型（GRAPH_COMPLETION vs RAG_COMPLETION vs CYPHER），如何构造高效查询，如何处理无结果的情况。

### SKILL_ANALYTICS.md - 图谱分析
何时运行 PageRank、社区检测、中心性分析。如何解读分析结果并给出有价值的洞察。

### SKILL_MAINTAIN.md - 维护与治理
如何运行 health check、清理孤立节点、管理数据集生命周期、审计日志查看。

### SKILL_EXPORT.md - 导出与镜像（本文档）
如何将 AOF 的内部图谱导出为 Markdown，让企业知识库保持"人类可读"。

---

## SKILL_EXPORT: 将知识导出为人类可读格式

AOF 的核心优势是构建企业级知识图谱，但图谱数据对企业用户来说往往是"黑盒"。
通过 `exporters/markdown_exporter.py`，你可以把图谱中的实体、关系、摘要导出为
Markdown 文件，采用 GBrain 风格的 `compiled_truth + timeline` 结构。

### 何时导出

- **知识库验收**：业务方想"看到"图谱里到底存了什么
- **版本控制**：想把知识库的关键内容纳入 Git 管理
- **人工校验**：专家需要审阅和修正机器提取的实体
- **迁移/备份**：导出为通用格式，便于迁移到其他系统

### 如何导出

```python
from exporters.markdown_exporter import export_dataset_to_markdown

result = await export_dataset_to_markdown(
    dataset_id="your-dataset-uuid",
    output_dir="./brain_mirror/",
)

print(f"导出 {result.pages_exported} 页到 {result.output_dir}")
```

### 导出模式

- **graph 模式**：优先从 Cognee 底层图数据导出，按实体类型分目录
- **dataset 模式**：如果图数据获取失败，自动回退到原始 dataset 数据导出

### 导出的文件结构

```
brain_mirror/
├── person/
│   ├── garry_tan.md
│   └── pedro_franceschi.md
├── company/
│   ├── y_combinator.md
│   └── initialized_capital.md
└── concept/
    └── superlinear_returns.md
```

### 每个 Markdown 的结构

```markdown
---
type: person
title: Garry Tan
aof_id: node-uuid-123
tags: [investors, yc]
---

Garry Tan 是 YC 的现任 CEO...

** outgoing 关系：**
- works_at → Y Combinator
- founded → Initialized Capital

** incoming 关系：**
- Pedro Franceschi → knows

---

- 2023-01-01: 成为 YC CEO
- 2024-06-15: 在会议中提到 AI agents
```

### 注意事项

1. **导出是只读的**：不会修改 AOF 中的任何数据
2. **文件名会处理冲突**：同名节点会自动加 `_1`、`_2` 后缀
3. **大属性会被截断**：超过 200 字符的复杂 JSON 属性不会写入 frontmatter
4. ** timeline 可能不完整**：取决于 Cognee 节点中是否包含时间字段
