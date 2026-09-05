# AOF 通用本体抽提 SOP（文档 + 数据库）

> 适用性更新（2026-09-05）：本 SOP 是 Cognee/传统抽提兼容路径，可用于探索和候选资产生成；它不是受治理知识发布流程。对企业生产知识，完成本 SOP 后还必须走 `source snapshot -> SemanticResource -> proposal -> validate -> approve -> compile -> signed release -> release-pinned consumption`。

受治理主路径的具体协议见 [语义中间层接口契约](语义中间层接口契约.md) 和 [API 参考](../services/semantic_middle_layer_api/API.md)。未经验证/审批的 OWL、mapping、回归产物不得作为生产 graph、vector、SQL 或 Skill 的真相源。

## 1. 目标

把任意 topic 的原始资料（文档、数据库导出、结构化表）接入 AOF，完成：

1. 数据加入知识引擎（`add`）
2. 结合本体约束进行抽取与图谱化（`cognify`）
3. 产出可追溯运行结果（`logs/*.json`）

## 2. 输入要求

1. 文档输入：`txt/md/pdf/csv/json` 等可被 cognee loader 识别的格式
2. 数据库输入：建议先导出为 `csv/json/sql` 文件，再按文件路径接入
3. 本体输入：OWL/RDF 文件路径（`ontology.file`）

数据库导出可先过适配层：

```bash
python3 /Users/chaihao/LLM/AOF/tools/data_adapter/normalize_for_aof.py \
  --input /ABS/PATH/db_export.csv \
  --output-txt /ABS/PATH/normalized.txt \
  --output-jsonl /ABS/PATH/normalized.jsonl
```

## 3. 最小配置

复制并改造 spec：

```json
{
  "project_root": "/Users/chaihao/LLM/AOF",
  "dataset": "your_dataset_name",
  "runtime": {
    "run_in_background": false,
    "incremental_loading": true,
    "data_per_batch": 20,
    "retries": 0,
    "backoff_seconds": 1.0
  },
  "ontology": {
    "file": "/ABS/PATH/your_ontology.owl",
    "matching_cutoff": 0.8
  },
  "cognee": {
    "root": "/Users/chaihao/LLM/cognee"
  }
}
```

## 4. 执行顺序（生产链路）

### 4.1 环境体检

```bash
LLM_API_KEY="YOUR_KEY" \
LLM_PROVIDER="custom" \
LLM_MODEL="deepseek/deepseek-chat" \
LLM_ENDPOINT="https://api.deepseek.com/v1" \
EMBEDDING_PROVIDER="custom" \
EMBEDDING_MODEL="deepseek/deepseek-embedding" \
EMBEDDING_ENDPOINT="https://api.deepseek.com/v1" \
/Users/chaihao/LLM/AOF/.venv/bin/python /Users/chaihao/LLM/AOF/aof_doctor.py \
  --spec /ABS/PATH/your_spec.json --require-api-key
```

### 4.2 加载数据

```bash
LLM_API_KEY="YOUR_KEY" \
LLM_PROVIDER="custom" \
LLM_MODEL="deepseek/deepseek-chat" \
LLM_ENDPOINT="https://api.deepseek.com/v1" \
EMBEDDING_PROVIDER="custom" \
EMBEDDING_MODEL="deepseek/deepseek-embedding" \
EMBEDDING_ENDPOINT="https://api.deepseek.com/v1" \
COGNEE_SKIP_CONNECTION_TEST="true" \
/Users/chaihao/LLM/AOF/.venv/bin/python /Users/chaihao/LLM/AOF/aof_add.py \
  --spec /ABS/PATH/your_spec.json \
  --data-path /ABS/PATH/your_data_file_or_dir \
  --skip-preflight \
  --result-file /Users/chaihao/LLM/AOF/logs/aof_add_result.your_dataset.json
```

### 4.3 本体约束抽取

```bash
LLM_API_KEY="YOUR_KEY" \
LLM_PROVIDER="custom" \
LLM_MODEL="deepseek/deepseek-chat" \
LLM_ENDPOINT="https://api.deepseek.com/v1" \
EMBEDDING_PROVIDER="custom" \
EMBEDDING_MODEL="deepseek/deepseek-embedding" \
EMBEDDING_ENDPOINT="https://api.deepseek.com/v1" \
COGNEE_SKIP_CONNECTION_TEST="true" \
/Users/chaihao/LLM/AOF/.venv/bin/python /Users/chaihao/LLM/AOF/aof_run.py \
  --spec /ABS/PATH/your_spec.json \
  --run-cognify --skip-preflight \
  --result-file /Users/chaihao/LLM/AOF/logs/aof_run_result.your_dataset.json
```

## 5. 验收标准

1. `aof_add` 结果中 `stages.add` 为 `PipelineRunCompleted`
2. `aof_run --run-cognify` 结果中 `stages.cognify.ok=true`
3. 日志中可见抽取任务完成（`extract_graph_from_data` completed）

## 6. 常见问题

1. `LLM Provider NOT provided`：`LLM_MODEL` 缺少前缀，改为 `deepseek/deepseek-chat`
2. `LLM_API_KEY is not set`：未传 `LLM_API_KEY`
3. 结果写入失败：AOF 已支持自动回落 `/tmp`
4. `Dataset ... already completed`：换新的 `dataset` 名称强制新跑

## 7. 快速验证模式（非生产）

若只验证链路结构，可临时加：

```bash
MOCK_EMBEDDING="true"
```

这会跳过真实 embedding 调用，仅用于流程验证，不用于最终效果评估。

## 8. DeepSeek 真实 embedding 现状说明

在当前组合（`cognee + litellm + deepseek`）下，`deepseek/deepseek-embedding` 可能出现 provider route 映射错误（`Unmapped LLM provider`）。

建议：

1. 日常开发与抽取流程验证：默认 `MOCK_EMBEDDING=true`
2. 生产前：单独验证可用的 embedding provider/model，再切换到真实 embedding
