#!/usr/bin/env python3
"""
三框架预判回测脚本 (Backtest v2)

目标定义（唯一）：
  预判：Day N 收盘后，预判 Day N+1 收盘相对 Day N 收盘的涨跌。
  验证：Day N+1 收盘相对 Day N 收盘的涨跌。
  预测与验证使用同一公式，禁止再用“开盘到收盘”的日内方向冒充次日预判。

数据与口径：
  - 新浪 A股指数日K，抓取 FETCH_DAYS 个交易日，回测最近 BACKTEST_DAYS 天。
  - 加入 always-up / always-down / random / momentum 基准。
  - 加入交易成本与滑点，保存全部样本而非前 20 条。
  - 报告 95% Wilson 置信区间、滚动窗口方向准确率、训练/测试切分。

用法：
  python3 ~/.hermes/skills/finance/three-framework-analyst/scripts/backtest.py
输出：
  终端汇总 + ~/.hermes/data/backtest/results.json
"""
from __future__ import annotations

import json
import math
import random
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BACKTEST_VERSION = "2.0"
FETCH_DAYS = 250
BACKTEST_DAYS = 90
TRAIN_RATIO = 0.70
COST_PCT = 0.05       # 单边成本（%）
SLIPPAGE_PCT = 0.05   # 单边滑点（%）
ROUND_TRIP_COST_PCT = 2 * (COST_PCT + SLIPPAGE_PCT)
ROLLING_WINDOW = 20

DATA_DIR = Path.home() / ".hermes" / "data" / "backtest"


def fetch_kline(days: int = FETCH_DAYS):
    """拉取上证指数日K线（新浪）。"""
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol=sh000001&scale=240&ma=no&datalen={days}"
    )
    r = subprocess.run(
        ["curl", "-sL", "--connect-timeout", "10", url],
        capture_output=True, timeout=20, text=True,
    )
    data = json.loads(r.stdout)
    if not isinstance(data, list):
        raise RuntimeError("指数K线返回格式异常")
    return data


def predict_next(day, prev_close: float):
    """
    模拟 16:00 收盘后的预判逻辑。
    今日涨跌幅 = (今日收盘 - 前收) / 前收；预测目标为次日收盘相对今日收盘。
    """
    close = float(day["close"])
    high = float(day["high"])
    low = float(day["low"])
    change_pct = (close - prev_close) / prev_close * 100 if prev_close else 0.0
    amp = (high - low) / close * 100 if close else 0.0

    if change_pct > 1.5:
        direction = "震荡偏空" if amp > 3 else "偏多延续"
    elif change_pct > 0.5:
        direction = "偏多/震荡"
    elif change_pct > -0.5:
        direction = "窄幅震荡"
    elif change_pct > -1.5:
        direction = "偏空/震荡"
    else:
        direction = "惯性下跌" if amp > 3 else "偏空/弱反弹"

    vol = 1.5 if amp > 4 else (0.6 if amp < 1 else 1.0)
    support = round(low * (1 - 0.008 * vol), 1)
    resistance = round(high * (1 + 0.008 * vol), 1)
    if support > low * 0.99:
        support = round(low * 0.99, 1)
    if resistance < high * 1.01:
        resistance = round(high * 1.01, 1)

    return {
        "direction": direction,
        "support": support,
        "resistance": resistance,
        "amplitude": round(amp, 2),
        "today_change_pct": round(change_pct, 2),
    }


def direction_bucket(direction: str) -> str:
    if direction in ("偏多延续", "偏多/震荡", "弱反弹", "偏多"):
        return "up"
    if direction in ("惯性下跌", "偏空/震荡", "震荡偏空", "偏空/弱反弹", "偏空"):
        return "down"
    return "flat"


def score_prediction(pred, current_close: float, actual_next):
    """用“次日收盘相对今日收盘”的统一公式验证。"""
    actual_close = float(actual_next["close"])
    actual_change = (
        (actual_close - current_close) / current_close * 100
        if current_close else 0.0
    )
    bucket = direction_bucket(pred["direction"])
    actual_bucket = "up" if actual_change > 0 else ("down" if actual_change < 0 else "flat")

    if bucket == "flat":
        dir_score = 1.0 if abs(actual_change) < 1.0 else 0.0
    else:
        dir_score = 1.0 if bucket == actual_bucket else 0.0

    s, r = pred["support"], pred["resistance"]
    if s <= actual_close <= r:
        range_score = 1.0
    else:
        dev = (
            ((s - actual_close) if actual_close < s else (actual_close - r)) / (r - s)
            if r > s else 0.5
        )
        range_score = max(0.0, 1 - min(dev, 2))

    # 方向性策略收益：看多持有、看空做空、震荡空仓；扣除一次往返成本。
    if bucket == "up":
        gross_return = actual_change
        traded = True
    elif bucket == "down":
        gross_return = -actual_change
        traded = True
    else:
        gross_return = 0.0
        traded = False
    cost = ROUND_TRIP_COST_PCT if traded else 0.0
    net_return = gross_return - cost

    return {
        "direction_score": dir_score,
        "range_score": range_score,
        "overall": round((dir_score + range_score) / 2, 4),
        "actual_change": round(actual_change, 4),
        "actual_bucket": actual_bucket,
        "gross_return_pct": round(gross_return, 4),
        "cost_pct": cost,
        "net_return_pct": round(net_return, 4),
    }


