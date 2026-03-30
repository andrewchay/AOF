# TEST_DATA 对齐报告：miyoushe_factory_demo

- 输入: `/Users/chaihao/LLM/AOF/tools/data_adapter/samples/TEST_DATA_miyoushe_post_metrics_case.sql`
- 规范化TXT: `/Users/chaihao/LLM/AOF/logs/TEST_DATA_normalized_miyoushe_factory_demo_20260325_160719.txt`
- 本体文件: `/Users/chaihao/LLM/AOF/ontologies/TEST_DATA_miyoushe_factory_demo_ontology.owl`
- 状态: `aligned`

## 迭代结果

### Iter 1
- dataset: `test_miyoushe_factory_demo_20260325_160719_iter1`
- matched: `14`
- unmatched: `3`
- log: `/Users/chaihao/LLM/cognee/logs/2026-03-25_16-07-35.log`
- unmatched_terms:
  - `mihoyo community` (individuals)
  - `datacategory` (classes)
  - `test data` (individuals)
- auto_added:
  - `individual:mihoyo_community:CommunityPlatform`
  - `class:Datacategory`
  - `individual:test_data:Process`

### Iter 2
- dataset: `test_miyoushe_factory_demo_20260325_160719_iter2`
- matched: `15`
- unmatched: `0`
- log: `/Users/chaihao/LLM/cognee/logs/2026-03-25_16-08-38.log`
