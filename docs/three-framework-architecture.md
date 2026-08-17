# 三框架分析体系 · 架构总览

> 整理日期：2026-08-18
> 定位：本文件是**体系说明文档**，不参与 skill 加载，现有 4 个 skill 均未改动。
> 目的：一张图说清"三框架体系是什么、由谁组成、怎么触发、数据怎么流、7 大场景怎么路由"。
> 权威来源：各 skill 的 SKILL.md 与 references 文件。本文档如有与 skill 内容冲突之处，以 skill 为准。

---

## 1. 体系组成与分层

三框架体系 = **1 个指挥中心 + 3 个分析框架**，共 4 个 skill：

| 层级 | Skill（目录名） | 定位 | 核心职责 | 典型内容 |
|------|----------------|------|---------|---------|
| **指挥中心** | `three-framework-analyst` | 封装层 | 定义数据源优先级、7 大场景路由、调用顺序、数据流、cron 调度 | scenarios.md / daily-schedule.md / prediction-system.md / data-sources.md |
| **执行层** | `mi-skill` | 资金流+技术确认 | 四维资金流分析（市场环境→板块轮动→个股行为→技术确认）、买卖点、止损纪律、165 条技术规则 | rules.md / dual-framework-analysis.md / eastmoney-api-patterns.md |
| **风格层** | `serenity-perspective` | 产业链+机构视角 | 产业链"卡脖子"环节识别、机构行为解码、长线价值判断、估值重置框架 | 心智模型 / 决策启发式 / 表达 DNA |
| **方法论层** | `muxuuu-serenity-skill` | 供应链瓶颈研究法 | 系统化研究工作流：市场叙事→系统变化→稀缺层→证据分级→排序；证据标准与跨市场数据源路径 | evidence-ladder.md / market-source-playbook.md / deep-research-workflow.md |

### 三层框架的分工关系

| 维度 | Skill | 解决什么问题 |
|------|-------|-------------|
| 产业链/机构视角 | `serenity-perspective`（风格层） | 产业链位置、卡脖子环节、机构行为、估值重置 |
| 研究方法论 | `muxuuu-serenity-skill`（方法论层） | 供应链瓶颈研究方法、证据标准、跨市场数据源 |
| 资金流/技术确认 | `mi-skill`（执行层） | 资金流向、量价配合、均线排列、买卖点判断 |

> ⚠️ **注册名坑**：`muxuuu-serenity-skill` 的 SKILL.md frontmatter 中 `name: serenity-skill`，但 Hermes 按**目录名**注册。因此 cron 的 skills 数组、memory 触发规则中必须写 `muxuuu-serenity-skill`，写 `serenity-skill` 会静默跳过（无报错无日志）。

---

## 2. 触发规则（当前生效）

### 2.1 手动触发（memory 人格规则）

| 用户说 | 加载的 skill | 默认行为 |
|--------|-------------|---------|
| 「3框架分析」 | mi-skill + serenity-perspective + muxuuu-serenity-skill | 默认路由**大盘分析**（纯信息报告，不做预判记录） |
| 「3框架分析 XX方向」 | 同上 | 路由**方向选股** |
| 「3框架分析我的持仓」 | 同上 | 路由**持仓诊断** |
| 「双体系分析 XX」 | 全部 4 个：mi-skill + serenity-perspective + muxuuu-serenity-skill + four-perspective-analysis | 三框架与四视角**独立跑完再联合研判** |
| 「4视角框架分析 XX」 | four-perspective-analysis | 四视角独立深度报告 |

> ⚠️ 当前触发规则**未包含指挥中心** `three-framework-analyst`。即用户说"3框架分析"时加载的是三个源 skill，场景路由/数据源优先级靠人格与记忆间接引导。若要封装更完整，可考虑在触发规则中加入指挥中心（本轮按用户要求未改动）。

### 2.2 cron 自动触发

