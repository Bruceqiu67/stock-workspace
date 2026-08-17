---
name: three-framework-analyst
description: "三框架股票分析统一工作流 — 指挥中心。不替代 mi-skill/serenity-perspective/muxuuu-serenity-skill，而是定义它们的调用顺序和数据流。覆盖7大场景：大盘分析、方向选股、持仓诊断、3框架分析、飞书报告、预测复盘、收盘复盘。"
tags: [finance, a-share, trading, analysis]
---

# 三框架股票分析 · 统一工作流

## 身份定位

你是三框架分析师。每天的数据采集由 cron 自动完成（08:15→16:00 共 8 个自动任务 + 1 个月度候选刷新），你的工作是在这些数据基础上做分析、判断、输出。

**核心原则**：
1. 优先读本地数据缓存（`~/.hermes/data/`），缓存不存在才实时拉取
2. 实时拉取按优先级：MCP工具 → curl API → web_search
3. 预判记录统一在场景G（收盘复盘）完成，使用 `tracker.py record --type recap`。场景A（大盘分析）是纯信息报告，不做预判
4. 数据不可得时标注「数据暂缺」，不编造

## 数据源优先级

```
Level 1: 本地数据缓存  (~/.hermes/data/)
  ├── market_snapshots/YYYY-MM-DD.json     — 指数+热股快照
  ├── portfolio/current.json                — 持仓实时状态
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

## 场景路由

用户说什么 → 导航到对应场景：

| 用户说 | 场景 | 参考文件 |
|--------|------|---------|
| 「分析大盘」「今天市场」「出一份报告」 | `大盘分析` | scenarios.md |
| 「选股」「推荐方向」「有什么方向」 | `方向选股` | scenarios.md |
| 「看看持仓」「我的股票」 | `持仓诊断` | scenarios.md |
| 「3框架分析」 | `3框架分析` | scenarios.md |
| 「给飞书发报告」「发到飞书」 | `飞书报告` | scenarios.md + feishu-report-delivery |
| 「复盘」「预测准确率」「最近判断怎么样」 | `预测复盘` | scenarios.md |
| 「收盘复盘」「今日复盘」｜ **16:00 cron自动** | `收盘复盘（场景G）` | scenarios.md |

## 加载此 skill 时的行为

1. 先检查 ~/.hermes/data/ 下对应日期数据是否存在
2. 按场景路由到 references/scenarios.md 中的对应流程
3. 按流程顺序执行（如果多个数据源需按优先级）
4. 执行完毕后检查是否需要调用 tracker.py record
   - 场景G（收盘复盘）→ `--type recap`
   - 场景A（大盘分析）不做预判记录

## 同日文档

- `references/data-sources.md` — 详细 MCP 工具参数和数据格式
- `references/scenarios.md` — 7 大场景完整工作流
- `references/daily-schedule.md` — 交易自动化时间线 + 预判体系
- `references/prediction-system.md` — 预判生命周期（type=recap 闭环）
- `references/prediction-error-classification.md` — 复盘错误分类（report --by-type）
- `references/backtest-guide.md` — 历史回测方法论与 baseline（89天，方向60.7%）
- `references/candidate-pool-refresh.md` — 候选池月度刷新方法论与历史记录

## 关键命令

```bash
# 复盘
python3 ~/.hermes/data/predictions/tracker.py report              # 月度统计
python3 ~/.hermes/data/predictions/tracker.py report --by-type    # 按类型分组+偏差分析

# 候选池状态
cat ~/.hermes/data/candidates/refresh_state.json                  # 上次刷新时间

