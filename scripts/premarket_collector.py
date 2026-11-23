#!/usr/bin/env python3
"""
盘前数据采集 — 交易日 08:15 自动运行 (no_agent cron)
拉取外围市场 + 隔夜美股，供盘前简报使用
"""
import json, os, subprocess, sys, re
from datetime import date, datetime
from pathlib import Path

try:
    from trading_calendar import (
        freshness as calendar_freshness,
        is_trading_day as calendar_is_trading_day,
        previous_trading_day,
    )
except ImportError:  # 日历模块缺失时退回日历昨天，并在输出中标注
    calendar_freshness = None
    calendar_is_trading_day = None
    previous_trading_day = None

try:
    from market_data_adapter import fetch_tencent_quotes
except ImportError:
    fetch_tencent_quotes = None

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "premarket"
DATA_DIR.mkdir(parents=True, exist_ok=True)

errors = []
data = {
    "us_stocks": {},
    "asia": {},
    "a_share_yesterday": {},
    "hot_sectors": {},
}


def fetch_tencent(codes):
    """腾讯行情（解析统一走 market_data_adapter）。"""
    if fetch_tencent_quotes is None:
        return {}
    quotes = fetch_tencent_quotes(codes, min_fields=40)
    return {
        quote.get("name", ""): {
            "price": quote.get("price", 0),
            "change_pct": quote.get("change_pct", 0),
            "high": quote.get("high", 0),
            "low": quote.get("low", 0),
        }
        for quote in quotes.values()
        if quote.get("name")
    }


def fetch_sina(codes, prefix="gb_"):
    """新浪行情（美股/国际指数）"""
    urls = "+".join(f"{prefix}{c}" for c in codes)
    url = f"https://hq.sinajs.cn/list={urls}"
    try:
        r = subprocess.run(
            ["curl", "-s", "--connect-timeout", "8",
             "-H", "Referer: https://finance.sina.com.cn", url],
            capture_output=True, timeout=15
        )
        raw = r.stdout.decode("gbk", errors="replace")
    except Exception as e:
        errors.append(f"sina fail: {e}")
        return {}

    result = {}
    for line in raw.strip().split("\n"):
        m = re.search(r'hq_str_\w+="([^"]+)"', line)
        if not m:
            continue
        fields = m.group(1).split(",")
        if len(fields) < 3:
            continue
        name = fields[0]
        try:
            result[name] = {
                "price": float(fields[1]) if fields[1] else 0,
                "change_pct": float(fields[2]) if fields[2] else 0,
                "timestamp": fields[3] if len(fields) > 3 else "",
            }
        except (ValueError, IndexError):
            continue
    return result


def fetch_sina_int(codes):
    """新浪国际指数"""
    url = f"https://hq.sinajs.cn/list={','.join('int_'+c for c in codes)}"
    try:
        r = subprocess.run(
            ["curl", "-s", "--connect-timeout", "8",
             "-H", "Referer: https://finance.sina.com.cn", url],
            capture_output=True, timeout=15
        )
        raw = r.stdout.decode("gbk", errors="replace")
    except Exception as e:
        errors.append(f"sina_int fail: {e}")
        return {}

    result = {}
    for line in raw.strip().split("\n"):
        m = re.search(r'hq_str_\w+="([^"]+)"', line)
        if not m:
            continue
        fields = m.group(1).split(",")
        if len(fields) < 2:
            continue
        name = fields[0]
        try:
            result[name] = {
                "price": float(fields[1]) if fields[1] else 0,
                "change": float(fields[2]) if len(fields) > 2 and fields[2] else 0,
                "change_pct": float(fields[3]) if len(fields) > 3 and fields[3] else 0,
            }
        except (ValueError, IndexError):
            continue
    return result


