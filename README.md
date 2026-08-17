# Stock Workspace · A股三框架分析体系

基于资金流逆向交易法（mi-skill）+ 产业链机构视角（Serenity）+ 供应链瓶颈研究方法论，
封装为一套完整的 A 股分析工作流。由 Hermes Agent 驱动，可手动触发，也可通过 cron 全自动运行。

## 体系架构

```
三框架分析体系
├── skills/three-framework-analyst    ← 指挥中心（封装层）
│     数据源优先级 / 7大场景路由 / 调用顺序 / 数据流 / cron调度
├── skills/mi-skill                   ← 执行层（资金流+技术确认）
│     四维资金流分析 / 165条技术规则 / 买卖点 / 止损纪律
├── skills/serenity-perspective       ← 风格层（产业链+机构视角）
│     产业链"卡脖子"环节 / 机构行为解码 / 长线价值判断
└── skills/muxuuu-serenity-skill      ← 方法论层（供应链瓶颈研究法）
      市场叙事→系统变化→稀缺层→证据分级→排序的研究工作流
```

**执行顺序**：产业链研究（serenity + muxuuu）→ 资金流验证（mi-skill）→ 技术确认 → 综合操作建议。

## 目录结构

```
stock-workspace/
├── README.md                         ← 本文件
├── docs/
│   └── three-framework-architecture.md   ← 体系架构总览（数据流图/触发规则/场景路由）
├── skills/
│   ├── three-framework-analyst/      ← 指挥中心：7大场景工作流 + 调度时间线 + 预判体系
│   ├── mi-skill/                     ← 执行层：资金流交易法 + 技术规则库
│   ├── serenity-perspective/         ← 风格层：Serenity 股神思维框架
│   └── muxuuu-serenity-skill/        ← 方法论层：供应链瓶颈研究 + 证据标准
└── scripts/
    ├── portfolio_monitor.py          ← 持仓监控（PORTFOLIO 为示例数据，使用前替换）
    ├── candidate_scanner.py          ← 候选池扫描（CANDIDATES 为示例方向池）
    ├── market_snapshot.py            ← 收盘数据快照（指数+热股情绪代理）
    ├── premarket_collector.py        ← 盘前外围数据采集
    ├── candidate_refresh_state.py    ← 候选池刷新状态记录
    ├── xuji_stop_drop_monitor.py     ← 个股止跌信号监控示例
    └── robotics_monitor.sh           ← 板块三股监控示例（buy/stop 为占位）
```

## 7 大场景

| 场景 | 触发词 | 说明 |
|------|--------|------|
| A 大盘分析 | 「分析大盘」「今天市场」 | 指数/情绪/方向/关注点，纯信息报告 |
| B 方向选股 | 「选股」「推荐方向」 | Serenity 产业链评分 + 双框架 0-20 分打分 |
| C 持仓诊断 | 「看看持仓」 | 持仓仪表盘 + 逐只操作思路 |
| D 3框架分析 | 「3框架分析」 | 自动路由到 A/B/C |
| E 飞书报告 | 「给飞书发报告」 | PDF 生成 + 飞书 API 发送 |
| F 预测复盘 | 「复盘」「准确率」 | tracker.py 月度统计 + 偏差分析 |
| G 收盘复盘 | 工作日 16:00 自动 | 复盘昨日预判 + 记录明日预判（type=recap） |

## 数据源优先级

```
Level 1: 本地数据缓存  (~/.hermes/data/)
Level 2: MCP 工具 (mcp-eastmoney)  — sector_fund_flow / main_fund_rank / get_stock_quote / get_kline
Level 3: curl API                  — qt.gtimg.cn / hq.sinajs.cn / web.ifzq.gtimg.cn
Level 4: web_search                — 产业逻辑验证、新闻催化
```

## 使用方式

### 手动触发（Hermes Agent）

```
用户说「3框架分析」        → 加载 3 个分析 skill，默认大盘分析
用户说「3框架分析XX方向」  → 方向选股
用户说「3框架分析我的持仓」→ 持仓诊断
用户说「双体系分析 XX」    → 三框架 + 四视角联合研判
```

### 自动运行（cron 调度）

```
08:15 盘前数据采集 → 08:30 盘前简报 → 09:25 晨间报告(飞书PDF)
11:30 午盘速览(飞书PDF) → 15:05 收盘快照 → 15:10 持仓日报 → 15:15 候选池扫描
16:00 收盘复盘(飞书PDF, 预判明日 type=recap)
月首(1-7日) 候选池月度刷新
```

## 安全与隐私

本仓库为公开仓库，已做以下脱敏处理：

- `portfolio_monitor.py` 的 `PORTFOLIO` 持仓配置 → 替换为示例数据
- `robotics_monitor.sh` 的 `buy`/`stop` → 置为占位 0.0
- 飞书 `chat_id` → 替换为 `<YOUR_FEISHU_CHAT_ID>` 占位符
- 移除 skill 内嵌的 `.git` 目录

使用前请将示例/占位替换为你的真实配置。

## 风险提示

股票投资风险极高，可能损失全部本金。本仓库提供的是**思维框架和分析方法**，不是投资建议。
所有投资决策需个人负责，盈亏自负。
