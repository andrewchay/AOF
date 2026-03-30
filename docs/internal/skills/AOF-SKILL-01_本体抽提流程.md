---
name: aof-skill-01
description: AOF本体抽提标准流程，涵盖从数据摄取到本体生成、对齐迭代的完整工序。适用于基于SQL/文档自动生成OWL本体并进行多轮迭代对齐的场景。
---

# AOF-SKILL-01: 本体抽提流程

> **定位**: AOF核心工序，将结构化/非结构化数据转化为机器可理解的本体知识。

---

## 一、核心思想

基于 **Lean Semantic Web** 方法论：
- **最小本体先行**: 不追求完美初始本体，快速生成后迭代优化
- **数据驱动涌现**: 让本体从数据中"长"出来，而非预设
- **对齐闭环**: 每轮对齐产生反馈，反馈驱动本体演化

---

## 二、使用场景

| 场景 | 示例 |
|------|------|
| 数据库表结构本体化 | 将 `inventory` 表结构转化为 `Inventory` 类本体 |
| 业务指标本体构建 | 从 SQL 指标查询中提取 `Metric` 定义 |
| 文档概念抽取 | 从非结构化文档中抽取 `Entity` 和 `Relation` |
| 多源数据融合 | 统一多个数据源的术语和概念体系 |

---

## 三、执行步骤

### Phase 0: 数据准备与摄取

**目标**: 将原始数据转化为 AOF 可处理格式

#### 结构化数据（SQL/CSV/JSON）

```bash
# SQL 数据
python tools/data_adapter/normalize_for_aof.py \
  --input data/inventory.sql \
  --output-txt logs/normalized_inventory.txt \
  --output-jsonl logs/normalized_inventory.jsonl

# 验证输出
head -5 logs/normalized_inventory.jsonl
```

#### 混合文档（代码+文本标注）

AOF 支持从混合文档中提取本体，包括：

| 文档类型 | 扩展名 | 内容结构 | 典型用途 |
|----------|--------|----------|----------|
| Jupyter Notebook | `.ipynb` | 代码 + Markdown | 数据分析流程 |
| Markdown | `.md` | 代码块 + 说明 | API 文档 |
| XML 标注 | `.xml`, `.annot` | `<snippet code="..." annotation="..."/>` | 标注数据集 |
| 代码文件 | `.py`, `.sql`, `.js` | 代码 + 注释 | 代码库语义 |

```bash
# Jupyter Notebook
python tools/data_adapter/normalize_for_aof.py \
  --input docs/analysis.ipynb \
  --output-txt logs/normalized_notebook.txt \
  --output-jsonl logs/normalized_notebook.jsonl

# Markdown 文档
python tools/data_adapter/normalize_for_aof.py \
  --input docs/api_guide.md \
  --output-txt logs/normalized_guide.txt \
  --output-jsonl logs/normalized_guide.jsonl

# XML 标注文件
python tools/data_adapter/normalize_for_aof.py \
  --input data/annotated_snippets.xml \
  --output-txt logs/normalized_annotated.txt \
  --output-jsonl logs/normalized_annotated.jsonl

# Python 代码文件（自动提取注释和代码关系）
python tools/data_adapter/normalize_for_aof.py \
  --input src/data_processor.py \
  --output-txt logs/normalized_code.txt \
  --output-jsonl logs/normalized_code.jsonl
```

**混合文档输出格式**:

```jsonl
{"type": "code", "lang": "sql", "_query_sql": "SELECT * FROM users", "_context_annotation": "查询所有用户"}
{"type": "annotation", "_concepts": ["User", "Query"], "_text": "用户表存储基本信息"}
{"type": "code_with_annotation", "_code": "def get_users():", "_annotation": "获取用户列表", "_tags": ["API", "User"]}
```

---

#### 数据库 Schema 直接提取（推荐）

AOF 支持直接连接数据库，自动提取 Schema 并生成 OWL 本体。这种方式比导出 SQL 文件更准确、更高效。

