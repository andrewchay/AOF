# TEST_DATA 对齐报告：miyoushe_loop_auto_demo

- 输入: `/Users/chaihao/LLM/AOF/tools/data_adapter/samples/TEST_DATA_miyoushe_post_metrics_case.sql`
- 规范化TXT: `/Users/chaihao/LLM/AOF/logs/TEST_DATA_normalized_miyoushe_loop_auto_demo_20260326_145040.txt`
- 本体文件: `/Users/chaihao/LLM/AOF/ontologies/TEST_DATA_miyoushe_loop_auto_demo_ontology.owl`
- 状态: `max_iterations_reached`

## 迭代结果

### Iter 1
- dataset: `test_miyoushe_loop_auto_demo_20260326_145040_iter1`
- matched: `21`
- unmatched: `4`
- log: `/Users/chaihao/LLM/cognee/logs/2026-03-26_14-50-58.log`
- unmatched_terms:
  - `database_column` (classes)
  - `create_datetime` (individuals)
  - `item_id` (individuals)
  - `content_tag` (individuals)
- auto_added:
  - `class:DatabaseColumn`
  - `individual:create_datetime:FilterCondition`
  - `individual:item_id:FilterCondition`
  - `individual:content_tag:FilterCondition`

### Iter 2
- dataset: `test_miyoushe_loop_auto_demo_20260326_145040_iter2`
- matched: `12`
- unmatched: `2`
- log: `/Users/chaihao/LLM/cognee/logs/2026-03-26_14-52-17.log`
- unmatched_terms:
  - `mihoyo community` (individuals)
  - `data analysis` (individuals)
- auto_added:
  - `individual:mihoyo_community:CommunityPlatform`
  - `individual:data_analysis:Process`
