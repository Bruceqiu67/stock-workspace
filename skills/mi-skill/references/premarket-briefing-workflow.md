# 盘前简报工作流

> 每日开盘前的自动化简报生成。与 `daily-market-scan-workflow.md`（收盘后分析）互补。

## 数据源读取顺序

```
premarket/{today}.json          → 隔夜外围 + 昨日A股 + hot_sectors（可能为空）
  + portfolio/current.json       → 持仓状态
  + market_snapshots/{latest}.json → 昨日热股成交 + 板块明细
  + web_search 新闻搜索            → 催化逻辑
```

## 数据源故障恢复矩阵

| 故障场景 | 替代方案 |
|----------|----------|
| premarket.json 不存在 | 用 web_search 搜索「A股 昨日 收盘 上证」补指数数据，搜索「美股 隔夜 收盘 道琼斯」补外围 |
| premarket.hot_sectors 为空（{}） | 从 market_snapshots/{latest}.json 的 hot_stocks 推断板块热度（看跌幅/振幅/成交额最大的标的集中在哪些方向） |
| portfolio/current.json 不可用 | 从 premarket 缓存搜昨日的持仓快照 |
| market_snapshots 无当日文件 | 读前一日文件，标注"快照数据暂缺" |
| MCP eastmoney 全部挂掉 | 走 web_search 搜索各方向的新闻（见下方"多方向并行搜索"） |
| web_extract 失败 | web_search 本身返回的 snippets + titles 已可提供方向性信息 |
| browser 超时 | 放弃，用 web_search + 已有文件数据输出 |
| **全部外部工具同时失效**（MCP + curl + browser + web_extract 全挂） | 靠 premarket.json + market_snapshots + web_search 三件套即可输出可用简报。web_search 是最顽强的数据源 |

## 多方向并行搜索策略

盘前简报需要 4 个方向的新闻。按以下方式一次性搜索（不要串行）：

1. **AI算力/半导体**：搜索「美股 AI 半导体 2026年X月X日」或「A股 半导体 最新」
2. **机器人**：搜索「人形机器人 最新进展 2026年X月」
3. **电网设备**：搜索「特高压 电网 十五五 最新政策」
4. **当日外围传导**：基于 premarket 的外围数据推断（美股跌→A股科技承压，港股涨→外资回流等）

## 输出模板

```
# 📋 盘前简报 · YYYY年MM月DD日（周X）

## 隔夜外围
[美股三大指数涨跌 + 解读]
[亚太主要市场涨跌 + 解读]

## A股回顾（昨日）
[上证/深成/创业板/科创50 收盘 + 量能 + 方向轮动]
[持仓回顾]

## 今日关注
[重点方向 + 催化逻辑 3-5条]
[每个方向给出看好/观望/回避的判断]

## 风险提示
[当日需要注意的风险点]

## 持仓参考
[基于外围和新闻，今日四只持仓的应对思路]
```

## 数据标注纪律

- 数据来源于文件读取和 web_search，不要凭空编造
- 如果某个数据源不可用，标注"数据暂缺"而不是编造
- 持仓浮亏百分比以 portfolio/current.json 为准，不要引用用户口头说的一个旧数字
- web_search 返回的 snippets 摘要内容已足够，不需每条都点开全文

## 常见失败模式

### 模式1：MCP eastmoney 全线不可用
mcp-eastmoney 的 sector_fund_flow / main_fund_rank / get_stock_quote / get_kline 全部 error。
**应对**：跳过 sector flow 分析，完全依赖文件数据 + web_search。板块轮动的判断从 market_snapshots 的 hot_stocks 涨跌分布推断。

### 模式2：curl 到新浪/东方财富超时
Sina / Eastmoney API 的 curl 请求可能 timeout（15s+）。
**应对**：不要重试，直接进入 web_search 兜底。已有 premarket.json 的外围数据。

### 模式3：browser_navigate 到中国财经网站超时
Sina / 东方财富 / 财联社 的页面打开可能 CDP timeout。
**应对**：放弃 browser 路径。web_search 的 snippets 已提供足够的方向性新闻摘要。

### 模式4：所有数据管道同时不可用
在今天的实际执行中，MCP、curl、browser 同时不可用，但 web_search 正常工作。
**应对路径**：premarket.json（指数+外围）→ market_snapshots（热股）→ web_search（新闻催化）→ 输出。足够产出一份可用的简报。

### 模式5：Cron 发送失败，用户要求重新发送

