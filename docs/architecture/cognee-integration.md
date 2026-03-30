# AOF 与 Cognee 深度集成架构

## 概述

本文档描述 AOF 如何深度 leverage Cognee 的能力，包括数据库 Schema 提取、增强搜索、反馈闭环等高级功能。

## 新增能力矩阵

| 能力 | 实现位置 | Cognee 功能 | 状态 |
|------|----------|-------------|------|
| 数据库 Schema 提取 | `bridge/database_schema_extractor.py` | `extract_schema()` + `migrate_relational_database()` | ✅ 已完成 |
| 增强搜索 | `bridge/enhanced_search.py` | 全部搜索类型 | ✅ 已完成 |
| 反馈闭环 | `bridge/memify_feedback_loop.py` | `memify` 模块 | ✅ 已完成 |
| YAML 标注支持 | `tools/data_adapter/mixed_document_parser.py` | - | ✅ 已完成 |

## 架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              AOF 应用层                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │  tools/             │  │  services/          │  │  bridge/            │  │
│  │  ├── extract_db_    │  │  ├── API 端点       │  │  ├── cognee_runner  │  │
│  │  │   schema.py     │  │  │   (新增)          │  │  ├── cognee_add_    │  │
│  │  │   [数据库提取]   │  │  │   • 增强搜索      │  │  │   runner          │  │
│  │  │                 │  │  │   • 反馈收集      │  │  │   [原有]          │  │
│  │  └── normalize_     │  │  └── ...           │  │  │                 │  │
│  │      for_aof.py     │  │                    │  │  ├── database_      │  │
│  │      [YAML支持]     │  │                    │  │  │   schema_         │  │
│  │                     │  │                    │  │  │   extractor       │  │
│  │                     │  │                    │  │  │   [新增]          │  │
│  │                     │  │                    │  │  │                 │  │
│  │                     │  │                    │  │  ├── enhanced_      │  │
│  │                     │  │                    │  │  │   search          │  │
│  │                     │  │                    │  │  │   [新增]          │  │
│  │                     │  │                    │  │  │                 │  │
│  │                     │  │                    │  │  └── memify_        │  │
│  │                     │  │                    │  │      feedback_      │  │
│  │                     │  │                    │  │      loop           │  │
│  │                     │  │                    │  │      [新增]          │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
│                              │                                                │
└──────────────────────────────┼────────────────────────────────────────────────┘
                               │
                               ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                          Cognee 引擎层                                       │
├─────────────────────────────────────────────────────────────────────────────┤
│                                                                              │
│  ┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐  │
│  │  Database Layer     │  │  Search Layer       │  │  Feedback Layer     │  │
│  │  ├── SqlAlchemy     │  │  ├── Search Types:  │  │  ├── memify         │  │
│  │  │   Adapter        │  │  │   • GRAPH_       │  │  │   [用户会话分析]   │  │
│  │  │   [extract_      │  │  │     COMPLETION   │  │  │                 │  │
│  │  │   schema]        │  │  │   • TEMPORAL     │  │  └── feedback_      │  │
│  │  │                 │  │  │   • CODING_RULES │  │      weights        │  │
│  │  └── Migration      │  │  │   • CYPHER       │  │      [权重学习]     │  │
│  │      Engine         │  │  │   • ...          │  │                    │  │
│  │      [migrate_      │  │  │                 │  │                    │  │
│  │      relational_db] │  │  └── Search         │  │                    │  │
│  │                    │  │      Methods        │  │                    │  │
│  │                    │  │                    │  │                    │  │
│  └─────────────────────┘  └─────────────────────┘  └─────────────────────┘  │
│                                                                              │
│  ┌───────────────────────────────────────────────────────────────────────┐  │
│  │                        Core Pipelines                                 │  │
│  │  • add()      - 数据摄取                                              │  │
│  │  • cognify()  - 知识图谱构建                                          │  │
│  │  • search()   - 语义搜索                                              │  │
│  └───────────────────────────────────────────────────────────────────────┘  │
│                                                                              │
└─────────────────────────────────────────────────────────────────────────────┘
```

## 详细能力说明

### 1. 数据库 Schema 自动提取

**文件**: `bridge/database_schema_extractor.py`, `tools/extract_db_schema.py`

**功能**:
- 直接连接 PostgreSQL/MySQL/SQLite 数据库
- 自动提取表结构、列、主键、外键关系
- 将 Schema 映射为 OWL 本体（Table→Class, Column→Property, FK→Relation）
- 支持合并/替换/差异报告三种模式

**使用**:
```bash
python tools/extract_db_schema.py \
  --db-url "postgresql://user:pass@localhost/db" \
  --output ontologies/db_schema.owl \
  --mode merge
