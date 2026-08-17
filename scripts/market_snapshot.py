#!/usr/bin/env python3
"""
收盘数据快照采集器 — 交易日 15:05 自动运行 (no_agent cron)
采集指数、热股、板块排行，存为 JSON 供分析时直接读取。
"""
import json, os, subprocess, sys
from datetime import date, datetime
from pathlib import Path

DATA_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "market_snapshots"
DATA_DIR.mkdir(parents=True, exist_ok=True)

TODAY = date.today().isoformat()
NOW = datetime.now().isoformat(timespec="minutes")

errors = []
indices = {}
hot_stocks = {}


def fetch_tencent(codes, label="data"):
    """调用 qt.gtimg.cn 获取行情，返回 dict {name: {fields}}"""
    url = f"http://qt.gtimg.cn/q={','.join(codes)}"
    try:
        result = subprocess.run(
            ["curl", "-s", "--connect-timeout", "8", url],
            capture_output=True, timeout=15
        )
        raw = result.stdout.decode("gbk", errors="replace")
    except Exception as e:
        errors.append(f"{label}: curl failed - {e}")
        return {}

    data = {}
    for line in raw.strip().split(";"):
        if "~" not in line or len(line) < 30:
            continue
        parts = line.split("~")
        name = parts[1]
        try:
            data[name] = {
                "code": parts[2],
                "price": float(parts[3]),
                "change_pct": float(parts[32]) if len(parts) > 32 and parts[32] else 0,
                "high": float(parts[33]) if len(parts) > 33 and parts[33] else 0,
                "low": float(parts[34]) if len(parts) > 34 and parts[34] else 0,
                "amount_wan": float(parts[37]) if len(parts) > 37 and parts[37] else 0,
                "turnover_rate": float(parts[38]) if len(parts) > 38 and parts[38] else 0,
                "amplitude": float(parts[43]) if len(parts) > 43 and parts[43] else 0,
            }
        except (ValueError, IndexError) as e:
            errors.append(f"{label}: parse error for {name} - {e}")
    return data


def main():
    # ----- 1. 指数数据 -----
    idx_codes = [
        "sh000001",    # 上证
        "sz399001",    # 深证
        "sz399006",    # 创业板
        "sh000688",    # 科创50
        "sh000300",    # 沪深300
        "sz399303",    # 国证2000
        "sz399852",    # 中证1000
    ]
    indices.update(fetch_tencent(idx_codes, "indices"))

    # ----- 2. 热股情绪代理 -----
    hot_codes = [
        "sz002371",    # 北方华创 - 半导体设备
        "sh688981",    # 中芯国际 - 晶圆代工
        "sz300502",    # 新易盛 - 光模块
        "sz002050",    # 三花智控 - 机器人
        "sz002472",    # 双环传动 - 减速器
        "sh600276",    # 恒瑞医药 - 创新药
        "sz000977",    # 浪潮信息 - AI服务器
        "sz300059",    # 东方财富 - 证券
    ]
    hot_stocks.update(fetch_tencent(hot_codes, "hot_stocks"))

    # ----- 3. 成交量合计 -----
    total_amount = sum(
        s.get("amount_wan", 0) for s in hot_stocks.values()
    )

    # ----- 4. 构建输出 -----
    snapshot = {
        "date": TODAY,
        "captured_at": NOW,
        "indices": indices,
        "hot_stocks": hot_stocks,
        "hot_total_amount_wan": total_amount,
        "errors": errors if errors else None,
    }

    # 写入文件
    out_path = DATA_DIR / f"{TODAY}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)

    # 输出摘要（no_agent cron 模式下 stdout 会推送给用户）
    sh = indices.get("上证指数", {})
    sz = indices.get("深证成指", {})
    cy = indices.get("创业板指", {})
    kc = indices.get("科创50", {})

    print(f"📊 收盘快照 {TODAY}")
    print(f"   上证: {sh.get('price','-'):>8} ({sh.get('change_pct',0):+.2f}%)")
    print(f"   深成: {sz.get('price','-'):>8} ({sz.get('change_pct',0):+.2f}%)")
    print(f"   创业板: {cy.get('price','-'):>6} ({cy.get('change_pct',0):+.2f}%)")
    print(f"   科创50: {kc.get('price','-'):>6} ({kc.get('change_pct',0):+.2f}%)")
    print(f"   热股成交额合计: {total_amount/10000:.1f}亿")

    # 领涨/领跌热股
    sorted_stocks = sorted(hot_stocks.items(), key=lambda x: x[1].get("change_pct", 0))
    if sorted_stocks:
        worst = sorted_stocks[0]
        best = sorted_stocks[-1]
        print(f"   领涨: {best[0]} {best[1].get('change_pct',0):+.2f}%")
        print(f"   领跌: {worst[0]} {worst[1].get('change_pct',0):+.2f}%")

    if errors:
        print(f"   ⚠️  {len(errors)} 个采集错误", file=sys.stderr)

    # 退出码：有错误但数据不为空 → 0（成功但有瑕疵），完全空 → 1
    if not indices and not hot_stocks:
        print("   ❌ 全部数据采集失败", file=sys.stderr)
        sys.exit(1)

    sys.exit(0)


if __name__ == "__main__":
    main()
