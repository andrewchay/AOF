# AOF 能力验证：用 AOF 重建图谱 vs CSO 对比

**日期**: 2026-08-26
**验证目标**: 用 AOF 包独立重建临床研究知识图谱，验证其能否自动得出 CSO (Objective/Endpoint/Method) 结构

## 验证方法
1. 用真实文本(NCT02458638 Atezolizumab objectives/endpoints/statistics) 
2. 通过 AOF 链路：`document_parser` → `cognee.add` → `cognee.cognify` → `cognee.search` / `graph_retrieval.load_graph_nodes_edges`
3. 对比"无 ontology" vs "注入 CSO ontology"两次抽取

## 关键产物
- `cso_oncology.owl`: 将 CSO v0.3 转成 cognee 可消费的 OWL(类+实例+关系)
- 相应环境变量: `ONTOLOGY_FILE_PATH`, `ONTOLOGY_RESOLVER=rdflib`, `ONTOLOGY_MATCHING_STRATEGY=fuzzy`

## 决定性发现

### AOF 默认抽取（无 ontology）
```
实体: sarah_chen, acme_ai, stanford_university, transformer_architectures
类:   person, company, organization, university, subject
关系: is_part_of, contains, is_a, partner, ceo_and_co-founder
```
→ **通用命名实体，与临床研究无关**

### AOF + CSO ontology 抽取
```
实体: advancedsolidtumors, simon optimal two-stage design,
      non-progression rate (npr) at 18 weeks, overall survival (os),
      overall response rate (orr), atezolizumab, pfs, dor
类:   statisticalmethod, disease, drug, endpoint, clinicaltrial
```
→ **正确识别了 CSO 领域实体（Endpoint/Drug/Disease/StatisticalMethod）**

## 结论
> AOF/cognee **默认抽取通用命名实体**，只有注入 CSO ontology 后才能正确识别临床研究领域概念（Objective/Endpoint/Method/Drug/Disease）。
> 这证明: AOF + 领域 ontology 是正确组合，且 CSO ontology 是 AOF 抽取正确语义的前提。

## 边界与后续
- edges 存在跨 dataset 残留（acme_ai 等旧数据），需清理测试 dataset
- nodes/edges 的 ID 映射需进一步打磨才能拿到完整可读关系
- 下一步: 用完整 CSO + 真实多份 protocol 验证关系抽取

---

# 追加：走「AOF 官方链路」重做验证（2026-08-26 下午）

> 背景：上一版 `build_graph.py` 直接 `import cognee` + 手动设 `ONTOLOGY_*` 环境变量，
> **绕过了 AOF 的封装层**（preflight / spec / document_parser / ontology_adapter / result log）。
> 本版本用 AOF 官方链路重做，证明"在 AOF 项目空间内"确实执行了 AOF 代码。

## 官方链路 vs 裸调用（关键差异）

| 层 | 裸调用 build_graph.py（旧） | 官方链路 run_via_aof_chain.py（新，推荐） |
|----|--------------------------|------------------------------------------|
| 入口 | `import cognee` 裸调 add/cognify | `from bridge import ...`，spec 驱动 |
| ontology | 手动设 `ONTOLOGY_*` 环境变量 | `spec.ontology.file` → `bridge/ontology_adapter` → `cognify(config=...)` |
| 摄取 | 裸字符串 `cognee.add(TEXT)` | `run_add_from_spec`（document_parser 解析层） |
| 预检 | 无 | `ensure_preflight_for_add/cognify` |
| 结果 | 控制台打印 | 落盘 `runs/aof_chain_result.*.json` |

## 运行证据（WITH_ONTOLOGY，`examples/cso_validation/cso_spec.json`）

关键断言：**ontology 由 AOF bridge 注入** cognee 的 `config.ontology_config.resolver`
（走 `run_cognify_from_spec` → `_build_ontology_config` → `build_cognee_ontology_config`），**而非环境变量**。

```
[1] preflight_add + run_add_from_spec ...   add 完成: PipelineRunCompleted
[2] preflight_cognify + run_cognify_from_spec ...
    ✅ ontology 已由 AOF bridge 注入 cognee config(ontology_config.resolver)
    cognify 完成
[3] load_graph_nodes_edges ...  识别出 22 实体, 38 边
    CSO/临床实体(19): clinicaltrial, atezolizumab, drug, advancedsolidtumors,
      phase ii/studyphase, non-progression rate (npr) at 18 weeks, endpoint,
      overall response rate (orr), duration of response (dor),
      progression-free survival (pfs), overall survival (os),
      safety and tolerability, simon optimal two-stage design, statisticalmethod,
      safetypopulation/population/analysispopulation, disease
```

> ✅ 结果落盘：`examples/cso_validation/runs/aof_chain_result.with_ontology.json`
> 22 nodes / 38 edges / 19 临床实体 —— **AOF 官方链路完整跑通并产出规整临床图谱**。

## 关于"无 ontology"的精确认知（重要修正）

干净的对照（同文本，`--no-onto`，占位 dataset 后缀 `_noonto`）实际**也能抽出**临床领域实体
（17 个，19/36 边）。原因：cognee **始终用 `RDFLibOntologyResolver`**，`ontology_file=None` 时走
`get_default_ontology_resolver()`，而实体 label 的抽取本身主要靠 **LLM**（deepseek 泛化强）。

→ **ontology 的真实职责是"定型"（type 归属精确化），而非"能否抽取"**：

| 维度 | 无 ontology | 注入 CSO ontology |
|------|------------|-------------------|
| 实体 label 抽取 | 能抽（靠 LLM） | 能抽（靠 LLM） |
| 类型归属精确度 | 泛化标签（`safety population`/`population`） | 明确 CSO 类（`analysispopulation`/`safetypopulation` 归入 AnalysisPopulation 类） |

> 上一版 README"No ontology → 只见 person/company（sarah_chen 等）"其实是**跨 dataset 残留观察**
> （当时的对照用了旧 Genshin 文本片段），非本次同文本对照结论。

## 结论（官方链路版）
> AOF 官方链路（preflight → spec → run_add_from_spec[文档解析] → run_cognify_from_spec[ontology 注入]
> → graph_retrieval）**被真实、完整触发**，注入 CSO ontology 后产出 19 个临床实体、38 条边的规整图谱。
> 正确用法 = `aof_add.py --spec ...` 或本目录 `run_via_aof_chain.py`，**请勿直接 `import cognee` 裸调**。

## 复现方式
```bash
cd /Users/chaihao/LLM/AOF
# 带 ontology（推荐）
.venv/bin/python examples/cso_validation/run_via_aof_chain.py
# 干净对照（独立 dataset）
.venv/bin/python examples/cso_validation/run_via_aof_chain.py --no-onto
# 结果
cat examples/cso_validation/runs/aof_chain_result.with_ontology.json
```

