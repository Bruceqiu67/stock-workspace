# Sina Finance API 实战笔记（WSL 环境）

> 补充 eastmoney-api-patterns.md 中未覆盖的 Sina Finance API 实操细节。
> 更新时间：2026-07-06

## 基础调用

```bash
curl -sL -H "Referer: https://finance.sina.com.cn" \
  "https://hq.sinajs.cn/list=sh688017,sz002472,..." \
  -o /tmp/sina_prices.txt
```

- `sh` 前缀 = 上海主板/科创板，`sz` 前缀 = 深圳主板/创业板
- **必须加 `-H "Referer: https://finance.sina.com.cn"`**，否则返回空
- 从 WSL 调用成功率高于东方财富 push2 API（push2 常返回 exit code 52 "empty reply"）

## 编码问题

返回的数据是 **GBK 编码**。股票名称中的中文会显示为乱码。
```python
# 正确读取方式
with open('/tmp/sina_prices.txt', 'r', encoding='gbk', errors='replace') as f:
    data = f.read()
```

## 字段映射（0-indexed CSV）

```
var hq_str_sh688017="名称,今开,昨收,现价,最高,最低,买1价,...,日期,时间"
```

| 索引 | 字段 | 说明 |
|:--:|:--|:--|
| 0 | 股票名称 | GBK中文，需 decode |
| 1 | 今开 | 今日开盘价 |
| 2 | 昨收 | 昨日收盘价 |
| 3 | 现价 | 当前最新价 |
| 4 | 最高 | 当日最高价 |
| 5 | 最低 | 当日最低价 |
| 6-31 | 买卖盘 | 不常用 |
| 32+ | 日期/时间/其他 | |

**涨跌幅计算**：(现价 - 昨收) / 昨收 × 100%

## 实战脚本模板

```python
import re

data = open('/tmp/sina_prices.txt', 'r', encoding='gbk', errors='replace').read()
lines = data.strip().split('\n')

for line in lines:
    match = re.search(r"hq_str_(\w+)=\"([^\"]+)\"", line)
    if match:
        code = match.group(1)        # e.g. "sh688017"
        fields = match.group(2).split(',')
        name = fields[0]             # GBK中文名
        open_p = float(fields[1])
        close_yest = float(fields[2])
        cur = float(fields[3])
        high = float(fields[4])
        low = float(fields[5])
        chg_pct = (cur - close_yest) / close_yest * 100
        # → 输出处理
```

## 注意事项

- 数据为**实时行情**，非延时数据
- 没有资金流向字段（不同于东方财富 API）
- 板块板块指数可通过 `hq.sinajs.cn/list=bkXXXX` 获取
- 批量查询最多建议 30-40 只，过多可能截断
