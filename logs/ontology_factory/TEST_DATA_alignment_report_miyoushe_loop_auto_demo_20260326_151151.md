# TEST_DATA 对齐报告：miyoushe_loop_auto_demo

- 输入: `/Users/chaihao/LLM/AOF/tools/data_adapter/samples/TEST_DATA_miyoushe_post_metrics_case.sql`
- 规范化TXT: `/Users/chaihao/LLM/AOF/logs/TEST_DATA_normalized_miyoushe_loop_auto_demo_20260326_151151.txt`
- 本体文件: `/Users/chaihao/LLM/AOF/ontologies/TEST_DATA_miyoushe_loop_auto_demo_ontology.owl`
- 状态: `max_iterations_reached`

## 迭代结果

### Iter 1
- dataset: `test_miyoushe_loop_auto_demo_20260326_151151_iter1`
- matched: `11`
- unmatched: `7`
- log: `/Users/chaihao/LLM/cognee/logs/2026-03-26_15-12-04.log`
- unmatched_terms:
  - `mihoyo community` (individuals)
  - `data` (classes)
  - `test data` (individuals)
  - `date` (classes)
  - `yesterday` (individuals)
  - `daterange` (classes)
  - `last 30 days` (individuals)
- auto_added:
  - `class:Data`
  - `individual:test_data:Process`
  - `class:Date`
  - `individual:yesterday:Process`
  - `class:Daterange`
  - `individual:last_30_days:Process`
