# BFO-2020 参考目录

本目录存放 BFO (Basic Formal Ontology) 2020 参考工件，用于 AOF 领域本体的**设计期对齐**。

## 文件

| 文件 | 说明 |
|---|---|
| `bfo-core.owl` | BFO 2020 核心本体（ISO/IEC 21838-2），CC BY 4.0 |
| `MAPPING_GUIDE.md` | AOF 领域本体挂接 BFO 的映射指南（v0.1 草稿） |

## 用途

- 不强制所有领域本体 import BFO；仅作为审阅阶段的可选对齐参考
- 运行期不加载完整 BFO 公理，避免推理成本；挂点以 `rdfs:subClassOf` 断言保留
- 详细映射规则与 SHACL 质控约束见 `MAPPING_GUIDE.md`

## 来源

- 上游仓库: https://github.com/BFO-ontology/BFO-2020
- 下载日期: 2026-09-02
- 版本: `bfo-core.owl` (versionIRI: `http://purl.obolibrary.org/obo/bfo/2020/bfo-core.owl`)
