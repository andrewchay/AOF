# 让 AOF 抽提真正走"AOF 官方链路"

> 日期: 2026-08-26
> 目标(C: A+B): ① 用 AOF 官方链路重做 CSO 临床抽提，确证封装层被触发且复现"识别出临床实体"；② 从机制上杜绝"绕到 cognee 底层"。

---

## 一、根因回顾（已诊断确认）

上一次 CSO 临床数据抽提（`examples/cso_validation/build_graph.py`）之所以"在 AOF 空间内却没执行 AOF 代码"，是因为脚本**绕过了 AOF 的 runner 层**：

```python
os.environ["ONTOLOGY_FILE_PATH"] = CSO_OWL   # 手动设环境变量喂 cognee
await cognee.add(TEXT, dataset_name=ds)        # 裸调 cognee
await cognee.cognify(dataset_name=ds)          # 裸调 cognee
```

而 AOF 已有正确链路（只是没被用）：
- `aof_add.py` → `run_add_from_spec`（含 document_parser 解析层）
- `run_cognify_from_spec`（`bridge/cognee_runner.py`）→ `_build_ontology_config` → `bridge/ontology_adapter` 打包 `config["ontology_config"]` → `cognee.cognify(config=...)`

cognee 侧确认：`cognify(...)` 原生接受 `config` 参数，`config["ontology_config"]["ontology_resolver"]` 存在时**不会**回退到环境变量。因此走 AOF 官方链路时 ontology 注入是干净、显式的。

真正的缺陷（需要 B 修复）：
1. **没有强制门**：`import cognee` 可直接穿透，AOF 无法约束"摄取/抽提必须经 aof 入口"。
2. **`run_add_from_spec` 不消费 ontology**：add 阶段本身不需要 ontology（cognify 才用），但希望 `cognee_add_runner` 至少做校验与落盘，避免用户误以为"spec 里写了 ontology.file 就会生效于 add"——实际上**只有走 cognify_runner 才生效，且现在没有任何报错/提示**。

---

## 二、方案设计

### 阶段 A：真·AOF 链路重做 CSO 抽提（证明能工作）

**目标**：产出可复现的 spec + 运行结果，确证 ontology 通过 AOF 层注入、封装层（preflight/parse/result log）全部触发。

#### A1. 写 CSO 专用 spec
新增 `examples/cso_validation/cso_spec.json`：
```json
{
  "project_root": "/Users/chaihao/LLM/AOF",
  "knowledge_repo": "/Users/chaihao/LLM/AOF/data",
  "dataset": "cso_via_aof_chain_<ts>",
  "runtime": {
    "run_in_background": false,
    "incremental_loading": true,
    "retries": 2,
    "backoff_seconds": 1.0
  },
  "ontology": {
    "file": "/Users/chaihao/LLM/AOF/examples/cso_validation/data/cso_oncology.owl",
    "matching_cutoff": 0.8
  },
  "cognee": { "root": "/Users/chaihao/LLM/cognee" }
}
```

#### A2. 把临床文本文档落到数据目录（走 document_parser 解析层）
将 NCT02458638 文本写为 `data/cso_clinical/NCT02458638_atezolizumab.md`，让 `run_add_from_spec` 的 `maybe_parse_local_file` 真正处理本地文件（而非裸字符串直入）。

#### A3. 走官方入口执行
参考 `aof_add.py` 的调用序列，写一个最小的、**只依赖 AOF 抽象**的驱动脚本 `examples/cso_validation/run_via_aof_chain.py`：
```
ensure_preflight_for_add(spec)  →  run_add_from_spec(spec, data_path)
ensure_preflight_for_cognify(spec) → run_cognify_from_spec(spec)
→ bridge.graph_retrieval.load_graph_nodes_edges 取图，复现"识别出 ClinicalTrial/Drug/Endpoint/Disease/Method"
```
关键点：脚本本身**不直接 `import cognee`、不设 `ONTOLOGY_*` 环境变量**，把一切交给 `bridge/cognee_add_runner` + `bridge/cognee_runner`。

#### A4. 对比验证
- 有 ontology（走 AOF 链路 + cso_spec.json）→ 应识别出临床实体
- 无 ontology（走 AOF 链路 + 去掉 ontology.file）→ 只识别通用实体
- 记录两类结果，写入 `examples/cso_validation/README_aof_validation.md`（追加"官方链路版"小节）与结果 json。

**验收标准**：`run_cognify_from_spec` 的 `config` 非 None 且携带 `ontology_config`（可在脚本里 assert `config["ontology_config"]`），preflight / parse 日志 / result log 均有输出，图谱复现临床实体识别。→ 证明"AOF 封装层被真实触发"。

---

### 阶段 B：机制上杜绝穿透 + 补正式 ontology 注入触点

