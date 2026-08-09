# 检索增益量化（retrieval_gain）

验收标准 4：同一批文档，加解析层前后各跑 20 真实查询，量化 hit_rate / MRR 提升。

## 概念

用 **6 份领域 PDF**（零售 CRM：会员/积分/KPOS/商圈/营销/站点数据）建两个 cognee 数据集：

| 管道 | 建档方式 |
|---|---|
| **baseline** | `cognee.add(原 PDF)`，cognee 内置通用解析 |
| **enhanced** | `cognee.add(parse_document() 产物的 clean markdown)`，走 document_parser 解析层 |

两管道各跑 `queries.json` 的 **20 条查询**，用 `eval.py` 算 `hit_rate@k`（TOP-k 是否命中 golden 文档）与 `MRR`，对比量化解析层增益。

## 运行前提：配置 cognee embedding / LLM 端点

cognee 检索与 cognify 需要 embedding + LLM。当前环境缺 `OPENAI_API_KEY`/`EMBEDDING_ENDPOINT`，需在项目根 `.env`（已 gitignore）提供，支持 OpenAI-compatible 端点（OpenRouter / vLLM / Ollama 等）：

```env
# embedding（检索/向量化）
EMBEDDING_ENDPOINT=https://...            # OpenAI-compatible 端点
EMBEDDING_API_KEY=sk-...                  # 可用 LLM_API_KEY 复用
EMBEDDING_MODEL=openai/text-embedding-3-large

# LLM（cognify 实体抽取）
OPENAI_API_KEY=sk-...                     # 或 LLM_API_KEY
LLM_MODEL=openai/gpt-5-mini               # 或 gpt-4o 等
LLM_ENDPOINT=https://api.openai.com/v1
```

> 例：OpenRouter → `LLM_ENDPOINT=https://openrouter.ai/api/v1`，模型 `openrouter/...`
> 本地 Ollama → `EMBEDDING_ENDPOINT=http://localhost:11434/v1`，模型 `ollama/nomic-embed-text` 等。

## 运行

```bash
# 生成/验证数据集（可选；dataset/ 已含 6 份 PDF）
/tmp/aof_poc_venv/bin/python tools/retrieval_gain/gen_dataset.py

# 跑对比（需 .env 配置好）
.venv/bin/python tools/retrieval_gain/run_pipeline.py --cognee-root /Users/chaihao/LLM/cognee

# 只看指标（用已有结果 json）
.venv/bin/python tools/retrieval_gain/eval.py --results-json runs/enhanced_xxx.json --queries-json queries.json
```

输出：`tools/retrieval_gain/runs/{baseline,enhanced}_{ts}.json`（含 metrics + per-query）。

## 文件

```
dataset/          6 份领域 PDF（ground truth 可知）
queries.json      20 条查询 + golden 命中文档
gen_dataset.py    文档生成（reportlab）
eval.py           hit_rate@k / MRR 纯函数计算（可单测）
run_pipeline.py   两管道建档 + 检索 + 对比
```

---
> 关联：`docs/architecture/document-parser-design.md` §9 验收标准 4
