# 七场景完整工作流

---

## Pre-note: 预判记录规范

仅场景G（收盘复盘）需要记录预判，使用 `--type recap` 参数：

```bash
python3 ~/.hermes/data/predictions/tracker.py record \
  --type recap \
  --summary "明日走势预判" \
  --base-scenario "..." --base-prob 65 \
  ... 
```

> 规则：场景A（大盘分析）是纯信息报告，不做预判记录。收盘复盘才是预判的唯一入口。

---

## 场景A：大盘分析

**触发词**：「分析大盘」「今天市场」「出一份报告」「今日行情」

**Step 1: 读取缓存**
```
market_snapshots/{today}.json   → 今日收盘快照（如果在15:05后）
market_snapshots/{yesterday}.json → 如果没有今日快照
premarket/{today}.json           → 盘前外围数据
```

**Step 2: 补充实时数据（如果缓存缺失）**
- 指数行情 → `qt.gtimg.cn/q=sh000001,sz399001,...`（腾讯）
- 热股情绪代理 → 同上，加 `sz002371,sh688981,...`
- 板块排行 → MCP `sector_fund_flow(kind="industry")`
- 外围 → 新浪 `gb_dji,gb_ixic,gb_inx`

**Step 3: 三框架分析**
| 维度 | 数据 | 来源 | 关键问题 |
|------|------|------|---------|
| 资金流 | 指数量价/振幅/成交额 | 腾讯/MCP | 今日主力意图？ |
| 产业链 | 板块轮动/领涨方向 | MCP sector_fund_flow | 是什么产业逻辑在驱动？ |
| 研究方法 | 外围联动/情绪温度 | 新浪+web_search | 市场处于什么阶段？ |

**Step 4: 输出报告**
按以下结构输出：

```
# 📊 三框架市场分析 · YYYY年MM月DD日

## 一、大盘全景
[指数表: 上证/深成/创业板/科创50/沪深300 — 收盘/涨跌/振幅/成交额]

## 二、情绪与资金
[外围联动 + 情绪温度计 🟢🟡🔴 + 综合判断]

## 三、方向与轮动
[板块排行前5 + 领涨逻辑 + 资金流向判断]

## 四、今日关注
| 关注点 | 逻辑 |
```

**输出完报告后结束。场景A不做预判记录（预判统一在16:00场景G完成）。**

---

## 场景B：方向选股

**触发词**：「选股」「推荐方向」「有什么方向」「想买点股票」「选一只股」

**Step 1: 读候选池**
```python
candidates/{today}.json  # 收盘后有数据
# 如果缓存不存在 → 临时跑一次批量查询
```

**Step 2: 按方向筛选**
- 对各方向做 Serenity 产业链评分：
  - 上游稀缺性(0-3) — 是否是产业链卡脖子环节？
  - 机构验证(0-3) — 是否有公开订单/业绩支撑？
  - 业绩确定性(0-2) — 半年内业绩确定性如何？
  - 估值合理性(0-2) — PE是否合理？

**Step 3: 双框架数值评分（0-20分）**
对候选池内通过硬过滤的股票评分：

| 维度 | 满分 | 评分标准 |
|------|:----:|---------|
| S-上游稀缺性 | 3 | 卡脖子=3，龙头=2，普通=1 |
| S-机构验证 | 3 | 订单/业绩=3，机构调研=2，纯概念=0 |
| S-业绩确定性 | 2 | 半年内确定增长=2，稳定=1，暴雷=0 |
| S-估值合理性 | 2 | PE<20+增长=2，合理=1，亏损/PE>100=0 |
| M-均线排列 | 3 | 完美多头=3，多头=2，纠缠=1，空头=0 |
| M-相对位置 | 3 | <20%=3, <40%=2, <60%=1, ≥70%淘汰 |
| M-量价配合 | 2 | 缩量企稳=2，正常=1，异常放量=0 |
| M-资金流向 | 2 | 近5日主力净流入=2，平衡=1，流出=0 |

**Step 4: 输出**

```
## 今日候选排名

| 排名 | 股票 | 现价 | 方向 | 总分 | 核心逻辑 |
|------|------|:----:|:----:|:----:|---------|
| 1 | ... | ... | ... | 18 | ... |
| 2 | ... | ... | ... | 16 | ... |

前三名详细解读...
配置建议：核心进攻 / 攻守兼备 / 低位反击
```

