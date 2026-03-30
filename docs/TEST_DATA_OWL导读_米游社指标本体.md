# TEST_DATA：OWL导读（米游社指标本体）

> 说明：本文件是 **测试数据导读**，对应本体文件：
> `/Users/chaihao/LLM/AOF/ontologies/TEST_DATA_miyoushe_metrics_minimal.owl`

## 1. 先看整体（这份 OWL 在做什么）

这份 OWL 把“米游社 SQL 指标口径”表达成 3 层：

1. 概念层（Class）：定义“世界里有什么类型”
2. 关系层（ObjectProperty）：定义“类型之间如何连接”
3. 实例层（NamedIndividual）：放入“具体表/指标/查询/字段”

当前规模（自动统计）：

- Class：21
- ObjectProperty：5
- NamedIndividual：67

## 2. 概念层（Class）怎么读

核心业务相关类：

- `DatabaseTable`：数据库表
- `SQLQuery`：SQL 查询
- `Metric`：指标
- `FilterCondition`：过滤条件字段
- `CommunityPlatform`：社区平台
- `Platform`：平台抽象类
- `Query`：查询抽象类
- `Process`：过程抽象类

继承关系（subClassOf）里，和本案例最相关的两条：

- `CommunityPlatform -> Platform`
- `SQLQuery -> Query`

## 3. 关系层（ObjectProperty）怎么读

这份本体里最重要的关系：

- `usesTable`：`SQLQuery -> DatabaseTable`
- `definesMetric`：`SQLQuery -> Metric`
- `usesFilterCondition`：`SQLQuery -> FilterCondition`

其余 `produces/develops` 是从基础样例本体继承来的通用关系，不是本案例重点。

## 4. 实例层（NamedIndividual）怎么读

### 4.1 表实例

- `dim_community.dim_community_post_info_snapshot`
- `dwd_community.dwd_community_post_tag_item_df`

### 4.2 指标实例

- `post_cnt_30d`
- `vertical_post_cnt_yesterday`
- `vertical_tag_post_cnt_yesterday`
- `quality_vertical_post_cnt_yesterday`

### 4.3 查询实例

- `sql_query_1`
- `sql_query_2`
- `sql_query_3`
- `sql_query_4`

### 4.4 字段/过滤条件实例

- `logregion`
- `logdate`
- `is_delete`
- `is_sandbox`
- `post_type`
- `forum_id`
- `game_id`
- `create_datetime`
- `content_tag`

### 4.5 平台实例

- `miyoushe`
- `mihoyo_community`（别名语义）

## 5. SQL 到 OWL 的映射（最关键）

可把每条 SQL 看成一个 `SQLQuery` 个体：

- `sql_query_1`
  - `usesTable -> dim_community.dim_community_post_info_snapshot`
  - `definesMetric -> post_cnt_30d`
- `sql_query_2`
  - `usesTable -> dim_community.dim_community_post_info_snapshot`
  - `definesMetric -> vertical_post_cnt_yesterday`
- `sql_query_3`
  - `usesTable -> dim_community.dim_community_post_info_snapshot`
  - `usesTable -> dwd_community.dwd_community_post_tag_item_df`
  - `definesMetric -> vertical_tag_post_cnt_yesterday`
- `sql_query_4`
  - `usesTable -> dim_community.dim_community_post_info_snapshot`
  - `usesTable -> dwd_community.dwd_community_post_tag_item_df`
  - `definesMetric -> quality_vertical_post_cnt_yesterday`

## 6. 你可以用这份导读回答什么问题

1. 一个指标来自哪条 SQL、依赖哪张表？
2. 某条 SQL 产出哪个业务指标？
3. 当前本体是否覆盖了主要过滤字段？
4. 新增指标时，应补“概念/关系/实例”的哪一层？

## 7. 为什么这份本体可用（验收）

在 AOF/cognee 第4轮运行中，本体匹配日志已达到：

- `No close match found = 0`

对应日志：

- `/Users/chaihao/LLM/cognee/logs/2026-03-25_12-05-58.log`

这说明：当前测试案例中的核心语义（表/指标/查询/平台/抽象概念）已被本体覆盖。

## 8. 推荐阅读顺序（5分钟速读）

1. 先读本导读第2、3、5节（建立框架）
2. 再打开 OWL 文件，用关键字搜索：
   - `owl#Class`
   - `owl#ObjectProperty`
   - `owl#NamedIndividual`
   - `sql_query_`
3. 最后对照日志确认匹配质量。

