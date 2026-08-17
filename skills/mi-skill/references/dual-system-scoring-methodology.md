# 双体系自动评分方法

> 输出物来自 2026-07-28 双体系分析实践 + 自动化方案设计
> 供后续开发自动化评分脚本时参考

## 评分框架

```
综合分 = 技术形态分 × 0.40 + 产业链价值分 × 0.40 + 催化情绪分 × 0.20
```

## 1. 技术形态分 (0-5)

### 来源数据
`mcp_eastmoney_get_kline(code, period="daily", limit=30)` → klines数组，每根含：
- `close`, `volume`, `high`, `low`, `amplitude`

### 评分维度

| 维度 | 权重 | 输入 | 评分逻辑 |
|:----|:---:|:----|:--------|
| 52周相对位置 | 0.30 | klines[30天]高低点 | 位置越低分越高：0%=5分, 50%=2.5分, 100%=0分 |
| 缩量程度 | 0.25 | 当日量/20日均量 | <0.3→5分, 0.3~0.7→4分, 0.7~1.3→3分, 1.3~2→2分, >2→1分 |
| MA排列 | 0.20 | MA5/MA10/MA20 | 多头→5分, 混乱→3分, 空头→1分 |
| 5日涨跌幅 | 0.15 | (close-closes[-6])/closes[-6] | >+3%→4, -3%~+3%→3, -8%~-3%→2, <-8%→1 |
| 振幅收敛 | 0.10 | 近5日均振幅 | <3%→5, 3-5%→4, 5-8%→3, 8-12%→2, >12%→1 |

### Python骨架

```python
def calc_technical_score(klines: list) -> float:
    closes = [k['close'] for k in klines]
    volumes = [k['volume'] for k in klines]
    high52 = max(k['high'] for k in klines)
    low52 = min(k['low'] for k in klines)

    # 相对位置 (0-1, 低位分高)
    rel_pos = (closes[-1] - low52) / (high52 - low52) if high52 > low52 else 0.5
    score_pos = max(0, 1 - rel_pos) * 5  # 0%→5, 100%→0

    # 缩量比
    avg_vol_20 = sum(volumes[-20:]) / 20
    vol_ratio = volumes[-1] / avg_vol_20 if avg_vol_20 > 0 else 1
    score_vol = {r: s for r, s in [(0.3, 5), (0.7, 4), (1.3, 3), (2.0, 2)]}
    score_vol = next((s for threshold, s in sorted(score_vol.items()) if vol_ratio < threshold), 1)

    # MA排列
    ma5 = sum(closes[-5:]) / 5
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    if ma5 > ma10 > ma20: score_ma = 5
    elif ma5 > ma10 and ma10 < ma20: score_ma = 3
    elif closes[-1] < ma5 and ma5 < ma10: score_ma = 2
    else: score_ma = 1

    # 5日涨跌
    chg_5d = (closes[-1] - closes[-6]) / closes[-6]
    score_chg = 4 if chg_5d > 0.03 else 3 if chg_5d > -0.03 else 2 if chg_5d > -0.08 else 1

    # 振幅
    avg_amp = sum(k['amplitude'] for k in klines[-5:]) / 5
    score_amp = 5 if avg_amp < 3 else 4 if avg_amp < 5 else 3 if avg_amp < 8 else 2 if avg_amp < 12 else 1

    return round(score_pos*0.3 + score_vol*0.25 + score_ma*0.2 + score_chg*0.15 + score_amp*0.1, 2)
```

## 2. 产业链价值分 (0-5)

### 方向权重表

```python
SECTOR_WEIGHTS = {
    "半导体/封测/材料": {"卡脖子": 4, "关税受益": 5, "长期确定性": 5, "base": 3.5},
    "AI算力/信创":     {"卡脖子": 3, "关税受益": 4, "长期确定性": 4, "base": 3.0},
    "功率半导体":      {"卡脖子": 3, "关税受益": 2, "长期确定性": 3, "base": 3.0},
    "电网设备":        {"卡脖子": 3, "关税受益": 2, "长期确定性": 4, "base": 3.0},
    "机器人":          {"卡脖子": 2, "关税受益": 2, "长期确定性": 4, "base": 2.5},
    "锂电/新能源":     {"卡脖子": 2, "关税受益": 1, "长期确定性": 2, "base": 2.0},
    "消费电子":        {"卡脖子": 1, "关税受益": 1, "长期确定性": 2, "base": 1.5},
    "创新药":          {"卡脖子": 1, "关税受益": 1, "长期确定性": 2, "base": 1.5},
    "证券":            {"卡脖子": 1, "关税受益": 1, "长期确定性": 1, "base": 1.0},
}
```

