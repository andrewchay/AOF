# 任务追踪 — 真实轨迹样本通道进训练导出（RawTrajectory 增强）

> 开始: 2026-08-26
> 状态: ✅ 全部完成
> 目标：让真实企业对话消息 + Agent trajectory（含工具调用）能直接进入训练数据导出，无需"图谱→模板合成"。
> 用户选择：① 对话→SFT 多轮；② 轨迹→SFT reasoning；③ 专用后端 API。

## 设计
- [x] RawTrajectoryLoader：加载原始对话 jsonl / trajectory json
- [x] RawTrajectoryGenerator（遵循 GeneratorBase，覆写 requires_data_loaders=False + bypass_quality_filter=True）：
      - 企业对话(role/content, 兼容 message 字段) → SFTSample 多轮
      - Agent trajectory(含 steps/tool_call) → SFTSample 带推理链
- [x] GeneratorConfig 增加 raw_sources 字段
- [x] pipeline 支持"无图谱/文档后端"运行（needs_loaders 守卫）+ 忠实数据跳过合成质量过滤
- [x] 后端 API：/v1/training-data/generate 支持 generators=["raw"] + raw_sources
- [x] exporter 支持 raw_sources（export / 便捷函数 / _resolve_generators）
- [x] 导出面注册（bridge/training_data/__init__.py）

## 实施
- [x] 核心代码 + 全链路接入
- [x] 单测 13 个（对话→SFT、轨迹→SFT、坏输入、无加载器、max_samples、config注入、require_data_loaders）
- [x] 端到端：exporter 3 样本 / pipeline 单元测试 / **后端 API 200 + 3 样本** ✅
- [x] ruff 通过
- [x] 全量 pytest 619 通过（含新增 13）无回归
- [ ] 沉淀方案文档（进行中）

## 涉及文件
- 新增：bridge/training_data/raw_trajectory.py、tests/test_training_data_raw.py
- 修改：bridge/training_data/models.py（raw_sources）、pipeline.py（needs_loaders+bypass）、generators/base.py（两个 capability 属性）、__init__.py、exporters/training_data_exporter.py、services/.../app.py（API）
