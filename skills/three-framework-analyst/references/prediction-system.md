# 预判体系

## 角色定位

预判是每日分析的核心产出，形成「复盘昨日→记录今日→明日验证」的闭环。
系统每天只产生一条预判（`type=recap`），在 16:00 收盘复盘时完成。
09:25 晨间报告是纯信息报告，不做预判记录。

## 核心规则

> **只有 16:00 复盘做预判。09:25 晨报不做。**

## 生命周期

```
Day N  16:00  review(复盘昨日 recap) → record type=recap  ──→  预判 Day N+1
                                                                ↓
Day N+1 09:25  晨报（纯信息，不预判）
Day N+1 16:00  review(复盘 Day N recap) → record type=recap  ──→  预判 Day N+2
```

首日运行时无昨日预判可复盘，标注「首日运行」正常跳过。

## 记录命令

```bash
# 场景G：收盘复盘预判
python3 ~/.hermes/scripts/prediction_tracker.py record \
  --type recap \
  --summary "明日走势预判" \
  --base-scenario "..." --base-prob 65 \
  --opt-scenario "..." --opt-prob 20 \
  --pes-scenario "..." --pes-prob 15 \
  --support XXX --resistance XXX \
  --observation "..." \
  --provider "当前provider名"
```

## 复盘命令

```bash
# 复盘：拉取实际行情并写回规范字段（推荐显式指定预判文件日期）
python3 ~/.hermes/scripts/prediction_tracker.py review --date {yesterday}
python3 ~/.hermes/scripts/prediction_tracker.py review --date {yesterday} --dry-run

# 查看历史准确率
python3 ~/.hermes/scripts/prediction_tracker.py report
python3 ~/.hermes/scripts/prediction_tracker.py report --by-type   # 按预判类型分组+偏差分析

# 列出最近记录
python3 ~/.hermes/scripts/prediction_tracker.py list
```

**注意**：`review --date` 会读取该日期文件中的 `target_date`（缺失时按下一交易日），
拉取实际指数日K，并写回 `reviewed/review_score/error_type/actual_outcome` 等规范字段。
未复盘记录在报告中显示为“待复盘”，不会静默计入均分。旧的 `accuracy` 字段会自动迁移。

评分口径为唯一 rubric（`RUBRIC_VERSION=2.0`）：方向 50 + 区间 30 + 情景 20。
支撑/压力缺失时区间分记 N/A；情景分按三情景概率的 Brier 分数计算，概率和必须为 100。
评分结果同时输出 `score_coverage` 与 `confidence`，避免缺维度时虚高。

## 复盘连续性校验（16:00 场景G 专用）

`54e63b84a4b4` cron 在复盘昨日预判时，执行以下连续性检查（已固化到 prompt 的 Step 2）：

1. **文件检查**：尝试读取 `predictions/daily/{yesterday}.json`。不存在则 `ls -t predictions/daily/*.json | head -3` 找最近文件
2. **日期间隔计算**：`days_gap = today - last_recap_date`
   - `== 1` → 正常
   - `> 1` → 标注缺失天数（中间可能是非交易日或运行失败）
   - 无任何记录 → 跳过复盘
3. **绝对不**因昨日缺失而回退到更早的预判评分。详见 SKILL.md 的 pitfall。

## 注意事项

- 记录改为**追加式**：同一天同 type 重跑会新增 `revision` 并给旧记录写 `superseded_by`，历史版本不再被静默覆盖
- 每条记录包含 `target_date`（预判指向的交易日）、`trading_day`、`data_source`、`schema_version`、`rubric_version`
- 三情景概率必须各为 0-100 且总和为 100，否则 `record` 直接拒绝写入；未显式指定时默认 60/20/20 会标记 `probability_source=default_60_20_20`
- `target_date` 必须晚于预判日；显式传入非交易日会顺延到下一交易日并标记 `target_date_adjusted`
- **复盘连续性校验**：不要回退到更早预判来评分今日市场
- 预判文件自动创建，无需手动初始化
- **历史回测发现**：旧口径（开盘→收盘）方向准确率 ~60.7%，偏空预判偏低（~48%）。该口径与“次日收盘相对前收”不同，**不可用于校准**；新口径回测见 `backtest-guide.md` v2.0

## 数据文件

每条预判存储为 JSON 对象，追加写入 `daily/{today}.json`：

```json
{
  "id": "pred_20261123_recap_01",
  "schema_version": "1.1",
  "rubric_version": "2.0",
  "probability_source": "explicit_cli",
  "revision": 1,
  "supersedes": null,
  "date": "2026-11-23",
  "target_date": "2026-11-24",
  "trading_day": true,
  "type": "recap",
  "direction_label": "震荡",
  "scenarios": {"base": {"text": "...", "prob": 60}},
  "support": 3300,
  "resistance": 3380,
  "reviewed": false,
  "review_score": null,
  "error_type": null,
  "actual_outcome": null,
  "data_source": "market_snapshots+manual",
  "provider": "local"
}
```
