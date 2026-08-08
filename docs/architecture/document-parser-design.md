# AOF 企业文档解析层（document_parser）设计

> 状态：已评审定稿（Reviewed & Finalized，待按阶段实施）
> 日期：2026-08-08
> 评审：Proma Agent · 已对照全部摄取入口源码核验
> 变更记录：
> - 2026-08-08 评审修订：修正第 5.1 节「4 条入口已收敛 run_add_from_spec」的不实前提；改为「公共解析模块 + 逐入口接入」策略；补充 cognee.add 接受纯文本直接喂入的证据；落地 3 项待办决策结论。
> - 2026-08-08 阶段 0 实测修订：8 份真实文档三引擎 PoC 完成，主引擎从「MinerU 为主」改为「**Docling 首选 + MinerU 高配开关**」；补工程约束（三引擎不能共用 venv、MinerU 为 CLI 架构）。详见 §4 与 POC_REPORT.md。
> 适用范围：AOF 摄取链路的文档解析能力增强
> 相关模块：`bridge/batch_ingestion.py`、`bridge/cognee_add_runner.py`、`bridge/incremental_loader.py`、`bridge/s3_ingestion.py`、`bridge/url_ingestion.py`

## 一、背景与问题

AOF 的摄取链路已经形成了完整的「文件 → cognee.add → cognify → 图谱/向量 → 检索召回」管线，检索层（hybrid_search、graph_retrieval、RAG service、MCP）能力完善。但**上游文档解析层是当前最短的板**。

现状是所有摄取入口最终都收敛到同一个动作——`cognee.add(文件路径)`：

```
BatchIngestor / IncrementalLoader / S3Ingestor / URLIngestor
        │                      （4 条入口，全部直连）
        ▼
  cognee.add(文件路径)    ← 解析质量完全依赖 cognee 内置通用解析器
        ▼
  cognee.cognify → 实体/关系/向量 → 图谱+向量库 → 检索召回
```

问题：cognee 内置解析器是通用型，对简单文本够用，但企业文档中的**表格、层级标题、阅读顺序、页眉页脚、中英混排**处理粗糙。召回质量的**天花板取决于上游解析出的文本有多干净**——解析层质量直接决定下游实体抽取和向量化的上限。

### 需求确认（已与业务对齐）

| 项 | 结论 |
|---|---|
| 文档构成 | Office 三件套、Markdown、PDF 为主 |
| 扫描件 | 占比不高，但需支持 |
| 语言 | 中英文都要支持 |
| 部署约束 | 可接受 GPU |
| 落地节奏 | 先规划，分阶段实施 |

## 二、目标

1. 在 `cognee.add` 之前插入一层**统一解析适配层**，把复杂格式归一化为「干净的 Markdown + 结构化元数据」。
2. 让 4 条摄取入口（batch / incremental / s3 / url）受益于解析层。接入方式采用「公共 `parse_document()` 模块 + 逐入口接入」（详见 5.1）——解析实现只写一处，各入口仅在 `cognee.add` 前加一次调用，不复制解析逻辑。
3. 保证链路健壮：引擎故障自动降级，不中断摄取。
4. 可量化验证解析质量对检索召回的影响。

## 三、总体思路

```
文件 ─► MIME/扩展名路由 ─► 引擎适配器 ─► 归一化输出 ─► cognee.add ─► cognify ─► 图谱/向量
                            │  MinerU / Docling / Unstructured      ▲
                            ▼                                       │
                     标准化 Markdown + 元数据（含指纹缓存）   解析层插入点
```

核心：**把"用什么引擎解析"与"下游怎么用"解耦**。下游（add/cognify/RAG）只消费统一输出模型，引擎可插拔、可切换、可混用。

> **接入说明**：现有 4 条摄取入口均为内联 `cognee.add(文件路径)`，并未收敛到 `run_add_from_spec`。因此解析层的接入采用「新增公共 `parse_document()` + 逐入口将 `cognee.add(路径)` 替换为 `cognee.add(parse_document(路径))`」的方式，详见 5.1。

