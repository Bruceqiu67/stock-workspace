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
python3 ~/.hermes/data/predictions/tracker.py record \
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
# 查看历史准确率
python3 ~/.hermes/data/predictions/tracker.py report
python3 ~/.hermes/data/predictions/tracker.py report --by-type   # 按预判类型分组+偏差分析

# 列出最近记录
python3 ~/.hermes/data/predictions/tracker.py list
```

**注意**：tracker.py 的 `review` 命令只复盘今日记录（`_load_today()`），不支持 `--date` 参数。收盘复盘 cron 的场景G 通过直接读取 `predictions/daily/{yesterday}.json` 来复盘昨日预判，详见下方连续性校验。

## 复盘连续性校验（16:00 场景G 专用）

`54e63b84a4b4` cron 在复盘昨日预判时，执行以下连续性检查（已固化到 prompt 的 Step 2）：

1. **文件检查**：尝试读取 `predictions/daily/{yesterday}.json`。不存在则 `ls -t predictions/daily/*.json | head -3` 找最近文件
2. **日期间隔计算**：`days_gap = today - last_recap_date`
   - `== 1` → 正常
   - `> 1` → 标注缺失天数（中间可能是非交易日或运行失败）
   - 无任何记录 → 跳过复盘
3. **绝对不**因昨日缺失而回退到更早的预判评分。详见 SKILL.md 的 pitfall。

## 注意事项

- 同一日期下只有一条 recap 预判，不会相互覆盖
- **复盘连续性校验**：不要回退到更早预判来评分今日市场
- 预判文件自动创建，无需手动初始化
- **历史回测发现**：简化版方向准确率 ~60.7%，偏空预判准确率偏低（~48%），存在系统性偏悲观倾向。详见 backtest-guide.md

## 数据文件

每条预判存储为 JSON 对象，合并写入 `daily/{today}.json`：
