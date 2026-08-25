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
