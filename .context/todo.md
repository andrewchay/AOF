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
- [x] P3-B 前端工程骨架（Vue3+ElementPlus+Vite+pnpm：router/api/stores/layout + 5 视图注册）
- [x] P3-C 核心页面：Dashboard/OKF浏览 + Graph(vis-network)/Ingest(目录+URL摄取)/Agent(OKF检索问答)
- [x] P3-D 前端构建通过 + 三个页面模块编译 + 端点经代理连通
- [x] P3 轻量部署：后端托管 web/dist 单端口访问（SPA fallback 不抢 API）+ run_web.sh 一键启动
- [x] 全量 286 测试无回归
- [ ] P3 提交并 push

## P2: MCP Server - OKF 知识消费工具 (2026-08-07) ✅ 已提交并推送
- [x] 探索现有 mcp_server.py（轻量 JSON-RPC stdio，7 个既有工具，无 OKF 工具）
- [x] 确认知识包定位方式（默认目录 + AOF_OKF_DIR 环境变量可覆盖）
- [x] 确认范围（index/get_concept/search_concepts/lint 四个消费工具，不含导出）
- [x] 在 mcp_server.py 新增 4 个 OKF 知识消费工具 + 目录定位（AOF_OKF_DIR + 默认）
- [x] 顺手修复 mcp_server 既有 ruff 问题（unused field、死代码 dataset）
- [x] mcp_server 的 OKF 工具单测（10 用例）
- [x] ruff + 全量 pytest 验证（266 全过，无回归）+ 协议级 & 端到端 smoke
- [ ] 拆分提交并 push

## P4: 企业文档解析层设计（document_parser）(2026-08-08)
- [x] 评审设计稿（.context 待办决策、引擎选型、架构盲区）
- [x] 对照源码核验：读取 4 条摄取入口 + cognee_add_runner + cognee.add 签名 + 增量加载器 blake2b 指纹
- [x] 发现并修正核心盲区：4 条入口实际均为内联 `cognee.add(path)`，未经 `run_add_from_spec`（原稿第 5.1 节前提不成立）
- [x] 通过 AskUserQuestion 确认插入策略 =「公共 parse_document() 模块 + 逐入口接入」
- [x] 落地 3 项待办决策（PoC 先做 / GPU 待业务给 / 结构化 JSON 产出）
- [x] 定稿 docs/architecture/document-parser-design.md（已标注已评审定稿 + 变更记录）
- [ ] （后续）设计稿提交 git

## P5: document_parser 阶段 1 核心落地 (2026-08-08)
- [x] 确认部署架构：全隔离 subprocess（主 venv 零新增依赖，各引擎独立解释器）
- [x] document_parser 包：core(parse_document + 路由 + 缓存 + 降级) / engine_base / subprocess_runner / cache(blake2b) / config / normalizer / ingest_helper / worker_entry
- [x] docling 主引擎（首选），隔离子进程调用；核心链路冷/热缓存 e2e 验证（冷16.1s→热0.000s）
- [x] 接入 cognee_add_runner.run_add_from_spec + 4 条入口（batch/incremental/s3/url）
- [x] 循证 diff 收敛：入口仅+5行解析逻辑，无格式噪声
- [x] 单测 18 个（routes/cache/parse/imgest_helper）+ 全量 381 通过
- [ ] 阶段1 提交 git

## P5: document_parser 阶段 0 PoC（引擎对比）(2026-08-08)
- [x] 搭独立 Python 3.12 PoC venv（不污染主项目 3.13 venv，清华镜像加速）
- [x] 安装三引擎：docling 2.118.1（主解析 venv）+ unstructured 0.25.2 + mineru 3.4.4（隔离 venv，pipeline backend）
- [x] 写 run_poc/gen_samples 脚本（引擎抽象 + 遍历 + 元数据 + 报告聚合），ruff 通过
- [x] 发现关键工程结论：三引擎不能共享同一 venv（MinerU 需 transformers<5 与 Docling/Unstructured 的 5.x 冲突）；MinerU 为 CLI/FastAPI 架构非 Python API
- [x] 合成样例（中英 PDF/DOCX/XLSX）三条引擎全跑通，中文表格/标题层级/耗时对比定量产出
- [x] Unstructured 中文表格严重扁平化 + 缺 xlsx 依赖（msoffcrypto 已补）→ 退出主引擎候选
- [x] 产出 POC_REPORT.md（主引擎初步建议：Docling 或 MinerU，待真实文档再定）
- [x] gitignore 隔离 data/poc（sampe+run 不入库）；tools/document_parser_poc 可提交
- [x] 真实企业文档到位（8 份：中英 PDF×4 + 扫描件 + PPTX×2 + DOCX + XLSX）
- [x] Docling 全量 8 份真实文档跑通，中文/扫描件/大文档质量优（DOCX 95表/XL?SX 17表）
- [x] MinerU 代表性真实文档（新合创17p/扫描件/XLSX/PPTX，25页批~55s）质量优，图片抽存 56 张
- [x] Unstructured 全量真实文档：PDF 表格全失灵+标题过度分节，确认淘汰
- [x] 更新 POC_REPORT.md：真实文档结论 + 主引擎最终建议（Docling 首选，MinerU 高精增强）
- [x] 主引擎拍板确认（用户接受 Docling 首选 / MinerU 高配）— commit ed43997
- [x] 提交：设计稿 + PoC 工具 + .gitignore + todo（ed43997，7 文件 983 行）
- [x] push 到 origin/codex/internal-edition（ed43997 + RAG 8bbc36a，分支已同步）

## P1: LLM Wiki / OKF 导出器 (2026-08-07) ✅ 已提交并推送
