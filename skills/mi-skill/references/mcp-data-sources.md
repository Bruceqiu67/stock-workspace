# MCP 数据源：A股行情/研报的 MCP Server 生态

> 替代 curl+手动解析的下一代数据获取方式。
> 调查时间：2026-07-13

## 为什么需要 MCP

当前的数据获取方式依赖 terminal/curl 手动拉取 API 然后解析 GBK 编码/字段索引，问题：

| 问题 | 表现 |
|------|------|
| push2 clist 被封锁 | 板块排行端点返回空，被迫用热股情绪代理法 |
| 手动解析 GBK | 每次 curl 要 iconv + python 分割字段，易错 |
| 字段索引靠查表 | Tencent 字段[47][48]是年线不是90日线，Sina 成交额单位是元不是万 |
| 安全审批疲劳 | 每次 curl 批量拉行情都要确认，打断节奏 |
| 无结构化工具调用 | 数据是原始 CSV 字符串，需额外处理 |

MCP 把数据封装为**结构化工具调用**，直接返回 JSON，不必管编码/字段索引/单位换算。

## 可用 MCP Server 一览

### 1. mcp-eastmoney（推荐首选 — 轻量级）

- **仓库**: https://github.com/27dream/mcp-eastmoney
- **安装**: `uvx --from git+https://github.com/27dream/mcp-eastmoney.git mcp-eastmoney`
- **工具数**: 5 个
- **覆盖**: A股实时行情、资金流向、板块排行、K线、股票搜索
- **零配置，免费，无 API Key 需求**

**✅ 推荐理由**：依赖极轻（仅 `mcp` + `httpx` + `pydantic` 三个包，无 pandas/numpy），首次下载 < 2MB，10 秒内装完。从 WSL/GFW 环境下无压力。

**对我们三框架分析的关键能力**：

| 能力 | 当前做法 | MCP 替代 |
|------|---------|---------|
| 板块排行 | 已完全不可用（push2封杀） | `sector_fund_flow(kind="concept"/"industry")` 返回板块涨跌幅+主力净流入+领涨股 |
| 主力资金排行 | 单股逐个拉 | `main_fund_rank(market="all")` 批量返回排行榜 |
| 个股实时行情 | curl `qt.gtimg.cn` + iconv | `get_stock_quote(code="600519")` 直接返回 |
| 股票搜索 | web_search | `search_stock(keyword="宁德", limit=10)` |
| K线数据 | curl 腾讯K线 API + 手动算均线 | `get_kline(code="300750", period="daily", limit=30)` |

**依赖对比**：

| MCP | 依赖大小 | 首次安装时间 | 从GFW后可行性 |
|-----|:--------:|:-----------:|:------------:|
| mcp-eastmoney | ~2MB | < 10秒 | ✅ 轻松 |
| StockHub MCP | ~70MB | 5-10分钟+ | ❌ 极困难 |
| AKShare系列 | ~60MB+ | 3-5分钟 | ⚠️ 要看网络 |

### 2. StockHub MCP（功能最多，但依赖极重 — 备选）

- **仓库**: https://github.com/TimWu0101/stockhub-mcp
- **安装**: `uvx stockhub-mcp`
- **工具数**: 43 个
- **覆盖**: A股、港股、美股、基金、ETF、期货、指数
- **数据源兜底链**: yfinance → efinance → 腾讯 → 新浪 → 东方财富 → AKShare

**⚠️ 依赖体积警告**：依赖 pandas (10.8MB)、numpy (16.1MB)、lxml (5.0MB)、cryptography (4.5MB)、curl-cffi (10.6MB)、akracer (9.6MB)、py-mini-racer (5.2MB) 等，**首次安装需下载 ~70MB**。从 WSL/GFW 环境下 `uvx stockhub-mcp` 可能耗时 5-10 分钟甚至超时。

**何时用它**：当 mcp-eastmoney 的 5 个工具不够用，且环境有代理/非 GFW 时。StockHub 的 43 个工具覆盖龙虎榜、港股通、全市场情绪等 mcp-eastmoney 没有的功能。

**避坑**：uv tool install 中途取消会留下 malformed tool entry，需 `uv tool uninstall stockhub-mcp` 清理。

### 3. AKShare 系列 MCP

多个社区版，都基于 AKShare（1000+ 金融数据接口）：

| 名称 | 仓库 | 安装方式 | 特点 |
|------|------|---------|------|
| **akshare-one-mcp** | https://github.com/zwldarren/akshare-one-mcp | `npx -y @smithery/cli install @zwldarren/akshare-one-mcp` | 实时行情/历史K线/新闻/财务报表 |
| **china-stock-mcp** | https://github.com/xinkuang/china-stock-mcp | `npx -y @smithery/cli install @xinkuang/china-stock-mcp` | AKShare 封装，A股全覆盖 |
| **china-stock-mcp-server** | https://github.com/peikuo/china-stock-mcp-server | python uv/pip | 沪深京三市，支持 Baostock |