#### B1. 新增「强制入口」门 —— `bridge/entry_gate.py`
一个可被任意入口/脚本引用的守卫函数：
```python
def assert_aof_gate(context: str = "ingest", spec: dict | None = None):
    """若调用方绕开 spec/runner，抛出 AOFEntryGateError。"""
```
- 检测是否经由 `run_add_from_spec` / `run_cognify_from_spec` 进入（通过一个进程级标志 env/AOF_GATE/ 或模块级 sentinel）。
- 若直接 `import cognee` 做 add/cognify 而未设置 sentinel → 抛错并提示应改走 `aof_add`/bridge runner。
- 提供给 CSR 验证脚本、docstring、README 使用，作为"官方穿针引线"示例。

设计权衡：强制门**不能破坏已有合法用法**（REST / MCP / aof_add CLI 均走 runner，天然通过）；它主要拦"第三方脚本直连 cognee 且声明要做 AOF 抽提"的场景。实现为**显式断言 + 教程引导**，而非硬性 import hook（避免过度侵入，保持薄封装哲学）。

> 补充修订：更稳妥的做法是——不强加进程级 sentinel（复杂且易脆），而是把 B 的重心放在「提供一个官方、唯一的 AOF 抽提入口函数」，并配合 preflight 校验 + spec 强制。下面 B2/B3 是核心。

#### B2. 让 add 阶段显式校验 ontology 配置并提示（`bridge/cognee_add_runner.py` 增强）
- 在 `run_add_from_spec` 内读取 `spec["ontology"]`：
  - 若 ontology.file 存在：**校验文件存在**，并写一条结构化日志 `ontology_applied_at="cognify"`（强调 ontology 在 cognify 阶段生效，add 阶段不消费），避免误用。
  - 若 ontology.file 缺失但调用方传了裸 `ONTOLOGY_FILE_PATH` 环境变量：**打警告**"检测到环境变量注入，建议改走 spec.ontology"，作为穿透的哨兵提示。

#### B3. 对外暴露正式 ontology 注入 API（把薄 adapter 提升为可用入口）
- 现状：`bridge/ontology_adapter/build_cognee_ontology_config` 已存在，但只被 `cognee_runner._build_ontology_config` 内部用，未作为公共 API 文档化、也未暴露给 REST/MCP。
- 增强：
  a. 新增公共函数 `bridge/ontology_adapter/apply_ontology(spec) -> config`（等价现 `_build_ontology_config`，从 `cognee_runner` 抽取共用，避免重复）。
  b. 让 `cognee_runner._build_ontology_config` 改为调用它。
  c.（可选）若 REST `/v1/ingest` 存在同源逻辑，接入同一 adapter，使 ontology 注入在三入口（CLI/REST/MCP）行为一致。

#### B4. 文档与示例收口
- 更新 `examples/cso_validation/`：
  - `run_via_aof_chain.py` 作为**官方链路模板**（注释标明"这是唯一推荐做法，勿直接 import cognee"）。
  - `build_graph.py` 标注为"遗留快速验证"，不删除但加 banner 提示改走官方链路。
- 在 README / CHANGELOG 补一条：AOF 抽提正确用法 = `aof_add.py --spec` 或三入口，ontology 通过 `spec.ontology` 注入于 cognify。

---

## 三、执行计划（tasks）

| # | 动作 | 交付 |
|---|------|------|
| A1 | 写 `cso_spec.json` + 临床文本落地 `data/cso_clinical/` | 数据与 spec |
| A2 | 写 `run_via_aof_chain.py`（走 preflight→run_add→run_cognify→read_graph，零 import cognee） | 官方链路脚本 |
| A3 | 跑通有/无 ontology 两组，验证 `config["ontology_config"]` 非空 + 复现临床实体 + 落盘 result json | 运行证据 |
| A4 | 追加官方链路验证记录 → `README_aof_validation.md` | 文档 |
| B1 | 新增 `bridge/cognee_add_runner.py` ontology 校验 + 穿透警告日志 | 代码 |
| B2 | 抽取 `apply_ontology` 公共函数，`cognee_runner` 复用 | 代码 |
| B3 | （如可行）REST/MCP ingestion 接入同一 adapter | 代码 |
| B4 | 文档收口 + 给 `build_graph.py` 加提示 banner | 文档 |
| QA | ruff + 全量 pytest 无回归 | 验证 |

---

## 四、风险与边界
- **cognee.cognify 需要 LLM_API_KEY**（实体抽取需 LLM）：运行 A 阶段需确保 `.env`/环境有 key（此前验证能跑，说明已具备）。
- **打断既有行为风险低**：B1 只加警告不拦截（避免破坏 REST/MCP）；B2 抽取公共函数不改语义。
- **`cso_oncology.owl` 有效性**：上次验证已证明注入后能识别领域实体，沿用之。
- 不删 `build_graph.py`（保留对照），仅加提示。

## 附：如何判断"AOF 代码被真正执行"
三层证据：
1. **入口层**：脚本 `run_via_aof_chain.py` 只 `from bridge import ...`，无 `import cognee`、无 `ONTOLOGY_*` 环境变量。
2. **ontology 注入层**：`run_cognify_from_spec` 内 `config` 非 None 且含 `ontology_config`（可 assert）。
3. **装配层**：preflight 校验通过、document_parser 解析日志出现、aof result log 落盘。