def wilson_interval(successes: int, n: int, z: float = 1.96):
    if n <= 0:
        return [0.0, 0.0]
    phat = successes / n
    denom = 1 + z * z / n
    center = (phat + z * z / (2 * n)) / denom
    margin = z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n) / denom
    return [round(max(0.0, center - margin) * 100, 1),
            round(min(1.0, center + margin) * 100, 1)]


def summarize_direction(results):
    n = len(results)
    successes = sum(1 for r in results if r["score"]["direction_score"] == 1.0)
    accuracy = successes / n * 100 if n else 0.0
    return {
        "samples": n,
        "direction_correct": successes,
        "direction_accuracy_pct": round(accuracy, 1),
        "direction_accuracy_ci95_pct": wilson_interval(successes, n),
    }


def benchmark_results(rows):
    """四个基准：always-up / always-down / random / momentum。"""
    rng = random.Random(42)
    benchmarks = {
        "always_up": [],
        "always_down": [],
        "random": [],
        "momentum": [],
    }
    for row in rows:
        actual_change = row["score"]["actual_change"]
        actual_bucket = row["score"]["actual_bucket"]
        today_change = row["today_change"]
        today_bucket = "up" if today_change > 0 else ("down" if today_change < 0 else "flat")
        random_bucket = rng.choice(["up", "down", "flat"])

        def add(name, bucket):
            if bucket == "flat":
                score = 1.0 if actual_bucket == "flat" else 0.0
                gross = 0.0
                traded = False
            else:
                score = 1.0 if bucket == actual_bucket else 0.0
                gross = actual_change if bucket == "up" else -actual_change
                traded = True
            cost = ROUND_TRIP_COST_PCT if traded else 0.0
            benchmarks[name].append({
                "direction_score": score,
                "gross_return_pct": gross,
                "net_return_pct": gross - cost,
            })

        add("always_up", "up")
        add("always_down", "down")
        add("random", random_bucket)
        add("momentum", today_bucket)

    summary = {}
    for name, items in benchmarks.items():
        n = len(items)
        correct = sum(1 for item in items if item["direction_score"] == 1.0)
        total_net = sum(item["net_return_pct"] for item in items)
        summary[name] = {
            "samples": n,
            "direction_correct": correct,
            "direction_accuracy_pct": round(correct / n * 100, 1) if n else 0.0,
            "direction_accuracy_ci95_pct": wilson_interval(correct, n),
            "cumulative_net_return_pct": round(total_net, 2),
            "avg_net_return_pct": round(total_net / n, 4) if n else 0.0,
        }
    return summary


def rolling_accuracy(results, window: int = ROLLING_WINDOW):
    if len(results) < window:
        return {"window": window, "windows": 0, "values": []}
    values = []
    for i in range(window, len(results) + 1):
        chunk = results[i - window:i]
        correct = sum(1 for r in chunk if r["score"]["direction_score"] == 1.0)
        values.append(round(correct / window * 100, 1))
    return {
        "window": window,
        "windows": len(values),
        "min_pct": min(values) if values else None,
        "max_pct": max(values) if values else None,
        "mean_pct": round(sum(values) / len(values), 1) if values else None,
        "values": values,
    }


