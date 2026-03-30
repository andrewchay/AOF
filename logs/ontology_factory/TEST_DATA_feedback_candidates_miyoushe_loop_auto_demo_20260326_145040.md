# TEST_DATA 用户评价清单：miyoushe_loop_auto_demo

- 来源迭代：`Iter 2`
- unmatched 数：`2`
- 已导出草稿：`/Users/chaihao/LLM/AOF/logs/ontology_factory/TEST_DATA_feedback_candidates_miyoushe_loop_auto_demo_20260326_145040.jsonl`

## 待评审项

| # | term | category | 建议草稿 |
|---|---|---|---|
| 1 | `mihoyo community` | `individuals` | `map_term -> TODO_CLASS` |
| 2 | `data analysis` | `individuals` | `map_term -> TODO_CLASS` |

## 使用方式

1. 编辑 JSONL 中每一行，把 `TODO_CLASS` 改成你确认的类。
2. 如需新增类/关系，改成 `add_class`/`add_relation`/`add_individual` 动作。
3. 下次运行 ontology factory 时传 `--feedback-jsonl` 即可自动注入。