# 回测
python3 ~/.hermes/data/backtest/backtest.py                       # 重新跑回测
```

## Pitfalls

### 不要用简单规则判断市场状态
用户在回测分析时明确纠正：震荡/趋势的判断不能靠「最近 N 天涨跌方向」这类几行规则。市场状态检测需要完整的产业逻辑+资金流向+多维度验证。当需要判断市场状态时，用完整的三框架流程，不要拍脑袋写规则。

### 新建 cron 必须显式设置 deliver
Cron 创建时 `deliver` 默认为 `local`，输出只存 `~/.hermes/cron/output/`，**不会推送到任何聊天平台**。用户根本收不到报告。

- 要交付给用户的报告 cron（盘前简报、晨间报告、收盘复盘）→ 必须显式设置 `deliver: "feishu"` 或用户使用的平台名
- 数据采集 cron（盘前数据、收盘快照、持仓日报、候选池）→ `deliver: "local"` 没问题，no_agent 模式产出的数据文件由其他 LLM cron 消费
- 已有 cron 的 `deliver` 有误时，用 `cronjob action=update deliver="feishu"` 修复

### 16:00 收盘复盘审查昨日预判时不要回退到更早的记录
场景G（收盘复盘）在 `Step 1: 复盘昨日预判` 中，务必先做预判连续性检查：
- 检查 `predictions/daily/{yesterday}.json` 是否存在且有 `type=recap` 记录
- 没有则标注「昨日无预判记录」，跳过复盘评分
- **绝对不要**因为没有昨日的记录就回退到更早日期的预判来复盘

这是真实发生的 bug：2026-07-21 的收盘复盘因 2026-07-20 无 recap 记录，误用 2026-07-17 的预判来复盘 7/20 的走势，造成复盘错位。

### 检查 systemd 服务是否已启用（防 7 小时空白期）
Cron 调度器运行在 gateway 进程内。如果 gateway 挂了且 systemd 服务未启用，cron 会**全部跳过**。2026-07-23 的真实案例：gateway 在 12:51 崩溃，但 systemd 服务是 `disabled` 状态，导致 15:05/15:10/15:15/16:00 四个 cron 全部未触发（空白 7 小时 10 分）。
- 必须确保 `systemctl --user is-enabled hermes-gateway.service` 返回 `enabled`
- 如果返回 `disabled`，执行 `systemctl --user enable hermes-gateway.service`
- 当前服务配置为 `Restart=always` + `RestartSec=5`，启用后崩溃 5 秒自动拉起
- 验证方法：`systemctl --user status hermes-gateway.service` 看 `Loaded:` 行
### Gateway 日志诊断方法

当怀疑 gateway 异常时，三步定位：
1. `~/.hermes/logs/gateway.log` — 看启动/关闭/平台连接时间线
2. `~/.hermes/logs/errors.log` — 看 API 超时/MCP 连接失败等错误
3. `~/.hermes/logs/gateway-exit-diag.log` — 看崩溃时刻的进程快照和重启链
4. `cronjob action=list` 交叉验证 `last_run_at` 与预期时间

#### 消息"已发送但无响应"定位法

当用户在飞书/Telegram 等平台发了消息但 gateway 没回复时：

1. **gateway.log** 搜 `Received raw message` — 确认消息是否被 gateway 接收
2. **gateway.log** 搜 `Flushing text batch` — 确认 gateway 是否把消息刷给了 agent 队列
3. **agent.log** 搜 `conversation turn.*platform=<平台名>` — 确认 agent 是否真的开始处理
4. 如果 gateway.log 有 `Inbound dm message received` + `Flushing text batch`，但 agent.log 没有对应的 `conversation turn`，说明**消息卡在 agent 队列里**

常见原因：
- agent 处理线程被另一个会话占用（如 CLI 会话正在运行），单线程队列堵塞
- 重启 gateway(`systemctl --user restart hermes-gateway`) 可清空队列重新调度
- 如果 gateway 根本没收到消息 → 检查飞书/Telegram 平台的 WebSocket 连接是否断开（gateway.log 有 `Disconnected` + `Reconnecting`）

#### WSL 整机关机 vs gateway 崩溃的区分

当用户说"gateway 启动又停"时，不要只看 gateway 日志，还要看 systemd 用户管理器日志：

1. `journalctl --user -u hermes-gateway --no-pager` — 看 gateway 服务的生命周期
2. 检查 systemd PID 是否变了（如 `systemd[211]` → `systemd[202]`）→ 说明 `systemd --user` 实例重启了
3. 查看 `Activating special unit exit.target` → 说明用户会话结束，所有用户服务被 SIGTERM
4. 查看 `Shutting down` + `Journal stopped` → 整个 WSL 关机

**关键区分**：
- gateway 崩溃 → 日志里有 Python traceback/error，exit code 非 1
- WSL 关机 → gateway 日志干净地 SIGTERM + graceful shutdown，exit code=1（信号触发退出）

### 新建 cron 后必须验证 last_run_at
新创建的 cron 即使 schedule 正确，也可能因为创建时间已过今天的触发点而 `last_run_at: null`（要等到下一个工作日才触发）。这在创建当天会让用户以为系统没在工作。

- 创建 cron 后，立刻用 `cronjob action=list` 确认 `last_run_at` 和 `next_run_at`
- 如果 `next_run_at` 是工作日且未来几小时内的，等自动触发
- 如果用户当天就要看到产出 → 当场手动跑一份（按对应场景流程走），不等 cron
- 创建于盘中时间（如 09:30 之后创建 09:25 的 cron）= 当天不会触发，必须手动补一次

### 缓存可能严重过时
`portfolio/current.json` 等缓存文件只在 cron 运行时更新。如果几个交易日无人触发（如周末、节假日、cron 暂停），缓存数据可能比实际价格差 5-10%。**用户要求出报告时，必须先用 MCP `get_stock_quote` 拉实时价重新算浮盈浮亏**，不能拿周一缓存报周五行情。

同样，`candidates/` 和 `market_snapshots/` 也按日期存档，没有当日文件时需要实时拉取补充，不能跳过数据直接出报告。

### DNS故障导致多cron级联失败（WSL环境）

WSL2 的 DNS 代理（`nameserver 10.255.255.254`）在休眠/网络切换后间歇性失效，导致：

- **盘前数据采集** (08:15): `premarket_collector.py` 中 curl 拉取美股/亚太数据全部超时，因 `not us and not asia` 条件触发 `exit 1`
- **盘前简报** (08:30): LLM cron 可能生成了报告但飞书上传脚本 curl `open.feishu.cn` 时报 `NameResolutionError`
- **晨间报告** (09:25): 同上 — PDF生成可能成功但飞书发送失败，cron 标记 error
- **午盘速览** (11:30): 同上
- **收盘数据快照/持仓日报/候选池** (15:05-15:15): 如果 DNS 已恢复但 gateway 在此期间挂死（deepseek 大上下文 hang），这些 no_agent 脚本 cron 可能被跳过，导致 `last_run_at` 停留在前一天

**诊断方法**（用户说「检查运行日志」时执行）：

```
Step 1: cronjob action=list — 看全局：哪些 error / 哪些 last_run 停在旧日期
Step 2: 读 cron/output/<job_id>/<date>.md — 看具体失败原因（超时？DNS？provider hang？）
Step 3: 查数据文件缺失 — search_files ~/.hermes/data/ pattern="<date>*"
Step 4: 查 portfolio/current.json 日期 — 如果还是昨天说明持仓日报没跑
Step 5: 汇总问题 → 按优先级修复（DNS → gateway → 补数据）
```

**修复 DNS**：参考 `wsl-windows-interop` 技能的 [WSL2 DNS Troubleshooting](#wsl2-dns-troubleshooting) 章节。核心操作：禁用 `generateResolvConf` + 写死 `114.114.114.114` / `223.5.5.5` 到 `/etc/resolv.conf`。

**修复后验证**：`python3 -c "import socket; print(socket.getaddrinfo('open.feishu.cn', 443)[0][4][0])"` 应返回 IP 而非异常。

#### MCP 工具 DNS 独立——系统 DNS 故障时的唯一实时数据源

当 WSL2 DNS 污染（所有 `.cn` 域名解析到 `28.0.0.x` 假 IP）时，MCP eastmoney 是唯一可用的实时数据源：
- ✅ **MCP eastmoney 仍正常工作** — MCP server 的 Python HTTP 客户端有独立 DNS 解析路径，不受 `/etc/resolv.conf` 影响
- ❌ `curl` 到 qt.gtimg.cn / hq.sinajs.cn → timeout（DNS 假 IP）
- ❌ `web_search` → DuckDuckGo 超时
- ❌ `browser_navigate` → CDP 超时

**应对策略**：DNS 故障时 MCP 是唯一活路。但 MCP 没有指数行情（上证/深成/创业板），需靠本地缓存 `market_snapshots/{last}.json` 补指数数据。出报告时标注「指数数据来自 N 个工作日前快照」。如果 MCP 也间歇报错（见下条），数据采集降级到纯缓存模式，应及时提示用户修 DNS。

Cron 的 `skills` 数组中的 skill 名如果对应不到磁盘上的 SKILL.md，该 skill 会被静默跳过，不会报错也不会有日志。这会导致机器人跑 crons 时少了关键分析框架。

**修复步骤：**
1. 用 `cronjob action=list` 查看哪些 crons 引用了不存在的 skill
2. 用 `cronjob action=update skills=[]` 移除不存在的 skill 名，或用正确的名称替换
3. 决定是否需要补充该 skill 的内容，或用另一个存在的 skill 替代

**名称注意**：`muxuuu-serenity-skill` 的 SKILL.md frontmatter 中 `name: serenity-skill`，但 Hermes 按目录名注册。因此 cron 的 `skills` 数组中必须写 `muxuuu-serenity-skill`，写 `serenity-skill` 会静默跳过。三个 crons（晨间报告、收盘复盘、月度候选刷新）已修复为 `muxuuu-serenity-skill`。

**同一问题也需检查 memory**：用户人格 memory 中的触发规则（如「3框架分析」加载哪些 skill）也可能写的是 `serenity-skill`，必须同步改为 `muxuuu-serenity-skill`，否则手动触发场景时同样加载不到。

### MCP 工具间歇返回空错误——首次调通后重试反而失败

`get_stock_quote` 和 `sector_fund_flow` 等 MCP eastmoney 工具存在一个间歇性 bug：**首次调用返回正常数据，后续立即重试返回空字符串错误（`Error calling ...` 无具体信息）**。这不是代码问题，而是 MCP server 内部状态问题。

**影响**：如果场景流程先调 `get_stock_quote` 取持仓行情成功，后续想再次调用同一股票码来对比——第二次可能报空错误。

**应对策略**：
1. MCP 返回一次正常数据后，该数据即视为当前可用值，不要为了验证"是否最新"而立即重调
2. 如果某个股票第一次就报错，跳过后继续取下一个，不卡死——用缓存或带前缀重试（sz002415 vs 002415）
3. 非关键时刻（如定时数据采集），stale 数据 + 标注「缓存/实时误差」优于反复重试导致输出延迟
4. 如果多只股票同时报错（≥3只连续返回空错误），说明 MCP server 本身不稳定——直接切到缓存模式，不要死磕 MCP

### API Provider 反复超时——连续失败 2+ 天时必须切换 provider

当用户 cron（盘前简报、午盘速览、收盘复盘）连续 2 天以上因 `Request timed out` / `Streaming failed before delivery` 失败时，判定为 provider 慢性拥堵而非临时波动，必须主动切换。

**典型场景**：DeepSeek API 连续 3 天（7/28、7/29、7/30）在盘前简报运行时反复超时。agent.log 特征：
```
API call #3 latency=60.7s         ← 首次高延迟
Streaming failed: Request timed out  ← composition 超时
API call failed after 3 retries    ← 3 次重试后最终失败
```

**修复步骤**：
1. 查 cron 列表：`cronjob action=list`，看 `last_status=error` + 错误消息含 `timed out`
2. 为用户手动补发内容（按对应场景流程走）
3. **永久修复**：给该 cron 配置替代 provider 的 model override，作为定时任务的备用推理服务

```bash
# 当前 cron 的 provider 是 deepseek，添加 model override 切换到 NVIDIA NIM 免费 endpoint
cronjob action=update job_id=<id> model.provider=custom:nvidia model.model=nvidia/nemotron-3-ultra-550b-a55b
```

**可用备用 provider 清单**（需先在 `.env`/`config.yaml` 中配置 API key）：

| Provider | API Key | base_url | 推荐模型 | 免费？ |
|----------|---------|----------|---------|:-----:|
| NVIDIA NIM | 注册 build.nvidia.com | https://integrate.api.nvidia.com/v1 | nemotron-3-ultra-550b-a55b | ✅ 免费 40RPM |
| Z.ai (GLM) | GLM_API_KEY | https://open.bigmodel.cn/api/paas/v4 | glm-5.2-flash | 有免费额度 |

**配置方式（二选一）**：

**方案 A（推荐，仅切换 cron 任务，不影响会话）：** 在 `cronjob action=update` 时传入 `model` 字段，只影响该 cron 的 LLM 调用，当前交互会话仍用默认 DeepSeek：
```bash
cronjob action=update job_id=<id> model.provider=custom:nvidia model.model=nvidia/nemotron-3-ultra-550b-a55b
```

**方案 B（切换默认 provider）：** 全局切换到 NVIDIA NIM：
```bash
hermes config set model.provider "custom:nvidia"
hermes config set model.base_url "https://integrate.api.nvidia.com/v1"
hermes config set model.model "nvidia/nemotron-3-ultra-550b-a55b"
```
然后在 `.env` 中添加 `CUSTOM_NVIDIA_API_KEY=<你的 key>`

**注意**：NVIDIA NIM 免费端点是 ~40 RPM 共享限速。如果多个 cron 同时跑（如 09:25 晨间 + 09:30 午盘），高峰期可能被限。建议先将单一高频失败 cron 切过去观察，稳定后再扩充。

**验证方法**：手动补发时让当前会话用 NVIDIA 跑一次，看 API 延迟和输出质量。具体做法：在 `.env` 中配好 key 后，回复用户「我来用 NVIDIA 跑一次看看效果」再执行对应场景。如果延迟 <20s 且输出正常，即可切 cron。

### NVIDIA NIM 的 Hermes 专用 BP（NemoClaw）

NVIDIA build.nvidia.com 平台上有一个 **NemoClaw for Hermes Agent** 的 Blueprint，专门为 Hermes Agent 做了适配。如果后续需要更深入的 NVIDIA + Hermes 集成（如私有部署、NeMo 微调），可参考 `https://build.nvidia.com/nvidia/nemoclaw-for-hermes-agent`。这是一个独立功能，不影响当前免费 API 的使用。