**支持的数据库**:

| 数据库 | 标识符 | 说明 |
|--------|--------|------|
| PostgreSQL | `postgresql` | 推荐，功能最全 |
| MySQL | `mysql` | 支持，注意字符集 |
| SQLite | `sqlite` | 本地开发测试 |

**Schema 到本体的映射规则**:

| 数据库元素 | OWL 元素 | 说明 |
|------------|----------|------|
| Table | `owl:Class` | 表名转驼峰命名 |
| Column | `owl:DatatypeProperty` | 列名作为属性，SQL 类型转 XSD 类型 |
| Primary Key | `owl:FunctionalProperty` | 函数属性，唯一性约束 |
| Foreign Key | `owl:ObjectProperty` | 对象属性，表间关系 |

```bash
# PostgreSQL - 从现有数据库提取 Schema
python tools/extract_db_schema.py \
  --db-url "postgresql://user:pass@localhost/mydb" \
  --provider postgresql \
  --output ontologies/mydb_schema.owl

# SQLite - 本地数据库
python tools/extract_db_schema.py \
  --db-url "sqlite:///data/local.db" \
  --provider sqlite \
  --output ontologies/local_schema.owl

# 生成差异报告（查看将添加哪些新类，但不修改本体）
python tools/extract_db_schema.py \
  --db-url "$DATABASE_URL" \
  --output ontologies/existing.owl \
  --mode diff

# 合并模式 - 将新发现的表/列添加到现有本体
python tools/extract_db_schema.py \
  --db-url "$DATABASE_URL" \
  --output ontologies/existing.owl \
  --mode merge
```

**环境变量方式**:

```bash
export DATABASE_URL="postgresql://user:pass@localhost/mydb"

python tools/extract_db_schema.py \
  --output ontologies/mydb_schema.owl \
  --mode merge
```

**生成的 OWL 结构示例**:

对于数据库表 `users` (id, name, email) 和 `orders` (id, user_id, amount):

```xml
<!-- 表映射为类 -->
<owl:Class rdf:about="http://example.org/ontology#User">
  <rdfs:comment>Database table: users</rdfs:comment>
  <rdfs:subClassOf rdf:resource="http://example.org/ontology#DatabaseTable"/>
</owl:Class>

<owl:Class rdf:about="http://example.org/ontology#Order">
  <rdfs:comment>Database table: orders</rdfs:comment>
  <rdfs:subClassOf rdf:resource="http://example.org/ontology#DatabaseTable"/>
</owl:Class>

<!-- 列映射为 DatatypeProperty -->
<owl:DatatypeProperty rdf:about="http://example.org/ontology#hasName">
  <rdfs:domain rdf:resource="http://example.org/ontology#User"/>
  <rdfs:range rdf:resource="http://www.w3.org/2001/XMLSchema#string"/>
</owl:DatatypeProperty>

<!-- 外键映射为 ObjectProperty -->
<owl:ObjectProperty rdf:about="http://example.org/ontology#relatesToUser">
  <rdfs:domain rdf:resource="http://example.org/ontology#Order"/>
  <rdfs:range rdf:resource="http://example.org/ontology#User"/>
  <rdfs:comment>Foreign key relationship to users</rdfs:comment>
</owl:ObjectProperty>
```

**质量检查**:
- [ ] JSONL 格式合法
- [ ] 每条记录包含 `_table`, `_kind` 字段
- [ ] 文本内容非空

**输出**:
- `logs/normalized_{topic}_{timestamp}.txt` - 纯文本格式
- `logs/normalized_{topic}_{timestamp}.jsonl` - JSONL格式（含元数据）

---

### Phase 0.5: 混合文档语义提取

**目标**: 从混合文档中提取代码语义和文本概念

当输入为混合文档（Jupyter/Markdown/XML/代码文件）时，系统会自动：

