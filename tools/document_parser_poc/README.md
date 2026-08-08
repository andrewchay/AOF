# 阶段 0 · 文档解析引擎 PoC（README）

> 目标：用真实企业文档对比 **MinerU / Docling / Unstructured** 三个引擎的解析质量，用数据拍板主引擎。
> 环境：Apple Silicon（M5 Pro，无 NVIDIA GPU）→ 本机仅 CPU 推理，**只验证解析质量**；GPU 吞吐待业务目标 GPU 到位后再压测。

## 目录约定

```
data/poc/
├── samples/        ← 【你放真实企业文档的地方】(已 gitignore，不进版本库)
└── runs/           ← 解析产出 + 质量报告 (已 gitignore)

tools/document_parser_poc/   ← PoC 脚本与说明（可提交）
```

## 请你放入 data/poc/samples/ 的样例（阶段 0 建议量）

| 类别 | 建议件数 | 说明 |
|---|---|---|
| 中文 PDF（电子版） | 5 | 尽量含表格、标题层级、页眉页脚、中英混排 |
| 英文 PDF（电子版） | 5 | 同上 |
| 扫描件 PDF（中英皆可） | 1–2 | 检验 OCR（本机 CPU 会偏慢） |
| Office（docx / pptx / xlsx） | 3–5 | 至少各 1 个 docx/xlsx，表格不乱为要 |

~15–17 份即可，用代表性文档（含表格/多栏/页眉页脚）优先。

> **注意**：这些文档可能含敏感内容，已加入 `.gitignore`，不会进版本库。提交前请确认无涉密信息（若涉密可不放仓库，改用 gitignore 无法覆盖的独立目录，我会按你给的路径读取）。

## 下一步（文档就位后自动执行）

1. 我用独立 Python 3.12 venv 搭建三引擎环境（不污染主项目 3.13 venv）。
2. 写统一 `run_poc.py`：遍历 samples、三引擎各跑一遍 → 输出标准化 Markdown + 元数据。
3. 生成对比报告（表格还原 / 阅读顺序 / 标题层级 / 中英混排），落地 `data/poc/runs/`。
4. 汇总给主引擎拍板建议。

---
> 关联设计稿：`docs/architecture/document-parser-design.md` §8 阶段 0、§11 决策 1/2
