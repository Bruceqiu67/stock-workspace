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
│   │   └── YYYY-MM-DD.json    # 7指数 + 8热股 + 成交额
│   ├── predictions/
│   │   ├── daily/             # 预判记录（每次出预判后写入）
│   │   │   └── YYYY-MM-DD.json
│   │   └── tracker.py         # record/review/report/list
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
- 脚本最后 `sys.exit(1 if alarm_count > 0 else 0)` — 有止损/浮亏>10%告警时 exit(1)
- Cron 把 exit 1 标记为 "error"，但数据已成功写入 `current.json`，不影响下游使用
- 这不是 bug，cron 的 error 状态可以忽略

**应对**：距上次更新超过1个交易日时，用 MCP `get_stock_quote` 逐只拉当前价，重算浮盈浮亏。持股数通过 `market_val ÷ last_price` 取整推算。用户说「清仓了」后，手动从脚本股票列表移除该标的。

### 收盘快照（market_snapshots JSON）

`market_snapshots/YYYY-MM-DD.json` 由 `market_snapshot.py` cron 在 15:05 采集。可能缺失的场景：
- cron 故障或重启未恢复
- 假期/非交易日不产生新快照

**应对**：先 `ls -1t` 找最新文件，检查日期是否匹配上一交易日。不匹配时用 web_search 补昨日收盘数据。

### 候选池（candidates JSON）

`candidates/YYYY-MM-DD.json` 由 `candidate_scanner.py` cron 在 15:15 采集。注意：
- 脚本有硬编码候选列表（~43-54只覆盖9-10个方向），随版本更新可能增减
- 硬过滤门会淘汰价格>100元或52周相对位置>70%的标的
- 如果次日发现候选池大小出入（如54→43只），可能是脚本版本不同，检查脚本内的 `CANDIDATES` 字典

## 注意

- no_agent cron 的 stdout 直接推送给用户，脚本设计时注意：正常运行时输出简洁摘要，完全失败时 exit 1
- 所有脚本读取 `HERMES_HOME` 环境变量，默认 fallback 到 `~/.hermes`