1. **分离代码和文本**：识别代码块 vs 文本说明
2. **提取概念**：从 Markdown/注释中提取 `ClassName`, `术语` 等
3. **建立关系**：关联代码块与其前置的文本注释
4. **语言检测**：自动识别 SQL/Python/JavaScript 等代码类型

**支持的语义提取**:

```python
# 从代码中提取
- SQL: 表名 (FROM/JOIN)、字段、别名
- Python: 函数名、类名、导入关系
- 通用: 代码结构、注释上下文

# 从文本中提取
- Markdown 章节标题
- 粗体/斜体强调的术语
- 反引号包裹的代码术语
- 大写驼峰命名（类名识别）
```

**混合文档本体结构**:

生成的本体将包含以下扩展类：

```
Class: CodeSnippet          # 代码片段
  ├── Property: lang        # 编程语言
  ├── Property: hasContext  # 关联的注释
  └── Property: implements  # 实现的概念

Class: Annotation           # 文本标注
  ├── Property: annotates   # 描述的代码
  ├── Property: defines     # 定义的概念
  └── Property: tags        # 标签列表

Class: SemanticConcept      # 提取的语义概念
  └── Property: mentionedIn # 出现的文档位置

Relation: CodeAnnotationRelation
  ├── Domain: CodeSnippet
  ├── Range: Annotation
  └── 表示 "代码被注释描述"
```

---

### Phase 1: 生成本体（冷启动）

**目标**: 基于数据语义生成最小本体

```bash
# 生成本体（跳过对齐，仅构建）
python tools/ontology_factory/build_testdata_ontology_factory.py \
  --input data/inventory.sql \
  --topic inventory \
  --skip-align \
  --max-iterations 1
```

**质量检查**:
- [ ] OWL 文件格式合法（XML 可解析）
- [ ] 包含核心类：`DatabaseTable`, `Metric`, `SQLQuery`
- [ ] 包含表名对应的 Individual

**输出**:
- `ontologies/TEST_DATA_{topic}_ontology.owl` - 生成的本体文件

---

### Phase 2: 对齐迭代

**目标**: 通过多轮对齐迭代，扩展本体覆盖度

```bash
# 执行对齐迭代
export LLM_API_KEY="your-key"
python tools/ontology_factory/build_testdata_ontology_factory.py \
  --input data/inventory.sql \
  --topic inventory \
  --max-iterations 4
```

**迭代过程**:

| 轮次 | 目标 | 检查点 |
|------|------|--------|
| Iter 1 | 发现未匹配概念 | unmatched_terms < 15 |
| Iter 2 | 系统性修正 | matched_count > 50% |
| Iter 3 | 收敛验证 | unmatched_terms < 5 |
| Iter 4 | 终态确认 | matched_count > 90% |

**质量检查**:
- [ ] 对齐报告生成成功
- [ ] 每轮 matched_count 递增
- [ ] 最终未匹配项 < 5% 或已人工确认

**输出**:
- `logs/ontology_factory/TEST_DATA_alignment_report_{topic}_{timestamp}.json`
- `logs/ontology_factory/TEST_DATA_alignment_report_{topic}_{timestamp}.md`
- `logs/ontology_factory/TEST_DATA_feedback_candidates_{topic}_{timestamp}.jsonl`

---

### Phase 3: 反馈注入（可选）

**目标**: 基于人工反馈修正本体

```bash
# 编辑反馈文件
vim logs/ontology_factory/TEST_DATA_feedback_candidates_inventory_*.jsonl

# 重新运行（带反馈）
python tools/ontology_factory/build_testdata_ontology_factory.py \
  --input data/inventory.sql \
  --topic inventory \
  --feedback-jsonl logs/ontology_factory/TEST_DATA_feedback_candidates_inventory_*.jsonl \
  --max-iterations 2
```

**反馈格式**:
```jsonl
{"action": "add_class", "name": "ProductCategory", "parent": "Class"}
{"action": "map_term", "term": "SKU", "class": "StockKeepingUnit"}
```

