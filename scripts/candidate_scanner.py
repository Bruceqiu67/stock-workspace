#!/usr/bin/env python3
"""
每日候选池生成 — 交易日 15:15 自动运行 (no_agent cron)
对预定义候选列表批量查行情 → 三道硬过滤 → 输出候选池
"""
import json, os, subprocess, sys, re
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "candidates"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── 预定义候选列表（覆盖 9 个方向，共 42 只） ──
# 2026-07-28 更新：半导体→半导体/封测/材料(加材料股), AI算力→AI算力/信创(加AI应用),
# 锂电/消费电子/证券各精简1-2只
CANDIDATES = {
    "半导体/封测/材料": [
        "sz002156",  # 通富微电(71.00) 封测龙头·位置13%低位
        "sh600584",  # 长电科技(80.91) 封测
        "sz002185",  # 华天科技(18.65) 封测
        "sh688126",  # 沪硅产业(26.08) 硅片 — 材料新增
        "sz300054",  # 鼎龙股份(75.79) CMP抛光垫 — 材料新增
    ],
    "AI算力/信创": [
        "sz000977",  # 浪潮信息(82.11)
        "sh601138",  # 工业富联(59.99)
        "sz000938",  # 紫光股份(40.71)
        "sh600536",  # 中国软件(31.46) 信创
        "sz002396",  # 星网锐捷(29.65)
        "sz300624",  # 万兴科技(47.23) AI应用 — 新增
    ],
    "机器人": [
        "sz300124",  # 汇川技术(59.01)
        "sz002472",  # 双环传动(36.36)
        "sz300660",  # 江苏雷利(25.46)
        "sz002050",  # 三花智控(36.41)
        "sz300911",  # 亿田智能(18.46)
    ],
    "功率半导体": [
        "sh688396",  # 华润微(59.15)
        "sh600460",  # 士兰微(31.77)
        "sh688187",  # 时代电气(45.55)
        "sz300623",  # 捷捷微电(25.81)
    ],
    "电网设备": [
        "sz000400",  # 许继电气
        "sh600406",  # 国电南瑞
        "sz300286",  # 安科瑞
        "sh600312",  # 平高电气
        "sh601567",  # 三星医疗
        "sz300693",  # 盛弘股份
    ],
    "锂电/新能源": [
        "sz300014",  # 亿纬锂能
        "sz002709",  # 天赐材料
        "sz300769",  # 德方纳米
    ],
    "消费电子": [
        "sz002475",  # 立讯精密
        "sz002241",  # 歌尔股份
        "sz300433",  # 蓝思科技
    ],
    "创新药": [
        "sh600276",  # 恒瑞医药
        "sh688180",  # 君实生物
        "sz300122",  # 智飞生物
        "sh600196",  # 复星医药
    ],
    "证券": [
        "sz300059",  # 东方财富
        "sh600030",  # 中信证券
        "sh601688",  # 华泰证券
    ],
}

# 方向的中文名到缩写映射（用于去重）
SECTOR_ALIAS = {
    "半导体/封测/材料": "半导体",
    "AI算力/信创": "AI算力",
    "机器人": "机器人",
    "功率半导体": "功率半",
    "电网设备": "电网",
    "锂电/新能源": "锂电",
    "消费电子": "消费电子",
    "创新药": "创新药",
    "证券": "证券",
}

# 去重（如新易盛同时出现在AI算力和光模块两个方向）
CODE_TO_SECTORS = {}
for sector, codes in CANDIDATES.items():
    short = SECTOR_ALIAS.get(sector, sector)
    for code in codes:
        CODE_TO_SECTORS.setdefault(code, []).append(short)


