# AOF 桥接层设计

最后更新：`2026-09-05`。受治理语义路径是企业知识发布与消费的规范路径；下方原有模块图保留为 Cognee/topic 兼容适配说明。

## P0–P2 规范路径

```text
CLI / FastAPI / MCP
  -> document_parser（白名单解析、标准化、来源上下文）
  -> semantic_core（资源、proposal、release、编译、查询、回放）
  -> ontology_governance（OWL/SKOS/SHACL、Datalog、审批）
  -> decision_provenance（哈希链决策和证据）
  -> release-pinned graph/vector/SQL/Skill/RAG/MCP 投影
```

`SemanticResource` 与 `KnowledgeRelease` 是规范对象。验证、审批、编译、发布要求职责分离；REST/MCP 从签名 principal headers 派生 actor/tenant/roles，不能采信请求体自报身份。旧的 `/v1/semantic/compile` 被显式退役为 HTTP 410，避免绕过策略与审计的裸 SQL 生成。

当前本地实现验证了来源到签名 release 的纵切，并增加 `semantic_core/agentic_system.py` 的查询编排和 `natural_query.py` 的受限词汇解析。生产存储连接器、容量/SLA 与完整目标的剩余事项见 [验收台账](p0-p6-acceptance.md)。

---

## 传统桥接层（兼容路径）

主入口与术语表：[/Users/chaihao/LLM/AOF/docs/architecture/README.md](/Users/chaihao/LLM/AOF/docs/architecture/README.md)

## 设计目标

桥接层（`bridge/`）负责把 AOF 的任务配置与业务语义，映射到 Cognee 能力与中间层流水线，同时保持：

1. 上层调用接口稳定（CLI/API 对桥接细节无感）
2. 业务逻辑尽量集中在可测试模块
3. 运行时错误可定位、可追溯

## 分层与边界

```text
AOF CLI/API
  ├─ aof_add.py / aof_run.py / aof_doctor.py
  └─ services/semantic_middle_layer_api/app.py
            │
            ▼
        bridge/
  ├─ 规格映射: spec_mapper, ontology_adapter
  ├─ 摄取同步: incremental/url/s3/batch/data_sync
  ├─ 检索反馈: enhanced_search/cypher/memify
  ├─ 数据管理: dataset_manager
  ├─ 图能力: graph_visualizer/graph_analytics
  ├─ 运行保障: preflight/quality_gate/errors
  └─ 执行入口: cognee_runner/cognee_add_runner
            │
            ▼
         Cognee 引擎
```

## 模块总览

| 模块 | 职责 | 上游入口 |
|---|---|---|
| `spec_mapper/` | AOF spec -> cognify 参数映射 | `aof_run.py` |
| `ontology_adapter/` | ontology 配置适配 | `aof_run.py` |
| `cognee_runner.py` | 统一执行 cognify | CLI/API |
| `cognee_add_runner.py` | 统一执行 add | CLI/API |
| `incremental_loader.py` | 增量文件变更检测与摄取 | `/v1/ingest/incremental*` |
| `url_ingestion.py` | URL 摄取与历史记录 | `/v1/ingest/url*` |
| `s3_ingestion.py` | S3 文件/前缀摄取与同步 | `/v1/ingest/s3*` |
| `batch_ingestion.py` | 目录发现与批量摄取 | `/v1/ingest/batch*` |
| `data_sync.py` | 同步任务与调度 | `/v1/sync*` |
| `enhanced_search.py` | 检索增强与类型选择 | `/v1/semantic/search/*` |
| `cypher_query.py` | 图查询执行封装 | `/v1/semantic/search/execute` |
| `memify_feedback_loop.py` | 反馈采集与改进建议 | `/v1/feedback/search` |
| `dataset_manager.py` | 数据集列表/状态/删除 | `/v1/datasets*` |
| `graph_visualizer.py` | 图谱 HTML 生成与管理 | `/v1/visualize*` |
| `graph_analytics.py` | 图分析指标计算 | `/v1/analytics*` |
| `preflight.py` | 执行前依赖与环境检查 | CLI |
| `quality_gate/` | 质量门命令编排 | CLI/E2E |
| `errors.py` | 桥接层错误统一封装 | 全链路 |
| `semantic_core/` | 受治理语义资源、发布、编译、查询与连续摄入 | REST/MCP 控制面 |
| `ontology_governance/` | OWL/SKOS/SHACL、草案发布与 Datalog | REST/MCP 控制面 |
| `document_parser/` | 来源受限解析与标准化 | 文档/连续摄入 |
| `decision_provenance.py` | 决策和证据哈希链 | 受治理生命周期 |

## 关键设计原则

1. 薄适配，不重复实现引擎能力  
2. 输入输出结构化（便于日志和回归）  
3. 失败可诊断（error surface 保留根因）  
4. 模块职责单一（便于替换和扩展）

## 与 API 层的关系

`services/semantic_middle_layer_api/app.py` 作为编排器，桥接层作为能力实现。  
截至 2026-03-31，业务端点覆盖：

- ingest: 20
- sync: 5
- analytics: 6
- semantic: 6
- datasets: 4
- visualize: 3
- export: 3
- build/artifacts/feedback/health: 4

## 当前架构风险

1. 传统 topic API 的鉴权和租户隔离仍未完全统一；受治理 API 已使用签名 principal 边界
2. 可观测性以日志为主，缺统一指标/告警
3. 复杂流程依赖外部环境变量，配置治理仍需加强

## 演进建议

1. 引入 API 鉴权中间件与 topic 级别访问控制
2. 增加统一 metrics/tracing 接口，完善 SLO
3. 固化桥接层契约测试，防止模块升级引入回归
