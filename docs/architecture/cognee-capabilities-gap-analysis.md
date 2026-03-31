# Cognee 能力与 AOF 实现状态（2026-03-31 快照）

主入口与术语表：[/Users/chaihao/LLM/AOF/docs/architecture/README.md](/Users/chaihao/LLM/AOF/docs/architecture/README.md)

> 本文档用于记录**当前真实实现状态**与剩余差距。  
> 统计口径基于仓库代码与 API 路由（`services/semantic_middle_layer_api/app.py`）。

## 1. 总体状态

- API 路由总数：`55`（含 `/docs`、`/openapi.json` 等系统路由）
- 业务路由总数：`51`
- Bridge 模块数：`11+`（含摄取、同步、搜索、分析、可视化、反馈）
- 自动化测试：`65 passed`（本地 `run_tests.sh`）

## 2. 能力覆盖矩阵（现状）

| 能力类别 | 关键能力 | AOF 状态 | 说明 |
|---|---|---|---|
| 数据摄取 | 本地、批量目录、URL、S3、增量 | ✅ 已实现 | 对应 `/v1/ingest/*` 20 个端点 |
| 知识图谱构建 | add/cognify + 本体构建链路 | ✅ 已实现 | CLI 与 API 均可触发 |
| 搜索查询 | 语义检索、增强检索、编译、评估 | ✅ 已实现 | `/v1/semantic/*` + `/v1/feedback/search` |
| 数据管理 | 数据集列表/状态/数据/清理 | ✅ 已实现 | `/v1/datasets*` |
| 可视化 | 生成、列表、打开 HTML 图谱 | ✅ 已实现 | `/v1/visualize/*` |
| 图分析 | metrics/pagerank/community/centrality/path/statistics | ✅ 已实现 | `/v1/analytics/*` |
| 同步更新 | 手动同步 + 定时同步 + 状态 | ✅ 已实现 | `/v1/sync/*` |
| 导出 | owl/mapping/regression | ✅ 已实现 | `/v1/export/*` |
| 多用户权限 | 用户、租户、细粒度 ACL | ❌ 未实现 | 当前是单租户工程模式 |
| 可观测性 | 指标、追踪、告警、SLO | ⚠️ 部分实现 | 有日志与健康检查，无完整观测体系 |

## 3. 已实现能力（按模块）

### 3.1 摄取与同步

- `bridge/incremental_loader.py`
- `bridge/url_ingestion.py`
- `bridge/s3_ingestion.py`
- `bridge/batch_ingestion.py`
- `bridge/data_sync.py`

### 3.2 搜索与反馈

- `bridge/enhanced_search.py`
- `bridge/cypher_query.py`
- `bridge/memify_feedback_loop.py`

### 3.3 数据管理与可视化

- `bridge/dataset_manager.py`
- `bridge/graph_visualizer.py`
- `bridge/graph_analytics.py`

### 3.4 本体与质量门

- `bridge/spec_mapper/*`
- `bridge/ontology_adapter/*`
- `bridge/quality_gate/*`
- `bridge/preflight.py`

## 4. 当前差距（真正未完成）

### 4.1 多租户与权限控制（高优先级）

当前 API 没有鉴权中间件与租户隔离策略，适合内网/单团队，不适合多团队共享生产环境。

### 4.2 可观测性与告警（高优先级）

目前具备健康检查与运行日志，但缺少：

- 指标标准化（QPS、p95、错误率、构建成功率）
- Trace 链路追踪
- 告警规则与值班流程

### 4.3 CI/CD 门禁深度（中优先级）

已具备基础 CI（lint/test），建议补齐：

- API smoke test（启动服务后真实调用）
- 中间层最小闭环集成测试（ingest -> build -> artifacts）
- Docker 构建与镜像扫描

### 4.4 数据治理规范化（中优先级）

建议补齐 mapping/regression 的版本标记与发布清单自动校验，减少“人工判断是否可发布”。

## 5. 下一阶段路线图（建议）

### Phase A（1-2 周）

1. 增加 API 鉴权（服务内 token 或网关签名）
2. 增加可观测性最小集（Prometheus 指标 + 5xx 告警）
3. 将 smoke test 纳入 CI 阻断门禁

### Phase B（2-4 周）

1. 推进多租户隔离（topic 命名空间与访问边界）
2. 建立发布流水线（构建、扫描、部署、回滚）
3. 建立中间层产物版本化规范（manifest 与 mapping 关联版本）

## 6. 备注

- 历史版本中“数据管理/可视化/批量摄取未实现”的结论已过时。  
- 本文档用于替代旧版差距结论，作为后续架构评审基线。