### 市场信号加权

```python
def calc_industry_score(candidate: dict, market_signal: dict) -> float:
    sector = candidate['primary_sector']
    w = SECTOR_WEIGHTS.get(sector, {"base": 1.5})
    score = w["base"]

    # 关税激活 → 关税受益≥4的方向加1分, ≥2加0.5分
    if market_signal.get('tariff_active'):
        if w["关税受益"] >= 4: score += 1.0
        elif w["关税受益"] >= 2: score += 0.5

    # 资金流入方向
    if sector in market_signal.get('top_capital_flow_sectors', []):
        score += 0.5

    # 位置修正
    pos = candidate.get('rel_position_52w', 50)
    score += 0.3 if pos < 30 else -0.5 if pos > 70 else 0

    return round(min(5, max(1, score)), 2)
```

## 3. 催化情绪分 (0-5)

```python
def calc_catalyst_score(candidate: dict, sector_flow_data: list) -> float:
    sector = candidate['primary_sector']
    score = 3.0

    # 找到该方向在sector_fund_flow中的条目
    flow = find_flow_for_sector(sector, sector_flow_data)
    if flow:
        inflow = parse_net_inflow(flow['main_net_inflow'])  # "8.11亿" → 8.11
        score += 1.5 if inflow > 10 else 1.0 if inflow > 5 else 0.5 if inflow > 2 else -1.0 if inflow < -5 else 0
        score += 0.5 if flow['change_pct'] > 2 else -0.5 if flow['change_pct'] < -2 else 0

    # 逆势抗跌加分
    if candidate['change_pct'] > 0 and score < 4:
        score += 0.3

    return round(min(5, max(1, score)), 2)
```

## 4. 综合输出格式

```json
{
  "date": "YYYY-MM-DD",
  "generated_at": "ISO timestamp",
  "weights": {"technical": 0.4, "industry": 0.4, "catalyst": 0.2},
  "market_signal": {
    "tariff_active": true,
    "top_capital_flow_sectors": ["软件开发", "半导体设备"],
    "notes": "free-text summary of market context"
  },
  "rankings": [
    {
      "rank": 1,
      "code": "sz300054",
      "name": "鼎龙股份",
      "price": 71.52,
      "change_pct": -3.57,
      "scores": {"technical": 3.5, "industry": 4.5, "catalyst": 3.8, "combined": 3.96},
      "signals": ["底部缩量", "关税受益", "材料卡脖子"],
      "action_label": "watch_70_below"
    },
    ...
  ]
}
```

## 权重调优建议

初始：0.4/0.4/0.2。运行一周后：

```
收集 N 天数据
每位候选有：自动评分 auto  + 人工评分 manual（如果记录了）
优化目标：min ∑(auto - manual)²
调优方法：网格搜索 {tech: [0.3,0.4,0.5], ind: [0.3,0.4,0.5], cat: [0.1,0.2,0.3]}
           使 tech+ind+cat = 1.0
```

## 验证用例

### 用例1：通富微电 2026-07-28
- 输入：价格69.00, 52w位置13%, 涨跌幅-10%, 成交132亿
- 预期：技术分 2.0-3.0 (位置低但暴跌扣分), 产业链分 3.5-4.5, 综合分 2.5-3.5

### 用例2：关税激活状态检测
- tariff_active=true → 半导体材料产业链分≥4.0, 消费电子≤2.0
- tariff_active=false → 差异缩小，材料~3.5, 消费电子~1.5

### 用例3：紫光股份 2026-07-28日内反转
- 早盘-3.3% → 收盘+1.25%, 成交159亿放量
- 催化情绪分应≥3.5（大盘跌它涨）, 技术分~3.0（高位回撤中）