---

## 四、常见错误模式

### 模式1: 解析失败

**症状**: `normalize_for_aof.py` 报错或输出为空

**原因**:
- 输入文件编码错误（非 UTF-8）
- SQL 语法过于复杂（存储过程、触发器）
- 文件格式识别错误

**修复**:
```bash
# 检查编码
file -i data/input.sql

# 转换编码
iconv -f GBK -t UTF-8 data/input.sql > data/input_utf8.sql

# 手动指定格式
python tools/data_adapter/normalize_for_aof.py \
  --input data/input.sql \
  --kind sql \
  ...
```

---

### 模式2: 混合文档解析失败

**症状**: Jupyter/Markdown 文件解析报错或提取内容为空

**原因**:
- 文件编码错误（非 UTF-8）
- Jupyter Notebook 格式损坏（nbformat 版本不兼容）
- Markdown 代码块语法不规范（未闭合的 ```）
- XML 标注文件格式错误

**修复**:
```bash
# 检查文件编码并转换
file -i docs/notebook.ipynb
iconv -f GBK -t UTF-8 docs/notebook.ipynb > docs/notebook_utf8.ipynb

# 验证 Jupyter Notebook JSON 结构
python -c "
import json
with open('docs/notebook.ipynb') as f:
    nb = json.load(f)
    print(f\"cells: {len(nb.get('cells', []))}\")
    print(f\"format: {nb.get('nbformat', 'unknown')}\")
"

# 手动指定格式（跳过自动检测）
python tools/data_adapter/normalize_for_aof.py \
  --input docs/guide.md \
  --kind mixed \
  --output-txt logs/normalized.txt \
  --output-jsonl logs/normalized.jsonl

# 检查提取结果
python tools/data_adapter/mixed_document_parser.py \
  --input docs/notebook.ipynb \
  --output /tmp/parsed.jsonl
cat /tmp/parsed.jsonl | head -10
```

---

### 模式3: 本体生成失败

**症状**: OWL 文件为空或格式错误

**原因**:
- 输入数据无有效表结构
- 权限问题导致无法写入 ontologies/ 目录

**修复**:
```bash
# 检查输入数据
python -c "
from tools.ontology_factory.build_testdata_ontology_factory import parse_sql_semantics
from pathlib import Path
result = parse_sql_semantics(Path('data/input.sql'))
print(f'Tables: {result[\"tables\"]}')
"

# 检查目录权限
ls -la ontologies/
```

---

### 模式3: 对齐迭代不收敛

**症状**: 多轮迭代后 unmatched_count 不降低

**原因**:
- 初始本体过于简单，无法覆盖数据概念
- 存在系统性术语不匹配

**修复**:
1. 检查对齐日志，提取高频未匹配项
2. 手动编辑 feedback.jsonl，添加映射规则
3. 重新运行对齐

---

### 模式4: LLM API 调用失败

**症状**: 对齐过程报错 `LLMAPIKeyNotSetError`

**修复**:
```bash
export LLM_API_KEY="your-key"
export LLM_PROVIDER="custom"
export LLM_MODEL="deepseek/deepseek-chat"
export LLM_ENDPOINT="https://api.deepseek.com/v1"
```

---

## 五、最佳实践

### 1. 数据准备阶段

- **优先使用 SQL**: 结构化数据比非结构化更容易抽取本体
- **清理测试数据**: 移除敏感信息，确保数据可公开处理
- **分批次处理**: 大数据量分批处理，避免内存溢出

### 2. 本体生成阶段

- **接受不完美**: 初始本体只需覆盖 60-70% 概念即可
- **保留中间产物**: 每轮迭代的本体文件都保留，便于回溯
- **版本控制**: 将本体文件纳入版本控制，跟踪演化过程

### 3. 对齐迭代阶段

- **设置合理的 max_iterations**: 通常 3-5 轮足够收敛
- **监控关键指标**: matched_count, unmatched_count, auto_added
- **及时人工介入**: 当自动对齐停滞时，通过反馈引导

---

## 六、示例

### 示例0: 数据库 Schema 自动提取（推荐）

直接从 PostgreSQL 数据库提取 Schema 生成本体，无需手动导出 SQL 文件。

```bash
# 前提：设置数据库连接
export DATABASE_URL="postgresql://username:password@localhost:5432/ecommerce_db"

# 步骤1: 提取 Schema 并生成本体（合并模式）
python tools/extract_db_schema.py \
  --db-url "$DATABASE_URL" \
  --provider postgresql \
  --output ontologies/ecommerce_db.owl \
  --mode merge

# 预期输出:
# ============================================================
# 数据库 Schema 提取完成
# ============================================================
# 
# 发现的表数量: 5
# 
# 新类 (5):
#   • User
#   • Order
#   • Product
#   • Category
#   • OrderItem
# 
# 新属性 (18):
#   • hasAmount
#   • hasCategoryId
#   • hasCreatedAt
#   • hasEmail
#   • hasId
#   ...
# 
# ✅ 输出文件: /path/to/ontologies/ecommerce_db.owl

# 步骤2: 验证生成的本体
head -50 ontologies/ecommerce_db.owl

# 步骤3: 使用生成的本体进行对齐迭代
export LLM_API_KEY="your-api-key"
python tools/ontology_factory/build_testdata_ontology_factory.py \
  --input ontologies/ecommerce_db.owl \
  --topic ecommerce_db \
  --max-iterations 3

# 步骤4: 数据库 Schema 变更后更新本体
# 当数据库新增表或字段时，重新运行提取命令
python tools/extract_db_schema.py \
  --db-url "$DATABASE_URL" \
  --output ontologies/ecommerce_db.owl \
  --mode merge
```

**查看差异报告（不修改本体）**:

```bash
# 在正式更新前，先查看将添加哪些内容
python tools/extract_db_schema.py \
  --db-url "$DATABASE_URL" \
  --output ontologies/ecommerce_db.owl \
  --mode diff

# 输出示例:
# # Schema 差异报告
# 
# ## 新发现的类 (2)
# - Inventory: Database table: inventory
# - Warehouse: Database table: warehouses
# 
# ## 新发现的属性 (8)
# - hasStock (datatype): Column stock_quantity of type integer
# - hasWarehouseCode (datatype): Column code of type varchar
# - relatesToWarehouse (object): Foreign key relationship to warehouses
# ...
```

---

### 示例1: 电商库存本体抽取

```bash
# 1. 数据准备
cat > data/inventory.sql << 'EOF'
CREATE TABLE inventory (
    sku VARCHAR(50) PRIMARY KEY,
    product_name VARCHAR(200),
    quantity INT,
    warehouse_location VARCHAR(100)
);

INSERT INTO inventory VALUES 
('SKU-001', 'iPhone 15', 100, 'WH-BJ-01'),
('SKU-002', 'MacBook Pro', 50, 'WH-SH-02');
EOF

# 2. 生成本体
python tools/data_adapter/normalize_for_aof.py \
  --input data/inventory.sql \
  --output-txt logs/inventory.txt \
  --output-jsonl logs/inventory.jsonl

python tools/ontology_factory/build_testdata_ontology_factory.py \
  --input data/inventory.sql \
  --topic ecommerce_inventory \
  --max-iterations 3

# 3. 验证结果
ls -la ontologies/TEST_DATA_ecommerce_inventory_ontology.owl
ls -la logs/ontology_factory/*ecommerce_inventory*
```

---

### 示例2: Jupyter Notebook 数据分析流程本体抽取

```bash
# 1. 创建示例 Notebook
cat > docs/user_analysis.ipynb << 'EOF'
{
 "cells": [
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": ["# 用户行为分析\\n", "## 目标：分析活跃用户的行为模式"]
  },
  {
   "cell_type": "code",
   "execution_count": 1,
   "metadata": {},
   "source": [
    "-- 查询活跃用户\\n",
    "SELECT user_id, login_count, last_login\\n",
    "FROM users\\n",
    "WHERE login_count > 10;"
   ]
  },
  {
   "cell_type": "markdown",
   "metadata": {},
   "source": ["## 计算用户留存率\\n", "使用 `retention_rate` 指标评估"]
  },
  {
   "cell_type": "code",
   "execution_count": 2,
   "metadata": {},
   "source": [
    "SELECT date_trunc('week', login_date) as week,\\n",
    "       COUNT(DISTINCT user_id) as dau\\n",
    "FROM user_logins GROUP BY 1;"
   ]
  }
 ],
 "metadata": {"kernelspec": {"display_name": "SQL"}},
 "nbformat": 4,
 "nbformat_minor": 4
}
EOF

# 2. 数据准备（自动识别 Jupyter 格式）
python tools/data_adapter/normalize_for_aof.py \
  --input docs/user_analysis.ipynb \
  --output-txt logs/notebook_analysis.txt \
  --output-jsonl logs/notebook_analysis.jsonl

# 3. 生成本体
python tools/ontology_factory/build_testdata_ontology_factory.py \
  --input docs/user_analysis.ipynb \
  --topic user_analysis_notebook \
  --max-iterations 3

# 4. 验证提取的语义
python -c "
import json
with open('logs/notebook_analysis.jsonl') as f:
    for i, line in enumerate(f, 1):
        rec = json.loads(line)
        print(f'{i}. {rec[\"raw\"].get(\"_kind\", \"unknown\")}: {rec[\"text\"][:50]}...')
"

# 5. 查看生成的本体
ls -la ontologies/TEST_DATA_user_analysis_notebook_ontology.owl
```

**生成的本体包含**：
- `CodeSnippet` 类（SQL/Python 代码块）
- `Annotation` 类（Markdown 章节、说明文字）
- `SemanticConcept` 类（提取的 `retention_rate`, `活跃用户` 等概念）
- `usesTable` 关系（代码使用的 users/user_logins 表）

---

### 示例3: Markdown API 文档本体抽取

```bash
# 1. 创建 Markdown 文档
cat > docs/api_guide.md << 'EOF'
# 订单管理 API

## 创建订单

使用 `createOrder` 方法创建新订单。

```sql
INSERT INTO orders (user_id, product_id, quantity)
VALUES (?, ?, ?);
```

**参数说明**：
- `user_id`: 用户ID，关联 `users` 表
- `product_id`: 产品ID

## 查询订单状态

```sql
SELECT status, created_at FROM orders WHERE order_id = ?;
```
EOF

# 2. 提取本体
python tools/data_adapter/normalize_for_aof.py \
  --input docs/api_guide.md \
  --output-txt logs/api_guide.txt \
  --output-jsonl logs/api_guide.jsonl

python tools/ontology_factory/build_testdata_ontology_factory.py \
  --input docs/api_guide.md \
  --topic api_guide \
  --max-iterations 2

# 3. 验证概念提取
grep -o 'createOrder\|user_id\|product_id\|orders\|users' \
  logs/api_guide.jsonl | sort | uniq -c
```

---

## 七、关联文档

- **元技能**: [迭代工作流](../methodology/00-META-02_迭代工作流.md), [质量控制](../methodology/00-META-10_质量控制.md)
- **工具文档**: [Ontology Factory](../tools/ontology_factory/README.md)
- **API文档**: [Semantic Middle Layer API](../../services/semantic_middle_layer_api/API.md)

---

## 八、变更日志

| 版本 | 日期 | 变更 |
|------|------|------|
| v1.1 | 2024-03-28 | 新增混合文档支持（Jupyter/Markdown/XML/代码文件） |
| v1.0 | 2024-03-28 | 初始版本，基于 AOF 项目实践总结 |
