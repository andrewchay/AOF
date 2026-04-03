# AOF 与 Cognee 集成架构（2026-03-31）

主入口与术语表：[/Users/chaihao/LLM/AOF/docs/architecture/README.md](/Users/chaihao/LLM/AOF/docs/architecture/README.md)

## 概述

AOF 作为语义中间层，通过 `bridge/` 将 Cognee 的图谱能力封装为稳定的 CLI/API 能力：

- 输入：文档、元数据、反馈
- 输出：OWL、本体对齐结果、mapping 库、regression 样例、可视化与分析结果

## 集成能力矩阵

| 能力 | AOF 实现 | 入口 |
|---|---|---|
| 数据摄取（本地/URL/S3/批量/增量） | `incremental_loader/url_ingestion/s3_ingestion/batch_ingestion` | `/v1/ingest/*` |
| 构建编排 | `cognee_runner`, `tools/middle_layer/*` | `aof_run.py`, `/v1/build/topic` |
| 搜索与执行 | `enhanced_search`, `cypher_query` | `/v1/semantic/*` |
| 反馈闭环 | `memify_feedback_loop` | `/v1/feedback/search` |
| 数据集管理 | `dataset_manager` | `/v1/datasets*` |
| 图谱可视化 | `graph_visualizer` | `/v1/visualize/*` |
| 图分析 | `graph_analytics` | `/v1/analytics/*` |
| 同步调度 | `data_sync` | `/v1/sync/*` |
| 数据库 Schema 到本体 | `database_schema_extractor` | `tools/extract_db_schema.py` |

## 运行时架构

```text
上游输入（docs/metadata/feedback）
        │
        ▼
services/semantic_middle_layer_api (FastAPI)
        │
        ▼
bridge/* (映射、摄取、搜索、同步、分析、反馈)
        │
        ▼
Cognee（add/cognify/search + 图能力）
        │
        ▼
中间层产物（data/middle_layer/<topic>/*）
```

## API 端点分布（业务口径）

- ingest: 20
- sync: 5
- semantic: 6
- feedback: 1
- datasets: 4
- visualize: 3
- analytics: 6
- export: 3
- build/artifacts/health: 3

合计：`51` 个业务端点（系统路由另计）。

## 关键集成设计

1. 桥接层只做“能力适配 + 编排”，不复制引擎核心逻辑
2. 所有中间层结果统一落盘到 `data/middle_layer/<topic>/`
3. 通过 regression 样例与清单（manifest）支撑下游门禁
4. 反馈链路可回流为本体改进候选，形成闭环

## 当前限制

1. 默认单租户模式，尚未内建权限隔离
2. 可观测性以日志为主，指标与追踪需加强
3. 生产化发布依赖外部部署规范，自动化深度可继续提升

## 下一步建议

1. 增加鉴权与租户隔离（topic 级访问边界）
2. 建立可观测性标准（metrics/tracing/alerts）
3. 在 CI 增加 API smoke 与镜像构建门禁
4. 扩展更多数据库与仓储连接器（如 BigQuery/Snowflake）
