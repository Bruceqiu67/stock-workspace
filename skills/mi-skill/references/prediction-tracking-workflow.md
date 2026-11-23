# 预测跟踪工作流

## 用途

每次输出明日预判（Step 5）后，自动记录预判到文件系统，供后续复盘对比。

## 三步流程

### Step 1: 出预判时自动记录

在输出完「明日预判」后，立即用以下方式记录：

> ⚠️ 不要再手工拼 JSON 写 `accuracy` 字段。唯一入口是
> `prediction_tracker.py record`；它负责追加式 revision、`target_date`、
> `schema_version/rubric_version`、概率校验与旧字段迁移。

**方式 A — terminal / execute_code 调用同一 CLI（推荐）**：
```bash
python3 ~/.hermes/scripts/prediction_tracker.py record \
  --type recap \
  --summary "探底回升收小阴/小阳" \
  --direction 偏多 \
  --base-scenario "探底回升收小阴/小阳" --base-prob 65 \
  --opt-scenario "直接反弹收阳" --opt-prob 20 \
  --pes-scenario "继续中阴下跌" --pes-prob 15 \
  --support 3200 --resistance 3250 \
  --observation "科创50抗跌" \
  --data-source "manual" \
  --provider "deepseek-v4-flash"
```

> **记录语义（P5）**：同一天同 `type` 重跑不会覆盖历史，而是 `revision+1`，
> 旧记录写 `superseded_by`，新记录写 `supersedes`；`target_date` 缺失时按
> 交易日历取下一交易日，显式传入非交易日会顺延并标记 `target_date_adjusted`。
> 三情景概率必须各 0-100 且总和 100；默认 60/20/20 会标记
> `probability_source=default_60_20_20`。`list --history` 可查看被替代版本。

### Step 2: 收盘后或下次分析时复盘

当用户要求复盘或下次启动分析时：

```bash
# 复盘指定预判文件（推荐）
python3 ~/.hermes/scripts/prediction_tracker.py review --date 2026-11-23

# 不指定日期时，复盘最近一个存在未复盘记录的日期文件
python3 ~/.hermes/scripts/prediction_tracker.py review
```

这会：
1. 按记录中的 `target_date` 拉取目标交易日实际指数日K（缺失时用下一交易日）
2. 对比每一条预判的实际表现
3. 计算方向分/区间分/情景分/综合分
4. 写回 `reviewed/review_score/error_type/actual_outcome` 等规范字段
5. 自动迁移旧 `accuracy` 字段（报告会标注口径差异）

### Step 3: 查看准确率报告

```bash
python3 ~/.hermes/scripts/prediction_tracker.py report
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

## 评分规则（唯一 rubric，RUBRIC_VERSION = 2.0）

`prediction_tracker.py review` 是唯一评分入口；报告只消费它写回的字段。

| 维度 | 满分 | 规则 |
|------|:----:|------|
| 方向分 | 50 | 偏多正确：实际涨 > +0.5%；偏空正确：实际跌 < -0.5%；震荡正确：\|实际\| ≤ 1.5%；弱反弹：0~+1.0% 满分，>+1.0% 得35。方向相反=0，落在边界内=25 |
| 区间分 | 30 | 支撑/压力都存在时：实际高低点落在区间内=30，轻微越界=15，明显越界=0；**缺失支撑/压力时记 N/A，不默认给分** |
| 情景分 | 20 | 三情景概率必须各 0-100 且总和=100；按实际落点计算多分类 Brier 分数 `BS=Σ(p_c-o_c)^2`，情景分 = `20 × (1 - BS/2)` |
| 综合 | 0-100 | 已评分维度原始分之和；同时输出 `score_coverage`（已评分维度满分/100）与 `confidence` |

关键约定：

- **概率参与评分**：`brier_score` 与 `predicted_probabilities` 会写入复盘记录；概率和不为 100 时 `scenario_status=invalid_probabilities`、情景分 N/A。
- **不再有固定 15/20 分兜底**：区间或情景缺失时对应维度不进入总分，`score_coverage` 降低，避免“只判方向也拿 85 分”。
- **方向标签优先结构化**：记录里的 `direction_label` 优先；缺失时才用关键词分类，且已补充“冲高回落/低开高走/先跌后涨”等混合表述规则。
- 错误分类阈值与 `prediction-error-classification.md` 一致：方向 0.5%、震荡 1.5%。

## 文件位置

- 记录脚本: `~/.hermes/scripts/prediction_tracker.py`
- 每日记录: `~/.hermes/data/predictions/daily/YYYY-MM-DD.json`
- Cron prompt（收盘复盘）: `~/.hermes/cron/jobs.json` → job id `54e63b84a4b4`
- 工作流参考: `references/prediction-tracking-workflow.md`（本文件）
