# 交易日自动化时间线

## 完整时间线

```text
08:15  📡 盘前数据采集 (no_agent)
        拉取: 美股三大指数 + 日经/恒生 + KOSPI + 昨收A股
        脚本: ~/.hermes/scripts/premarket_collector.py
        产出: ~/.hermes/data/premarket/{today}.json
        cron: 6b6bf3ad9773

08:30  📋 盘前简报 (LLM + mi-skill + serenity-perspective)
        读取: premarket/{today}.json + portfolio/current.json
        搜索: web_search 当日新闻
        产出: 盘前简报
        cron: c4f7d92df114

09:25  📊 晨间三框架报告 (LLM + 4 skills)
        读取: 所有缓存数据
        分析: 三框架综合研判
        产出: 完整市场日报 → 飞书PDF
        注意: 不做预判记录（预判统一在16:00完成）
        cron: 2ce447d6b75d

        ── [上午盘中] ──

11:30  🌤 午盘市场速览 (LLM + 4 skills)
        读取: 上午盘实时数据
        分析: 半日回顾 + 板块轮动 + 持仓上午表现
        产出: 午盘速览PDF → 飞书
        注意: 纯信息快报，不做预判记录
        cron: f6f7db852470

        ── [下午盘中] ──

15:00  收盘

15:05  📸 收盘数据快照 (no_agent)
        脚本: ~/.hermes/scripts/market_snapshot.py
        产出: ~/.hermes/data/market_snapshots/{today}.json
        cron: 7c70d6eea4fa

15:10  📈 持仓日报 (no_agent)
        脚本: ~/.hermes/scripts/portfolio_monitor.py
        产出: ~/.hermes/data/portfolio/current.json
        cron: fec5a277200a

15:15  🎯 候选池扫描 (no_agent)
        脚本: ~/.hermes/scripts/candidate_scanner.py
        产出: ~/.hermes/data/candidates/{today}.json
        cron: 1ee1017ed106

16:00  🔄 收盘复盘报告 (LLM + 4 skills)
        复盘: tracker.py review --date {yesterday} → 复盘昨日 recap 预判
        分析: 今日实际走势 + 持仓表现 + 方向轮动
        产出: 收盘复盘PDF → 飞书
        预判: tracker.py record --type recap  ← 预判明日
        cron: 54e63b84a4b4

        ── [以上为每日固定任务，以下为月度任务] ──

月首  🗂️ 候选池月度刷新 (LLM + 3 skills)
        检查: refresh_state.json，距上次 >=28 天则执行
        分析: MCP 板块排行 + 三框架选方向
        产出: 更新 ~/.hermes/scripts/candidate_scanner.py 的 CANDIDATES 字典
        状态: ~/.hermes/data/candidates/refresh_state.json
        cron: f20f94900f74 (schedule: 0 16 1-7 * 1-5)
        注意: 未到刷新周期时输出 [SILENT] 不打扰
```

## 预判体系

| 时间 | 类型 | 预判内容 | 复盘时机 |
|------|------|---------|---------|
| 16:00 | `recap` | 明日走势 | 下一交易日 16:00 复盘 |

> 规则：只有 16:00 复盘做预判。09:25 晨报是纯信息报告，不做预判记录。

预判文件存储路径：`~/.hermes/data/predictions/daily/{today}.json`

每条预判记录格式：
```json
{
  "id": "pred_20260713_001",
  "date": "2026-07-13",
  "type": "recap",              // recap=明日预判（唯一类型）
  "prediction": { ... },
  "actual": null,
  "accuracy": null,
  "provider": "deepseek-v4-flash"
}
```

## 时间线规则

| 规则 | 说明 |
|------|------|
| **数据不可覆盖** | 15:05→15:10→15:15 顺序执行，互不覆盖 |
| **周六日不执行** | 所有 cron 均为 1-5（工作日） |
| **节假日** | 需手动暂停（hermes cron pause） |
| **no_agent 模式** | 脚本 stdout 直接推送，零 token 消耗 |
| **失败处理** | 单个 cron 失败不影响其他 cron 运行 |

## 各 cron 的状态文件

| Cron | 数据文件 | 用途 |
|------|---------|------|
| 盘前采集 | premarket/{today}.json | 盘前简报读取 |
| 收盘快照 | market_snapshots/{today}.json | 大盘分析/复盘读取 |
| 持仓日报 | portfolio/current.json | 持仓诊断/复盘读取 |
| 候选池扫描 | candidates/{today}.json | 方向选股/复盘读取 |

## 数据覆盖规则

- `portfolio/current.json` 每次运行被覆盖（只保留最近一次）
- 其他文件按日期存储，历史数据保留
