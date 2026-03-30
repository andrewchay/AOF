# AOF 组件来源

## 外部依赖

### Cognee（执行引擎）
- **来源**: https://github.com/topoteretes/cognee
- **许可证**: Apache-2.0
- **使用方式**: 运行时动态导入（配置 `cognee.root`）
- **核心用途**:
  - 知识图谱构建 (`cognify`)
  - 数据摄取 (`add`)
  - 本体解析与匹配
  - LLM 任务编排

### Python 依赖
详见项目根目录 `requirements.txt`（如存在）或虚拟环境配置。

## 内部资产

### Methodology（方法论体系）
- **位置**: `docs/internal/methodology/`
- **内容**: 14 篇元技能文档 + 1 本方法手册
- **用途**: AOF 的方法规范核心，指导本体抽提、质量控制和迭代优化

### Bridge（桥接层）
- **位置**: `bridge/`
- **用途**: AOF spec 与 cognee API 之间的适配层
- **设计原则**: 只做参数映射，不承载业务逻辑

### Quality Gate（质量门）
- **位置**: `tools/quality_gate/`
- **用途**: 文本完整性检查、格式验证
- **方法基础**: AOF 质量控制方法论

### Ontology Factory（本体工厂）
- **位置**: `tools/ontology_factory/`
- **用途**: 自动本体生成、对齐迭代、反馈闭环
- **方法基础**: AOF 迭代工作流方法论

### Data Adapter（数据适配器）
- **位置**: `tools/data_adapter/`
- **用途**: 多源数据（SQL/CSV/JSON）规范化

### Semantic Middle Layer（语义中间层）
- **位置**: `tools/middle_layer/`
- **用途**: 文档、元数据、反馈的统一处理

## 使用建议

### 如需更新 cognee
1. 备份当前稳定版本
2. 更新 cognee 代码（git pull / submodule update）
3. 运行 `aof_doctor.py` 检查兼容性
4. 执行 dry-run 测试验证功能
5. 全量测试通过后再部署

### 如需扩展 AOF 能力
1. 优先复用现有 `tools/` 组件
2. 新增组件遵循 `bridge/` 设计模式
3. 文档补充到 `docs/internal/` 或 `docs/api/`