**场景**：盘前简报 cron 运行失败（状态 error），用户说「重新发一次」。

**错误做法**：只调用 `cronjob(action='run')` — 异步运行需数分钟，用户等不起。

**正确做法**：立即在当前会话中手动生成简报并直接发送：

1. **读本地缓存数据**（最快，不需网络）：
   - `~/.hermes/data/premarket/{today}.json` — 指数 + 外围 + 持仓
   - `~/.hermes/data/portfolio/current.json` — 持仓明细

2. **补充实时数据**（按优先级尝试，不要全做）：
   - **首选：`browser_navigate` 到财联社电报页** `https://www.cls.cn/telegraph` — web_search 超时时仍可用，浏览器走 CDN 而非搜索聚合引擎，网络路径不同
   - 备选：`web_search` 搜索「A股 今日 盘前 新闻」+ 个股关键词
   - 兜底：`curl -s "https://qt.gtimg.cn/q=..."` 获取个股行情

3. **直接在当前会话输出简报并发送**，不等 cron 异步结果

4. 可选：最后再 `cronjob(action='run')` 作为后台补发，但用户已收到的简报才是第一优先级

**根因排查思路**（当同个 cron 反复失败时）：
- 检查 `last_delivery_error`：是 Feishu API 超时还是 agent 内部超时？
- 飞书 API 的 `open.feishu.cn` CDN edge 可达但 API endpoint 可能被 GFW 阻止 — 单独测试 `curl -s -w '%{http_code}:%{time_total}s' -o /dev/null --connect-timeout 10 --max-time 20 'https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal' -X POST -H 'Content-Type: application/json' -d '{"app_id":"test","app_secret":"test"}'`：root 响应但 API 超时 ⇒ 网络级封锁，需走代理
- 检查 cron 的 provider 是否设为同一个 LLM provider，该 provider 第一次调用可能长期挂起（大上下文 >70k tokens 时 deepseek 会 silent hang）
- 检查 cron 是否消耗完了 600s agent timeout 却还没走到交付步骤

### 模式6：API Provider + web_search 同时超时的双重故障

**场景**：DeepSeek API 调用反复超时，与此同时 web_search 也全部超时（DDGS 多个引擎同时 timeout）。简报 cron 失败，且手动恢复时也用不了 web_search。

**典型日志特征（在 agent.log 中排查）**：
```
08:34:24 API call #3 latency=60.7s  <-- 首次出现高延迟
08:34:59 Streaming failed: Request timed out.  <-- 第一次 composition 超时
08:36:21 web_search failed: ConnectTimeout  <-- web_search 也同时挂了
08:37:04 web_extract failed: ddgs is search-only
08:38:53 browser_navigate timed out after 60s
...
08:41:48 API call failed after 3 retries.  <-- 最终失败
```

**特点**：多个独立网络通道同时阻塞。LLM provider 和搜索引擎走不同出口但都超时，说明是网络层（DNS/GFW/代理）间歇性拥堵。

**恢复步骤**：
1. **读本地缓存**：premarket/{today}.json + portfolio/current.json
2. **用浏览器替代 web_search**：browser_navigate 到 https://www.cls.cn/telegraph — 财联社电报走 CDN，网络路径不同，通常在 DDGS 和 curl 都超时的时候仍然可达
3. **从浏览器的电报页提取实时新闻**：页面按时间倒序展示，可直接获取当日所有重要快讯
4. **手动组合输出**：本地数据（指数+持仓）+ 浏览器新闻（催化逻辑）= 完整简报
5. **报告根因给用户**：说明是 API provider + 搜索引擎双重超时导致 cron 失败，已手动补发

**如果浏览器也超时（极端情况）**：
- 放弃所有实时数据，只靠本地缓存输出精简版简报
- 标注：实时数据暂缺
- 等网络恢复后再跑 cronjob(action='run')

**警告信号（提前发现）**：
- cron 首次 API 调用 latency >60s：即使这次成功了，后续 composition 阶段可能超时
- cron 运行期间 web_search 返回 timeout：说明网络已有波动
- **连续两天同个 cron 超时**：按 `references/provider-fallback-strategy.md` 中的步骤切换 provider，不要再等第三天的失败

## 参见

- `references/provider-fallback-strategy.md` — 备用 LLM provider（NVIDIA NIM / GLM）的配置和使用
- `finance/three-framework-analyst` 技能的「API Provider 反复超时」pitfall — cron provider 切换完整决策树
