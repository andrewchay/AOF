# spec_mapper

职责：把 AOF 任务规格映射为 cognee `cognify/run_custom_pipeline` 参数。

上游调用目标：
- `/Users/chaihao/LLM/cognee/cognee/api/v1/cognify/cognify.py`
- `/Users/chaihao/LLM/cognee/cognee/modules/run_custom_pipeline/run_custom_pipeline.py`

约束：
- 仅做字段映射与默认值填充
- 不实现抽取逻辑
