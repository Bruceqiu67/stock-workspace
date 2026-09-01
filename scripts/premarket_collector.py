#!/usr/bin/env python3
"""
盘前数据采集 — 交易日 08:15 自动运行 (no_agent cron)
拉取外围市场 + 隔夜美股，供盘前简报使用
"""
import json, os, subprocess, sys, re
from datetime import date, datetime
from pathlib import Path

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
    """腾讯行情"""
    url = f"http://qt.gtimg.cn/q={','.join(codes)}"
    try:
        r = subprocess.run(["curl", "-s", "--connect-timeout", "8", url],
                          capture_output=True, timeout=15)
        raw = r.stdout.decode("gbk", errors="replace")
    except Exception as e:
        errors.append(f"tencent fail: {e}")
        return {}

    result = {}
    for line in raw.strip().split(";"):
        if "~" not in line or len(line) < 40:
            continue
        parts = line.split("~")
        try:
            name = parts[1]
            result[name] = {
                "price": float(parts[3]),
                "change_pct": float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                "high": float(parts[33]) if len(parts) > 33 and parts[33] else 0,
                "low": float(parts[34]) if len(parts) > 34 and parts[34] else 0,
            }
        except (ValueError, IndexError):
            continue
    return result


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

    # 3. A股昨日收盘（如果有快照就读取）
    snap_path = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "market_snapshots" / f"{today}.json"
    if not snap_path.exists():
        # 取前一天
        from datetime import timedelta
        yesterday = (date.today() - timedelta(days=1)).isoformat()
        snap_path = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "market_snapshots" / f"{yesterday}.json"
    if snap_path.exists():
        with open(snap_path, "r", encoding="utf-8") as f:
            snap = json.load(f)
            data["a_share_yesterday"] = {
                "date": snap.get("date"),
                "indices": snap.get("indices"),
                "hot_total_amount_wan": snap.get("hot_total_amount_wan"),
            }

    # 4. 输出
    output = {
        "date": today,
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
        print(f"  🇨🇳 昨收 A股:")
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
