# AOF 生产化缺口落地计划 v1

最后更新：2026-04-01

## 1. 范围

本计划只覆盖以下 4 个缺口：

1. 可观测性（指标/Tracing/SLO/告警）
2. 组织级知识治理（版本/审批/冲突仲裁）
3. 实时性与在线服务化（低延迟/高并发）
4. Agent 通用编排接口（统一 SDK/协议）

## 2. 已完成（本次落地）

### 2.1 可观测性

- 在 API 增加请求级中间件，采集请求总量、错误量、延迟
- 新增 `/metrics`（Prometheus 文本格式）
- 新增 `/v1/ops/slo`（p50/p95/p99 + error rate 快照）

### 2.2 实时性

- mapping 读取增加内存缓存（TTL，可配置）
- 新增缓存运维端点：
  - `/v1/ops/cache/stats`
  - `/v1/ops/cache/refresh`

### 2.3 组织治理脚手架

- 新增变更申请脚本：
  - `tools/governance/create_change_request.py`
- 新增审批脚本：
  - `tools/governance/approve_change_request.py`
- 支持审计日志写入（审批动作留痕）

### 2.4 Agent 通用协议

- 新增最小 SDK：
  - `sdk/aof_agent_sdk.py`
- 统一流程：`retrieve -> constrain -> generate -> validate`

## 3. 下一步（4-8 周）

### 3.1 可观测性强化

1. 接入 OpenTelemetry tracing
2. 补 Prometheus 告警规则（5xx、p95、构建失败率）
3. 建立 SLO 仪表盘与周报

### 3.2 组织治理强化

1. 变更单与发布流水线打通（无审批不发布）
2. 引入口径冲突仲裁角色（业务 Owner + 数据 Owner）
3. 增加版本兼容性检测（向后兼容检查）

### 3.3 在线服务强化

1. 热点 topic 预热与分级缓存
2. 编译结果缓存（intent + topic 级）
3. 限流与熔断（保护核心端点）

### 3.4 Agent 接口强化

1. SDK 支持异步、重试、超时策略
2. 增加结构化错误码规范
3. 增加多语言 SDK（Python/TypeScript）

## 4. 验收指标

1. `/metrics` 可被监控系统稳定抓取
2. API p95 和 error rate 可在 `/v1/ops/slo` 查看
3. 治理变更单全流程可审计
4. 至少 2 类 Agent 使用统一 SDK 协议完成调用