---

## 场景C：持仓诊断

**触发词**：「看看持仓」「我的股票」「帮我看看XX股」「持仓分析」

**Step 1: 读持仓状态**
```python
portfolio/current.json  # 由15:10 cron 更新
```

**Step 2: 补充实时行情（如需要）**
```python
# MCP 或腾讯 API 拉当前实时价
get_stock_quote(code="603019")
```

**Step 3: 输出持仓诊断表**

```
## 持仓监控仪表盘

| 股票 | 成本 | 现价 | 今日 | 浮亏 | 止损 | 状态 |
|------|:---:|:----:|:----:|:----:|:----:|------|
| 中科曙光 | 90.13 | ... | +X% | +X% | 82.00 | ✅ |
| ... | ... | ... | ... | ... | ... | ... |

总投入: ¥XX,XXX
总浮盈: ¥X,XXX (+X%)
总仓位: 40% / 现金 60%

操作建议：
- 中科曙光：浮盈可观，...（每只个股逐一给出操作思路）
```

---

## 场景D：3框架分析

**触发词**：「3框架分析」

**路由逻辑**：自动判断用户意图，路由到最匹配的场景：
- 用户没有指定具体方向 → **默认为大盘分析（场景A）**
- 用户说「3框架分析XX方向」 → **方向选股（场景B）**
- 用户说「3框架分析我的持仓」 → **持仓诊断（场景C）**

---

## 场景E：飞书报告

**触发词**：「给飞书发报告」「发到飞书」

**Step 1: 先输出到终端**（让用户确认内容）

**Step 2: 加载 feishu-report-delivery skill**
```python
skill_view(name="feishu-report-delivery")
```

**Step 3: 按 feishu-report-delivery 流程执行**
1. 用 reportlab + WQY 字体生成 PDF（在 /tmp/pdfgen-env/）
2. 用 pymupdf 验证 PDF 无乱码
3. 读 FEISHU_APP_ID / FEISHU_APP_SECRET 从 ~/.hermes/.env
4. POST 获取 tenant_access_token
5. POST 上传 PDF 文件
6. POST 发送 file message 到飞书 chat_id: oc_<YOUR_FEISHU_CHAT_ID>
7. 同时发送文本摘要到飞书

**注意**：hermes send 不支持二进制附件，必须直接调 Feishu API。

---

## 场景F：预测复盘

**触发词**：「复盘」「预测准确率」「最近判断怎么样」「看看预测记录」

**Step 1: 运行复盘**
```bash
python3 ~/.hermes/data/predictions/tracker.py review
```

**Step 2: 查看报告**
```bash
# 月度/累计统计（默认）
python3 ~/.hermes/data/predictions/tracker.py report

# 按预判类型分组分析 —— 快速定位框架的强项和弱项
# 输出：偏多/偏空/震荡各类型的方向正确率 + 错误类型分布
python3 ~/.hermes/data/predictions/tracker.py report --by-type
```

**Step 3: 输出复盘结论**

```
## 预测复盘 · 截至 YYYY年MM月DD日

累计预判: X 条
综合准确率: XX/100

月度趋势:
  2026-07: XX/100 (N条)
  2026-08: XX/100 (N条)

偏差分析：
  [如果近期准确率下降，分析可能原因]
  [如果某个 provider 持续偏乐观/偏悲观，标注]
```

---

## 场景G：收盘复盘

**触发词**：「复盘」「收盘复盘」「今日复盘」｜ **定时触发**：工作日 16:00 cron

**数据准备（15:00-15:15 已采集完毕）：**
```
market_snapshots/{today}.json            → 今日指数+热股实际数据
portfolio/current.json                   → 今日持仓盈亏
candidates/{today}.json                  → 今日候选池表现
predictions/daily/{yesterday}.json       → 昨日复盘预判（type=recap，待复盘）
```

**Step 1: 复盘昨日预判**

⚠️ **预判连续性检查（前置）** — 在读取昨日预判前，先检查昨日是否有记录：
1. 检查 `~/.hermes/data/predictions/daily/{yesterday}.json` 是否存在
2. 如果存在，检查其中是否有 `type=recap` 的记录
3. **如果既没有文件，也没有 recap 记录** → 标注「昨日无预判记录可复盘，可能原因：非交易日/首日运行/预判记录遗漏」，然后直接跳转到 Step 2（今日分析），跳过复盘评分
4. 绝对**不要**回退到更早日期的预判来做复盘 — 非昨日对应日期的 recode 不能替代昨日复盘