## 四、引擎选型与路由策略（经阶段 0 PoC 实测修订）

> **2026-08-08 修订**：阶段 0 用 8 份真实企业文档（中英 PDF/扫描件/PPTX/DOCX/XLSX）实测三引擎后，主引擎结论从「MinerU 为主」调整为「**Docling 首选 + MinerU 高配开关**」。完整实测证据见 `tools/document_parser_poc/POC_REPORT.md`。

### 4.1 选型结论

结合「Office/md/PDF 为主、扫描件不多、中英文都要、可接受 GPU」+ 阶段 0 真实文档实测：推荐**以 Docling 为首选主引擎，MinerU 为高精度增强开关，Unstructured 为兜底**。

| 文件类型 | 首选 | 理由（实测依据） |
|---|---|---|
| PDF（电子版） | **Docling** | 中英文/布局/阅读顺序稳定优，中文 PDF 17p 约 30s；MinerU 抽出图片+HTML表格更好但慢、首启重 |
| PDF（扫描件，偶发） | **Docling** | 扫描件 OCR 实测中文全识别；扫描比重大可切 MinerU |
| DOCX / PPTX / XLSX | **Docling** | XLSX 17 表 / DOCX 95 表全还原；MinerU 亦可（HTML table 结构更细） |
| 复杂表格/版式/需图 | **MinerU**（开关） | 表格输出 HTML+span、图片真实抽存（实测新合创抽 56 张图），多模态/RAG 有价值 |
| MD / TXT | 不解析，直接读取 | 本来就是干净文本，跳过解析层零开销 |

### 4.2 引擎对比依据（阶段 0 实测）

| 维度 | Docling 2.118.1 | MinerU 3.4.4(pipeline) | Unstructured 0.25.2 |
|---|---|---|---|
| 中文 PDF 质量 | ✅ 层级/正文/扫描件 OCR 均优 | ✅ 优 + 图片抽存 + HTML表 | ⚠️ 标题过度分节，PDF 表格全失灵 |
| 表格还原 | ✅ markdown 表；DOCX 95/XLSX 17 表 | ✅ HTML 表（含行列结构） | ⚠️ PDF 表 0、xlsx 仅 7 且结构简化 |
| 图片处理 | ⚠️ `<!-- image -->` 占位不存图 | ✅ `![](images/)` 真实抽存 | — |
| 中文 PDF 耗时(CPU) | **~1.7s/页**（80页 115s） | ~2.2s/页（25页批 ~55s单发首启重） | <0.1s/页 |
| 部署 | transformers 5.x | **需隔离 venv（transformers<5）** | 需补 msoffcrypto |
| 适合角色 | **首选主引擎** | 高精度增强开关 | 轻量文本兜底 |

> **关键工程约束（PoC 实测）**：三引擎**不能共享同一 venv**——MinerU 需 `transformers<5`，Docling/Unstructured 需 5.x，`find_pruneable_heads_and_indices` 在 4→5 被移除。生产化各引擎须隔离部署。MinerU 为 CLI/本地 FastAPI 架构（非 Python API），应封装为隔离进程调用。

### 4.3 路由策略

- 按文件类型（MIME/扩展名）路由到引擎，优先级可配置。
- 默认：`PDF/Office → Docling`；`MD/TXT → 直读`；`MinerU 开关`打开时复杂文档走 MinerU。
- 引擎解析失败或不可用时，**降级回 `cognee.add(原文件)`**，保证链路永不中断。
- 支持环境变量/配置文件调整引擎优先级，便于后续按质量数据优化。

## 五、架构设计要点

### 5.1 接入策略：公共解析模块 + 逐入口接入

> **评审修正（2026-08-08）**：设计稿原稿假定「4 条入口已统一走 `run_add_from_spec`，解析只写一处」，经逐文件核验该前提**不成立**——`batch_ingestion / incremental_loader / s3_ingestion / url_ingestion` 四处均为内联 `import cognee; await cognee.add(path, dataset_name=...)`，**没有任何入口经过 `run_add_from_spec`**（后者仅被 `aof_add.py` 与 `scripts/seed_test_dataset.py` 使用）。因此定稿采用如下策略：