def main():
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")

    # 1. 美股 (腾讯 + 新浪双源)
    data["us_stocks"] = fetch_tencent(["usDJI", "usIXIC", "usINX"])

    # 2. 亚太 (新浪国际指数)
    data["asia"] = fetch_sina_int(["nikkei", "kospi", "hangseng", "hscei"])

    # 3. A股上一交易日收盘（如果有快照就读取）
    # I3：周一盘前必须找上一交易日（通常是周五），不能用“日历昨天”。
    snapshots_dir = (
        Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
        / "data" / "market_snapshots"
    )
    previous_day = (
        previous_trading_day(today) if previous_trading_day else None
    )
    if previous_day is None:
        from datetime import timedelta
        previous_day = (date.today() - timedelta(days=1)).isoformat()

    today_is_trading = (
        calendar_is_trading_day(today) if calendar_is_trading_day else True
    )
    candidate_paths = [
        snapshots_dir / f"{previous_day}.json",    # 盘前默认读上一交易日
    ]
    if today_is_trading:
        candidate_paths.insert(0, snapshots_dir / f"{today}.json")
    snap_path = next((p for p in candidate_paths if p.exists()), None)
    if snap_path is not None:
        with open(snap_path, "r", encoding="utf-8") as f:
            snap = json.load(f)
        source_date = snap.get("date") or snap_path.stem
        fresh = (
            calendar_freshness(source_date, today, allow_previous=True)
            if calendar_freshness else
            {"status": "unknown", "is_stale": source_date != today,
             "data_age_days": None, "data_age_trading_days": None}
        )
        data["a_share_yesterday"] = {
            "date": source_date,
            "expected_date": previous_day,
            "indices": snap.get("indices"),
            "hot_total_amount_wan": snap.get("hot_total_amount_wan"),
            "breadth": snap.get("breadth"),
            "freshness": fresh,
        }
    else:
        data["a_share_yesterday"] = {
            "date": None,
            "expected_date": previous_day,
            "indices": None,
            "freshness": {
                "status": "missing",
                "is_stale": True,
                "data_age_days": None,
                "data_age_trading_days": None,
            },
        }

    # 4. 输出
    output = {
        "date": today,
        "target_date": today,
        "as_of": data["a_share_yesterday"].get("date") or previous_day,
        "data_freshness": data["a_share_yesterday"].get("freshness"),
        "captured_at": now,
        "data": data,
        "errors": errors if errors else None,
    }

    out_path = DATA_DIR / f"{today}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

    # 打印摘要
    us = data["us_stocks"]
    asia = data["asia"]

    print(f"🌏 盘前数据 {today} {now}")
    print()

    if us:
        for name in ["道琼斯", "纳斯达克", "标普500"]:
            if name in us:
                s = us[name]
                print(f"  🇺🇸 {name}: {s['price']:.2f} ({s['change_pct']:+.2f}%)")
        print()

    if asia:
        for name in ["日经指数", "韩国KOSPI", "恒生指数", "恒生国企指数"]:
            if name in asia:
                s = asia[name]
                chg = s.get("change_pct", s.get("change", 0))
                print(f"  🌏 {name}: {s['price']:.2f} ({chg:+.2f}%)")
        print()

    ay = data["a_share_yesterday"]
    if ay and ay.get("indices"):
        idx = ay["indices"]
        sh = idx.get("上证指数", {})
        cy = idx.get("创业板指", {})
        kc = idx.get("科创50", {})
        fresh = ay.get("freshness") or {}
        print(f"  🇨🇳 昨收 A股 (数据日 {ay.get('date') or '?'}"
              f"{'，状态 ' + str(fresh.get('status')) if fresh else ''}):")
        print(f"     上证: {sh.get('price','-'):>8} ({sh.get('change_pct',0):+.2f}%)")
        print(f"     创业板: {cy.get('price','-'):>6} ({cy.get('change_pct',0):+.2f}%)")
        print(f"     科创50: {kc.get('price','-'):>6} ({kc.get('change_pct',0):+.2f}%)")
        amt = ay.get("hot_total_amount_wan", 0)
        if amt:
            print(f"     热股成交: {amt/10000:.1f}亿")

    if errors:
        print(f"\n  ⚠️  {len(errors)} 个采集错误", file=sys.stderr)

    if not us and not asia:
        print("  ❌ 数据全部采集失败", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
