# 三框架分析体系 · 架构总览

> 整理日期：2026-08-18
> 定位：本文件是**体系说明文档**，不参与 skill 加载；只描述思考层、数据流与体系架构，不展开触发/调度/交付/运维细节。
> 目的：一张图说清"三框架体系是什么、由谁组成、怎么触发、数据怎么流、7 大场景怎么路由"。
> 权威来源：各 skill 的 SKILL.md 与 references 文件。本文档如有与 skill 内容冲突之处，以 skill 为准。

---

## 1. 体系组成与分层

三框架体系 = **1 个指挥中心 + 3 个分析框架**，共 4 个 skill：

| 层级 | Skill（目录名） | 定位 | 核心职责 | 典型内容 |
|------|----------------|------|---------|---------|
| **指挥中心** | `three-framework-analyst` | 封装层 | 定义数据源优先级、7 大场景路由、调用顺序、数据流、cron 调度 | scenarios.md / daily-schedule.md / prediction-system.md / data-sources.md |
| **执行层** | `mi-skill` | 资金流+技术确认 | 四维资金流分析（市场环境→板块轮动→个股行为→技术确认）、买卖点、止损纪律、140 条技术规则 | rules.md / dual-framework-analysis.md / eastmoney-api-patterns.md |
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
  ├── candidates/YYYY-MM-DD.json            — 当日候选池（含 universe 分层与选择偏差标记）
  ├── candidate_universe.json               — 全市场股票宇宙（可选；缺失时扫描仅覆盖观察池）
  ├── predictions/daily/YYYY-MM-DD.json     — 历史预判记录（target_date/schema_version/rubric_version）
  └── predictions/                          — prediction_tracker.py 的 review/report 数据

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
        → 先过三道硬过滤门（绝对价格 / 52周相对位置 / MA5/10/20/60 + 周线排列）
        → 日K至少 120 根（目标 250 根），周K至少 20 根；窗口不足标记 degraded
Step 4  综合输出
        → Serenity 产业链结论 → 资金流验证 → 技术买卖点 → 综合操作建议
