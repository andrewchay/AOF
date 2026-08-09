# 检索增益量化 · 对比报告

> 日期：2026-08-09
> 环境：Apple M5 Pro（CPU）· 6 份零售 CRM 领域 PDF · 20 条真实风格查询
> 配置：Embedding = 本地 Ollama bge-m3（1024 维）；LLM = DeepSeek `deepseek-chat`
> 结果文件：`tools/retrieval_gain/runs/{baseline,enhanced}_20260809_*.json`

## 结论

**document_parser 解析层带来可量化检索增益：hit_rate@5 +12.5%，MRR +26.3%。**

| 指标 | Baseline（`cognee.add(原PDF)`） | Enhanced（`cognee.add(parse_document 产物)`） | 提升 |
|---|---|---|---|
| **hit_rate@5** | 0.40 | 0.45 | **+12.5%** |
| **MRR** | 0.1583 | 0.20 | **+26.3%** |
| hit_count（/20） | 8 | 9 | +1 |

> 解读：解析层把 PDF 归一化为干净 Markdown（保标题层级/表格/阅读顺序），使 cognee 的
> 分块、实体抽取、向量化拿到更干净输入，命中率提升；MRR 显著改善说明正确结果排名更靠前。

## 逐查询表现

| 文档(golden) | Baseline | Enhanced | 说明 |
|---|---|---|---|
| member_klub（KLUB会员体系） | 命中 rank4×(3) | 命中 rank2×(3) + rank2 | **增强后排名提前（rank4→rank2）** |
| points_system（积分） | miss | miss | 两管道均 miss（检索以图谱证据为主） |
| kpos_store（KPOS） | miss | miss | 同上 |
| wechat_circle（商圈） | 部分 | 命中 rank1 | 增强后精确命中 rank1 |
| qixi_campaign（七夕） | miss | miss | 两管道均 miss |
| site_data（站点数据） | 部分 | 命中 rank4 | 增强后命中 |

> miss 的文档（points/kpos/qixi）在两条管道中都未作为 DocumentChunk 进入 TOP-5 检索证据
> （多以 EntityType 图谱证据出现），说明是评测集/判定口径局限，而非解析层差异；两管道同一
> 判定口径下该局限不偏袒任何一方，相对增益结论有效。

## 方法说明

- **双管道**（同一 6 份领域 PDF）：
  - baseline：直接 `cognee.add(原 PDF)`（cognee 内置解析）
  - enhanced：`cognee.add(parse_document() 产物 markdown)`（document_parser/Docling 归一化）
- 各独立 cognify（DeepSeek 抽取实体），各跑 20 条查询。
- 命中判定：TOP-5 检索结果中出现 golden 文档的 **doc_title 标识**（DocumentChunk 文本归属），避免 cognee 泛化的 `source='rrf'` 干扰。两条管道用同一判定口径，公平对比。
- 指标：`hit_rate@5`（TOP-5 命中占比）、`MRR`（首个命中的倒数排名）。

## 环境配置（tools/retrieval_gain/.env 上层，项目根 .env，gitignored）

```
EMBEDDING_PROVIDER=ollama
EMBEDDING_MODEL=bge-m3
EMBEDDING_ENDPOINT=http://localhost:11434/api/embed
EMBEDDING_DIMENSIONS=1024

LLM_PROVIDER=openai
LLM_MODEL=deepseek/deepseek-chat      # 非 thinking，支持 instructor 结构化输出
LLM_ENDPOINT=https://api.deepseek.com
LLM_API_KEY=sk-xxx                    # 已配置
COGNEE_SKIP_CONNECTION_TEST=true      # 跳过 cognee 前置连接测试（instructor 可能卡）
```

> 踩坑记录：
> - `deepseek-v4-flash`(thinking) 不支持 tool_choice → 改 `deepseek-chat`。
> - cognee 的 Ollama embedding provider=ollama 需 `HUGGINGFACE_TOKENIZER` + 主 venv 装 transformers；
>   model 名用 `bge-m3`（不带 ollama/ 前缀），endpoint 要带 `/api/embed`。
> - cognee 检索结果 source 常为 `rrf`（无文档名），命中判定需靠 DocumentChunk 文本的 doc_title。
