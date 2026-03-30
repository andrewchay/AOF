# Data Adapter（数据库导出适配层）

把数据库导出文件标准化为 AOF 可 ingest 的文本文件。

## 支持输入

- CSV（`.csv`）
- JSON（`.json` / `.jsonl`）
- SQL（`.sql`，当前轻量支持 `INSERT INTO ... VALUES ...` 与 `SELECT ...` 语句块）

## 输出

- `--output-txt`：AOF 可直接用 `--data-path` 输入
- `--output-jsonl`：保留结构化追溯信息（可选）

## 用法

```bash
python3 /Users/chaihao/LLM/AOF/tools/data_adapter/normalize_for_aof.py \
  --input /ABS/PATH/input.csv \
  --kind auto \
  --output-txt /ABS/PATH/normalized.txt \
  --output-jsonl /ABS/PATH/normalized.jsonl
```

## 示例

```bash
python3 /Users/chaihao/LLM/AOF/tools/data_adapter/normalize_for_aof.py \
  --input /Users/chaihao/LLM/AOF/tools/data_adapter/samples/customers.csv \
  --output-txt /Users/chaihao/LLM/AOF/logs/normalized_customers.txt \
  --output-jsonl /Users/chaihao/LLM/AOF/logs/normalized_customers.jsonl
```

生成后可直接接 AOF：

```bash
/Users/chaihao/LLM/AOF/.venv/bin/python /Users/chaihao/LLM/AOF/aof_add.py \
  --spec /ABS/PATH/your_spec.json \
  --data-path /ABS/PATH/normalized_customers.txt \
  --skip-preflight
```

## 说明

- SQL 适配是模板级别，适合快速起步；复杂 SQL 建议先导出 CSV/JSON 再接入。
- 若只想快速验证链路，建议与 `MOCK_EMBEDDING=true` 联用。
- 示例目录里若文件名以 `TEST_DATA_` 开头，表示仅用于测试/案例，不代表生产数据口径。
