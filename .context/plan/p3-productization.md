# P3 产品化计划：知识资产管理 Web 界面

> 日期: 2026-08-07
> 状态: 待审批
> 技术选型: Vue 3 + Element Plus + Vite + pnpm（前端）| FastAPI（后端）

## 一、背景与目标

AOF 已完成 Agent-Ready 知识资产引擎的实验能力（P1 OKF 导出器 + Lint，P2 MCP 知识消费工具），但都是以 API/MCP 形式存在，非技术人员用不了。P3 目标是落地一个**知识资产管理 Web 界面**，让用户：
1. 上传/摄取企业知识 → 自动构建知识图谱与多形态资产
2. 可视化浏览 OKF 知识包（图谱 + LLM Wiki）
3. 通过对话测试台验证 Agent 消费知识的能力

目标用户：中小企业团队（知识运维人员、业务分析师、管理层）。

## 二、现状盘点

- **后端**：FastAPI（`services/semantic_middle_layer_api/app.py`），72 个 REST 端点，覆盖摄取/图谱/搜索/分析/训练数据/可视化。
- **前端**：无现代框架；仅有纯 HTML 图谱可视化模板（`visualization/*.html`，基于 vis-network）。
- **OKF 对接**：P1/P2 的 OKF 能力在 `exporters` / `tools` / `mcp_server.py`，**尚未暴露为 REST 端点**（Web UI 需要）。
- **环境**：Node v24、pnpm 10、npm 11、yarn 4 均可用。

## 三、技术选型（已确认）

| 层 | 选型 | 理由 |
|----|------|------|
| 前端框架 | **Vue 3** (Composition API) | 中后台生态成熟，模板直接 |
| UI 组件库 | **Element Plus** | 表格/表单/上传/布局齐备 |
| 构建工具 | **Vite** + pnpm | 快、pnpm 已装 |
| 状态管理 | Pinia | Vue 3 官方推荐 |
| 路由 | Vue Router 4 | 标准路由 |
| HTTP | Axios | 标准 |
| 图谱可视化 | vis-network (复用) / ECharts | 组件化封装现有能力 |

## 四、目录结构（新增 `web/`）

```
web/
  package.json
  vite.config.ts
  index.html
  src/
    main.ts
    App.vue
    router/index.ts
    stores/           # Pinia
      assets.ts
      okf.ts
      agent.ts
    api/              # Axios 封装
      client.ts
      assets.ts
      okf.ts
      agent.ts
    views/
      DashboardView.vue     # 资产总览
      IngestView.vue        # 知识摄取向导
      GraphView.vue         # 知识图谱可视化
      OkfBrowseView.vue     # OKF 知识包浏览（LLM Wiki）
      AgentTestView.vue     # Agent 对话测试台
    components/
      GraphCanvas.vue
      ConceptPanel.vue
      MetricCard.vue
```

## 五、页面与信息架构

| 页面 | 功能 | 对接 API |
|------|------|---------|
| **Dashboard** | 资产统计卡片、最近摄取、系统健康 | `/v1/analytics/statistics`, `/healthz`, `/v1/graph/statistics` |
| **Ingest 摄取** | 上传文档/URL/S3，创建数据集，触发 cognify | `/v1/ingest/batch`, `/v1/ingest/url`, `/v1/datasets` |
| **Graph 图谱** | 交互式图谱浏览、节点搜索、社区/PageRank | `/v1/graph/nodes`, `/v1/graph/edges`, `/v1/analytics/*` |
| **OKF 浏览** | 浏览/搜索知识包，读 Concept，Lint 体检 | **新增** `/v1/okf/*` |
| **Agent 测试** | 选知识包，对话问答，看 Agent 检索溯源 | `/v1/semantic/retrieve`, MCP 能力复用 |

## 六、后端需新增的能力（P3 的关键工程）

当前 OKF/MCP 能力未进 REST 层，Web UI 需要补齐：

| 新增 REST 端点 | 功能 |
|---------------|------|
| `GET /v1/okf/bundles` | 列出可用知识包（目录） |
| `GET /v1/okf/bundles/{name}/index` | 读 index.md 渐进式目录 |
| `GET /v1/okf/bundles/{name}/concept` ?path= | 读单 Concept（**复用 mcp_server 的路径穿越防护逻辑**） |
| `GET /v1/okf/bundles/{name}/search` ?query=&type= | 搜索 Concept |
| `POST /v1/okf/bundles/{name}/lint` | 运行 Lint 体检 |
| `POST /v1/export/okf` | 把数据集导出为 OKF 包（对 Web UI 的创建动作） |

> 建议：把 `mcp_server.py` 中可复用的 OKF 读取/搜索/防护逻辑**抽到共享模块**（如 `bridge/okf/` 或 `exporters/okf_service.py`），让 REST 与 MCP 复用同一套知识消费逻辑，避免重复。

## 七、分阶段实施

### P3-A 后端 OKF 服务化（先行，约 1 周）
1. 抽取 `mcp_server.py` 的 OKF 工具逻辑为共享模块 `exporters/okf_service.py`
2. 在 FastAPI app.py 新增 `/v1/okf/*` 与 `/v1/export/okf` 端点
3. 单测 + ruff

### P3-B 前端工程骨架（约 1-2 天）
4. `pnpm create vite` 初始化 `web/`，Vue3+TS+ElementPlus+Vite
5. 配 Axios（dev proxy 到 FastAPI 8787）、Pinia、Router
6. 基础布局（侧边栏 + 顶栏 + 内容区）

### P3-C 核心页面（约 1-2 周）
7. Dashboard 统计卡片
8. Ingest 摄取向导（上传表单）
9. Graph 图谱可视化（封装 vis-network）
10. OKF 浏览页（浏览/搜索/读 Concept/Lint）
11. Agent 对话测试台（结合 semantic 检索）

### P3-D 联调与打磨（约 1 周）
12. 前后端联调
13. 空态/加载/错误处理
14. 轻量部署（`web/` 构建产物由 FastAPI 托管，或独立起静态服务）

## 八、与轻量版/企业版衔接

- 本次聚焦**单机/中小团队可用的完整 Web 界面**（对齐轻量版 Agentic OS）。
- 企业版的多租户/RBAC 后续再叠加（现有 auth 层已具备，可平滑接入）。
- 知识资产化的核心（OKF/MCP）已就绪，Web 界面是"让非技术用户用起来"的最后一块。

## 九、成功标准
- 用户能通过 Web 界面完成：上传知识 → 构建图谱 → 浏览 OKF 知识包 → 对话测试 Agent 检索。
- OKF REST 端点与 MCP 复用同一套逻辑，无重复实现。
- 前后端联调通过，全部测试无回归。

## 十、风险
- 图谱可视化性能（大图需采样/聚合）—— 默认限制节点数，提供分页/搜索下钻。
- OKF REST 端点设计需与 MCP 对齐 —— 通过共享 service 消除漂移。