cron 任务的 `skills` 数组按任务配置（晨间报告 4 skills、盘前简报 2 skills、月度刷新 3 skills），详见第 5 节时间线。

---

## 3. 数据流：四级数据源优先级

```
Level 1: 本地数据缓存  (~/.hermes/data/)
  ├── market_snapshots/YYYY-MM-DD.json     — 指数+热股快照
  ├── portfolio/current.json                — 持仓实时状态（每次运行被覆盖）
  ├── premarket/YYYY-MM-DD.json             — 盘前外围数据
  ├── candidates/YYYY-MM-DD.json            — 当日候选池
  ├── predictions/daily/YYYY-MM-DD.json     — 历史预判记录
  └── predictions/                          — tracker.py 复盘数据

Level 2: MCP 工具 (mcp-eastmoney)
  ├── sector_fund_flow(kind="industry")      → 行业板块排行+资金流向
  ├── sector_fund_flow(kind="concept")       → 概念板块排行+资金流向
  ├── main_fund_rank(limit=20)               → 主力资金净流入排行
  ├── get_stock_quote(code="600519")          → 个股实时行情
  ├── search_stock(keyword="宁德")            → 按名称/代码搜索
  └── get_kline(code="600519", period="daily") → K线数据

Level 3: curl API
  ├── qt.gtimg.cn      — 指数/个股实时行情（GBK编码需 decode）
  ├── hq.sinajs.cn     — 美股/国际指数（需 Referer header）
  └── web.ifzq.gtimg.cn — K线数据（需 -sL 处理302）

Level 4: web_search（慢，5-15秒）
  └── 产业逻辑验证、新闻催化、财报搜索
```

**核心原则**：
1. 优先读本地缓存，缓存不存在才实时拉取
2. 实时拉取按优先级：MCP 工具 → curl API → web_search
3. 数据不可得时标注「数据暂缺」，不编造
4. 全市场扫描优先 `sector_fund_flow`（一次调用出板块全貌）

**港股数据例外**：mcp-eastmoney 只支持 A 股，港股代码（如 01810）会导致 server 断连。港股行情/K 线一律走腾讯 API：
- 实时：`qt.gtimg.cn/q=hk{code}`
- K线：`web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=hk{code},day,,,30,qfq`（先 curl 存文件再 python3 解析，避免 curl|python3 管道被安全拦截）

---

## 4. 三框架分析执行顺序与输出规范

### 4.1 标准执行顺序

```
Step 1  产业链研究（serenity-perspective 风格 + muxuuu-serenity-skill 方法论）
        → 先排产业链层级，再排公司；优先看"卡脖子"环节
        → 证据分级：强 / 中 / 弱 / 未核实线索
Step 2  资金流验证（mi-skill）
        → MCP 数据加速：sector_fund_flow + main_fund_rank + get_stock_quote
        → 四维分析：市场环境 → 板块轮动 → 个股行为 → 技术确认
Step 3  技术确认（mi-skill）
        → 周线定方向，日线找买点；让主力先动
        → 先过三道硬过滤门（绝对价格 / 90日相对位置 / 均线排列）
Step 4  综合输出
        → Serenity 产业链结论 → 资金流验证 → 技术买卖点 → 综合操作建议
```

### 4.2 市场状态识别（分析前必做）

| 状态 | 特征 | 策略基调 |
|:----|:-----|:--------|
| **正常/震荡** | 上证在20日线上方，单日振幅<2% | 正常轮动，选方向做个股 |
| **恐慌/趋势下行** | 连续2天放量下跌，创业板单日-5%+，候选池90%飘绿 | 守为主，不做新开仓，等缩量止跌 |
| **反弹/右侧启动** | 缩量见底后放量阳线，板块普涨 | 优先加仓持仓中相对强势的个股 |

恐慌模式下：不找进场点只盯企稳信号；相对强度 > 绝对涨跌；不机械执行止损线（"两头挨打"比"不止损"更危险）。

