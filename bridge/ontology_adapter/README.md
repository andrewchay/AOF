# ontology_adapter

职责：把 AOF 本体配置映射为 cognee `ontology_resolver` 配置对象。

上游调用目标：
- `/Users/chaihao/LLM/cognee/cognee/modules/ontology/rdf_xml/RDFLibOntologyResolver.py`
- `/Users/chaihao/LLM/cognee/cognee/modules/ontology/matching_strategies.py`

约束：
- 只做 resolver 构造与 config 封装
- 不改写 resolver 内部实现