```

**映射规则**:
| 数据库 | OWL |
|--------|-----|
| Table | `owl:Class` (subClassOf DatabaseTable) |
| Column | `owl:DatatypeProperty` |
| Primary Key | `owl:FunctionalProperty` |
| Foreign Key | `owl:ObjectProperty` |

### 2. 增强搜索

**文件**: `bridge/enhanced_search.py`

**功能**:
- 封装 Cognee 全部搜索类型（9+ 种）
- 基于意图的智能搜索类型选择
- 本体指导的检索增强

**搜索类型**:
```python
SearchType = {
    # 基础
    GRAPH_COMPLETION,    # 自然语言 Q&A
    RAG_COMPLETION,      # 传统 RAG
    CHUNKS,             # 文本块检索
    
    # 高级
    GRAPH_COMPLETION_COT,        # Chain-of-Thought
    GRAPH_COMPLETION_CONTEXT_EXT, # 上下文扩展
    TEMPORAL,            # 时序感知
    CODING_RULES,        # 代码规则
    CYPHER,             # 原生图查询
    FEELING_LUCKY,       # 智能选择
}
```

**使用**:
```python
from bridge.enhanced_search import EnhancedSemanticSearch, SearchContext

search = EnhancedSemanticSearch()
result = await search.search(
    query="查询过去30天活跃用户",
    context=SearchContext(user_intent="analytics"),
)
```

### 3. Memify 反馈闭环

**文件**: `bridge/memify_feedback_loop.py`

**功能**:
- 捕获用户交互和反馈
- 分析低分反馈识别缺失的本体元素
- 生成改进建议（add_class, add_property, add_relation）
- 导出 feedback_candidates.jsonl 格式

**工作流程**:
```
User Query → Search → User Rates → Memify Analysis → Ontology Suggestions
```

**使用**:
```python
from bridge.memify_feedback_loop import MemifyFeedbackLoop

feedback_loop = MemifyFeedbackLoop()

# 捕获反馈
await feedback_loop.capture_interaction(
    query="...",
    feedback_score=2,  # 低分
    used_graph_elements={"node_ids": [...]},
)

# 分析并生成建议
result = await feedback_loop.analyze_and_improve_ontology(
    min_feedback_score=3
)
```

### 4. YAML 标注支持

**文件**: `tools/data_adapter/mixed_document_parser.py`

**支持格式**:
```yaml
data:
  - code: |
      SELECT * FROM users
    description: 查询所有用户
    tags: [SQL, query]
    
  - text: "用户行为分析说明"
    metadata:
      topic: analytics
```

**使用**:
```bash
python tools/data_adapter/normalize_for_aof.py \
  --input data/annotations.yaml \
  --output-txt logs/normalized.txt \
  --output-jsonl logs/normalized.jsonl
```

## API 新增端点

### 增强搜索
```
POST /v1/semantic/search/enhanced
{
  "topic": "my_topic",
  "query": "查询活跃用户",
  "search_type": "auto",  // 或 graph_completion, temporal, code 等
  "user_intent": "analytics",
  "top_k": 10
}
```

### 反馈收集
```
POST /v1/feedback/search
{
  "topic": "my_topic",
  "query": "查询活跃用户",
  "search_results": [...],
  "feedback_score": 4,
  "feedback_text": "结果很准确",
  "used_graph_elements": {
    "node_ids": ["..."],
    "edge_ids": ["..."]
  }
}
```

## 依赖要求

```bash
# Cognee 必须安装
pip install -e /path/to/cognee

# YAML 支持 (可选，用于 YAML 标注)
pip install pyyaml
```

## 环境变量

```bash
# 数据库连接
export DATABASE_URL="postgresql://user:pass@localhost/db"

# LLM API (用于高级搜索)
export LLM_API_KEY="sk-..."
export LLM_PROVIDER="custom"
export LLM_MODEL="deepseek/deepseek-chat"

# Cognee 路径
export COGNEE_ROOT="/path/to/cognee"
```

## 下一步计划

1. **本体可视化**: 集成 Cognee 的 visualize 能力
2. **代码图谱**: 支持 codify 代码分析
3. **分布式处理**: 利用 Cognee 的分布式能力
4. **更多数据库**: 支持 Snowflake, BigQuery 等数据仓库
