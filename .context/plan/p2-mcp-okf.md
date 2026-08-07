# P2 实现计划：MCP Server 新增 OKF 知识消费工具

> 日期: 2026-08-07
> 状态: 待审批

## 背景
P1 已实现 OKF/LLM Wiki 导出器（`exporters/okf_exporter.py`）+ Lint（`tools/knowledge_lint.py`）。
P2 目标是让 **AI Agent 通过 MCP 直接消费这些 OKF 知识包**——这是"Agent-Ready 知识资产化"的闭环。
项目已有轻量级 stdio MCP Server（`mcp_server.py`，JSON-RPC 2.0，7 个既有工具），本次在其上**新增 OKF 知识消费工具**。

## 交付物

### 1. `mcp_server.py` 新增 OKF 知识访问层
- **知识包目录定位**：默认目录 + `AOF_OKF_DIR` 环境变量可覆盖。
  - 环境变量 `AOF_OKF_DIR` → 若未设，则尝试 `AOF_SPEC_PATH` 中的 `okf.dir`（若有）→ 否则默认 `./okf_bundle`。
- 新增 4 个 MCP 工具（复用 P1 的 OKFExporter/OKFLinter 能力）：

| 工具 | 功能 | 关键参数 |
|------|------|---------|
| **`aof_okf_index`** | 读取知识包 `index.md`（渐进式披露目录），Agent 先看全局 | `bundle_dir`(可选) |
| **`aof_okf_get_concept`** | 按 path 读取单个 Concept 完整内容（frontmatter+body） | `path`(必须,如 `person/alice.md`)、`bundle_dir` |
| **`aof_okf_search_concepts`** | 按 type/title/tags/文本 搜索知识包内 Concept | `query`/`type`/`title`/`tag`/`limit`、`bundle_dir` |
| **`aof_okf_lint`** | 对知识包运行结构体检（断链/重复/口径冲突），返回报告 | `bundle_dir`(可选) |

- `bundle_dir` 参数可选；缺省时用解析出的默认知识包目录。
- 输出统一 JSON（确保 `json.dumps default=str` 可序列化）。

### 2. 知识包读取/搜索实现（工具 handler）
- **index**：读 `<bundle>/index.md`，返回 frontmatter + 正文分组内容。
- **get_concept**：`path` 需做路径安全校验（防目录穿越 `../`），解析为 bundle 内路径后读取。
- **search_concepts**：遍历 bundle 内所有 `.md`（排除 index/log），对 frontmatter + body 做小写子串匹配（type/title/tags/正文），按 limit 截断返回列表（path/title/type/description snippet）。
- **lint**：实例化 `OKFLinter`，`lint(bundle_dir)` 返回报告 dict。

### 3. 测试 `tests/test_mcp_server.py`
- 构造临时知识包目录 fixture。
- 用 JSON-RPC 请求模拟：`tools/call` 调用各 OKF 工具，断言响应。
- 覆盖：index 读取、get_concept 正常/路径穿越拒绝、search 命中/空、lint 报告、默认目录解析（monkeypatch 环境变量）。

## 设计要点
- **纯消费侧**：不新增导出工具（用户未选）；复用既有导出能力生成知识包。
- **路径安全**：`get_concept`/`search` 对 `bundle_dir` 外的访问做防护。
- **代码风格**：对齐现有 mcp_server.py（工具注册 McpTool + handler 函数 + build_server 组装）。
- 不破坏既有 7 个工具。

## 成功标准
- 4 个 OKF 工具能通过 JSON-RPC 正常调用并返回正确 JSON。
- 路径穿越被拒绝，搜索命中正确，lint 报告正确。
- 新测试全过 + ruff 通过 + 全量测试无回归。