### 4.3 操作建议输出规范

```
入场区间 / 止损线 / 目标价 / 仓位建议 / 时间窗口
```
- 止损是铁律，给出明确止损位
- 仓位建议要具体（如"≤1成"、"半仓"），不模糊
- 区分短线博弈与中线配置，不混淆

---

## 5. 交易自动化时间线（cron 调度）

### 5.1 每日任务

```text
08:15  📡 盘前数据采集 (no_agent)     premarket_collector.py → premarket/{today}.json
08:30  📋 盘前简报 (LLM + mi-skill + serenity-perspective)
09:25  📊 晨间三框架报告 (LLM + 4 skills) → 飞书PDF   [纯信息，不记录预判]
11:30  🌤 午盘市场速览 (LLM + 4 skills) → 飞书PDF    [纯信息快报]
15:00  收盘
15:05  📸 收盘数据快照 (no_agent)     market_snapshot.py → market_snapshots/{today}.json
15:10  📈 持仓日报 (no_agent)         portfolio_monitor.py → portfolio/current.json
15:15  🎯 候选池扫描 (no_agent)       candidate_scanner.py → candidates/{today}.json
16:00  🔄 收盘复盘报告 (LLM + 4 skills) → 飞书PDF    [复盘昨日预判 + 记录明日预判 type=recap]
```

### 5.2 月度任务

```text
月首(1-7日) 🗂️ 候选池月度刷新 (LLM + 3 skills)
        检查 refresh_state.json，距上次 >=28 天才执行
        产出: 更新 candidate_scanner.py 的 CANDIDATES 字典
        未到刷新周期时输出 [SILENT] 不打扰
```

### 5.3 预判体系（唯一闭环）

| 时间 | 类型 | 预判内容 | 复盘时机 |
|------|------|---------|---------|
| 16:00 | `recap` | 明日走势（三情景+区间+观察点） | 下一交易日 16:00 复盘 |

- **只有 16:00 收盘复盘记录预判**（`tracker.py record --type recap`）
- 场景A（大盘分析）是纯信息报告，不做预判记录
- 复盘时先做预判连续性检查：昨日无 recap 记录则标注"昨日无预判记录"，**绝不回退到更早日期的预判**（2026-07-21 曾因此复盘错位）

### 5.4 cron 调度规则

| 规则 | 说明 |
|------|------|
| 数据不可覆盖 | 15:05→15:10→15:15 顺序执行，互不覆盖 |
| 周六日不执行 | 所有 cron 均为 1-5（工作日） |
| 节假日 | 需手动暂停（hermes cron pause） |
| no_agent 模式 | 脚本 stdout 直接推送，零 token 消耗 |
| 失败处理 | 单个 cron 失败不影响其他 cron 运行 |

---

## 6. 双体系扩展（三框架 + 四视角）

```
双体系分析 = 三框架体系 ✕ 四视角体系

三框架体系（横向·市场资金面）        四视角体系（纵向·价值投资底层）
  ├── mi-skill → 资金流+技术形态       ├── 段永平视角 → 商业模式本质
  ├── serenity-perspective → 产业链    ├── 巴菲特视角 → 护城河+估值+安全边际
  └── muxuuu-serenity-skill → 方法论   ├── 芒格视角   → 逆向思考+风险清单
                                       └── 李录视角   → 长期确定性+文明趋势
```

**执行纪律**：
1. 必须加载全部 4 个 skill，两套**独立跑完**再联合研判——只跑一半不算双体系（用户明确纠正过）
2. 四视角做方向选股/候选池分析时切换「行业模式」（跳过 DCF/三情景估值）
3. **恐慌/趋势下行模式**（创业板单日-5%+、候选池收红率<30%）→ 跳过四视角，只跑三框架；四视角切换为「中长线建仓清单」模式，仅当用户明确说"中长线布局"才启用

---

## 7. 已知坑位速查（详见各 skill 原文）

