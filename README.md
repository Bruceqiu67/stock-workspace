# Stock Workspace · A股三框架分析体系

基于资金流逆向交易法（mi-skill）+ 产业链机构视角（Serenity）+ 供应链瓶颈研究方法论，
封装为一套完整的 A 股分析工作流。由 Hermes Agent 驱动，可手动触发，也可通过 cron 全自动运行。

## 体系架构

```
三框架分析体系
├── skills/three-framework-analyst    ← 指挥中心（封装层）
│     数据源优先级 / 7大场景路由 / 调用顺序 / 数据流 / cron调度
├── skills/mi-skill                   ← 执行层（资金流+技术确认）
│     四维资金流分析 / 140条技术规则 / 买卖点 / 止损纪律
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
│   ├── three-framework-architecture.md   ← 体系架构总览（数据流/场景路由/裁决协议）
│   ├── output-contract.md                ← 操作层输出契约（入场/止损/目标/仓位/失效）
│   └── testing.md                        ← 逻辑测试 vs 预测有效性评估的边界
├── skills/
│   ├── three-framework-analyst/      ← 指挥中心：7大场景工作流 + 调度时间线 + 预判体系
│   ├── mi-skill/                     ← 执行层：资金流交易法 + 技术规则库
│   ├── serenity-perspective/         ← 风格层：Serenity 股神思维框架
│   └── muxuuu-serenity-skill/        ← 方法论层：供应链瓶颈研究 + 证据标准
└── scripts/
    ├── scoring_engine.py             ← 核心层：确定性打分引擎 (0.4技术+0.4产业+0.2催化)
    ├── scoring_config.py             ← 核心层：评分/权重/阈值单一事实源（0-100 规范量表）
    ├── market_state.py               ← 核心层：市场状态机与仲裁总线 (NORMAL/PANIC/REBOUND/UNKNOWN)
    ├── trading_calendar.py           ← 核心层：交易日历 + 上一交易日 + freshness 语义
    ├── prediction_tracker.py         ← 核心层：预判追踪与错误归因引擎 (record/review/report)
    ├── test_core_engine.py           ← 核心层：逻辑/边界/集成/属性测试
    ├── candidate_universe.py         ← 全市场宇宙 + 重点观察池分层（含纳入原因/日期/退出条件）
    ├── candidate_scanner.py          ← 候选池扫描与打分 (250日日K+60周周K+FilterPolicy+组合+操作契约)
    ├── filter_policy.py              ← I5：观察池/交易池过滤策略 + 方向级淘汰 + near-miss
    ├── portfolio_construction.py     ← A1：组合构建与风险预算（单票/方向上限、regime 暴露）
    ├── output_contract.py            ← A2：入场/止损/目标/仓位/失效条件输出契约
    ├── market_data_adapter.py        ← A3：腾讯/新浪行情与K线解析唯一入口
    ├── research_depth.py             ← I4：快速扫描 vs 深度研究 + 证据覆盖/claim_level
    ├── docs_consistency.py           ← A3：规则/候选/权重/命令路径的文档一致性 lint
    ├── portfolio_monitor.py          ← 持仓监控（消费市场状态 + 组合层目标权重）
    ├── market_snapshot.py            ← 收盘数据快照（指数+全市场宽度/情绪）
    ├── premarket_collector.py        ← 盘前外围数据采集（上一交易日语义）
    ├── candidate_refresh_state.py    ← 候选池刷新状态记录
    ├── xuji_stop_drop_monitor.py     ← 遗留示例：不可用于真实信号（见下方警告）
    └── robotics_monitor.sh           ← 遗留示例：不可用于真实信号（见下方警告）
```

## 核心量化层设计 (Core Layer Architecture)

为了彻底消除大模型在数值计算、均线对比与加权得分上的心算幻觉与随机漂移，系统采用 **「定性归 LLM，定量归 Python」** 的解耦架构：

