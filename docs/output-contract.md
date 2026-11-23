# 操作层输出契约 (Execution Output Contract)

> 实现：`scripts/output_contract.py`（`OUTPUT_CONTRACT_VERSION = 1.0.0`）
> 上游：`candidate_scanner.py` 的候选评分、`portfolio_construction.py` 的目标权重
> 下游：候选池 JSON、持仓报告、复盘/审计

## 1. 为什么需要操作层

研究报告给出“强买/观察”只是**研究评级**，不等于可执行指令。操作层把评级转换为
可追溯的交易计划，并明确在什么情况下计划作废。研究评级与交易动作必须分开：

- `research_rating`：评分引擎给出的研究排序结论（`STRONG_BUY_WATCH` 等）。
- `trade_action`：是否可执行（`BUY_ZONE` / `WAIT_ENTRY` / `WAIT_CONFIRMATION` /
  `OBSERVE` / `NO_TRADE`）。

非可排名、数据过期、恐慌未企稳、方向级淘汰、仲裁否决、组合约束未分配仓位时，
`trade_action=NO_TRADE` 或 `OBSERVE`，`position_size=0`。

## 2. 字段定义

| 字段 | 类型 | 说明 |
|------|------|------|
| `schema_version` | string | 操作契约版本 |
| `as_of` | date | 推导所用行情/K线日期 |
| `entry_zone` | [float, float] | 入场区间下沿/上沿，由现价、近20日低点、ATR 推导 |
| `stop` | float | 止损价，取入场下沿、近20日低点、MA20 的较严者，并限制最大回撤 |
| `target_1` | float | 第一目标，至少 1.2R，且不低于近20日高点/ATR 目标 |
| `target_2` | float | 第二目标，高于 target_1 |
| `position_size` | float | 目标仓位占总组合比例（0-1），来自风险预算与组合权重上限 |
| `position_size_pct` | float | 上述仓位的百分比展示 |
| `risk_pct` | float | 入场中值到止损的距离（%） |
| `risk_reward` | float | `(target_1 - entry_mid) / (entry_mid - stop)` |
| `invalidation` | string[] | 失效条件（跌破止损、未站稳入场区、方向淘汰、数据过期等） |
| `valid_until` | date | 计划有效期（按交易日历顺延） |
| `valid_until_basis` | string | `trading_calendar` 或 `weekday_approximation` |
| `confidence` | string | `high/medium/low`，随评分覆盖度与可排名状态下降 |
| `research_rating` | string | 研究评级，保留评分引擎原始标签 |
| `trade_action` | string | 可执行动作；与 research_rating 分离 |
| `atr_pct` | float | 推导使用的 ATR%（限制在 1%-12%） |
| `derivation` | object | 近20日高低点、MA20、风险预算、regime 乘数等审计信息 |

## 3. 推导规则

1. **入场区间**：`entry_low` 取“近20日低点×0.995”和“现价×(1-0.6×ATR%)”的较高者；
   `entry_high` 不超过现价×1.01。
2. **止损**：取 `entry_low×(1-ATR%)`、`近20日低点×0.985`、`MA20×0.97` 的最小值，
   再限制在 `entry_low×0.85` 之上，避免止损过远。
3. **目标**：`target_1` 至少为 1.2R；`target_2` 高于 `target_1`。目标价不是收益承诺。
4. **仓位**：`risk_budget=0.5%` / `risk_pct`，再乘市场状态乘数（正常 1.0、
   反弹 0.9、恐慌 0.3、未知 0），乘置信度乘数（high 1.0、medium 0.75、low 0.4），
   最后受单票上限 15% 与组合层目标权重约束；低于 0.5% 视为不建仓。
5. **有效期**：默认 5 个交易日；交易日历缺失时用工作日近似并标注
   `valid_until_basis=weekday_approximation`。

## 4. 示例

```json
{
  "schema_version": "1.0.0",
  "as_of": "2026-01-05",
  "entry_zone": [10.86, 11.20],
  "stop": 10.60,
  "target_1": 11.86,
  "target_2": 12.42,
  "position_size": 0.1020,
  "position_size_pct": 10.2,
  "risk_pct": 4.90,
  "risk_reward": 1.35,
  "invalidation": [
    "收盘价跌破止损 10.60",
    "有效期内未站稳入场区上沿 11.20",
    "评分数据降级/弃权，研究结论不可执行"
  ],
  "valid_until": "2026-01-12",
  "valid_until_basis": "trading_calendar",
  "confidence": "high",
  "research_rating": "STRONG_BUY_WATCH",
  "trade_action": "BUY_ZONE",
  "atr_pct": 2.1
}
```

## 5. 校验

`validate_trade_plan(plan)` 检查：

- `stop < entry_low <= entry_high < target_1 < target_2`
- `position_size` 在 0-1；可执行计划的风险收益比不低于 1.0
- `NO_TRADE/OBSERVE` 时仓位必须为 0
- 必须给出 `invalidation` 与 `valid_until`

扫描器会把校验失败写入 `operation_contract_errors`，不会静默放过。
