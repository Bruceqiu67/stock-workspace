# 数据缓存使用规则

## 缓存目录结构

```
~/.hermes/data/
├── market_snapshots/        # 收盘快照（cron 7c70d6eea4fa, 15:05）
│   ├── 2026-07-13.json      # 7指数 + 8热股 + 成交额
│   └── ...
├── predictions/             # 预判记录
│   ├── daily/               # 逐日预判（每次出预判自动写入）
│   │   ├── 2026-07-13.json
│   │   └── ...
│   └── tracker.py           # record/review/report/list 工具
└── portfolio/               # 持仓状态
    └── current.json         # 当前持仓（cron fec5a277200a, 15:10 更新）
```

## 读取优先级

```
MCP 工具（mcp-eastmoney）可用  →  优先用 MCP
  ↓ MCP 不可用
缓存匹配今日日期  →  读缓存文件
  ↓ 缓存不存在或日期不匹配
curl 实时拉取
  ↓ curl 失败
web_search 或热股情绪代理法
```

## 何时读缓存

- **指数行情**：如果当天已有收盘快照，直接读 `market_snapshots/今天.json`
- **持仓监控**：读 `portfolio/current.json`（15:10 后包含当日收盘数据）
- **预判复盘**：读 `predictions/daily/今天.json` 进行对比

## 何时必须实时拉取

- 用户询问「现在」「当前」「实时」行情 → 必须实时 curl/MCP，不能用缓存
- 跨日对比（「昨天比今天」）→ 昨天数据读缓存，今天数据实时拉取
- 用户明确要求刷新 → 忽略缓存强制实时
