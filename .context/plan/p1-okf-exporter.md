# P1 实现计划：LLM Wiki / OKF 导出器 + Lint 工具

> 日期: 2026-08-07
> 状态: 待审批

## 背景
AOF 定位为"企业知识的 Agent-Ready 资产化引擎"。P1 目标是落地差异化能力：
将知识图谱/文档导出为 **OKF（Open Knowledge Format）兼容的 LLM Wiki 知识包**，
让 AI Agent 通过 index.md 渐进式披露低成本消费，配套 Lint 工具保证知识包结构健康。

调研参考：Karpathy LLM Wiki（Ingest→Query→Lint 闭环）、Google OKF（原子化 Concept +
class="parameter">index.md/log.md + 容错消费）。

## 交付物

### 1. `exporters/okf_exporter.py` — OKF/LLM Wiki 导出器（新模块，复用取数）
- 复用 `MarkdownExporter` 已有的图数据拉取（`_try_get_graph_data` / `_try_get_nodes_edges`），不重复造轮子。
- **原子化 Concept**：每个实体/概念生成一个 Markdown 文件，按 `type` 分目录（对齐现有 MECE 结构）。
- **OKF 标准 frontmatter**（YAML）：`type`（必填）、`title`、`description`、`resource`、`tags`、`timestamp`（符合 OKF 规范高频推荐字段）。
- **交叉链接**：Concept 间的关系用相对 Markdown 链接 `[title](../type/file.md)` 表达，支持双向（双向 build）。
- **`index.md`（渐进式披露）**：生成知识包总目录，按 type 分组列出各 Concept 标题+描述，Agent 先读全局再下钻，避免 context 爆炸。
- **`log.md`（审计日志）**：append-only 记录知识包创建/更新/版本变更，含时间戳与条目。
- **容错消费模型**：缺字段也不抛错（退化为通用文档），宽松解析。
- 便捷函数 `export_dataset_to_okf(...)`。
- `ExportResult` 统计（concepts_exported / files_created / errors / mode）。

### 2. `tools/knowledge_lint.py` — OKF 知识包结构体检
- **断链检测**：扫描 Concept 间链接，找出指向不存在文件的空链接。
- **重复检测**：title/type 重复、内容 hash 完全重复的 Concept。
- **口径冲突检测**：同一 type+title 下不同概念属性差异超阈值提示（轻量启发式）。
- 输出结构化 LintReport（issues/warnings）。

### 3. 测试 `tests/`
- `test_okf_exporter.py`：frontmatter 正确性、index.md 生成、log.md 生成、交叉链接、容错（缺字段不抛）。
- `test_knowledge_lint.py`：断链/重复/口径冲突检测用例。

## 设计要点
- 导出器数据来源优先走 Cognee 图数据；无 Cognee 时回退到 DatasetManager 原始数据（对齐现有双回退）。
- 代码风格对齐现有 exporters（dataclass ExportResult + class Exporter + 便捷函数 + logging.getLogger(__name__)）。
- 文件名安全：复用 `_sanitize_filename` / `_slugify` 逻辑（通过复用 MarkdownExporter 实例实现）。
- OKF 目录结构：
  ```
  <output>/
    index.md          # 渐进式披露目录
    log.md            # 审计日志
    <type>/
      <concept>.md    # 原子化 Concept
    ...
  ```

## 成功标准
- 生成的知识包符合 OKF 规范（必填 type，index/log 存在，交叉链接相对路径可解析）。
- Lint 能正确报告断链与重复。
- 全部单测通过 + ruff 通过。
