# TEST_DATA 用户评价清单：miyoushe_loop_auto_demo

- 来源迭代：`Iter 1`
- unmatched 数：`7`
- 已导出草稿：`/Users/chaihao/LLM/AOF/logs/ontology_factory/TEST_DATA_feedback_candidates_miyoushe_loop_auto_demo_20260326_151151.jsonl`

## 待评审项

| # | term | category | 建议草稿 |
|---|---|---|---|
| 1 | `mihoyo community` | `individuals` | `map_term -> TODO_CLASS` |
| 2 | `data` | `classes` | `map_term -> TODO_CLASS` |
| 3 | `test data` | `individuals` | `map_term -> TODO_CLASS` |
| 4 | `date` | `classes` | `map_term -> TODO_CLASS` |
| 5 | `yesterday` | `individuals` | `map_term -> TODO_CLASS` |
| 6 | `daterange` | `classes` | `map_term -> TODO_CLASS` |
| 7 | `last 30 days` | `individuals` | `map_term -> TODO_CLASS` |

## 使用方式

1. 编辑 JSONL 中每一行，把 `TODO_CLASS` 改成你确认的类。
2. 如需新增类/关系，改成 `add_class`/`add_relation`/`add_individual` 动作。
3. 下次运行 ontology factory 时传 `--feedback-jsonl` 即可自动注入。
