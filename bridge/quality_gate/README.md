# Quality Gate

职责：把质检门接入 AOF 流程。

## 组件

- `lint_text_integrity.py` - 文本完整性检查
- `lint_markdown.py` - Markdown 格式检查

## 设计原则

- 遵循 AOF 质量控制方法论（见 `docs/internal/methodology/00-META-10_质量控制.md`）
- 独立可运行的 lint 脚本
- 通过 `gate.py` 统一接入 AOF 流程
