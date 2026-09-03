# 采购 FIBO 映射试点 — 实验配置

## 实验设计

| 组 | spec 文件 | ontology | dataset 名 |
|---|---|---|---|
| 对照组 | `baseline_spec.json` | 无 | `procurement_fibo_pilot_baseline` |
| 实验组 | `fibo_spec.json` | `procurement_o2c_fibo.owl` | `procurement_fibo_pilot` |

## 文件说明

| 文件 | 用途 |
|---|---|
| `baseline_spec.json` | 对照组 spec（无 ontology） |
| `fibo_spec.json` | 实验组 spec（含 FIBO 本体） |
| `data/procurement_cases.md` | 15 个案例的文本数据 |
| `run_fibo_pilot.py` | 实验运行脚本 |
| `runs/` | 实验结果输出目录 |

## 运行方式

```bash
# 1. 确保环境变量已加载
set -a; source /Users/chaihao/LLM/AOF/.env; set +a

# 2. 运行对照组（基线）
cd /Users/chaihao/LLM/AOF/examples/procurement_fibo_pilot
python run_fibo_pilot.py --baseline

# 3. 运行实验组（FIBO 增强）
python run_fibo_pilot.py

# 4. 对比结果
diff runs/pilot_result.baseline.json runs/pilot_result.fibo.json
```

## 评估指标

1. **采购域实体识别率**: 识别出的实体中属于采购域的比例
2. **FIBO 语义实体识别率**: 是否正确识别 Contract、Commitment、Role 等 FIBO 概念
3. **跨案例一致性**: 15 个案例中的"审批人"是否统一识别为 role
4. **BFO 区分度**: 是否正确区分 continuant（承诺）vs occurrent（过程）

## 预期结果

| 指标 | 对照组 | 实验组 |
|---|---|---|
| 采购域实体占比 | ~30% | ~60%+ |
| FIBO 语义实体 | 0 | >10 |
| role 识别 | 0 | >5 |
| commitment vs process 区分 | 无 | 有 |

## 注意事项

- 实验需要 LLM_API_KEY（cognify 实体抽取）
- 每次运行会创建新的 dataset，不会覆盖已有数据
- 结果文件会落盘到 `runs/` 目录