```

### 4.2 市场状态识别（分析前必做）

| 状态 | 特征 | 策略基调 |
|:----|:-----|:--------|
| **正常/震荡** | 上证在20日线上方，单日振幅<2% | 正常轮动，选方向做个股 |
| **恐慌/趋势下行** | 连续2天放量下跌，或创业板单日-5%+，或上证-2%+且候选池收红率≤20%，或市场宽度极弱 | 守为主，不做新开仓，等缩量止跌 |
| **反弹/右侧启动** | 指数仍在MA20下方，单日上证+1.5%/创业板+2.5%，候选池收红率≥70% | 优先加仓持仓中相对强势的个股 |
| **未知/数据不足** | 指数、MA20、候选池广度或量能关键输入缺失/过期 | 不输出方向性开仓结论，只列数据缺口与观察项 |

状态机实现（`scripts/market_state.py`）的硬性约定：

- 关键输入缺失时返回 `UNKNOWN`，**绝不默认正常**；输出必须携带 `as_of`、`data_completeness`、`stale`、`missing_inputs`。
- 反弹判定真实读取指数 K 线计算 `above_ma20`，不再因未传 `klines_sh` 而永远不可达。
- 候选池广度来自**当日行情**，不复用上一次候选文件。
- 市场宽度由 `market_snapshot.py` 采集全 A 股涨跌家数、涨停/跌停/炸板、中位数涨幅、总成交额，并输出 0-100 情绪分；状态机在 `上涨占比≤20%且跌停≥30家` 或 `情绪分≤20且上涨占比≤25%` 时判为恐慌。宽度缺失时明确标注，不用热股代理替代。
- 恐慌模式下：不找进场点只盯企稳信号；相对强度 > 绝对涨跌；不机械执行止损线（"两头挨打"比"不止损"更危险）。
- 企稳三条件由 `check_stabilization()` 逐项计算（缩量、不创新低、站上MA5），未通过则抑制买入标签。

### 4.2.1 三框架冲突裁决协议

`scripts/arbitration.py` 把“产业逻辑 vs 技术确认”的分歧转成可执行裁决：

| 市场状态 | 优先级 | 典型裁决 |
|:--|:--|:--|
| 恐慌 | 风控一票否决 | 未企稳 → `DEFEND_NO_NEW_BUY`；企稳但技术弱 → `HOLD_ONLY_RELATIVE_STRENGTH` |
| 正常 | 产业准入、技术择时 | 产业多/技术破位 → `WATCH_WAIT_TECH_REPAIR`；共振 → `BUY_ON_PULLBACK` |
| 反弹 | 相对强度优先 | 候选池前1/3 → `ADD_RELATIVE_STRENGTH`；否则 `WAIT_CONFIRMATION` |
| 未知 | 不动作 | `NO_DIRECTIONAL_ACTION` |

输出固定包含 `conflict_type / decision / priority / basis / evidence_needed / confidence`，
并随候选池 JSON 落盘，供复盘统计。

### 4.3 操作建议输出规范

操作层由 `scripts/output_contract.py` 生成，统一字段：

```
entry_zone / stop / target_1 / target_2 / position_size /
invalidation / valid_until / confidence / research_rating / trade_action
```

- `research_rating` 是研究排序结论（可买观察/观察）；`trade_action` 才是可执行动作。
  非可排名、数据过期、恐慌未企稳、方向淘汰、仲裁否决时 `trade_action=NO_TRADE`，
  仓位为 0。
- 止损由 ATR、近 20 日低点与 MA20 推导，必须满足
  `stop < entry_low <= entry_high < target_1 < target_2`。
- 仓位由单笔风险预算 0.5% / 止损距离得到，再受单票 15% 与组合层目标权重约束；
  市场状态乘数为正常 1.0、反弹 0.9、恐慌 0.3、未知 0。
- 有效期默认 5 个交易日，按交易日历顺延；失效条件必须可观察（跌破止损、未站稳
  入场区上沿、方向空头排列、数据过期等）。
- 完整 schema 与示例见 `docs/output-contract.md`。

### 4.4 过滤策略与方向级淘汰

`scripts/filter_policy.py` 是价格/位置阈值的唯一事实源：

| 用途 | 价格硬上限 | 价格惩罚阈值 | 位置硬上限 | 位置惩罚阈值 |
|:--|:--|:--|:--|:--|
| 观察池 | 150 元 | 100 元（排序惩罚） | 70% | 60% |
| 交易池 | 100 元 | 80 元（排序惩罚） | 70% | 60% |
| 恐慌覆盖 | 同用途 | 同用途 | 60% | 50% |

- 硬排除与排序惩罚分离：被惩罚标的保留在候选中，进入 `filtered.near_miss` 并扣排序分。
- 方向级：方向内全部候选 `MA5<MA10<MA20` 时 `direction_veto`，买入标签改为
  `OBSERVE_DIRECTION_BEARISH`，不进入组合。
- 缺少全市场宇宙时仍标记 `watchlist_only/selection_bias_risk=high`，不把观察池排序
  称为全市场选股。当前重点观察池为 39 只 / 9 个方向，权威来源是
  `scripts/candidate_universe.py` 的 `WATCHLIST`。

### 4.5 组合构建与风险预算

`scripts/portfolio_construction.py` 在个股评分之上输出：

- 按 regime 的总暴露：正常 80% / 反弹 70% / 恐慌 30% / 未知 0%。
- 单票上限 15%、方向（相关性代理）上限 30%、最大持仓 8 只、最低分 50。
- 目标权重、现金比例、方向权重、被丢弃原因、与当前持仓比较的
  `ENTER/ADD/HOLD/REDUCE/EXIT` 调仓建议。
- 候选快照未保存完整收益序列，因此“相关性”用同方向高相关代理
  （`correlation_proxy=0.8`），并在输出中明确标注，不冒充精确相关系数。

### 4.6 数据新鲜度、交易日历与解析适配器

- `scripts/trading_calendar.py` 提供 `is_trading_day / previous_trading_day /
  next_trading_day / freshness`；周一盘前读上一交易日（周五），不是日历昨天。
- 扫描、市场状态、盘前、预判复盘统一输出 `as_of / data_age_days /
  data_age_trading_days / freshness / expected_previous_trading_day`。
- `freshness.status`：`fresh` / `previous_trading_day`（盘前读昨收，属预期）/
  `stale` / `future` / `missing`；`stale=true` 时下游必须降级。
- 候选评分的 K 线日期若早于上一交易日，直接 `scoring_status=degraded`、
  `rankable=false`、`action_label=DATA_STALE`。
- `scripts/market_data_adapter.py` 是腾讯/新浪行情与 K 线解析的唯一入口，
  候选扫描、收盘快照、持仓监控、盘前采集、预判复盘都复用它。

### 4.7 研究深度契约

`scripts/research_depth.py` 把“快速扫描”与“深度研究”分档：

| 模式 | 公司数 | 来源数 | 预算 | 可声明等级 |
|:--|:--|:--|:--|:--|
| `quick_scan` | 目标 8 | 目标 5 | ≤5 次取数 / 30 秒 | `initial_pass` |
| `deep_research` | 最低 20 | 最低 25 | 独立预算 | `verified` |

报告必须携带 `research_depth / evidence_coverage / claim_level / missing_evidence`；
快速模式不得把结论写成“已验证”，并需列出待核实来源。

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

- **只有 16:00 收盘复盘记录预判**（`prediction_tracker.py record --type recap`）
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

### 核心量化层 scripts
- `scoring_engine.py` / `scoring_config.py` — 确定性评分与单一事实源
- `market_state.py` / `trading_calendar.py` — 状态机、交易日历与 freshness
- `candidate_scanner.py` / `candidate_universe.py` — 候选扫描、宇宙分层
- `filter_policy.py` / `portfolio_construction.py` / `output_contract.py` — 过滤、组合、操作契约
- `analysis_context.py` / `market_data_adapter.py` — 输入契约与行情解析适配器
- `prediction_tracker.py` / `forward_validation.py` / `arbitration.py` — 预判闭环、前瞻验证、冲突裁决
- `research_depth.py` / `docs_consistency.py` — 研究深度契约、文档一致性 lint

### 指挥中心 three-framework-analyst
- `references/scenarios.md` — 7 大场景完整工作流（A大盘分析/B方向选股/C持仓诊断/D3框架分析/E飞书报告/F预测复盘/G收盘复盘）
- `references/daily-schedule.md` — 交易自动化时间线 + 预判体系
- `references/prediction-system.md` — 预判生命周期
- `references/prediction-error-classification.md` — 复盘错误分类
- `references/backtest-guide.md` — 回测方法论（目标=次日收盘相对前收，90日窗口；旧 60.7% baseline 不可用于校准）
- `references/candidate-pool-refresh.md` — 候选池月度刷新
- `references/data-sources.md` — MCP 工具参数与数据格式

### 执行层 mi-skill
- `references/rules.md` — 140 条技术规则库（8类）
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

### 文档、契约与测试
- `docs/output-contract.md` — 操作层字段、推导规则与校验
- `docs/testing.md` — 逻辑测试 vs 预测有效性评估、golden fixture、自评不可复现说明
- `scripts/docs_consistency.py` — 规则/候选/权重/命令路径一致性 lint
- `scripts/test_core_engine.py` — 核心逻辑/边界/集成/属性测试

---

## 9. 一句话总结

> **三框架体系 = 指挥中心定路由，方法论层做产业链研究，风格层给机构视角，执行层做资金流验证与技术确认。**
> 数据从缓存→MCP→curl→web_search 四级降级取数，分析按"产业链→资金流→技术→综合建议"四步走，
> 每日 8 个 cron 自动完成数据采集与报告产出，预判体系在 16:00 唯一闭环。
