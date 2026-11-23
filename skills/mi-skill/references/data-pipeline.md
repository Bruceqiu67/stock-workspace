# 每日数据管道

交易日自动化调度链，按时间顺序排列。

## 调度表

| 时间 | 类型 | 脚本/cron ID | 功能 | 产出 |
|:----:|:----:|-------------|------|------|
| 08:15 | no_agent | `premarket_collector.py` / `6b6bf3ad9773` | 盘前数据采集 | `premarket/YYYY-MM-DD.json` |
| 08:30 | LLM | 盘前简报 / `c4f7d92df114` | 盘前简报生成 | CLI/飞书推送 |
| 09:25 | LLM+4skills | 晨间三框架报告 / `2ce447d6b75d` | 三框架深度分析 | PDF/飞书 |
| 15:05 | no_agent | `market_snapshot.py` / `7c70d6eea4fa` | 收盘数据快照 | `market_snapshots/YYYY-MM-DD.json` |
| 15:10 | no_agent | `portfolio_monitor.py` / `fec5a277200a` | 持仓日报 | `portfolio/current.json` |
| 15:15 | no_agent | `candidate_scanner.py` / `1ee1017ed106` | 候选池扫描 | `candidates/YYYY-MM-DD.json` |
| 16:00 | LLM+4skills | 收盘复盘报告 / `54e63b84a4b4` | 复盘+明日预判 | PDF/飞书 |

## 数据目录

```
~/.hermes/
├── data/
│   ├── market_snapshots/      # 收盘快照（15:05 cron）
│   │   └── YYYY-MM-DD.json    # 7指数 + 8热股 + 全市场宽度/情绪分 + 成交额
│   ├── predictions/
│   │   ├── daily/             # 预判记录（每次出预判后写入）
│   │   │   └── YYYY-MM-DD.json
│   │   └── 工具：仓库 scripts/prediction_tracker.py（record/review/report/list）
│   ├── portfolio/
│   │   └── current.json       # 当前持仓状态（15:10 cron 更新）
│   ├── premarket/
│   │   └── YYYY-MM-DD.json    # 盘前数据（08:15 cron 采集）
│   └── candidates/
│       └── YYYY-MM-DD.json    # 当日候选池（15:15 cron 扫描）
└── scripts/
    ├── market_snapshot.py      # 收盘快照采集
    ├── portfolio_monitor.py    # 统一持仓监控
    ├── premarket_collector.py  # 盘前数据采集
    ├── candidate_scanner.py    # 候选池扫描
    ├── robotics_monitor.sh     # 旧版机器人三股监控（可删除）
    └── xuji_stop_drop_monitor.py  # 旧版许继电气监控（用户已清仓，可删除）
```

## 读取优先级

分析时按此顺序获取数据：

```
MCP 工具（mcp-eastmoney）可用  →  优先用 MCP（结构化、无编码问题）
  ↓ MCP 工具不可用
缓存匹配今日日期              →  读缓存文件（免 curl）
  ↓ 缓存不存在或日期不匹配
curl 实时拉取                →  腾讯/新浪/东方财富 API
  ↓ curl 失败
web_search / 热股情绪代理法  →  最低精度兜底
```

## 交易日语义与数据新鲜度（I3）

- `scripts/trading_calendar.py` 提供 `is_trading_day / previous_trading_day / next_trading_day / freshness`。
- **周一盘前读上一交易日（通常是周五），不是“日历昨天（周日）”**；节假日可在 `~/.hermes/data/trading_calendar.json` 配置 `holidays` 与 `extra_trading_days`。
- 所有缓存读取（盘前快照、市场状态、预判复盘）都要校验目标日期；输出必须携带 `as_of`、`data_age_days`、`data_age_trading_days`、`freshness`、`expected_previous_trading_day`。
- `freshness.status`：`fresh`（同日）/ `previous_trading_day`（盘前读昨收，属预期）/ `stale`（早于上一交易日）/ `future` / `missing`；`stale=true` 时下游必须降级并显示数据缺口。
- 候选扫描的 `data_freshness`、市场快照的 `freshness`、市场状态的 `freshness`、盘前 JSON 的 `a_share_yesterday.freshness` 使用同一套语义。

## 缓存 vs 实时判断

