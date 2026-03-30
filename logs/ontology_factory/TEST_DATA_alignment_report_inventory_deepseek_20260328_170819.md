# TEST_DATA 对齐报告：inventory_deepseek

- 输入: `/Users/chaihao/LLM/AOF/tools/data_adapter/samples/inventory.sql`
- 规范化TXT: `/Users/chaihao/LLM/AOF/logs/TEST_DATA_normalized_inventory_deepseek_20260328_170819.txt`
- 本体文件: `/Users/chaihao/LLM/AOF/ontologies/TEST_DATA_inventory_deepseek_ontology.owl`
- 状态: `max_iterations_reached`

## 迭代结果

### Iter 1
- dataset: `test_inventory_deepseek_20260328_170819_iter1`
- matched: `1`
- unmatched: `15`
- log: `/Users/chaihao/LLM/cognee/logs/2026-03-28_17-08-46.log`
- unmatched_terms:
  - `table` (classes)
  - `column` (classes)
  - `sku` (individuals)
  - `name` (individuals)
  - `stock` (individuals)
  - `product` (classes)
  - `sku-1` (individuals)
  - `竹简` (individuals)
  - `quantity` (classes)
  - `120` (individuals)
  - `sku-2` (individuals)
  - `帛书` (individuals)
  - `45` (individuals)
  - `operation` (classes)
  - `insert operation` (individuals)
- auto_added:
  - `class:Table`
  - `class:Column`
  - `individual:sku:Process`
  - `individual:name:Process`
  - `individual:stock:Process`
  - `class:Product`
  - `individual:sku_1:Process`
  - `individual:item:Process`
  - `class:Quantity`
  - `individual:120:Process`
  - `individual:sku_2:Process`
  - `individual:45:Process`
  - `class:Operation`
  - `individual:insert_operation:Process`

### Iter 2
- dataset: `test_inventory_deepseek_20260328_170819_iter2`
- matched: `12`
- unmatched: `3`
- log: `/Users/chaihao/LLM/cognee/logs/2026-03-28_17-09-39.log`
- unmatched_terms:
  - `productname` (classes)
  - `竹简` (individuals)
  - `帛书` (individuals)
- auto_added:
  - `class:Productname`
