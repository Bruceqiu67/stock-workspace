#!/usr/bin/env python3
"""
前瞻收益验证 (Forward Return Validation)

把每日候选池评分快照与未来 1/5/20 日收益关联，回答：
  - 高分是否对应更高未来收益？（rank IC）
  - Top-N 是否跑赢候选池中位数？（命中率）
  - 分数分桶后收益是否单调？（分桶有效性）
  - 不同市场状态（regime）下表现是否不同？

严格防未来函数：
  - 评分只读取 candidates/{date}.json 中已落盘的历史分数；
  - 未来收益只读取 date 之后第 N 个交易日的收盘价；
  - 不使用当前时点的价格回填历史。

用法：
  python3 scripts/forward_validation.py --horizons 1,5,20 --top-n 10
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

try:
    from market_data_adapter import fetch_tencent_klines
except ImportError:
    fetch_tencent_klines = None

VALIDATION_VERSION = "1.0"
HERMES_HOME = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
CANDIDATES_DIR = HERMES_HOME / "data" / "candidates"
DEFAULT_OUTPUT = HERMES_HOME / "data" / "validation" / "forward_validation.json"


def fetch_kline(code_full: str, limit: int = 250) -> List[Dict[str, Any]]:
    """腾讯前复权日K；解析统一走 market_data_adapter，失败返回空列表。"""
    if fetch_tencent_klines is None:
        return []
    rows = fetch_tencent_klines(code_full, limit=limit, period="day")
    return [
        {"date": row["date"], "close": row["close"]}
        for row in rows
        if row.get("date") and row.get("close")
    ]


def load_snapshots(directory: Path = CANDIDATES_DIR) -> List[Tuple[str, Dict[str, Any]]]:
    snapshots = []
    if not directory.exists():
        return snapshots
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith("refresh_state"):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            print(f"⚠️  跳过损坏的候选快照: {path}", file=sys.stderr)
            continue
        if not isinstance(data, dict) or not data.get("candidates"):
            continue
        date = str(data.get("as_of") or data.get("date") or path.stem)[:10]
        snapshots.append((date, data))
    return snapshots


def build_price_index(codes: List[str]) -> Dict[str, List[Dict[str, Any]]]:
    price_map: Dict[str, List[Dict[str, Any]]] = {}
    for code in sorted(set(codes)):
        rows = fetch_kline(code)
        if rows:
            price_map[code] = rows
    return price_map


def forward_return(
    rows: List[Dict[str, Any]],
    snapshot_date: str,
    horizon: int,
) -> Optional[float]:
    """从 snapshot_date 之后的第 horizon 个交易日收盘相对 snapshot 日收盘的收益。"""
    if not rows or horizon <= 0:
        return None
    dates = [row["date"] for row in rows]
    try:
        base_index = dates.index(snapshot_date)
    except ValueError:
        # 快照日停牌/缺失时，用之后第一个交易日作为基准，仍不使用未来信息之外的日期
        base_index = next((i for i, d in enumerate(dates) if d >= snapshot_date), None)
        if base_index is None:
            return None
    target_index = base_index + horizon
    if target_index >= len(rows):
        return None
    base_close = rows[base_index]["close"]
    target_close = rows[target_index]["close"]
    if not base_close or base_close <= 0:
        return None
    return (target_close - base_close) / base_close * 100


def _rank(values: List[float]) -> List[float]:
    """平均秩（处理并列）。"""
    sorted_pairs = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(sorted_pairs):
        j = i
        while j + 1 < len(sorted_pairs) and sorted_pairs[j + 1][1] == sorted_pairs[i][1]:
            j += 1
        average_rank = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[sorted_pairs[k][0]] = average_rank
        i = j + 1
    return ranks


def spearman_ic(scores: List[float], returns: List[float]) -> Optional[float]:
    if len(scores) != len(returns) or len(scores) < 3:
        return None
    rank_scores = _rank(scores)
    rank_returns = _rank(returns)
    n = len(scores)
    mean_s = sum(rank_scores) / n
    mean_r = sum(rank_returns) / n
    cov = sum((s - mean_s) * (r - mean_r) for s, r in zip(rank_scores, rank_returns))
    var_s = sum((s - mean_s) ** 2 for s in rank_scores)
    var_r = sum((r - mean_r) ** 2 for r in rank_returns)
    if var_s <= 0 or var_r <= 0:
        return None
    return cov / math.sqrt(var_s * var_r)


def _score_of(candidate: Dict[str, Any]) -> Optional[float]:
    value = candidate.get("canonical_score")
    if value is None:
        value = candidate.get("combined_score")
        if value is not None:
            value = float(value) * 20.0
    return float(value) if value is not None else None


def bucket_returns(pairs: List[Tuple[float, float]], buckets: int = 5) -> List[Optional[float]]:
    """按分数升序分桶，返回每桶平均未来收益（低分→高分）。"""
    if len(pairs) < buckets:
        return [None] * buckets
    ordered = sorted(pairs, key=lambda item: item[0])
    result: List[Optional[float]] = []
    n = len(ordered)
    for bucket in range(buckets):
        start = int(bucket * n / buckets)
        end = int((bucket + 1) * n / buckets)
        chunk = ordered[start:end]
        result.append(round(sum(item[1] for item in chunk) / len(chunk), 3) if chunk else None)
    return result


def validate_snapshots(
    snapshots: List[Tuple[str, Dict[str, Any]]],
    price_map: Dict[str, List[Dict[str, Any]]],
    horizons: List[int],
    top_n: int = 10,
) -> Dict[str, Any]:
    per_horizon: Dict[str, Dict[str, Any]] = {
        f"{h}d": {"ics": [], "top_hits": [], "bucket_values": [], "regimes": {}}
        for h in horizons
    }
    used_days = 0

    for date, snapshot in snapshots:
        candidates = [
            c for c in snapshot.get("candidates", [])
            if _score_of(c) is not None and c.get("rankable", True)
        ]
        if len(candidates) < 3:
            continue
        regime = (snapshot.get("market_state") or {}).get("regime", "UNKNOWN")
        used_days += 1

        for horizon in horizons:
            pairs: List[Tuple[float, float]] = []
            for candidate in candidates:
                code = str(candidate.get("code", ""))
                rows = price_map.get(code)
                ret = forward_return(rows, date, horizon) if rows else None
                if ret is None:
                    continue
                pairs.append((_score_of(candidate), ret))
            if len(pairs) < 3:
                continue

            key = f"{horizon}d"
            ic = spearman_ic([p[0] for p in pairs], [p[1] for p in pairs])
            if ic is not None:
                per_horizon[key]["ics"].append(round(ic, 4))

            ordered = sorted(pairs, key=lambda item: item[0], reverse=True)
            top = ordered[:top_n]
            all_returns = sorted(ret for _, ret in pairs)
            median = all_returns[len(all_returns) // 2]
            top_hit = (
                sum(1.0 for _, ret in top if ret > median) / len(top)
                if top else 0.0
            )
            per_horizon[key]["top_hits"].append(round(top_hit, 4))

            buckets = bucket_returns(pairs, buckets=5)
            per_horizon[key]["bucket_values"].append(buckets)
            per_horizon[key]["regimes"].setdefault(regime, []).append(ic)

    summary: Dict[str, Any] = {
        "validation_version": VALIDATION_VERSION,
        "used_days": used_days,
        "horizons": {},
    }
    for key, data in per_horizon.items():
        ics = data["ics"]
        hits = data["top_hits"]
        bucket_matrix = [b for b in data["bucket_values"] if all(v is not None for v in b)]
        bucket_avg = [
            round(sum(b[i] for b in bucket_matrix) / len(bucket_matrix), 3)
            if bucket_matrix else None
            for i in range(5)
        ]
        monotonic_days = 0
        for b in bucket_matrix:
            if all(b[i] <= b[i + 1] for i in range(len(b) - 1)):
                monotonic_days += 1
        regime_stats = {}
        for regime, values in data["regimes"].items():
            valid = [v for v in values if v is not None]
            regime_stats[regime] = {
                "days": len(valid),
                "mean_rank_ic": round(sum(valid) / len(valid), 4) if valid else None,
            }
        summary["horizons"][key] = {
            "rank_ic_mean": round(sum(ics) / len(ics), 4) if ics else None,
            "rank_ic_positive_days": sum(1 for v in ics if v > 0),
            "rank_ic_days": len(ics),
            "top_n_hit_rate": round(sum(hits) / len(hits), 4) if hits else None,
            "top_n": top_n,
            "bucket_avg_returns": bucket_avg,
            "bucket_monotonic_days": monotonic_days,
            "bucket_days": len(bucket_matrix),
            "regime": regime_stats,
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="候选评分前瞻收益验证")
    parser.add_argument("--horizons", default="1,5,20", help="未来收益天数，逗号分隔")
    parser.add_argument("--top-n", type=int, default=10, help="Top-N 命中率")
    parser.add_argument("--min-days", type=int, default=5, help="最少有效快照天数")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="输出 JSON 路径")
    args = parser.parse_args()

    horizons = [int(h.strip()) for h in args.horizons.split(",") if h.strip()]
    snapshots = load_snapshots()
    if not snapshots:
        print("ℹ️ 暂无可验证的候选评分快照")
        return 0

    codes = [
        str(c.get("code"))
        for _, snap in snapshots
        for c in snap.get("candidates", [])
        if c.get("code")
    ]
    print(f"📥 载入 {len(snapshots)} 个候选快照，抓取 {len(set(codes))} 只标的 K 线…")
    price_map = build_price_index(codes)
    result = validate_snapshots(snapshots, price_map, horizons, args.top_n)

    if result["used_days"] < args.min_days:
        print(f"⚠️ 有效快照天数 {result['used_days']} < {args.min_days}，结果仅作参考",
              file=sys.stderr)

    print(f"\n📊 前瞻收益验证（有效快照 {result['used_days']} 天）")
    for key, stat in result["horizons"].items():
        print(f"\n  [{key}]")
        print(f"    rank IC 均值: {stat['rank_ic_mean']} "
              f"(正 IC 天数 {stat['rank_ic_positive_days']}/{stat['rank_ic_days']})")
        print(f"    Top{stat['top_n']} 命中率: {stat['top_n_hit_rate']}")
        print(f"    分桶平均收益(低→高): {stat['bucket_avg_returns']}")
        print(f"    分桶单调天数: {stat['bucket_monotonic_days']}/{stat['bucket_days']}")
        for regime, rstat in stat["regime"].items():
            print(f"    {regime}: {rstat['days']}天, mean IC={rstat['mean_rank_ic']}")

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 结果已保存: {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
