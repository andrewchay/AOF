# AOF 通用图谱展示模板

文件：`visualization/kg_vis_aof_template.html`

## 1) 本地预览

```bash
cd /Users/chaihao/LLM/AOF/visualization
python3 -m http.server 8000
```

打开：
- `http://localhost:8000/kg_vis_aof_template.html`

## 2) URL 模式（默认）

在页面中填写：
- `nodes URL`
- `edges URL`

也可直接通过参数自动加载：

```text
http://localhost:8000/kg_vis_aof_template.html?nodes=/path/to/nodes.json&edges=/path/to/edges.json&autoload=1
```

## 3) API 模式

页面切到 `API`：
- `API Base URL`：如 `http://localhost:8787`
- `Dataset`：如 `default`

会调用：
- `/v1/datasets/{dataset}/data?limit=200000&offset=0`

## 4) 字段自动适配

### 节点字段（任一命中即可）
- id: `id | uid | _id | node_id`
- name: `name | title | label | properties.name | properties.title`
- category: `category | type | labels[0] | properties.category | properties.type`

### 边字段（任一命中即可）
- source: `source | source_id | from | src | start | subject`
- target: `target | target_id | to | dst | end | object`
- relation: `type | relation_type | relation | predicate | label`

## 5) 交互能力

- 搜索（节点名称/分类）
- 按评分切换核心/扩展/全量
- 分类 chip 筛选
- 物理模拟开关
- 双击节点聚焦、双击空白恢复
