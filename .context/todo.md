# 任务追踪 — 让 AOF 抽提真正走官方链路 (C)

> 开始: 2026-08-26
> 目标: A(走官方链路重做CSO抽提) + B(杜绝穿透+正式ontology注入API)
> 计划: .context/plan/aof-execute-via-official-chain.md

## 阶段 A：真·AOF 链路重做 CSO 抽提
- [x] A0 前置确认：cognee.cognify 需 LLM_API_KEY；.env 里已配 DeepSeek(ollama bge-m3 嵌入)，运行需先加载 .env
- [x] A1 写 cso_spec.json + 临床文本落地 data/cso_clinical/
- [x] A2 写 run_via_aof_chain.py（零 import cognee，走 preflight→run_add→run_cognify→read_graph）
- [x] A3 跑通有/无 ontology 两组：WITH_ONTOLOGY 22nodes/38edges/19临床实体，assert ontology_config 由 bridge 注入 ✅；对照 noonto 19/36/17；result json 已落盘
- [x] A4 追加官方链路验证记录 → README_aof_validation.md（含对"无 ontology 也能抽"的精确认知修正）

## 阶段 B：机制上杜绝穿透 + 正式 ontology 注入 API
- [x] B1 增强 run_add_from_spec：_check_ontology_adherence 校验 spec.ontology + 环境变量穿透警告
- [x] B2 抽取 bridge/ontology_adapter.apply_ontology(spec) 公共 API，cognee_runner._build_ontology_config 复用（兼容既有测试）
- [x] B3 评估：REST/MCP 不消费 cognee 摄取链路（REST 为语义查询层、MCP 为本体治理），无可接入点 → 定为不适用；摄取统一走 CLI/runner + run_via_aof_chain.py 模板
- [x] B4 文档收口：build_graph.py 加"遗留快速验证"banner；run_via_aof_chain.py 作为官方模板
- [x] B5 单测：tests/test_ontology_entry_gate.py 7 用例（adherence gate + apply_ontology）全过，既有 7 用例无回归

## QA
- [x] ruff 干净
- [x] 全量 606 tests 通过（新增 7 个 ontology entry gate 测试，0 回归）
