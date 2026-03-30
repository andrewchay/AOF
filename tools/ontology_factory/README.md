# Ontology Factory（TEST_DATA）

把任意 topic 的 SQL/文档输入，自动执行：

1. 规范化输入（复用 `tools/data_adapter/normalize_for_aof.py`）
2. 生成最小本体 `TEST_DATA_<topic>_ontology.owl`
3. 运行 AOF 对齐迭代（可选）
4. 产出对齐报告（JSON + Markdown）
5. 输出模式分析（JSON + Markdown）
6. 自动导出用户评价清单（feedback candidates JSONL + Markdown）

并支持用户反馈补丁：

- 你可以提供 `feedback.jsonl`，在每轮对齐前注入“用户评价修正”。
- 形成闭环：打标 -> 用户评价 -> 迭代 -> 总结。

## 一键命令

```bash
LLM_API_KEY="<YOUR_KEY>" \
/Users/chaihao/LLM/AOF/tools/ontology_factory/run_testdata_ontology_factory.sh \
  /ABS/PATH/your_input.sql \
  your_topic \
  4
```

## 闭环统一入口（推荐）

```bash
LLM_API_KEY="<YOUR_KEY>" \
/Users/chaihao/LLM/AOF/tools/ontology_factory/run_agentic_ontology_loop.sh \
  /ABS/PATH/your_input.sql \
  your_topic \
  4 \
  AUTO
```

说明：
- 第4参可选：`AUTO` / `/ABS/PATH/feedback.jsonl`
- `AUTO` 会自动尝试续用该 topic 最近一次非空反馈清单
- 每次执行结束都会打印本轮 `report_json` 和 `feedback_jsonl` 路径


参数：

- 第1参：输入文件（sql/csv/json）
- 第2参：topic（可选）
- 第3参：最大迭代次数（可选，默认4）

如果未设置 `LLM_API_KEY`，命令会自动切到 `--skip-align`，只生成本体不跑对齐。

## 直接用 Python 脚本

```bash
/Users/chaihao/LLM/AOF/.venv/bin/python \
  /Users/chaihao/LLM/AOF/tools/ontology_factory/build_testdata_ontology_factory.py \
  --input /ABS/PATH/your_input.sql \
  --topic your_topic \
  --max-iterations 4 \
  --feedback-jsonl /ABS/PATH/feedback.jsonl
```

反馈模板：

`/Users/chaihao/LLM/AOF/tools/ontology_factory/TEST_DATA_feedback_template.jsonl`

## 产物位置

- 本体：`/Users/chaihao/LLM/AOF/ontologies/TEST_DATA_<topic>_ontology.owl`
- 规范化输入：`/Users/chaihao/LLM/AOF/logs/TEST_DATA_normalized_<topic>_<timestamp>.txt/.jsonl`
- 报告：`/Users/chaihao/LLM/AOF/logs/ontology_factory/TEST_DATA_alignment_report_<topic>_<timestamp>.json/.md`
- 模式分析：`/Users/chaihao/LLM/AOF/logs/ontology_factory/TEST_DATA_alignment_patterns_<topic>_<timestamp>.json/.md`
- 用户评价清单：`/Users/chaihao/LLM/AOF/logs/ontology_factory/TEST_DATA_feedback_candidates_<topic>_<timestamp>.jsonl/.md`

## 报告内容

每轮迭代会记录：

- `matched_count`
- `unmatched_count`
- `unmatched terms`
- `auto_added`（根据未匹配自动补进本体的概念）

## 实现说明

以下模块基于 AOF 方法论实现：

- `analyze_alignment_patterns.py`：对齐模式分析，基于质量控制方法论
- `apply_feedback_patches.py`：反馈补丁应用，基于迭代工作流方法论
- `export_feedback_candidates.py`：反馈候选导出，基于标注体系设计方法论

详见 `docs/internal/methodology/` 相关文档。
