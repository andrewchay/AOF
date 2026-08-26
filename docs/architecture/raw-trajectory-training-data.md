# Real Trajectory Training-Data Channel（真实轨迹样本通道）

> 状态：已实现
> 日期：2026-08-26
> 用途：让 **真实企业对话消息** 和 **Agent 运行轨迹（含工具调用）** 直接进入训练数据导出，
> 无需经过「知识图谱 → 模板合成」链路，忠实保留真实数据中的多轮交互与推理步骤。

## 动机与背景

AOF 既有的三个训练数据生成器（`SFTGenerator` / `RAGEvalGenerator` / `AgentToolGenerator`）
都是「知识图谱节点/文档 chunks → 模板化合成样本」：

- sft：基于节点属性、三元组、邻居子图模拟对话
- agent_tool：基于工具 schema 模板 + 图谱策略节点合成 function calling

它们**都无法**把真实的历史客服对话、真实 Agent 一次运行的工具调用序列作为训练样本。
对于「企业文档之外还要处理企业对话消息 + Agent trajectory for later training」的场景，
这构成能力缺口。本特性补上这条「真实数据 → 训练样本」直达通道。

## 设计决策（用户确认）

| 决策点 | 选择 |
|--------|------|
| 企业对话 → | **SFTSample 多轮**（保留 role 序列为 user/assistant） |
| Agent trajectory → | **SFTSample 带推理链**（把 input→action→tool_call→output 重组为教学推理链） |
| 数据来源接入 | **专用后端 API + config**（`/v1/training-data/generate` 支持 `generators=["raw"]`） |

## 架构

```
原始对话 jsonl / trajectory json
        │
        ▼
RawTrajectoryLoader        —— 纯本地解析，聚合 conversation（按 session）与 trajectory
        │
        ▼
RawTrajectoryGenerator     —— GeneratorBase 子类，产出 SFTSample
        │   ├ 企业对话  → 多轮 messages（customer/user→user，agent/support→assistant）
        │   └ 轨迹      → system + 任务(user) + 各步推理(assistant含工具调用)
        ▼
TrainingDataPipeline       —— 支持「无图谱/文档后端」运行、忠实数据跳过合成质量过滤
        ▼
JSONL 训练样本文件 + 后端 API / v1/training-data/generate
```

## 新增/修改文件

| 文件 | 变更 |
|------|------|
| `bridge/training_data/raw_trajectory.py` | **新增**：`RawTrajectoryLoader` + `RawTrajectoryGenerator` |
| `bridge/training_data/models.py` | `GeneratorConfig` 增加 `raw_sources: list[str]` |
| `bridge/training_data/generators/base.py` | `GeneratorBase` 增加 `requires_data_loaders`、`bypass_quality_filter` 两个能力属性 |
| `bridge/training_data/pipeline.py` | 加载器守卫改造；忠实数据跳过合成质量过滤 |
| `bridge/training_data/__init__.py` | 导出 `RawTrajectoryLoader/Generator` |
| `exporters/training_data_exporter.py` | `export`/便捷函数/`_resolve_generators` 支持 `raw` + `raw_sources` |
| `services/semantic_middle_layer_api/app.py` | `TrainingDataGenerateReq.raw_sources`；`raw` 生成器注入 |

## 输入格式

### 1. 企业对话（jsonl，推荐带 session_id）
```jsonl
{"session_id": "conv-1", "role": "customer", "message": "我的笔记本黑屏了"}
{"session_id": "conv-1", "role": "agent",     "message": "请问电源指示灯亮吗？"}
{"session_id": "conv-1", "role": "customer", "message": "亮着但屏幕黑。"}
```
- 兼容字段名：正文支持 `content` / `message` / `text` / `body`
- 角色映射：`customer/user/human/patient→user`，`agent/support/assistant→assistant`
- 同一 `session_id` 的多条消息会聚合成一个**多轮** SFT 样本

### 2. Agent trajectory（json 对象，必带 steps）
```json
{
  "run_id": "run-1",
  "agent": "inventory-planner",
  "goal": "判断是否需要补货",
  "steps": [
    {"seq":1, "action":"reason", "input":"取库存", "output":"决定查库存",
     "tool_call": {"tool":"query_inventory", "args":{"sku":"A"}}},
    {"seq":2, "action":"decide", "input":"库存不足", "output":"创建采购单",
     "tool_call": {"tool":"create_purchase_order", "args":{"qty":100}}}
  ]
}
```
- 每个 step 的 `input/action/output/tool_call` 重组为带 reasoning 的 assistant 消息
- 一个 run → 一个 SFT 样本（system 说明 + 任务 user + 各步 assistant 推理链）

## 用法

### 方式一：后端 API
```
POST /v1/training-data/generate
{
  "dataset_name": "support-qa",
  "generators": ["raw"],
  "raw_sources": ["/data/conversations/2026-08.jsonl", "/data/agent_runs/r1.json"],
  "max_samples": 500
}
```
- `generators` 可混用：`["raw", "sft"]` 同时生成真实样本 + 图谱合成样本
- 返回 `job_id`，经 `/v1/training-data/jobs/{job_id}` 查询，`/download` 下载

### 方式二：代码 / exporter
```python
from exporters.training_data_exporter import export_dataset_to_training_data

result = await export_dataset_to_training_data(
    dataset_id="demo",
    output_dir="./training_data/",
    generators=["raw"],
    raw_sources=["examples/dialogue_trajectory_poc/data/agent_trajectory.json"],
)
```

## 关键实现点

1. **无后端依赖**：`RawTrajectoryGenerator.requires_data_loaders=False`，因此无需图谱/文档后端即可运行。
2. **忠实保留**：`bypass_quality_filter=True`，跳过为合成样本设计的长度阈值（如 `min_answer_length=10`），
   避免真实且短的对话消息被误杀。
3. **不破坏接口**：通过 `config.raw_sources` 或 `with_sources()` 注入，不改动 `generate()` 签名。

## 测试
- `tests/test_training_data_raw.py`：13 用例（loader 聚合/兼容字段/坏输入、生成器映射/max_samples/config注入、
  could承载 pipeline 无加载器运行）
- 全量 619 测试无回归
- E2E：后端 API `POST /v1/training-data/generate` 返回 200 + 3 个 SFT 样本（2 对话 + 1 轨迹）已验证