1. **定性分析 (LLM)**：负责提炼宏观事件、产业“卡脖子”瓶颈层稀缺度与商业护城河逻辑。
2. **定量计算 (Python Engine)**：
   - **分析上下文契约 (`analysis_context.py`)**：统一 `sector_flow_data / main_fund_rank / tariff_active / mainline_sectors / as_of / source` 的输入边界；动态数据缺失时显式返回 `degraded` / `abstain`，不再用静态默认值伪装成动态结论。
   - **评分单一事实源 (`scoring_config.py`)**：规范量定为 0-100（技术/产业/催化子分，风险为诊断维度），权重、行业基准、动作阈值与 0-5/0-20/五星/底部检查等展示量表换算全部集中于此并带版本号；`scoring_engine.py` / `market_state.py` 只引用，不再维护第二套权重表。
   - **确定性打分 (`scoring_engine.py`)**：用 250 日日K计算真实 52 周位置、20日缩量比率、MA5/10/20/60 排列、5日动量、5日振幅收敛度，并用周K计算周线趋势；相对位置只在技术分计一次。产业分 = 板块 beta（60%）+ 公司 alpha（40%），公司 alpha 由 `company_profiles.py` 按代码解析；产业分与催化分必须消费 `analysis_context`，缺失时该维度弃权。
   - **动态状态机 (`market_state.py`)**：识别当前宏观状态（`NORMAL_OSCILLATION` / `PANIC_DOWNTREND` / `REBOUND_RIGHT_SIDE` / `UNKNOWN`），在恐慌市自动将技术/风控权重提高至 60%，并强制执行企稳三条件一票否决；关键输入缺失时返回 `UNKNOWN` 并输出 `as_of/data_completeness/stale/missing_inputs`，不再默认正常。
   - **冲突裁决 (`arbitration.py`)**：把产业逻辑与技术确认的分歧转成可执行裁决（恐慌风控一票否决 / 正常产业准入技术择时 / 反弹相对强度优先 / 未知不动作），输出 `conflict_type/decision/basis/confidence`。
   - **预判复盘闭环 (`prediction_tracker.py`)**：每日 16:00 记录标准化预判，次日按方向(50分)+区间(30分)+情景(20分)自动化评分并输出偏差归因。
   - **前瞻有效性验证 (`forward_validation.py`)**：把每日候选评分快照与未来 1/5/20 日收益关联，输出 rank IC、Top-N 命中率、五分桶单调性与按市场状态的分层表现，防止“评分只是好看的排序”。
   - **候选宇宙分层 (`candidate_universe.py`)**：把「全市场股票宇宙」与「重点观察池（39 只）」拆成两层；每只观察池标的记录纳入原因、纳入日期与退出条件。缺少全市场宇宙配置时显式标记 `universe_status=watchlist_only`、`selection_bias_risk=high`，不把固定池排序称为全市场选股。
   - **高性能并发 K 线管道 (`candidate_scanner.py`)**：基于 `ThreadPoolExecutor` 并发获取候选标的 250 根日 K 与 60 根周 K，实现毫秒级量化扫描。
   - **输入/输出/组合契约**：`filter_policy.py` 统一观察池与交易池阈值（价格/位置/方向级空头排列），输出 near-miss 与排序惩罚；`portfolio_construction.py` 按 regime 给出总暴露、单票/方向上限与调仓建议；`output_contract.py` 输出 `entry_zone/stop/target_1/target_2/position_size/invalidation/valid_until`，并把研究评级与交易动作分开。
   - **数据新鲜度与交易日语义 (`trading_calendar.py` + `market_data_adapter.py`)**：盘前/状态/评分统一输出 `as_of/data_age_days/data_age_trading_days/freshness`；过期数据显式降级，不再把上一交易日或节假日数据当作当日。行情与 K 线解析收敛为 `market_data_adapter.py` 唯一入口，避免四套解析器漂移。
   - **研究深度契约 (`research_depth.py`)**：区分 `quick_scan`（快速扫描，只能声明 initial_pass）与 `deep_research`（≥20 家公司/≥25 个来源，才能声明 verified），输出 `research_depth/evidence_coverage/claim_level/missing_evidence`。
   - **文档单一事实源 (`docs_consistency.py`)**：从 `rules.md`、观察池、`scoring_config.py` 动态计算规则数/候选数/权重，校验文档中的数量、命令与路径；当前规范权重为 0.40/0.40/0.20，规则 140 条。

## 7 大场景

| 场景 | 触发词 | 说明 |
|------|--------|------|
| A 大盘分析 | 「分析大盘」「今天市场」 | 指数/情绪/方向/关注点，纯信息报告 |
| B 方向选股 | 「选股」「推荐方向」 | Serenity 产业链评分 + 双框架 0-20 分打分 |
| C 持仓诊断 | 「看看持仓」 | 持仓仪表盘 + 逐只操作思路 |
| D 3框架分析 | 「3框架分析」 | 自动路由到 A/B/C |
| E 飞书报告 | 「给飞书发报告」 | PDF 生成 + 飞书 API 发送 |
| F 预测复盘 | 「复盘」「准确率」 | `prediction_tracker.py report` 月度统计 + 偏差分析 |
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

## 遗留示例脚本（不可用于信号）

`xuji_stop_drop_monitor.py` 与 `robotics_monitor.sh` 是早期单票/单板块示例，已不再接入核心评分与组合层：

- 二者使用硬编码标的与占位价位，不消费 `market_state` / `filter_policy` / `output_contract`。
- `robotics_monitor.sh` 的 `buy=0.0` 是占位值；已加除零保护，但仍不能把占位成本当作真实持仓成本，运行前必须自行改造。
- 任何真实信号应以 `candidate_scanner.py` 落盘的 `operation` 与 `portfolio_plan` 为准，这两份示例输出**不可用于交易**。

## 风险提示

股票投资风险极高，可能损失全部本金。本仓库提供的是**思维框架和分析方法**，不是投资建议。
所有投资决策需个人负责，盈亏自负。
