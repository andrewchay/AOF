# middle_layer tools

把上游输入（文档 / 元数据 / 反馈）转成可供多下游消费的语义中间层产物。

## 一键构建

```bash
LLM_API_KEY="<YOUR_KEY>" \
/Users/chaihao/LLM/AOF/tools/middle_layer/run_semantic_middle_layer.sh \
  your_topic \
  /ABS/PATH/docs \
  /ABS/PATH/metadata.json \
  /ABS/PATH/feedback.jsonl \
  3
```

## 核心脚本

- `build_semantic_middle_layer.py`: 总装流程（ontology + mapping + regression + manifest）
- `build_mapping_library.py`: 构建业务 mapping 库
- `build_regression_library.py`: 构建回归样例库
- `build_skill_updates.py`: 生成 skills 更新建议（由反馈与回归自动沉淀）

## 主要输出

- `data/middle_layer/<topic>/mapping/`
- `data/middle_layer/<topic>/regression/`
- `data/middle_layer/<topic>/artifacts/middle_layer_manifest_*.json`
- `data/middle_layer/<topic>/artifacts/skills_update_suggestions_<topic>_<ts>.md`
