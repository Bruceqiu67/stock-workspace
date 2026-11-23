# 双体系自动评分方法

> 输出物来自 2026-07-28 双体系分析实践 + 自动化方案设计
> 供后续开发自动化评分脚本时参考

## 输入契约：analysis_context（唯一动态数据入口）

评分引擎不再从候选对象里“猜”市场状态。候选扫描必须先读取
`~/.hermes/data/analysis_context/YYYY-MM-DD.json`，显式传给
`calculate_combined_score(analysis_context=...)`。

```json
{
  "schema_version": "1.0",
  "as_of": "YYYY-MM-DD",
  "source": "mcp-eastmoney sector_fund_flow + main_fund_rank",
  "tariff_active": true,
  "mainline_sectors": ["半导体/封测/材料"],
  "top_capital_flow_sectors": ["半导体", "电网设备"],
  "sector_flow_data": [
    {"name": "半导体", "main_net_inflow": "12.5亿", "change_pct": 1.8}
  ],
  "main_fund_rank": [
    {"code": "sz300054", "name": "鼎龙股份", "main_net_inflow": "3.2亿"}
  ],
  "notes": ""
}
```

约定：

- `tariff_active` 为 `null` 表示「未知」，不等于「未激活」。
- `sector_flow_data` 缺失或无法匹配候选方向时，催化分返回
  `score=null + status=abstain`，该标的进入 `scoring_status=abstain`，
  **不参与买入排名**。
- 非关键动态字段缺失时，综合分可以计算用于诊断，但
  `rankable=false`、`action_label=DATA_DEGRADED`，不输出买入标签。
- 上下文过期（`stale=true`）时同样降级，下游报告必须显示 `as_of`
  与 `data_age_days`。

## 缺失数据处理（禁止“中等偏上”兜底）

| 缺失情形 | 返回 | 下游行为 |
|:--|:--|:--|
| K线 <5 根 / NaN / inf / 非正价格 | `score=null + status=abstain` | 列入 `no_data`，不参与排名 |
| 最新成交量为0（停牌） | `score=null + status=abstain` | 列入 `no_data`，不参与排名 |
| 未知行业 | 产业分 `status=degraded`，`missing_inputs=[sector_profile]` | `rankable=false`，不输出买入标签 |
| 板块资金流缺失/未匹配 | 催化分 `score=null + status=abstain` | 整个候选 `scoring_status=abstain` |
| 评分引擎导入失败 | `scoring_status=abstain`、`action_label=ABSTAIN_IMPORT_FAILED` | 不写 3.0 中性分，不输出买入标签 |
| 行情缺失 | 计入 `filtered.no_data` | 统计行不再长期显示 0 |

> 缺失数据不再得到 2.0/2.5/3.0 之类的“中等偏上”分数；要么弃权，要么显式降级并禁用操作标签。

## 规范量表（单一事实源）

**规范评分 = 0-100**，定义在 `scripts/scoring_config.py`（`CONFIG_VERSION`）：

```
canonical_0_100 = (技术0-5 × 0.40 + 产业0-5 × 0.40 + 催化0-5 × 0.20) × 20
```

- 风险子分（0-5，越高越安全）作为**诊断维度**输出，不改变既有排名权重。
- 动作阈值也统一在规范量表：`≥80 STRONG_BUY_WATCH`、`≥66 ACCUMULATE_ON_DIP`、`≥50 NEUTRAL_OBSERVE`、`<50 AVOID_DEFENSE`。
- 权重、行业基准表、阈值只允许在本文件定义；`scoring_engine.py` 和 `market_state.py` 直接引用，禁止再维护第二套。

其余量表都是**展示层**，由 `scale_mappings()` 显式换算：

| 量表 | 换算 | 说明 |
|:--|:--|:--|
| 0-5 引擎分 | canonical / 20 | 兼容旧报告 |
| 0-20 双框架分 | canonical / 5 | 场景B展示 |
| 0-100 底部检查 | canonical | 仅展示对齐，维度定义不同 |
| 五星制 | round(0-5) | 深度分析展示 |
| Serenity 0-100 | canonical | 公司级映射，需标注 `mapping_confidence=low` |

> 同一只股票在不同入口必须从同一个 canonical 分换算，不能各自维护权重表。

## 评分框架

```
综合分 = 技术形态分 × 0.40 + 产业链价值分 × 0.40 + 催化情绪分 × 0.20
```

## 恐慌态一票否决

`market_state.py::check_stabilization()` 对每只候选逐项计算：

1. 缩量：近两日均量 ≤ 恐慌日成交量 × 50%
2. 不创新低：近两日最低价均高于恐慌日最低价
3. 站上5日线：收盘价 ≥ MA5

三项未全部通过时，候选扫描会把 `STRONG_BUY_WATCH` / `ACCUMULATE_ON_DIP`
改写为 `OBSERVE_DEFENSE_PANIC_UNCONFIRMED`，并在输出中记录
`stabilization` 与 `panic_veto` 原因。只有全部通过才保留原评级。

## 1. 技术形态分 (0-5)