> ❌ 常见错误：昨日预测文件不存在或没有 recap 记录时，误读了前天的 recode，导致复盘错位。
> ✅ 正确做法：没有就是没有，跳过并标注，不要回退去找更早的。

**重要：每次复盘后必须将准确率写回昨日预判的 JSON 文件**，否则 tracker.py report 看不到累计数据。

```bash
# 读取昨日 predictions 文件中 type=recap 的记录
# 拉取今日指数数据对比评分
python3 ~/.hermes/data/predictions/tracker.py review --date {yesterday}
```

如果手动计算评分（不调 review），则需：
```python
# 手动写入 accuracy 字段到 yesterday.json
import json, pathlib
p = pathlib.Path.home() / '.hermes/data/predictions/daily'
yesterday = (pathlib.Path.home() / '...')  # 计算昨日日期
f = p / f'{yesterday}.json'
data = json.loads(f.read_text())
for r in data:
    if r.get('type') == 'recap' and r.get('accuracy') is None:
        r['accuracy'] = {'direction_score': X, 'level_score': Y, 'overall': Z}
        r['actual'] = {'sh_index_close': 3913.79, 'sh_index_change_pct': -2.06}
f.write_text(json.dumps(data, ensure_ascii=False, indent=2))
```
输出示例：
```
📊 复盘预判: 昨日(2026-07-10) recap
   上证: 3913.79 (-2.06%)  振幅: 2.08%

   [pred_20260710_001] 明日走势：震荡上行
     方向分: 0.0  |  区间分: 0.0
     🎯 综合准确率: 0/100
```

**Step 2: 分析今日实际走势**
- 读取 market_snapshots/{today}.json → 指数表
- 读取 portfolio/current.json → 持仓今日表现
- 读取 candidates/{today}.json → 方向轮动表现
- MCP sector_fund_flow → 今日资金流向

**Step 3: 输出复盘报告**

结构：
```
📊 三框架收盘复盘 · YYYY年MM月DD日

一、今日市场回顾
  指数表: 上证/深成/创业板/科创50/沪深300/国证2000 — 收盘/涨跌/振幅/成交额
  一句话定性: 今天是普跌/分化/反弹？

二、昨日预判复盘
  昨日预判: [summary]（type=recap）
  实际走势: 上证[close]([change_pct])
  评分: 方向X/区间X → 综合XX/100
  偏差分析: [判断对了什么/错了什么]

三、持仓今日表现
  逐只: 今日浮盈变化 + 止损检查
  总账: 今日盈亏 ±¥XX

四、方向轮动复盘
  今日领涨板块 & 资金流向
  候选池方向表现总结

五、明日预判 · type=recap
  基准场景 (概率XX%) | 乐观 (XX%) | 悲观 (XX%)
  关键区间: 支撑XXX / 压力XXX
  核心观察点
```

**Step 4: 记录明日预判（--type recap）**
```bash
python3 ~/.hermes/data/predictions/tracker.py record \
  --type recap \
  --summary "明日走势预判" \
  --base-scenario "..." --base-prob 65 \
  --opt-scenario "..." --opt-prob 20 \
  --pes-scenario "..." --pes-prob 15 \
  --support XXX --resistance XXX \
  --observation "..." \
  --provider "当前provider名"
```

**Step 5: 生成PDF→发送飞书**
- 按 feishu-report-delivery 技能流程
- PDF 模板用「收盘复盘」格式
- 同时发送文字摘要

**与场景A（大盘分析）的区别：**
| 维度 | 场景A 晨间 | 场景G 复盘 |
|------|-----------|-----------|
| 定位 | 展望今天（纯信息） | 回顾今天+预判明天 |
| 预判 | 不做预判记录 | type=recap（明日） |
| 核心环节 | 板块轮动+持仓诊断 | 复盘昨日预判偏差+分析今日 |
| 数据依赖 | 盘前数据+昨夜外围 | 收盘+持仓+候选池 |
| 必做动作 | 无（纯输出） | 复盘昨日recap + 记录今日recap |