def fetch_all(codes):
    """批量获取腾讯行情"""
    results = {}
    # 分批次，每批最多 20 只
    batch_size = 20
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        url = f"http://qt.gtimg.cn/q={','.join(batch)}"
        try:
            r = subprocess.run(
                ["curl", "-s", "--connect-timeout", "8", url],
                capture_output=True, timeout=15
            )
            raw = r.stdout.decode("gbk", errors="replace")
        except Exception as e:
            print(f"⚠️  批次 {i//batch_size+1} 失败: {e}", file=sys.stderr)
            continue

        for line in raw.strip().split(";"):
            if "~" not in line or len(line) < 50:
                continue
            parts = line.split("~")
            try:
                raw_prefix = parts[0]
                mkt = "sh" if "sh" in raw_prefix else "sz"
                code = parts[2]
                code_full = f"{mkt}{code}"
                price = float(parts[3])
                chg_pct = float(parts[32]) if len(parts) > 32 and parts[32] else 0
                high_52w = float(parts[47]) if len(parts) > 47 and parts[47] else 0
                low_52w = float(parts[48]) if len(parts) > 48 and parts[48] else 0
                turnover = float(parts[38]) if len(parts) > 38 and parts[38] else 0
                amount_wan = float(parts[37]) if len(parts) > 37 and parts[37] else 0

                results[code_full] = {
                    "code": code,
                    "name": parts[1],
                    "price": price,
                    "change_pct": chg_pct,
                    "high_52w": high_52w,
                    "low_52w": low_52w,
                    "turnover": turnover,
                    "amount_wan": amount_wan,
                }
            except (ValueError, IndexError):
                continue
    return results


def main():
    today = date.today().isoformat()

    # 收集所有不重复的代码
    all_codes = list(dict.fromkeys(
        code for codes in CANDIDATES.values() for code in codes
    ))
    print(f"📊 候选池扫描 · {today}")
    print(f"   总候选: {len(all_codes)} 只, {len(CANDIDATES)} 个方向")

    quotes = fetch_all(all_codes)
    print(f"   行情获取: {len(quotes)}/{len(all_codes)} 成功")

    # 运行硬过滤
    passed = []
    filtered_out = {"price": [], "position": [], "no_data": []}

    for code_full, q in quotes.items():
        price = q["price"]
        high_52w = q["high_52w"]
        low_52w = q["low_52w"]

        # 过滤门1: 绝对价格 < 100元
        if price >= 100:
            filtered_out["price"].append(f"{q['name']}({price:.0f}元)")
            continue

        # 过滤门2: 52周相对位置 < 70%
        if high_52w > 0 and low_52w > 0 and high_52w > low_52w:
            rel_pos = (price - low_52w) / (high_52w - low_52w) * 100
        else:
            rel_pos = None

        if rel_pos is not None and rel_pos >= 70:
            filtered_out["position"].append(f"{q['name']}({rel_pos:.0f}%)")
            continue

        # 通过过滤
        sectors = CODE_TO_SECTORS.get(code_full, ["未知"])
        passed.append({
            "code": code_full,
            "name": q["name"],
            "price": price,
            "change_pct": q["change_pct"],
            "rel_position_52w": round(rel_pos, 1) if rel_pos is not None else None,
            "turnover": q["turnover"],
            "amount_wan": q["amount_wan"],
            "sectors": sectors,
            "primary_sector": sectors[0] if sectors else "未知",
        })

    # 按方向分组输出
    print(f"\n   通过过滤: {len(passed)} 只")
    print(f"   价格超标: {len(filtered_out['price'])} 只")
    print(f"   位置过高: {len(filtered_out['position'])} 只")
    print(f"   数据缺失: {len(filtered_out['no_data'])} 只")

    # 按方向分组
    by_sector = {}
    for c in passed:
        for s in c["sectors"]:
            by_sector.setdefault(s, []).append(c)

    for sector in sorted(by_sector.keys()):
        stocks = by_sector[sector]
        print(f"\n   {sector} ({len(stocks)} 只):")
        for s in sorted(stocks, key=lambda x: x["rel_position_52w"] or 999)[:5]:
            rp = f"位置{s['rel_position_52w']:.0f}%" if s["rel_position_52w"] is not None else "位置?"
            print(f"     {s['name']:8s} ¥{s['price']:<6.2f} {s['change_pct']:+.2f}% {rp}")

    # 保存
    output = {
        "date": today,
        "captured_at": datetime.now().isoformat(timespec="minutes"),
        "total": len(all_codes),
        "fetched": len(quotes),
        "passed": len(passed),
        "filtered": {
            "price_high": filtered_out["price"],
            "position_high": filtered_out["position"],
            "no_data": filtered_out["no_data"],
        },
        "candidates": passed,
        "by_sector": {s: [c["code"] for c in stocks] for s, stocks in by_sector.items()},
    }

    out_path = DATA_DIR / f"{today}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 候选池已保存: {out_path}")

    # 退出码：没合格候选 = 异常
    if not passed:
        print("⚠️  今日无合格候选", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()
