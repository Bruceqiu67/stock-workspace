#!/usr/bin/env python3
"""
统一持仓监控脚本 — no_agent cron 模式
每30分钟/收盘后运行，输出持仓快照
"""
import json, os, subprocess, sys
from datetime import date, datetime
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "portfolio"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── 持仓配置 ──────────────────────────────────
# 格式: code: {name, cost, shares, stop_loss}
# ⚠️ 示例数据，使用前请替换为你的真实持仓
PORTFOLIO = {
    "sz000001": {"name": "示例股票A", "cost": 10.00, "shares": 1000, "stop_loss": 9.00},
    "sh600000": {"name": "示例股票B", "cost": 20.00, "shares": 500, "stop_loss": 18.00},
}

TOTAL_POSITION_PCT = 40  # 总仓位占比
CASH_PCT = 100 - TOTAL_POSITION_PCT


def fetch_prices(codes):
    """批量获取行情"""
    url = f"http://qt.gtimg.cn/q={','.join(codes)}"
    try:
        result = subprocess.run(
            ["curl", "-s", "--connect-timeout", "8", url],
            capture_output=True, timeout=15
        )
        raw = result.stdout.decode("gbk", errors="replace")
    except Exception as e:
        print(f"❌ 行情获取失败: {e}", file=sys.stderr)
        return {}

    data = {}
    for line in raw.strip().split(";"):
        if "~" not in line or len(line) < 40:
            continue
        parts = line.split("~")
        try:
            raw_prefix = parts[0]
            if "sh" in raw_prefix:
                mkt = "sh"
            elif "sz" in raw_prefix:
                mkt = "sz"
            elif "hk" in raw_prefix:
                mkt = "hk"
            else:
                mkt = "sz"
            code = parts[2]
            code_full = f"{mkt}{code}"
            name = parts[1]
            data[code_full] = {
                "name": name,
                "price": float(parts[3]),
                "change_pct": float(parts[32]),
                "high": float(parts[33]),
                "low": float(parts[34]),
            }
        except (ValueError, IndexError):
            continue
    return data


def main():
    codes = list(PORTFOLIO.keys())
    quotes = fetch_prices(codes)

    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")

    total_cost = sum(s["cost"] * s["shares"] for s in PORTFOLIO.values())
    total_market = 0
    rows = []

    print(f"📊 持仓监控 · {today} {now}")
    print(f"  总仓位: {TOTAL_POSITION_PCT}% / 现金: {CASH_PCT}%")
    print()

    alarm_count = 0
    for code, info in PORTFOLIO.items():
        q = quotes.get(code)
        name = info["name"]
        cost_p = info["cost"]
        shares = info["shares"]
        stop = info["stop_loss"]

        if q:
            cur = q["price"]
            chg = q["change_pct"]
            pnl_pct = (cur - cost_p) / cost_p * 100
            pnl_yuan = (cur - cost_p) * shares
            market_val = cur * shares
            total_market += market_val

            # 止损判断
            if cur < stop:
                alarm = " 🚨 破止损！"
                alarm_count += 1
            elif pnl_pct < -10:
                alarm = " 🔴 浮亏>10%"
                alarm_count += 1
            elif pnl_pct < -5:
                alarm = " ⚠️ 浮亏>5%"
            elif pnl_pct > 15:
                alarm = " ✅ 浮盈>15%"
            elif pnl_pct > 0:
                alarm = " ✅ 盈利"
            else:
                alarm = " 🔸 微亏"
        else:
            cur = chg = pnl_pct = pnl_yuan = market_val = 0
            alarm = " ⚠️ 数据缺失"

        cost_total = cost_p * shares
        rows.append({
            "name": name, "code": code,
            "cost": cost_p, "cur": cur,
            "chg": chg, "pnl_pct": pnl_pct,
            "pnl_yuan": pnl_yuan,
            "shares": shares,
            "market_val": market_val,
            "cost_total": cost_total,
            "stop": stop, "alarm": alarm,
        })

    # 输出表格
    header = f"{'股票':10s} {'现价':>6s} {'成本':>6s} {'今日':>6s} {'浮亏%':>7s} {'浮亏¥':>9s} {'止损':>6s} 状态"
    print(header)
    print("-" * len(header))

    total_pnl = 0
    for r in rows:
        pnl_pct_str = f"{r['pnl_pct']:+.1f}%" if r['cur'] else "N/A"
        chg_str = f"{r['chg']:+.2f}%" if r['cur'] else "N/A"
        cur_str = f"{r['cur']:.2f}" if r['cur'] else "N/A"
        total_pnl += r['pnl_yuan'] if r['cur'] else 0
        print(f"{r['name']:8s} {cur_str:>6} {r['cost']:>6.2f} {chg_str:>6} {pnl_pct_str:>7} {r['pnl_yuan']:>+8.0f} {r['stop']:>6.2f}{r['alarm']}")

    # 汇总
    print()
    print(f"  总投入: ¥{total_cost:,.0f}")
    print(f"  总市值: ¥{total_market:,.0f}")
    print(f"  总浮盈: ¥{total_pnl:+,.0f} ({total_pnl/total_cost*100:+.1f}%)")
    print(f"  仓位占比: {TOTAL_POSITION_PCT}%")

    if alarm_count > 0:
        print(f"\n  ⚠️  {alarm_count} 个标的需关注！")

    # 保存状态
    state = {
        "date": today,
        "time": now,
        "total_cost": total_cost,
        "total_market": total_market,
        "total_pnl": total_pnl,
        "total_pnl_pct": round(total_pnl / total_cost * 100, 2),
        "stocks": [
            {"code": r["code"], "name": r["name"], "price": r["cur"],
             "cost": r["cost"], "pnl_pct": round(r["pnl_pct"], 2),
             "pnl_yuan": round(r["pnl_yuan"]), "market_val": round(r["market_val"])}
            for r in rows
        ],
    }
    with open(DATA_DIR / "current.json", "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)

    sys.exit(0)  # Always exit clean — alarms are output, not errors


if __name__ == "__main__":
    main()
