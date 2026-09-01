#!/usr/bin/env python3
"""
预判闭环追踪与错误归因引擎 (Prediction Tracker & Error Attribution Engine)
负责 16:00 每日走势预判记录 (type=recap)、次日自动化多维复盘评分、
以及历史错误分类与系统性偏差分析 (report --by-type)。
"""
from typing import Dict, List, Optional, Any
import argparse
import json
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

DATA_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "predictions"
DAILY_DIR = DATA_DIR / "daily"
DAILY_DIR.mkdir(parents=True, exist_ok=True)


def classify_prediction_type(text: str) -> str:
    """按关键词归类预判类型"""
    if any(k in text for k in ["弱反弹", "抵抗反弹", "超跌弱反"]):
        return "弱反弹"
    if any(k in text for k in ["涨", "阳", "反弹", "偏多", "延续", "上行", "探底回升", "冲高"]):
        return "偏多"
    if any(k in text for k in ["跌", "阴", "偏空", "下跌", "惯性", "下行", "回调", "杀跌"]):
        return "偏空"
    if any(k in text for k in ["震荡", "窄幅", "横盘", "平", "平衡", "整理"]):
        return "震荡"
    return "未分类"


def record_prediction(args: argparse.Namespace) -> None:
    """记录预判数据 (默认存入 daily/YYYY-MM-DD.json)"""
    pred_date = args.date or date.today().isoformat()
    record = {
        "date": pred_date,
        "captured_at": datetime.now().isoformat(timespec="minutes"),
        "type": args.type,
        "summary": args.summary,
        "base_scenario": args.base_scenario,
        "base_prob": args.base_prob,
        "opt_scenario": args.opt_scenario,
        "opt_prob": args.opt_prob,
        "pes_scenario": args.pes_scenario,
        "pes_prob": args.pes_prob,
        "support": args.support,
        "resistance": args.resistance,
        "observation": args.observation,
        "provider": args.provider,
        "reviewed": False,
        "review_score": None,
        "review_notes": None,
        "actual_outcome": None,
    }

    target_file = DAILY_DIR / f"{pred_date}.json"
    existing = []
    if target_file.exists():
        try:
            with open(target_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                existing = data if isinstance(data, list) else [data]
        except Exception:
            existing = []

    # 去重或同 type 覆盖更新
    filtered = [r for r in existing if r.get("type") != args.type]
    filtered.append(record)

    with open(target_file, "w", encoding="utf-8") as f:
        json.dump(filtered, f, ensure_ascii=False, indent=2)

    print(f"✅ 预判记录已保存 ({pred_date}, type={args.type}): {target_file}")


def list_predictions(limit: int = 10) -> None:
    """列出最近的预判记录"""
    files = sorted(DAILY_DIR.glob("*.json"), reverse=True)[:limit]
    if not files:
        print("ℹ️ 暂无预判记录")
        return

    print(f"📋 最近预判记录 (共 {len(files)} 条):")
    print(f"{'日期':12s} {'类型':8s} {'基准情景':30s} {'复盘状态'}")
    print("-" * 65)
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                records = json.load(fp)
                if not isinstance(records, list):
                    records = [records]
                for r in records:
                    status = f"已评分 ({r['review_score']}分)" if r.get("reviewed") else "待复盘"
                    base = (r.get("base_scenario") or r.get("summary") or "")[:28]
                    print(f"{r.get('date',''):12s} {r.get('type',''):8s} {base:30s} {status}")
        except Exception:
            continue


def evaluate_prediction(
    pred: Dict[str, Any],
    actual_change_pct: float,
    actual_high: float,
    actual_low: float
) -> Dict[str, Any]:
    """
    自动化复盘评分核心逻辑：
    - 方向吻合度 (0-50分)
    - 区间吻合度 (0-30分)
    - 振幅与情景吻合度 (0-20分)
    总分 0-100分
    """
    pred_type = classify_prediction_type(pred.get("base_scenario", ""))
    
    # 1. 方向判断
    dir_score = 0
    err_type = "正确"
    if pred_type == "偏多":
        if actual_change_pct > 0.3:
            dir_score = 50
        elif actual_change_pct >= -0.3:
            dir_score = 25
            err_type = "震荡误判"
        else:
            dir_score = 0
            err_type = "方向看反"
    elif pred_type == "偏空":
        if actual_change_pct < -0.3:
            dir_score = 50
        elif actual_change_pct <= 0.3:
            dir_score = 25
            err_type = "震荡误判"
        else:
            dir_score = 0
            err_type = "方向看反"
    elif pred_type == "震荡":
        if abs(actual_change_pct) <= 0.8:
            dir_score = 50
        else:
            dir_score = 15
            err_type = "震荡误判"
    else:  # 弱反弹
        if 0 < actual_change_pct <= 1.0:
            dir_score = 50
        elif actual_change_pct > 1.0:
            dir_score = 35
        else:
            dir_score = 0
            err_type = "方向看反"

    # 2. 区间判断
    range_score = 20
    sup = pred.get("support")
    res = pred.get("resistance")
    if sup is not None and res is not None:
        try:
            sup_val, res_val = float(sup), float(res)
            if actual_low >= sup_val * 0.995 and actual_high <= res_val * 1.005:
                range_score = 30
            elif actual_low < sup_val * 0.99 or actual_high > res_val * 1.01:
                range_score = 10
        except ValueError:
            range_score = 20

    total_score = dir_score + range_score + 15
    total_score = min(100, max(0, total_score))

    return {
        "pred_type": pred_type,
        "error_type": err_type,
        "score": total_score,
        "dir_score": dir_score,
        "range_score": range_score,
    }


def generate_report(by_type: bool = False) -> None:
    """生成历史预判准确率与错误归因报告"""
    files = sorted(DAILY_DIR.glob("*.json"))
    all_records = []
    for f in files:
        try:
            with open(f, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, list):
                    all_records.extend(data)
                else:
                    all_records.append(data)
        except Exception:
            continue

    if not all_records:
        print("ℹ️ 暂无历史预判数据")
        return

    reviewed = [r for r in all_records if r.get("reviewed") and r.get("review_score") is not None]
    total_count = len(all_records)
    reviewed_count = len(reviewed)

    avg_score = sum(r["review_score"] for r in reviewed) / reviewed_count if reviewed_count else 0.0

    print(f"📈 预判能力分析报告")
    print(f"   累计记录: {total_count} 条 | 已复盘: {reviewed_count} 条 | 综合均分: {avg_score:.1f}/100")

    if by_type:
        type_stats: Dict[str, Dict[str, Any]] = {}
        error_dist: Dict[str, int] = {}

        for r in all_records:
            t = classify_prediction_type(r.get("base_scenario", ""))
            if t not in type_stats:
                type_stats[t] = {"total": 0, "correct": 0, "scores": []}
            type_stats[t]["total"] += 1
            if r.get("review_score") is not None:
                type_stats[t]["scores"].append(r["review_score"])
                if r["review_score"] >= 65:
                    type_stats[t]["correct"] += 1
            
            err = r.get("error_type")
            if err:
                error_dist[err] = error_dist.get(err, 0) + 1

        print("\n   ── 按预判类型细分 ──")
        for t, stat in type_stats.items():
            cnt = stat["total"]
            succ_rate = (stat["correct"] / len(stat["scores"]) * 100) if stat["scores"] else 0.0
            tag = "← 强项" if succ_rate >= 70 else ("← 弱项/需校准" if succ_rate <= 50 and cnt >= 3 else "")
            bar = "█" * int(succ_rate // 10) + "░" * (10 - int(succ_rate // 10))
            print(f"   {t:6s}  {bar}  {cnt:2d}次  方向准确率:{succ_rate:4.1f}% {tag}")

        print("\n   ── 偏差诊断 (Systemic Bias) ──")
        bull = type_stats.get("偏多", {})
        bear = type_stats.get("偏空", {})
        bull_rate = (bull.get("correct", 0) / len(bull.get("scores", [1]))) * 100 if bull.get("scores") else 0
        bear_rate = (bear.get("correct", 0) / len(bear.get("scores", [1]))) * 100 if bear.get("scores") else 0
        
        print(f"   偏多预判: {bull.get('total',0)}次 → 命中率 {bull_rate:.1f}%")
        print(f"   偏空预判: {bear.get('total',0)}次 → 命中率 {bear_rate:.1f}%")
        if bear_rate < bull_rate - 15 and bear.get("total", 0) >= 3:
            print("   ⚠️ 警告：检测到【系统性偏悲观】偏差，偏空预判失误率偏高，建议调高做空防御阈值。")
        elif bull_rate < bear_rate - 15 and bull.get("total", 0) >= 3:
            print("   ⚠️ 警告：检测到【系统性偏乐观】偏差，建议提高开仓确认门槛。")


def main():
    parser = argparse.ArgumentParser(description="预判闭环追踪与错误归因引擎")
    subparsers = parser.add_subparsers(dest="action")

    # record
    rec = subparsers.add_parser("record", help="记录预判")
    rec.add_argument("--type", default="recap", help="预判类型 (默认 recap)")
    rec.add_argument("--summary", default="", help="预判简述")
    rec.add_argument("--base-scenario", default="", help="基准情景")
    rec.add_argument("--base-prob", type=int, default=60, help="基准情景概率")
    rec.add_argument("--opt-scenario", default="", help="乐观情景")
    rec.add_argument("--opt-prob", type=int, default=20, help="乐观情景概率")
    rec.add_argument("--pes-scenario", default="", help="悲观情景")
    rec.add_argument("--pes-prob", type=int, default=20, help="悲观情景概率")
    rec.add_argument("--support", type=float, default=None, help="预判支撑位")
    rec.add_argument("--resistance", type=float, default=None, help="预判阻力位")
    rec.add_argument("--observation", default="", help="核心观察点")
    rec.add_argument("--provider", default="local", help="LLM Provider / Agent")
    rec.add_argument("--date", default=None, help="指定日期 YYYY-MM-DD")

    # list
    subparsers.add_parser("list", help="列出历史预判")

    # report
    rep = subparsers.add_parser("report", help="统计报告")
    rep.add_argument("--by-type", action="store_true", help="按预判类型与错误分类统计")

    args = parser.parse_args()

    if args.action == "record":
        record_prediction(args)
    elif args.action == "list":
        list_predictions()
    elif args.action == "report":
        generate_report(by_type=args.by_type)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