1. **新增公共解析入口 `parse_document()`**，作为唯一解析实现；其余模块只消费它，不复制解析逻辑。
2. **先接入 `run_add_from_spec`**（CLI / seed 立即受益）：在 `run_add_from_spec` 内对可解析文件先 `parse_document()` 再 `cognee.add(解析结果)`。
3. **逐入口接入 4 条业务入口**：将各入口内联的 `cognee.add(原始路径)` 替换为「`parse_document()` → `cognee.add(解析产物)`」，逐文件落地、逐个测试。
4. 解析不可解析类型（已有干净文本的 md/txt 等）时 `parse_document()` 直接透传原内容，链路零开销。

**关键技术依据（经 cognee 0.5.5 源码确认）**：`cognee.add` 的 `data` 参数官方支持纯文本字符串（`Direct text content (str) — any string not starting with "/" or "file://"`），因此解析层输出的 clean Markdown **可无需写临时文件、直接作为 `data` 喂入 `cognee.add`**。

### 5.2 统一输出模型

```json
{
  "format": "markdown",
  "content": "# 标题1\n\n正文...\n\n| 表格 | 列2 |\n| --- | --- |",
  "metadata": {
    "source_path": "...",
    "mime_type": "application/pdf",
    "pages": 12,
    "tables_count": 3,
    "languages": ["zh", "en"],
    "parser_engine": "mineru",
    "content_hash": "blake2b指纹",
    "parsed_at": "2026-08-08T19:25:00+08:00"
  }
}
```

下游 add/cognify/RAG 都消费同一份输出，图谱抽取质量直接受益。

### 5.3 解析缓存

复用现有增量加载器的 **Blake2b 内容指纹**：文件未变则不重新解析，避免大文档反复烧 GPU。缓存键 = 文件内容指纹 + 引擎版本 + 解析参数。

### 5.4 降级策略

解析失败/引擎不可用 → 自动 fallback 回 `cognee.add(原文件)`，并在结果中标记 `parser_engine: "fallback"`，保证摄取链路不中断、可观测。

### 5.5 异步任务化

GPU 解析是重活，不阻塞 API 请求，走任务队列（复用现有 `bridge/tasks` 或独立队列）。API 端返回任务 ID，任务完成后回调/轮询。

## 六、模块结构

```
bridge/document_parser/
├── core.py              # 统一入口 parse_document() + 类型路由 + 降级
├── engine_base.py       # 引擎抽象基类（parse(path) -> ParsedDoc）
├── engines/
│   ├── docling_engine.py       #【首选】CPU 可跑，中英/表格/OCR 实测优
│   ├── mineru_engine.py       #【高配开关】隔离进程调 CLI，复杂表格/图片保留/高精度
│   └── unstructured_engine.py #【兜底】轻量文本型文档
├── normalizer.py        # 各引擎输出 → 统一 Markdown + 元数据
├── cache.py             # Blake2b 指纹缓存
├── config.py            # 引擎路由策略配置（优先级、开关）
└── tasks.py             # 异步解析队列
```

## 七、与现有系统的集成点

| 集成点 | 改动 |
|---|---|
| `bridge/document_parser/core.py` | 新增公共 `parse_document()`（唯一解析实现） |
| `bridge/cognee_add_runner.py` | `run_add_from_spec` 内 add 前调 `parse_document()`（先接入，CLI/seed 受益） |
| `bridge/batch_ingestion.py` | `_ingest_file` 内 `cognee.add(路径)` → `parse_document()` 后 `add(产物)`（逐入口接入） |
| `bridge/incremental_loader.py` | 增量循环内 `cognee.add(路径)` → `parse_document()` 后 `add(产物)` |
| `bridge/s3_ingestion.py` | `cognee.add(local_path)` → `parse_document()` 后 `add(产物)` |
| `bridge/url_ingestion.py` | `_ingest_to_cognee` 内 `cognee.add(路径)` → `parse_document()` 后 `add(产物)` |
| API `app.py` | 新增 `POST /v1/documents/parse`；`/v1/ingest/docs` 增加 `parser` 参数 |
| MCP Server | 新增工具 `aof_document_parse` |
| Web 控制台摄取向导 | 可选解析引擎 |
| `skills/SKILL_INGEST.md` | 更新摄取协议文档 |