def main():
    print("📊 三框架预判回测 v" + BACKTEST_VERSION)
    data = fetch_kline(FETCH_DAYS)
    if len(data) < 2:
        print("❌ 数据不足", file=sys.stderr)
        return 2

    data = sorted(data, key=lambda item: item["day"])
    print(f"  数据范围: {data[0]['day']} ~ {data[-1]['day']}")
    print(f"  交易日数: {len(data)} | 回测窗口: {BACKTEST_DAYS} | 目标: 次日收盘相对前收")

    results = []
    start = max(1, len(data) - BACKTEST_DAYS)
    for i in range(start, len(data) - 1):
        prev_close = float(data[i - 1]["close"])
        current_close = float(data[i]["close"])
        pred = predict_next(data[i], prev_close)
        score = score_prediction(pred, current_close, data[i + 1])
        results.append({
            "date": data[i]["day"],
            "predict_date": data[i + 1]["day"],
            "prev_close": prev_close,
            "current_close": current_close,
            "today_change": pred["today_change_pct"],
            "prediction": pred,
            "tomorrow_change": score["actual_change"],
            "score": score,
        })

    total = len(results)
    if total == 0:
        print("❌ 没有可回测样本", file=sys.stderr)
        return 2

    overall = summarize_direction(results)
    train_n = int(total * TRAIN_RATIO)
    train = summarize_direction(results[:train_n])
    test = summarize_direction(results[train_n:])

    print(f"\n  回测天数: {total}")
    print(f"  方向准确: {overall['direction_correct']}/{total} "
          f"({overall['direction_accuracy_pct']}%, "
          f"95%CI {overall['direction_accuracy_ci95_pct'][0]}~{overall['direction_accuracy_ci95_pct'][1]}%)")
    print(f"  训练段({train['samples']}): {train['direction_accuracy_pct']}% | "
          f"测试段({test['samples']}): {test['direction_accuracy_pct']}%")

    benchmarks = benchmark_results(results)
    print("\n=== 基准对照（方向准确率 / 累计净收益）===")
    for name, stat in benchmarks.items():
        print(f"  {name:12s} {stat['direction_accuracy_pct']:5.1f}% "
              f"({stat['direction_correct']}/{stat['samples']})  "
              f"净收益 {stat['cumulative_net_return_pct']:+.2f}%")

    monthly = {}
    for r in results:
        monthly.setdefault(r["date"][:7], []).append(r)

    print("\n=== 月度统计 ===")
    for mo in sorted(monthly.keys()):
        chunk = monthly[mo]
        correct = sum(1 for r in chunk if r["score"]["direction_score"] == 1.0)
        avg = sum(r["score"]["overall"] for r in chunk) / len(chunk)
        net = sum(r["score"]["net_return_pct"] for r in chunk)
        print(f"  {mo}: {len(chunk)}天 方向{correct}/{len(chunk)}"
              f"({correct/len(chunk)*100:.0f}%)  综合{avg*100:.0f}/100  "
              f"净收益{net:+.2f}%")

    rolling = rolling_accuracy(results)
    if rolling["windows"]:
        print(f"\n  滚动{rolling['window']}日方向准确率: "
              f"min {rolling['min_pct']}% / mean {rolling['mean_pct']}% / max {rolling['max_pct']}%")

    up_days = [r for r in results if r["tomorrow_change"] > 0]
    down_days = [r for r in results if r["tomorrow_change"] < 0]
    bullish = [r for r in results if direction_bucket(r["prediction"]["direction"]) == "up"]
    bearish = [r for r in results if direction_bucket(r["prediction"]["direction"]) == "down"]

    print("\n=== 偏差分析 ===")
    if up_days:
        print(f"  实际上涨日: {len(up_days)}天  猜对: "
              f"{sum(1 for r in up_days if r['score']['direction_score']==1.0)}")
    if down_days:
        print(f"  实际下跌日: {len(down_days)}天  猜对: "
              f"{sum(1 for r in down_days if r['score']['direction_score']==1.0)}")
    if bullish:
        print(f"  偏多预判: {len(bullish)}次  正确率: "
              f"{sum(1 for r in bullish if r['score']['direction_score']==1.0)/len(bullish)*100:.0f}%")
    if bearish:
        print(f"  偏空预判: {len(bearish)}次  正确率: "
              f"{sum(1 for r in bearish if r['score']['direction_score']==1.0)/len(bearish)*100:.0f}%")

    output = {
        "backtest_version": BACKTEST_VERSION,
        "generated_at": datetime.now().isoformat(timespec="minutes"),
        "target_definition": "next_close_vs_prev_close",
        "data_source": "sina_sh000001",
        "fetch_days": FETCH_DAYS,
        "backtest_days": BACKTEST_DAYS,
        "train_ratio": TRAIN_RATIO,
        "cost_pct_per_side": COST_PCT,
        "slippage_pct_per_side": SLIPPAGE_PCT,
        "round_trip_cost_pct": ROUND_TRIP_COST_PCT,
        "date_range": {
            "data": f"{data[0]['day']}~{data[-1]['day']}",
            "backtest": f"{results[0]['date']}~{results[-1]['date']}",
        },
        "summary": {
            **overall,
            "train": train,
            "test": test,
            "rolling": rolling,
            "monthly": {
                mo: {
                    "days": len(monthly[mo]),
                    "direction_accuracy_pct": round(
                        sum(1 for r in monthly[mo] if r["score"]["direction_score"] == 1.0)
                        / len(monthly[mo]) * 100, 1
                    ),
                    "overall_avg_100": round(
                        sum(r["score"]["overall"] for r in monthly[mo])
                        / len(monthly[mo]) * 100, 1
                    ),
                    "cumulative_net_return_pct": round(
                        sum(r["score"]["net_return_pct"] for r in monthly[mo]), 2
                    ),
                }
                for mo in sorted(monthly.keys())
            },
        },
        "benchmarks": benchmarks,
        "results": results,  # 保存全部样本，不截断
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / "results.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已保存到 {path}（{len(results)} 条全部样本）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