### 来源数据

- 日K：`get_kline(period="daily", limit=250)`（至少 120 根，目标 250 根覆盖真实 52 周）
- 周K：`get_kline(period="weekly", limit=60)`（周线定方向）
- 每根含：`close`, `volume`, `high`, `low`, `amplitude`

### 评分维度

| 维度 | 权重 | 输入 | 评分逻辑 |
|:----|:---:|:----|:--------|
| 52周相对位置 | 0.25 | 最近 250 日（至少 120 日）高低点 | 位置越低分越高：0%=5分, 50%=2.5分, 100%=0分 |
| 缩量程度 | 0.20 | 当日量/20日均量 | <0.4→5分, 0.4~0.7→4.2, 0.7~1.3→3, 1.3~2→2.2, >2→1 |
| MA5/10/20/60排列 | 0.25 | MA5/MA10/MA20/MA60 | 含MA60多头→5, 多头→4.2, 短多→3.8, 混乱→3.0, 空头→1.0 |
| 5日涨跌幅 | 0.10 | (close-closes[-6])/closes[-6] | >+5%→4.5, 0~+5%→3.8, -5~0→2.8, -10~-5→2.0, <-10→1.0 |
| 振幅收敛 | 0.10 | 近5日均振幅 | <3%→5, 3-5%→4, 5-8%→3, 8-12%→2, >12%→1 |
| 周线趋势 | 0.10 | 周K MA5/MA10/MA20 | 站上MA20且MA5≥MA10→5, 仅站上MA20→3.5, 周线多头→2.5, 空头→1.0 |

### 数据窗口与降级

- `position_window_days` 会写入评分明细；<120 日时 `status=degraded`，并使用可用窗口，**不再把 30 日窗口当成 52 周**。
- 周K缺失（<20 根）时 `status=degraded`，周线因子记 2.5 并输出 `weekly_status=missing`；该标的不可排名、不输出买入标签。
- 相对位置只在技术分计一次；产业分不再对低位/高位重复加分或扣分。

## 2. 产业链价值分 (0-5)

产业分拆成 **板块 beta（60%）+ 公司 alpha（40%）**：

```
产业分 = 板块beta × 0.60 + 公司alpha × 0.40
```

### 板块 beta

由 `scoring_engine.py::SECTOR_ATTRIBUTES` 提供方向级基准与动态加成
（关税激活、主力主攻方向）。权重表以代码为单一事实源：

```python
SECTOR_ATTRIBUTES = {
    "半导体/封测/材料": {"卡脖子": 5, "关税受益": 5, "长期确定性": 5, "base": 3.8},
    "AI算力/信创":     {"卡脖子": 4, "关税受益": 4, "长期确定性": 4, "base": 3.2},
    "功率半导体":      {"卡脖子": 3, "关税受益": 2, "长期确定性": 3, "base": 3.0},
    "电网设备":        {"卡脖子": 3, "关税受益": 2, "长期确定性": 4, "base": 3.0},
    "机器人":          {"卡脖子": 3, "关税受益": 2, "长期确定性": 4, "base": 2.8},
    "锂电/新能源":     {"卡脖子": 2, "关税受益": 1, "长期确定性": 2, "base": 2.0},
    "消费电子":        {"卡脖子": 2, "关税受益": 1, "长期确定性": 2, "base": 1.8},
    "创新药":          {"卡脖子": 2, "关税受益": 1, "长期确定性": 2, "base": 1.8},
    "证券":            {"卡脖子": 1, "关税受益": 1, "长期确定性": 1, "base": 1.0},
}
```

### 公司 alpha（按代码，不按板块）

`scripts/company_profiles.py` 为每只股票解析公司级画像：

| 字段 | 含义 | 权重 |
|:--|:--|:--:|
| `chokepoint` | 卡脖子等级 | 0.30 |
| `evidence` | 证据等级（强/中/弱/未核实） | 0.20 |
| `order_validation` | 订单/业绩验证 | 0.20 |
| `competitive_position` | 竞争格局 | 0.15 |
| `rerate_potential` | 重新评级潜力 | 0.15 |

数据来源优先级：

1. `~/.hermes/data/company_profiles.json`（研究流程维护，最高优先级）
2. 内置 `COMPANY_OVERRIDES` 代码级基线
3. 内置 `SECTOR_BASELINE` 行业代理（`specificity=sector_proxy`，置信度低）

Serenity scorecard 的稳定 JSON 输出包含 `engine_profile`，可直接映射为公司画像：
`chokepoint_severity→chokepoint`、`evidence_quality→evidence`、
`catalyst_timing→order_validation`、`supplier_concentration→competitive_position`、
`valuation_disconnect→rerate_potential`。外部画像文件可用 `engine_profile` 直接覆盖内置基线。

> 行业代理只用于“没有公司研究时的显式降级”，`alpha_confidence=low`；
> 不得把行业 beta 当成公司 alpha，也不得在同一板块内所有公司共享同一结论。

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

    # 注意：相对位置只在技术分计一次，产业分不再重复修正。

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
