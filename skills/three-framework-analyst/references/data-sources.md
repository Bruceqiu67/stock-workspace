# 数据源参考

## Level 1：本地缓存目录结构

```
~/.hermes/data/
├── market_snapshots/YYYY-MM-DD.json    — 收盘快照（指数+热股）
├── portfolio/current.json              — 持仓实时状态
├── premarket/YYYY-MM-DD.json           — 盘前外围数据
├── candidates/YYYY-MM-DD.json          — 当日候选池
└── predictions/
    ├── daily/YYYY-MM-DD.json           — 逐日预判记录
    └── tracker.py                      — 记录/复盘/报告工具
```

## Level 2：MCP 工具详细参数

### sector_fund_flow — 板块资金流向排行（核心！替代被封的 push2 clist）

```python
# 行业板块排行
sector_fund_flow(kind="industry", limit=20)
# → 返回：[{"板块名称", "涨跌幅", "主力净流入", "领涨股", ...}, ...]

# 概念板块排行
sector_fund_flow(kind="concept", limit=20)
# → 同上
```

### main_fund_rank — 主力资金净流入排行

```python
main_fund_rank(limit=20, market="all")
# market: "all" / "sh"(沪市) / "sz"(深市) / "cyb"(创业板) / "kcb"(科创板)
```

### get_stock_quote — 个股实时行情

```python
get_stock_quote(code="600519")
# code: 6位数字，不含 sh/sz 前缀
```

### search_stock — 搜索股票

```python
search_stock(keyword="宁德", limit=10)
# keyword: 名称、代码、拼音
```

### get_kline — K线数据

```python
get_kline(code="600519", period="daily", limit=30)
# period: "daily" / "weekly" / "monthly" / "5min" / "15min" / "30min" / "60min"
```

## Level 3：curl API 速查

### 腾讯实时行情 qt.gtimg.cn

```bash
curl -s "http://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
# 返回 GBK 编码，需要 decode("gbk")
# 字段索引:
#   [3]  现价
#   [32] 涨跌幅(%)
#   [33] 当日最高
#   [34] 当日最低
#   [37] 成交额(万)
#   [38] 换手率(%)
#   [43] 振幅(%)
#   [47] 52周最高
#   [48] 52周最低
#   ⚠️ [51-53] MA值对个股不可信！
```

### 新浪美股 hq.sinajs.cn

```bash
curl -s -H "Referer: https://finance.sina.com.cn" \
  "https://hq.sinajs.cn/list=gb_dji,gb_ixic,gb_inx"
# 返回: 名称,现价,涨跌幅,时间戳

# 国际指数
curl -s -H "Referer: https://finance.sina.com.cn" \
  "https://hq.sinajs.cn/list=int_nikkei,int_kospi,int_hangseng"
# 返回: 名称,现价,涨跌额,涨跌幅
```

## Level 4：web_search

慢（5-15秒），只在以下场景使用：
- 产业逻辑验证（新方向的产业背景）
- 新闻催化（当日热点事件）
- 财报搜索（具体公司的财务数据）

连续失败 3 次后立即切换策略，不要死磕。