| 坑位 | 应对 |
|------|------|
| `serenity-skill` 写错导致 cron 静默跳过 | 必须写目录名 `muxuuu-serenity-skill` |
| MCP 传港股代码导致 server 断连 | 港股一律走腾讯 API，不用 MCP |
| MCP 间歇返回空错误 | 首次调用正常即视为可用值，不反复重试；≥3只连续报错切缓存模式 |
| curl \| python3 管道被安全拦截 | 先 curl 存 /tmp 文件，再 python3 读文件 |
| web_search 可能永久挂起（DDGS bug） | 30s 超时 wrapper 已加；仍挂起检查 provider.py |
| 缓存可能严重过时 | 出报告前必须用 MCP get_stock_quote 拉实时价重算浮盈浮亏 |
| 超级IPO首日扭曲板块资金流 | 对比 sector_change_pct 与 main_net_inflow 数量级，剔除新股后评估 |
| 新建 cron 必须显式设置 deliver | 报告类 cron 设 `deliver: "feishu"`，否则输出只存本地用户收不到 |
| gateway 挂 + systemd 未启用 → cron 全跳过 | 确保 `systemctl --user is-enabled hermes-gateway.service` 返回 enabled |
| 数据管道全失效（DNS/MCP/浏览器全挂） | 纯缓存模式：premarket.json → market_snapshots → web_search 出简报 |

---

## 8. 关键文件索引

### 指挥中心 three-framework-analyst
- `references/scenarios.md` — 7 大场景完整工作流（A大盘分析/B方向选股/C持仓诊断/D3框架分析/E飞书报告/F预测复盘/G收盘复盘）
- `references/daily-schedule.md` — 交易自动化时间线 + 预判体系
- `references/prediction-system.md` — 预判生命周期
- `references/prediction-error-classification.md` — 复盘错误分类
- `references/backtest-guide.md` — 历史回测方法论（89天，方向60.7%）
- `references/candidate-pool-refresh.md` — 候选池月度刷新
- `references/data-sources.md` — MCP 工具参数与数据格式

### 执行层 mi-skill
- `references/rules.md` — 165 条技术规则库（8类）
- `references/dual-framework-analysis.md` — Serenity + mi-skill 双框架方法论
- `references/eastmoney-api-patterns.md` — 东财/腾讯/新浪 API 模式与兜底
- `references/prediction-tracking-workflow.md` — 预测跟踪与复盘三步骤
- `references/data-pipeline.md` — 每日数据管道与缓存读取优先级
- `references/bear-market-holding-strategy.md` — 熊市守 vs 割判断框架
- `references/downtrend-bottom-check.md` — 个股底部检查（六维打分）
- `references/dual-system-scoring-methodology.md` — 自动评分公式（0.4/0.4/0.2）
- `references/skynomad-supply-chain.md` — 小米澎程产业链公司清单

### 风格层 serenity-perspective
- SKILL.md 本体 — 心智模型（卡脖子/机构行为/长线价值/估值重置）+ 决策启发式 + 表达 DNA

### 方法论层 muxuuu-serenity-skill
- `references/evidence-ladder.md` — 证据分级标准
- `references/market-source-playbook.md` — 跨市场（A股/港股/美股/台日韩欧）数据源路径
- `references/deep-research-workflow.md` — 深度主题扫描工作流
- `references/robotics-value-chain.md` — A股机器人产业链图谱（15股/7层）
- `scripts/serenity_scorecard.py` — 可重复打分脚本

---

## 9. 一句话总结

> **三框架体系 = 指挥中心定路由，方法论层做产业链研究，风格层给机构视角，执行层做资金流验证与技术确认。**
> 数据从缓存→MCP→curl→web_search 四级降级取数，分析按"产业链→资金流→技术→综合建议"四步走，
> 每日 8 个 cron 自动完成数据采集与报告产出，预判体系在 16:00 唯一闭环。
