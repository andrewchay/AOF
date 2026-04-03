# Changelog

所有对 AOF 项目的重大变更都将记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.0.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

---

## [Unreleased]

### 计划中
- [ ] 实时图谱更新（WebSocket 推送）
- [ ] 图谱版本控制（时间旅行）
- [ ] 联邦查询（跨多个图谱查询）
- [ ] GraphQL 接口支持
- [ ] 图谱数据质量评估

---

## [2.0.0] - 2026-04-01

### 🎉 重大更新

AOF 2.0 是一个企业级版本，引入了完整的权限管理、多租户支持、性能优化和分布式存储能力。

### 🔐 安全与权限

#### 新增
- **RBAC 权限系统** (`bridge/auth/`)
  - 4 级角色：admin/owner/editor/viewer
  - 资源级授权（dataset/ontology/system/audit）
  - 权限继承与委派
  
- **多租户隔离** (`bridge/tenant/`)
  - 租户级 GraphSpace 隔离（NebulaGraph）
  - 资源配额管理（数据集数量、图谱节点数、并发查询）
  - 租户生命周期管理（active/suspended/deleted）
  
- **认证机制**
  - JWT Token 认证（access/refresh token）
  - API Key 认证（服务间调用）
  - 请求签名验证（HMAC-SHA256）

- **审计日志** (`bridge/audit/`)
  - 5 级日志级别（DEBUG/INFO/WARNING/ERROR/CRITICAL）
  - 敏感数据自动脱敏（password/token/api_key）
  - 多格式导出（JSON/CSV/HTML/PDF）
  - 异常行为检测

### ⚡ 性能优化

#### 新增
- **多级缓存体系** (`bridge/cache/`)
  - L1：进程内 LRU 缓存（热数据）
  - L2：Redis 分布式缓存（跨进程共享）
  - L3：物化视图（预计算结果）
  - 防击穿保护
  
- **弹性组件** (`bridge/resilience/`)
  - 限流器：令牌桶（允许突发）、滑动窗口（精确计数）
  - 熔断器：CLOSED/OPEN/HALF_OPEN 三态自动切换
  - 重试策略：指数退避 + 抖动
  
- **异步任务队列** (`bridge/tasks/`)
  - 优先级队列（4 级优先级）
  - 状态追踪（pending/queued/running/success/failure/cancelled）
  - 实时进度更新（0-100%）
  - 自动重试机制

### 🏭 存储层扩展

#### 新增
- **存储抽象层** (`bridge/storage/`)
  - `GraphBackend` 统一接口
  - 支持 Cognee（默认）和 NebulaGraph（分布式）
  - 工厂模式动态切换
  
- **NebulaGraph 后端**
  - 完整 CRUD 支持
  - nGQL 查询构建器（流畅 API）
  - Cypher → nGQL 自动转换
  - 连接池管理
  - Schema 自动创建

### ☸️ 云原生部署

#### 新增
- **Kubernetes 部署配置** (`k8s/`)
  - Namespace、ConfigMap、Secret
  - Deployment（支持 HPA 自动扩缩容）
  - Service（ClusterIP + Headless）
  - Ingress（Nginx + TLS）
  - NebulaGraph 集群部署
  - Redis 缓存部署
  - Prometheus 监控告警

### 📊 测试覆盖

#### 新增
- 203 个单元测试，全部通过
- 核心模块测试覆盖率 > 90%
- 集成测试支持

### 🔧 改进

- 重构了项目结构，按功能模块化
- 优化了 API 响应时间（缓存层减少 60% 数据库查询）
- 提升了并发处理能力（限流器保护）
- 增强了系统稳定性（熔断器自动恢复）

### 📚 文档

- 更新了 README.md，添加企业级功能介绍
- 新增 ARCHITECTURE.md，详细架构设计文档
- 新增 CHANGELOG.md，版本变更记录
- 完善了 API 文档

---

## [1.0.0] - 2026-03-01

### 🎉 初始版本

AOF 1.0 是第一个稳定版本，提供完整的知识图谱构建、管理和分析能力。

### ✨ 功能

#### 数据摄取
- 本地文件批量摄取
- URL 网络数据摄取
- S3 云存储摄取
- 数据库 Schema 提取
- Jupyter/Markdown 混合文档摄取

#### 数据同步
- 增量更新（Blake2b 指纹）
- 定时同步调度
- 变更追踪（新增/修改/删除/重命名）

#### 知识图谱分析
- PageRank 节点重要性
- 社区检测（Louvain/标签传播/贪心模块度）
- 中心性分析（度/介数/接近/特征向量）
- 路径分析（最短路径）
- 统计指标（密度/直径/聚类系数）

#### 智能搜索
- 14 种搜索类型
  - 基础：graph_completion, rag_completion, chunks, summaries
  - 高级：graph_completion_cot, temporal, code, cypher
  - 探索：feeling_lucky, natural_language, triplet_completion

#### 企业级功能
- 5 层质量门（L1-L5）
- 语义中间层
- 反馈闭环（Memify）
- 交互式可视化

#### API 服务
- 51 个 REST API 端点
- FastAPI 框架
- 自动生成的 OpenAPI 文档

### 🏗️ 技术栈

- **后端**：Python 3.13, FastAPI
- **图谱引擎**：Cognee
- **分析**：NetworkX
- **存储**：SQLite（默认）
- **部署**：Docker

---

## 版本对比

| 特性 | v1.0 | v2.0 |
|------|------|------|
| 数据摄取 | ✅ | ✅ |
| 图谱分析 | ✅ | ✅ |
| 智能搜索 | ✅ | ✅ |
| API 服务 | ✅ | ✅ |
| RBAC 权限 | ❌ | ✅ |
| 多租户 | ❌ | ✅ |
| 审计日志 | ❌ | ✅ |
| 多级缓存 | ❌ | ✅ |
| 限流熔断 | ❌ | ✅ |
| 异步任务 | ❌ | ✅ |
| 分布式存储 | ❌ | ✅ |
| K8s 部署 | ❌ | ✅ |
| 测试数量 | ~50 | 203 |

---

## 升级指南

### 从 v1.0 升级到 v2.0

1. **备份数据**
   ```bash
   cp -r data/ data_backup/
   ```

2. **更新代码**
   ```bash
   git pull origin main
   ```

3. **安装新依赖**
   ```bash
   uv pip install -r requirements.txt
   ```

4. **配置环境变量**
   ```bash
   # 新增配置
   export STORAGE_BACKEND="cognee"  # 或 "nebula"
   export JWT_SECRET_KEY="your-secret"
   export REDIS_URL="redis://localhost:6379/0"
   ```

5. **初始化数据库**
   ```bash
   python tools/auth/init_roles.py --system
   ```

6. **验证安装**
   ```bash
   python -m pytest tests/ -v
   ```

---

## 兼容性说明

- v2.0 完全兼容 v1.0 的 API 接口
- 存储格式需要迁移（提供工具脚本）
- 配置文件格式有变化（参考文档更新）

---

## 贡献者

感谢所有为 AOF 项目做出贡献的开发者！

---

## 链接

- [项目主页](https://github.com/your-org/aof)
- [文档](https://docs.aof.example.com)
- [问题反馈](https://github.com/your-org/aof/issues)
- [CHANGELOG](CHANGELOG.md)