- 用户问「收盘怎么样」「昨天」→ 读缓存
- 用户问「现在」「当前」「实时」→ 必须实时 curl/MCP
- 跨日对比 → 昨天读缓存，今天实时拉取
- 用户说「刷新一下」→ 忽略缓存强制实时

## 已知数据缺口

### 盘前数据（premarket JSON）

`premarket/YYYY-MM-DD.json` 由 `premarket_collector.py` 在 08:15 采集。以下字段**可能为空**：
- `a_share_yesterday`：A股昨日收盘数据（依赖上一交易日收盘行情，API 有时不可用）
- `hot_sectors`：热点板块（依赖东方财富板块排行接口，可能被封锁）

**应对**：盘前简报/三框架任务中，不要因空字段而放弃。用 web_search + browser_navigate + MCP 补充：搜索「A股 昨日日期 收盘 上证 成交额」补昨日指数数据，用 `mcp-eastmoney sector_fund_flow` 补板块排行。

### 持仓数据（portfolio JSON）

`portfolio/current.json` 由 `portfolio_monitor.py` cron 在交易日 15:10 更新。可能过时的场景：
- 周一更新后，周三才做简报 → 数据已隔2个交易日，持仓价可能大幅变化
- cron 未运行 → 数据可能是一周前的
- 用户手动清仓/加仓 → 脚本仍显示旧持仓，因为它是从硬编码股票列表拉行情，不知道用户交易

**portfolio_monitor exit code 行为**：
- 脚本最后固定 `sys.exit(0)` — 告警只写入 stdout 与 `current.json`，不再用 exit(1) 表示“有告警”。
- 因此 cron 的 status=ok 不代表没有风险；必须读取 `stocks[].alarm_level` 与
  `target_portfolio.validation_errors`。
- 恐慌态告警不再机械按止损线，而是“绝对风险底线 + 相对强度 + 企稳条件”三层判断；
  `current.json` 会写入 `market_regime / alarm_bases / target_weight / rebalance_action`。

**应对**：距上次更新超过1个交易日时，用 MCP `get_stock_quote` 逐只拉当前价，重算浮盈浮亏。持股数通过 `market_val ÷ last_price` 取整推算。用户说「清仓了」后，手动从脚本股票列表移除该标的。

### 收盘快照（market_snapshots JSON）

`market_snapshots/YYYY-MM-DD.json` 由 `market_snapshot.py` cron 在 15:05 采集。可能缺失的场景：
- cron 故障或重启未恢复
- 假期/非交易日不产生新快照

**应对**：先 `ls -1t` 找最新文件，检查日期是否匹配上一交易日。不匹配时用 web_search 补昨日收盘数据。

### 候选池（candidates JSON）

`candidates/YYYY-MM-DD.json` 由 `candidate_scanner.py` cron 在 15:15 采集。注意：
- 候选来源分两层：`scripts/candidate_universe.py` 的重点观察池（9 个方向、39 只）+ 可选的全市场宇宙 `~/.hermes/data/candidate_universe.json`
- 缺少全市场宇宙配置时，输出 `universe.status=watchlist_only`、`selection_bias_risk=high`，报告不得称为“全市场选股”
- 过滤阈值由 `scripts/filter_policy.py` 统一提供（观察池/交易池分开），被过滤标的进入 `filtered.near_miss` 并记录原因
- 方向内全部空头排列时输出 `direction_veto` 与 `direction_health`；买入标签改为 `OBSERVE_DIRECTION_BEARISH`
- 组合层 `portfolio_plan` 给出目标暴露/现金/目标权重/`adjustments`；每只候选的 `operation` 给出 `entry_zone/stop/target_1/target_2/position_size/invalidation/valid_until/trade_action`
- 数据日期：顶层 `date` 是扫描日，`target_date/as_of` 是数据所属交易日；`data_freshness` 与每只候选的 `data_freshness` 都应检查。K线早于上一交易日时 `action_label=DATA_STALE`、`rankable=false`
- 候选池大小的权威来源是 `candidate_universe.py` 的 `WATCHLIST`，文档不再写死数量

## 注意

- no_agent cron 的 stdout 直接推送给用户，脚本设计时注意：正常运行时输出简洁摘要，完全失败时 exit 1
- 所有脚本读取 `HERMES_HOME` 环境变量，默认 fallback 到 `~/.hermes`
