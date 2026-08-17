# 候选池刷新工作流（手动/三框架）

> 当用户要求「重新筛候选池」「用3框架分析重筛」「跑一下刷新」时使用。
> 月度刷新 cron (f20f94900f74) 只在每月 1-7 日触发，超出窗口期必须手动执行。

## 工作流总览

```
数据采集 → 方向评估 → 个股核实 → 代码更新 → 扫描验证 → 状态记录
```

## 第一步：全市场数据采集 (2-3 次 MCP 调用)

一次并行拉取，不要串行：

1. **`mcp_eastmoney_sector_fund_flow(kind="industry", limit=25)`** — 行业板块资金排行
2. **`mcp_eastmoney_sector_fund_flow(kind="concept", limit=25)`** — 概念板块资金排行
3. **`mcp_eastmoney_main_fund_rank(market="all", limit=20)`** — 个股主力资金排行

### 数据判读要点

- **逆势吸筹**：板块跌但主力净流入为正（如半导体设备跌0.7%但流入3.7亿）→ 国产替代/自主可控强化
- **连续性**：观察板块排行里的方向是否与近期市场主线一致
- **关税/政策催化**：跨境支付、国产软件、光刻机等方向在关税加征后资金活跃度变化

### 个股方向验证 (Tencent API)

对于候选池中或可能新增的方向，用 Tencent qt.gtimg.cn API 批量获取价格和相对位置：

```bash
# 分批查询（max 20 per batch）
curl -s "http://qt.gtimg.cn/q=code1,code2,..." -o /tmp/stock_check.txt
# 读文件时注意 GBK 编码
python3 -c "
with open('/tmp/stock_check.txt', encoding='gbk') as f:
    raw = f.read()
for line in raw.strip().split(';'):
    if '~' not in line or len(line) < 50: continue
    p = line.split('~')
    code, name, price = p[2], p[1], float(p[3])
    chg = float(p[32]) if len(p)>32 and p[32] else 0
    high52 = float(p[47]) if len(p)>47 and p[47] else 0
    low52 = float(p[48]) if len(p)>48 and p[48] else 0
    rel = round((price-low52)/(high52-low52)*100, 1) if high52>0<low52 else None
    if rel: print(f'{code:>6s} {name:8s} ¥{price:<8.2f} {chg:+.2f}% 位置{rel}%')
"
```

## 第二步：三框架方向评估

加载 mi-skill + serenity-perspective + muxuuu-serenity-skill，基于采集数据判断：

### 保留/精简/新增的判断标准

| 动作 | 条件 |
|:----:|------|
| **保留** | 方向有持续资金流入 + 产业逻辑仍在（如电网设备中长期配置、机器人政策催化） |
| **精简单只** | 方向内个股过多（>5只）或个别标的长期无催化 |
| **移除方向** | 方向已无资金关注 + 无近期催化 |
| **新增方向** | 板块资金排行连续靠前 + 有主题催化（关税→信创/AI应用） |
| **改名** | 方向内涵拓展需调名（半导体/封测 → 半导体/封测/材料） |

### 价格超标股处理

候选池里可以有价格长期>100元的股票，每日扫描的硬过滤会自动剔除它们。但池子里超过2只此类股时建议直接移除，避免每次扫描报告里都出现"价格超标"的冗余信息。

## 第三步：更新代码

编辑 `candidate_scanner.py` (~/.hermes/scripts/ 下)：

### A. 更新 SECTOR_ALIAS（必须同步！）

方向名改后，SECTOR_ALIAS 必须加新名映射。漏掉会导致方向分组错误。

```python
SECTOR_ALIAS = {
    "半导体/封测/材料": "半导体",  # 改名后更新
    "AI算力/信创": "AI算力",       # 改名后更新
    ...
}
```

### B. 更新 CANDIDATES dict

```python
CANDIDATES = {
    "半导体/封测/材料": [
        "sz002156",  # 通富微电 — 封测
        "sz300054",  # 鼎龙股份 — 材料新增
    ],
    "AI算力/信创": [
        "sz000977",  # 浪潮信息
        "sz300624",  # 万兴科技 — AI应用新增
    ],
    ...
}
```

### C. 更新顶部注释行

```
# ── 预定义候选列表（覆盖 9 个方向，共 42 只）──
# 2026-07-28 更新：摘要说明
```

## 第四步：运行验证

```bash
cd ~/.hermes && python3 scripts/candidate_scanner.py
```

### 验证检查点

- [ ] `行情获取: N/N 成功` — 全部获取？部分失败检查代码是否正确
- [ ] 各方向通过过滤的结果合理（价格、位置正常）
- [ ] 无意外剔除

### 常见问题

- **获取率低**：检查代码前缀（sh/sz），科创板某些代码 Tencent API 可能不返回
- **位置100%**：涨停股位置到100%，被硬过滤剔除是预期的
- **价格过高**：新增股若价格>100但产业逻辑强，可先问用户是否放宽过滤

## 第五步：记录刷新状态

```bash
python3 -c "
from candidate_refresh_state import record_refresh
record_refresh('三框架重筛候选池：改了什么，为什么，N→M只')
"
```

summary 字段写清楚：改名/新增/移除内容+原因+数量变化。
