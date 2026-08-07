# 会话任务追踪 — AOF 市场调研与产品定位

> 开始: 2026-08-07

- [x] 理解 AOF 项目现状（技术栈、架构、未提交改动、Genshin KG 数据集）
- [x] 阅读两份 Agentic OS 附件（企业级通用版 / 小公司轻量版）
- [x] 市场调研：竞品（Dify/RAGFlow/FastGPT/Coze/企业级图谱厂商）+ 新趋势（LLM Wiki / OKF / KAG）
- [x] 输出市场调研报告 → 工作区级 `note.md`
- [x] 通过 AskUserQuestion 确认方向（目标客户=中小企业；四形态全做）
- [x] 产出《产品定位宣言》`product-positioning.md`
- [x] 产出《发展路线图》`roadmap.md`

## 后续可选
- [ ] 验证 LLM Wiki / OKF 导出器原型（Phase 1 最高优先）
- [ ] review 未提交的 training_data 模块并提交

## P3: 产品化 - 知识资产管理 Web 界面 (2026-08-07)
- [x] 探索现有服务层（FastAPI 72 端点，无前端，Node/pnpm 环境就绪）
- [x] 确认范围（Web 界面 + 先出 P3 详细计划）
- [x] 确认前端选型（Vue 3 + Element Plus）
- [x] 产出 P3 详细计划文档（.context/plan/p3-productization.md）
- [x] P3-A 后端 OKF 服务化：抽取 exporters/okf_service.py（共享），新增 /v1/okf/* REST 端点（bundles/index/concept/search/lint/export），重构 mcp_server 复用共享逻辑
- [x] P3-A 测试（okf_service 12 + okf_api 7）+ 全量 285 无回归 + 端到端 TestClient 验证
- [ ] P3-B 前端工程骨架（Vue3+ElementPlus+Vite+pnpm）
- [ ] P3-C 核心页面（Dashboard/Ingest/Graph/OKF浏览/Agent测试）
- [ ] P3-D 前后端联调 + 部署

## P2: MCP Server - OKF 知识消费工具 (2026-08-07) ✅ 已提交并推送
- [x] 探索现有 mcp_server.py（轻量 JSON-RPC stdio，7 个既有工具，无 OKF 工具）
- [x] 确认知识包定位方式（默认目录 + AOF_OKF_DIR 环境变量可覆盖）
- [x] 确认范围（index/get_concept/search_concepts/lint 四个消费工具，不含导出）
- [x] 在 mcp_server.py 新增 4 个 OKF 知识消费工具 + 目录定位（AOF_OKF_DIR + 默认）
- [x] 顺手修复 mcp_server 既有 ruff 问题（unused field、死代码 dataset）
- [x] mcp_server 的 OKF 工具单测（10 用例）
- [x] ruff + 全量 pytest 验证（266 全过，无回归）+ 协议级 & 端到端 smoke
- [ ] 拆分提交并 push

## P1: LLM Wiki / OKF 导出器 (2026-08-07) ✅ 已提交并推送
