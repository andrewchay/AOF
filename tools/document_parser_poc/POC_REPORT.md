# 阶段 0 · 文档解析引擎 PoC 对比报告

> 日期：2026-08-08
> 环境：Apple M5 Pro（16 核 / 24GB / Metal 4），无 NVIDIA GPU → **本机仅 CPU 推理**
> 数据：合成样例 4 份（可达性）+ **真实企业文档 8 份**（正式对比，中文为主）
> 阶段：引擎**可达性验证通过** + **真实文档正式对比完成**，可给主引擎最终建议

## 二、关键工程发现

### 2.1 三引擎无法共用同一 venv（最重要）

MinerU 的 pipeline 公式/表格模型依赖 **`transformers <5.0.0`**（实测 4.57.6 可用，4.x 的 `find_pruneable_heads_and_indices` 函数在 5.x 已移除）；
Docling / Unstructured 平滑依赖 **transformers 5.x**（实测 5.8.1）。两者在同一解释器下必然冲突（实测 MinerU 在 transformers 5.8.1 下直接 `ImportError`）。

**→ 结论：生产化时各引擎必须隔离部署（独立 venv / 独立进程 / 容器），或统一最小依赖集。** 这印证设计稿 §6「引擎可插拔」需配套隔离模型，而非共享一个大环境。

### 2.2 MinerU 是 CLI / 本地 FastAPI 服务架构

MinerU 3.4.4 无 `mineru.MinerU()` 顶层 Python API，主要入口为：
- CLI：`mineru -p <file> -o <out> -b pipeline`（本文用此）
- 本地服务：`mineru-api`，vlm/hybrid backend 需 `-u` 指定远程或本地模型服务
- 默认 backend 是 `hybrid-engine`（需 VLM 模型、较重）；本次用 `pipeline`（OCR+版面）在 CPU 上验证。

> 设计稿 §6 `mineru_engine.py` 应封装为「调用隔离进程中的 mineru CLI / mineru-api」，而非进程内 import。

### 2.3 本机无 NVIDIA GPU，吞吐是天花板

- 中文 PDF 1 页：MinerU pipeline 16.5s、Docling 3.1s（仅 CPU）。
- 本机 Apple Silicon 无 CUDA；MinerU 的 hybrid/vlm backend 需 VLM 大模型，不适合本机。
- **GPU 吞吐压测留待业务目标 GPU 到位**（设计稿决策 2）。

## 三、真实企业文档对比（正式结论）

### 3.1 文档清单（8 份真实）

| 文档 | 类型 | 规模 | 说明 |
|---|---|---|---|
| 新合创CRM介绍.pdf | 中文 PDF | 17 页 / 5.3MB | 产品介绍（demo 风格） |
| PwC NWD_1CRM...Presentation.pdf | 中英 PDF | 44 页 / 5.4MB | 咨询汇报 |
| 贝瑞咖啡品牌上市...BBDO提案.pdf | 中文 PDF | 80 页 / 26.7MB | 品牌提案（最大） |
| 避免重复积分-2.pdf | 中文扫描件 | 6 页 / 1.5MB | **需 OCR** |
| NWD - SFCDP & SFMC...docx | 中英 DOCX | 2.9MB | 方案设计，**含 95 表** |
| NWCS...Kick-off.pptx | 英文 PPTX | 8.4MB | 营销自动化 |
| 上海七夕 - CRM Data analysis.pptx | 中文 PPTX | 0.6MB | 活动运营 |
| 分site核心概况数据.xlsx | 中文 XLSX | 67KB | 运营数据，**含 17 表** |

### 3.2 结果矩阵（三引擎 × 真实文档）

| 文档 | Docling | MinerU(pipeline) | Unstructured |
|---|---|---|---|
| 新合创CRM介绍.pdf(17p) | ✅ 层级/正文优, table=1, 29.6s | ✅ 层级/正文优, 图抽存, 25页批~55s | ⚠️ 每句变`##`标题, table=0, 0.3s |
| PwC 44p | ✅ 结构清晰, table=0, 67.1s | — (未测) | ⚠️ table=0 |
| 贝瑞咖啡 80p | ✅ 1617行结构完整, table=2, 115s | — (未测) | ⚠️ table=0 |
| 避免重复积分 扫描件 6p | ✅ 中文OCR成功, table=0, 9.3s | ✅ 中文OCR成功, 关键词完整, 批~55s | ⚠️ table=0 |
| SFCDP...docx | ✅ **table=95**, 4.4s | — (未测) | ✅ table=94, 0.7s |
| 上海七夕.pptx | ✅ table=1, 0.1s | ✅ 层级/格式优 | ✅ table=1 |
| 分site核心概况.xlsx | ✅ **table=17**, 0.6s | ✅ HTML table 结构完整 | ⚠️ table=7, 0.3s |
| Kick-off.pptx(EN) | ✅ table=4, 2.3s | — | ✅ table=4 |

### 3.3 关键质量洞察（真实文档）

1. **扫描件 OCR 都是达标的**：当“避免重复积分”扫描件同时被 Docling 与 MinerU 识别出完整中文正文（含“微信支付流水号」「KPOS」「OCR能力」等关键短语）。扫怪件支持成立。
2. **图片处理差异显著**：Docling 用 `<!-- image -->` 占位（不保存图）；MinerU 抽出图片为 `![](images/xxx.jpg)`（真实保存）。对多模态/RAG 场景 MinerU 更有价值。
3. **XLSX 表格识别**：Docling 17 表 > MinerU 完整HTML表 > Unstructured 仅 7 表且结构简化。
4. **Word 表格都是强项**：Docling 95 表 vs Unstructured 94 表——原生结构文档两者接近。
5. **Unstructured 确认淘汰**：所有中文/演示 PDF（layout 型）table=0，且标题过度分节，只适合纯文本轻量场景。

## 四、主引擎最终建议

| 建议项 | 结论 | 依据 |
|---|---|---|
| **主引擎** | **Docling（首选），MinerU 为高精度增强备选** | Docling 在全部真实格式上稳定优、快、中文强；MinerU 在“表格结构/CD图片保留”上更强但慢、需隔离、首启重 |
| 适用分域 | 常规企业文档（默认）→ **Docling**；复杂表格/版式/需图→ **MinerU** | 表格 HTML+span、图片保存、中文细节强是 MinerU 差异化 |
| **兜底** | **Unstructured** | 轻量文本型文档；或 Docling 解析失败时降级 |
| 扫描件 | Docling 足够（OCR 达标）；扫描比重高可切 MinerU | 两者扫描件 OCR 均实测通过 |
| 隔离部署 | **必须** | transformers 4.x/5.x 冲突（见 2.1） |

> **结论**：在满足“中英文 / Office+PDF / 可接受 GPU”的前提下，**以 Docling 为主引擎能覆盖绝大多数场景且成本最低**；MinerU 作为“复杂表格/版式/图片保留”的高配开关，需要时启用。建议阶段 1 落地 `docling_engine` 为主，`mineru_engine` 为可切换选项。

## 五、后续动作

1. （可选）对 80 页最大案、PwC 补跑 MinerU，验证超大文档 MinerU 吞吐（本机 CPU 预计 10-20 min，暂缓）。
2. **GPU 吞吐压测**：待业务目标 GPU 到位后（设计稿决策 2）。
3. **阶段 1**：以 Docling 主引擎落地 `bridge/document_parser`（公共 parse_document + 逐入口接入），MinerU 作为可选高精度引擎。

---
> 关联：`docs/architecture/document-parser-design.md` §4 引擎选型、§8 阶段 0、§11 决策 1/2；`tools/document_parser_poc/`