## 八、实施路线（分阶段）

### 阶段 0 · PoC 对比 ✅ 已完成 (2026-08-08)

- 8 份真实企业文档（中英 PDF/扫描件/PPTX/DOCX/XLSX）三引擎全量实测。
- 结论：**Docling 首选主引擎，MinerU 高精度开关，Unstructured 兜底**；三引擎不能共用 venv。
- 报告：`tools/document_parser_poc/POC_REPORT.md`。

### 阶段 1 · 核心落地

- `document_parser` 框架（core/engine_base/engines/normalizer） + **Docling 主引擎**（mineru_engine 高配可切换）。
- 接入 `run_add_from_spec`（CLI / seed 立即受益）。
- 逐入口接入 batch / incremental / s3 / url 四条业务入口，各自跑通「干净 Markdown → cognify → 检索」。
- 单元测试 + 端到端 smoke，跑通「PDF → parse_document → cognify → 检索」全链路。

### 阶段 2 · 全格式与健壮性

- Office 全格式覆盖。
- 解析缓存、降级策略、异步队列。

### 阶段 3 · 对外能力与回归基线

- API 端点、MCP 工具、Web 集成。
- 建立自留样例集做**解析质量回归基准**，防止升级引擎版本后质量回退。

## 九、验收标准

1. 中英文 PDF：表格结构、阅读顺序、标题层级准确率 ≥ 90%（用 OmniDocBench 或自建样例集量化）。
2. Office 三件套可解析，正文干净、表格不乱。
3. 缓存命中时零重复解析；引擎故障自动降级不中断。
4. 检索增益：同一批文档，加解析层前后各跑 20 个真实查询，hit rate / MRR 可量化提升。

## 十、风险与对策

| 风险 | 对策 |
|---|---|
| Docling 对超大/扫描 PDF 吞吐受限（80页 115s CPU） | 磁盘充足；按需切 mineru-engine 高精度或异步队列；目标 GPU 后压测 |
| MinerU 模型体积大（首次下载数 GB） | 预下载/离线镜像；只在需高精度/图片保留时启用 |
| GPU 显存限制解析吞吐 | 按目标 GPU 型号压测，必要时队列限流或切 CPU 引擎（Docling 已够用） |
| MinerU 复杂嵌套 Office 边缘场景 | 已实测 Docling 对 Office 三件套表现优；MinerU 作高配验证 |
| 三引擎共享依赖冲突（transformers 4.x vs 5.x） | **各引擎隔离部署**（独立 venv/进程/容器），PoC 实测确认 |
| 依赖许可 | MinerU(MIT)/Docling(Apache-2.0)/Unstructured(Apache-2.0) 均无企业集成障碍，提交前再核对一次 |
| 解析质量随版本漂移 | 阶段 3 建立回归基准集，CI 中跑质量断言 |

## 十一、待办决策（已定稿结论 + PoC 实测）

> 经评审（2026-08-08）业务对齐拍板；阶段 0 实测后主引擎结论已更新（见 §4）。

1. **PoC（阶段 0）→ 已完成**：8 份真实企业文档三引擎实测结论 —— **Docling 首选主引擎，MinerU 高精度开关，Unstructured 兜底**。详细数据见 `tools/document_parser_poc/POC_REPORT.md`。
2. **目标 GPU / 显存 → 待业务提供**：本机 Apple M5 Pro 无 NVIDIA GPU，实测仅 CPU（Docling ~1.7s/页）；MinerU 高精度/吞吐压测待业务目标 GPU。
3. **结构化 JSON → 产出**：表格是中文企业文档高价值信息，Docling 表格 markdown + MinerU HTML 表（含行列结构）均可用；下游『表格问答/表格实体抽取』受益明确。normalizer 增加表格结构化模型即可。
