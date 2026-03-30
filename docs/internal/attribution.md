# AOF 组件来源说明

## 外部依赖

### Cognee（执行引擎）
- **来源**: https://github.com/topoteretes/cognee
- **许可证**: Apache-2.0
- **使用方式**: 运行时动态导入（`cognee.root` 配置）
- **用途**: 知识图谱构建、本体解析、LLM 任务编排

## 内部资产

### Methodology（方法论体系）
- **位置**: `docs/internal/methodology/`
- **说明**: AOF 的方法规范核心，定义本体抽提、质量控制和迭代优化方法

### Quality Gate（质量门）
- **位置**: `tools/quality_gate/`
- **说明**: 基于 AOF 方法论实现的质量控制工具

### Ontology Factory（本体工厂）
- **位置**: `tools/ontology_factory/`
- **说明**: 自动本体生成与对齐迭代系统

---

*本文档仅记录外部依赖，内部资产详见各目录文档。*
