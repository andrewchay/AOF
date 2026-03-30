# AOF 桥接层设计

## 设计目标

在 AOF spec 与外部执行引擎（cognee）之间建立最薄的适配层，确保：
1. AOF 保持配置驱动的简洁接口
2. 业务逻辑由执行引擎承载
3. 桥接层只做参数映射与调用编排

## 架构边界

```
┌─────────────────────────────────────────────────────────────┐
│                         AOF 层                              │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────────────┐  │
│  │ aof_run  │  │ aof_add  │  │ build_testdata_ontology  │  │
│  └────┬─────┘  └────┬─────┘  └───────────┬──────────────┘  │
│       │             │                    │                 │
│       └─────────────┴────────────────────┘                 │
│                       │                                     │
│                  ┌────┴────┐                               │
│                  │ bridge/ │  ← 本层                       │
│                  └────┬────┘                               │
└───────────────────────┼─────────────────────────────────────┘
                        │
┌───────────────────────┼─────────────────────────────────────┐
│                       │         Cognee 层                   │
│                  ┌────┴────┐                                │
│                  │ cognee  │  ← 外部引擎                    │
│                  │  .add() │                                │
│                  │.cognify()│                                │
│                  └─────────┘                                │
└─────────────────────────────────────────────────────────────┘
```

## 核心模块

### spec_mapper
- **文件**: `bridge/spec_mapper/mapper.py`
- **职责**: 将 AOF spec JSON 映射为 cognee 参数
- **输入**: AOF spec (dataset, runtime, ontology)
- **输出**: cognee.cognify() 关键字参数

### ontology_adapter
- **文件**: `bridge/ontology_adapter/adapter.py`
- **职责**: 构建 cognee 兼容的本体配置
- **输入**: ontology file path, matching cutoff
- **输出**: cognee Config 对象

### preflight
- **文件**: `bridge/preflight.py`
- **职责**: 执行前检查（路径、依赖、API key）

### quality_gate
- **文件**: `bridge/quality_gate/gate.py`
- **职责**: 质量门脚本调用（lint_text_integrity, lint_markdown）

## 配置契约

### AOF Spec 结构
```json
{
  "project_root": "...",
  "dataset": "dataset_name",
  "runtime": {
    "run_in_background": false,
    "incremental_loading": true,
    "data_per_batch": 20,
    "retries": 2,
    "backoff_seconds": 1.0
  },
  "ontology": {
    "file": ".../ontology.owl",
    "matching_cutoff": 0.8
  },
  "cognee": {
    "root": ".../cognee"
  }
}
```

### 运行时环境变量
- `LLM_API_KEY`: LLM 服务密钥
- `LLM_PROVIDER`: `openai` / `custom`
- `LLM_MODEL`: 模型名称
- `LLM_ENDPOINT`: 自定义端点（provider=custom 时）

## 设计原则

1. **最薄原则**: 桥接代码行数最小化，无重复业务逻辑
2. **可追溯**: 每个桥接点可追溯到 cognee 对应模块
3. **错误透明**: 上游错误原样传递，不吞没异常信息
4. **配置外化**: 所有可变参数通过 spec 文件配置，不硬编码

## 扩展指南

如需支持新的执行引擎：
1. 在 `bridge/` 下新增 `{engine}_runner.py`
2. 实现相同的 spec 解析接口
3. 在 `aof_run.py` 中通过配置切换引擎
