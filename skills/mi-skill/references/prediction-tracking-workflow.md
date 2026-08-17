# 预测跟踪工作流

## 用途

每次输出明日预判（Step 5）后，自动记录预判到文件系统，供后续复盘对比。

## 三步流程

### Step 1: 出预判时自动记录

在输出完「明日预判」后，立即用以下方式记录：

**方式 A — execute_code（推荐）**：
```python
from hermes_tools import terminal
import json, datetime

today = datetime.date.today().isoformat()
entry = {
    "id": f"pred_{datetime.date.today().strftime('%Y%m%d')}_001",
    "date": today,
    "type": "market_direction",
    "prediction": {
        "summary": "探底回升收小阴/小阳",  # ← 替换为实际预判摘要
        "base_scenario": "探底回升收小阴/小阳",  # ← 基准情景
        "base_probability": 65,
        "optimistic_scenario": "直接反弹收阳",
        "optimistic_probability": 20,
        "pessimistic_scenario": "继续中阴下跌",
        "pessimistic_probability": 15,
        "support_level": 3200,  # ← 实际支撑位
        "resistance_level": 3250,  # ← 实际压力位
        "key_observation": "科创50抗跌"  # ← 关键判断依据
    },
    "actual": None,
    "accuracy": None,
    "provider": "deepseek-v4-flash",
    "provider_index": 0,
    "created_at": datetime.datetime.now().isoformat(timespec="minutes"),
    "notes": ""
}

# 读取已有记录
import os, pathlib
data_dir = pathlib.Path(os.environ.get("HERMES_HOME", pathlib.Path.home() / ".hermes")) / "data" / "predictions" / "daily"
data_dir.mkdir(parents=True, exist_ok=True)

path = data_dir / f"{today}.json"
records = json.loads(path.read_text()) if path.exists() else []
records.append(entry)
path.write_text(json.dumps(records, ensure_ascii=False, indent=2))
```

**方式 B — terminal**（简单场景，不依赖 execute_code）：
```bash
python3 ~/.hermes/data/predictions/tracker.py record \
  --summary "探底回升收小阴/小阳" \
  --base-scenario "探底回升收小阴/小阳" \
  --base-prob 65 \
  --opt-scenario "直接反弹收阳" \
  --opt-prob 20 \
  --pes-scenario "继续中阴下跌" \
  --pes-prob 15 \
  --support 3200 \
  --resistance 3250 \
  --observation "科创50抗跌" \
  --provider "deepseek-v4-flash"
```

> **注意**：如果当日已有预判记录（如 cron 晨间报告已记录），新预判会追加到同一日期的 records 数组中，不覆盖。

### Step 2: 收盘后或下次分析时复盘

当用户要求复盘或下次启动分析时：

```bash
python3 ~/.hermes/data/predictions/tracker.py review
```

这会：
1. 自动拉取上证指数实时数据
2. 对比今日每一条预判的实际表现
3. 计算方向分/情景分/区间分/综合分
4. 更新记录文件

### Step 3: 查看准确率报告

```bash
python3 ~/.hermes/data/predictions/tracker.py report
```

输出示例：
```
📈 累计分析: 15 条已复盘
   累计综合准确率: 72.3/100

   月度统计:
     2026-07: 68.5/100 (8 条)
     2026-08: 76.1/100 (7 条)
```

## 与每日扫描工作流的集成

在 `daily-market-scan-workflow.md` 的 Step 5（明日预判）之后，**强制要求**立即执行 Step 1 的记录操作。不允许输出预判后跳过记录步骤。

## 与 cron 晨间报告的集成

交易日 09:25 的晨间报告 cron job，在输出报告后也需要记录当日预判（如果有明确的明日走势判断）。

## 复盘连续性校验（重要）

收盘复盘（场景G）读取昨日预判时，必须处理文件缺失和日期间断：

### 标准检查流程

1. **检查文件是否存在**：
   - 读取 `~/.hermes/data/predictions/daily/{yesterday}.json`
   - 如果文件不存在，`ls -t ~/.hermes/data/predictions/daily/*.json | head -3` 找最近文件
   - 读取最近有 `type=recap` 的预判文件，记为 `last_recap_date`

2. **计算日期间隔**：
   - `days_gap = today - last_recap_date`（按日历天计）
   - `days_gap == 1`：正常，标「昨日预判」
   - `days_gap > 1`：有间断（非交易日或运行失败），标「⚠️ 距上次预判已间隔 {N} 天缺失」
   - 无任何 `type=recap` 记录：跳过复盘，标「首日运行，无昨日预判可复盘」

3. **绝对不要**回退到更早的预判来评分当前市场。如果昨日无记录，跳过评分，标注缺失日期。

**真实 bug 案例**：2026-07-21 的收盘复盘因 2026-07-20 文件不存在（API 挂起导致 C 轮没跑），误用 2026-07-17 的预判复盘 7/20 的走势。

## 评分规则（两套模式）

### 模式A：tracker.py review 命令（3维度，满分各1.0）

| 维度 | 满分 | 规则 |
|------|:----:|------|
| 方向分 | 1.0 | 预判涨/跌 vs 实际涨/跌，一致=1.0，相反=0.0 |
| 情景分 | 1.0 | **当前为占位符** — tracker.py 中 scenario_score 固定为 1.0，未实现真实情景判断 |
| 区间分 | 1.0 | 收盘价在预判支撑~压力区间内=1.0，偏差<0.5%=0.7，偏差<1%=0.4，更大=0.0 |
| 综合 | (方向+情景+区间)/3 × 100 | 转为百分制 |

**注意**：tracker.py 的 cmd_review 中 `scenario_score` 始终返回 1.0（未实现情景匹配逻辑），实际综合分 ≈ (方向 + 1.0 + 区间) / 3 × 100。

### 模式B：收盘复盘 cron 场景G（2维度，满分各50）

收盘复盘 cron `54e63b84a4b4`（16:00 运行）使用简化版评分：

| 维度 | 满分 | 规则 |
|------|:----:|------|
| 方向分 | 50 | 预判涨跌方向 vs 实际涨跌方向。一致=50，不一致=0 |
| 区间分 | 50 | 收盘在预判支撑~压力之间=50，偏离按幅度扣分 |
| 综合 | 方向 + 区间 | 满分100 |

## 文件位置

- 记录脚本: `~/.hermes/data/predictions/tracker.py`
- 每日记录: `~/.hermes/data/predictions/daily/YYYY-MM-DD.json`
- Cron prompt（收盘复盘）: `~/.hermes/cron/jobs.json` → job id `54e63b84a4b4`
- 工作流参考: `references/prediction-tracking-workflow.md`（本文件）
