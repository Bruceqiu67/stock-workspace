# 三框架分析体系 · 优化路线图

> 更新于 2026-07-13，基于多次三框架分析实际运行反馈。
> 完整版本见 E:\Document\youhua\三框架分析优化报告.md

## 当前体系结构

数据采集                 分析层                   输出层
──────────────          ────────────              ─────────
行情(腾讯API)            mi-skill 资金流           CLI 文本
K线(腾讯API)             serenity-perspective       PDF 报告
资金流向(东财API)         serenity-skill             cron 推送
外围+新闻(web_search)    165条规则库

## 优化项优先级

### ✅ P0 · 板块排行数据源（已完成）

- push2 clist 封锁问题 → **已通过 mcp-eastmoney 的 `sector_fund_flow` 解决**
- 方案：mcp-eastmoney MCP 提供板块涨幅排行+主力净流入+领涨股，5个工具覆盖核心需求
- 依赖：仅 mcp + httpx + pydantic 三个包，~2MB
- 详见 `references/mcp-data-sources.md`

### ✅ P0 · 预测跟踪与复盘（已完成）

- 预测无反馈闭环 → **已搭建完整预测跟踪系统**
- 方案：`~/.hermes/data/predictions/tracker.py`（record / review / report / list）
- 每日预判自动记录到 daily/YYYY-MM-DD.json，收盘后 review 命令三维度评分
- 已写入 mi-skill 步骤8 强制执行
- 详见 `references/prediction-tracking-workflow.md`

### ✅ P1 · 收盘数据自动采集（已完成）

- 脚本：`~/.hermes/scripts/market_snapshot.py`（no_agent cron，15:05）
- 采集：7指数（上证/深成/创业板/科创50/沪深300/国证2000/中证1000）+ 8热股 + 成交额
- 产出：`~/.hermes/data/market_snapshots/YYYY-MM-DD.json`
- cron: `7c70d6eea4fa`，交易日 15:05

### ✅ P1 · 持仓自动化监控（已完成）

- 脚本：`~/.hermes/scripts/portfolio_monitor.py`（no_agent cron，15:10）
- 持仓：中科曙光(90.13/100股)、海康威视(33.84/300股)、双环传动(42.36/300股)、江苏雷利(30.72/400股)，总仓位40%
- 输出：表格（现价/成本/涨跌/浮亏%/浮亏¥/止损）+ 总投入/市值/浮盈
- 状态文件：`~/.hermes/data/portfolio/current.json`
- cron: `fec5a277200a`，交易日 15:10

### ✅ P2 · 盘前策略准备（已完成）

- **数据采集**：`~/.hermes/scripts/premarket_collector.py`（no_agent cron，08:15）
  - 美股：腾讯 usDJI/usIXIC/usINX
  - 亚太：新浪 int_nikkei/int_hangseng/int_kospi
  - 昨日A股：读 market_snapshots 缓存
  - 产出：`~/.hermes/data/premarket/YYYY-MM-DD.json`
- **盘前简报**：LLM cron（08:30，加载 mi-skill + serenity-perspective）
  - 读盘前数据 + web_search 产业新闻 → 生成结构化简报
  - cron: `c4f7d92df114`

### ✅ P2 · 研报/产业数据聚合（已完成，合并入盘前简报）

- 每日开盘前通过盘前简报 cron 的 web_search 覆盖 3-4 个重点方向
- 不单独运行 cron，避免重复消耗

### P3 · 选股候选池

- 方案：每日跑板块扫描+硬过滤输出候选池

### P3 · 多模型交叉验证

- 方案：关键判断同时调 2-3 个模型取交集

## 技能调用机制说明

三个 skill 不自动同时加载，触发方式：

1. 用户说「3框架分析」→ Hermes 注入的人格规则触发三个 skill_view()
2. cron 晨间报告(09:25) → cron 配置指定 skills: [mi-skill, serenity-perspective, serenity-skill]

当前问题：
- 依赖记忆/人格中的触发规则，非 Hermes 原生机制
- skill 间无显式依赖链，只是文本约定
- 数据不写入 skill，跨 session 不可复用
