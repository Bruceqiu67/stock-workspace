# 止跌信号监控模板

用于监控特定股票的止跌信号，检测到信号时通过飞书/Telegram提醒。

## 数据源

1. **腾讯K线API**（主）：`http://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={mkt}{code},day,,,10,qfq`
   - 24/7可用，无认证，返回后复权数据
   - 数据格式：`[日期, 开盘, 收盘, 最高, 最低, 成交量]`
   - 注意：index 2 是收盘价，与东方财富格式不同

2. **东方财富实时价**（辅）：`https://push2.eastmoney.com/api/qt/stock/get?secid={market}.{code}&fields=f43,f44,f45,...`

## 部署方式

使用 `cronjob` 的 `no_agent=True` 模式：
- 脚本输出空字符串 = 无信号，静默
- 脚本输出提醒文本 = 有信号，自动投递
- 频率建议：每30分钟（交易时段）

## 关键参数

- 止损位：按 mi-skill 规则，通常设为 MA20 × 0.95 或关键支撑位
- 买点区间：根据个股技术面确定
- 仓位上限：不超过总仓位5%（短线试探）