**三者关系**：都是 AKShare 的 MCP 包装，工具集相近，依赖体积均 ~60MB+。

### 4. 其他相关 MCP

| 名称 | 说明 | 对我们的价值 |
|------|------|------------|
| **A-Share MCP** (24mlight) | 基于 Baostock，A股基础数据/K线/财务/宏观 | 基础行情，工具数少 |
| **Fin AI Research Workflow** | 41个数据源 MCP 集合，含 akshare/eastmoney/yfinance/FRED/ArXiv/SEC EDGAR | 宏观+跨市场研究辅助 |
| **Market Intel MCP** | 加密货币/外汇/美股实时行情 | 对外围市场有用，但美股数据有限 |
| **TipRanks MCP** | 分析师评级、研报摘要（英文） | 美股利好研报，A股无用 |

## 研报数据源的现状

**目前无现成 MCP 能直接拉券商研报**。A股研报的主要来源和获取难度：

| 来源 | 可获取性 | 方式 |
|------|---------|------|
| 东方财富研报中心 | ❌ 页面动态加载，需登录 | 不可用 |
| 同花顺 iFinD | ❌ 需付费 | 不可用 |
| 慧博投研资讯 | ❌ 需登录付费 | 不可用 |
| 各券商APP/小程序 | ❌ 封闭生态 | 不可用 |
| 公开 web_search | ✅ 可搜到部分摘要 | 慢，5-15秒 |

**结论**：研报仍然只能靠 web_search 搜公开来源。MCP 解决行情/资金流/板块排行，不解决研报。

## MCP vs 当前 curl 方案对比

| 维度 | curl 手动方案 | MCP 方案 |
|------|-------------|---------|
| 速度 | 快（毫秒级返回） | 略慢（MCP server 进程间通信，每次工具调用多 ~100ms） |
| 可靠性 | 中（依赖 API 稳定性） | 中（MCP server 内部兜底链，腾讯→新浪→AKShare自动切换） |
| 数据质量 | 需要自己处理编码/单位/字段偏移 | 结构化 JSON，开箱即用 |
| 编码处理 | 需 iconv + python decode | MCP server 已处理好 |
| 安全审批 | 每次 curl 弹确认 | 首次配置后不弹 |
| 工具调用次数 | 多（1次curl + 1次python处理） | 少（1次工具调用返回完整 JSON） |
| 维护成本 | 高（API 被封要改字段/端点） | 低（MCP server 维护者更新） |

**混合策略推荐**：

```
板块排行/资金流向/行情  → mcp-eastmoney（轻量、结构化、开箱即用）
K线均线计算             → mcp-eastmoney get_kline 或 腾讯K线 API（看速度需求）
主力资金排行            → mcp-eastmoney main_fund_rank
研报/产业逻辑           → web_search（无 MCP 替代）
高频批量（20+只）       → curl qt.gtimg.cn（比 MCP 工具调用快，绕过 ~100ms 进程间通信）
```

## Hermes 配置示例

**推荐配置（mcp-eastmoney）**：

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  eastmoney:
    command: "uvx"
    args: ["--from", "git+https://github.com/27dream/mcp-eastmoney.git", "mcp-eastmoney"]
```

**备选配置（StockHub MCP）**：

```yaml
# ~/.hermes/config.yaml
mcp_servers:
  stockhub:
    command: "uvx"
    args: ["stockhub-mcp"]
    # 可选：只暴露需要的工具，减少 agent 的选项噪音
    # tools:
    #   include:
    #     - get_stock_quote
    #     - get_board_ranking
    #     - get_capital_flow
    #     - get_kline_data
    #     - get_market_sentiment
```

配置后重启 `hermes chat`，MCP 工具自动出现在工具集中。

## Pitfalls

- **MCP 不解决研报**：目前没有 MCP 可以拉券商研报，这块还是 web_search
- **首次安装需网络**：`uvx stockhub-mcp` 需要首次拉取依赖
- **MCP 工具调用有开销**：每次工具调用比直接 curl 多 ~100ms 进程间通信，高频场景（如一次拉20只股）可能不如 curl 快
- **MCP server 可能挂**：第三方 MCP server 没有 SLA，如果挂了仍需要 curl 兜底
- **Hermes 重启才能加载 MCP**：改 config.yaml 后必须重启 `hermes chat` 才能生效，hot-reload 不是100%可靠

## 总结：当前推荐优先级

```
全市场扫描（板块排行）
  1. mcp-eastmoney → sector_fund_flow(kind="concept"/"industry")    ← 推荐首选
  2. web_search "今日热点板块"                                        ← 兜底

个股验证（价格/均线/资金流）
  1. mcp-eastmoney → get_stock_quote + get_kline                     ← 推荐首选
  2. terminal/curl qt.gtimg.cn                                       ← 高频批量时更快

主力资金排行
  1. mcp-eastmoney → main_fund_rank(market="all")                   ← 推荐首选
  2. curl push2his.eastmoney.com 逐个拉                              ← 兜底

研报/产业逻辑
  web_search                                                         ← 无替代
```
