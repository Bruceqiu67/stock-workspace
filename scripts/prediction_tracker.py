#!/usr/bin/env python3
"""
预判闭环追踪与错误归因引擎 (Prediction Tracker & Error Attribution Engine)

闭环：record（记录明日预判）→ review（拉取次日实际行情并写回规范字段）→ report（统计）。

字段约定（SCHEMA_VERSION = 1.1）：
  - date:           做出预判的交易日
  - target_date:    预判所指向的交易日（下一交易日）
  - type:           预判类型，默认 recap
  - direction_label: 结构化方向标签（偏多/偏空/震荡/弱反弹/未分类）
  - reviewed/review_score/error_type/actual_outcome: 复盘写回字段
  - rubric_version/schema_version/data_source: 口径与数据来源追溯

兼容：
  - 旧的 `accuracy` 字典会在 review/report 时迁移为 review_score 等规范字段。
  - 旧的同 type 覆盖逻辑改为追加式：新记录 revision+1，旧记录写 superseded_by。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path

try:
    from trading_calendar import (
        freshness as calendar_freshness,
        is_trading_day as calendar_is_trading_day,
        next_trading_day as calendar_next_trading_day,
    )
except ImportError:  # 日历模块缺失时保留周末近似，但显式标注
    calendar_freshness = None
    calendar_is_trading_day = None
    calendar_next_trading_day = None

try:
    from market_data_adapter import (
        parse_sina_kline_payload,
        parse_tencent_kline_payload,
    )
except ImportError:  # 适配器缺失时下面保留最小解析，仍显式标记
    parse_sina_kline_payload = None
    parse_tencent_kline_payload = None

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCHEMA_VERSION = "1.1"
RUBRIC_VERSION = "2.0"

DATA_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "predictions"
DAILY_DIR = DATA_DIR / "daily"
DAILY_DIR.mkdir(parents=True, exist_ok=True)

DIRECTION_LABELS = ("偏多", "偏空", "震荡", "弱反弹", "未分类")
INDEX_SYMBOL = "sh000001"


# ── 日期与文件 ──────────────────────────────────────────────────────────────

def next_trading_day(day: str) -> str:
    """下一交易日：优先使用 trading_calendar（含可配置节假日）。"""
    if calendar_next_trading_day:
        result = calendar_next_trading_day(day)
        if result:
            return result
    try:
        current = date.fromisoformat(str(day)[:10])
    except ValueError:
        current = date.today()
    current += timedelta(days=1)
    while current.weekday() >= 5:
        current += timedelta(days=1)
    return current.isoformat()


def is_probably_trading_day(day: str) -> bool:
    """是否交易日；日历模块缺失时退回周末近似。"""
    if calendar_is_trading_day:
        return bool(calendar_is_trading_day(day))
    try:
        return date.fromisoformat(str(day)[:10]).weekday() < 5
    except ValueError:
        return False


def _load_records(path: Path) -> List[Dict[str, Any]]:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    raise ValueError(f"预判文件根节点必须是对象或数组: {path}")


def _write_records(path: Path, records: List[Dict[str, Any]]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)


# ── 分类 ────────────────────────────────────────────────────────────────────

def classify_prediction_type(text: str) -> str:
    """
    按关键词归类预判类型（P2 起支持结构化 direction_label 优先）。
    先处理混合表述，避免“冲高回落”被“冲高”抢先判为偏多。
    """
    text = str(text or "")
    if any(k in text for k in ["弱反弹", "抵抗反弹", "超跌弱反"]):
        return "弱反弹"
    if any(k in text for k in ["冲高回落", "高开低走", "先涨后跌", "上冲失败"]):
        return "偏空"
    if any(k in text for k in ["低开高走", "探底回升", "先跌后涨", "深V"]):
        return "偏多"
    if any(k in text for k in ["涨", "阳", "反弹", "偏多", "延续", "上行"]):
        return "偏多"
    if any(k in text for k in ["跌", "阴", "偏空", "下跌", "惯性", "下行", "回调", "杀跌"]):
        return "偏空"
    if any(k in text for k in ["震荡", "窄幅", "横盘", "平", "平衡", "整理"]):
        return "震荡"
    return "未分类"


def _record_direction(record: Dict[str, Any]) -> str:
    label = record.get("direction_label")
    if label in DIRECTION_LABELS and label != "未分类":
        return label
    return classify_prediction_type(
        record.get("base_scenario") or record.get("summary") or ""
    )


# ── 记录 ────────────────────────────────────────────────────────────────────

def _validate_probabilities(base: int, opt: int, pes: int) -> None:
    for name, value in (("base_prob", base), ("opt_prob", opt), ("pes_prob", pes)):
        if not isinstance(value, int) or value < 0 or value > 100:
            raise SystemExit(f"❌ {name} 必须是 0-100 的整数，当前为 {value}")
    if base + opt + pes != 100:
        raise SystemExit(
            f"❌ 三情景概率之和必须为 100，当前为 {base + opt + pes} "
            f"({base}/{opt}/{pes})"
        )


def record_prediction(args: argparse.Namespace) -> None:
    """追加式记录预判，不覆盖同 type 历史；新记录 revision+1 并 supersedes 旧记录。"""
    pred_date = args.date or date.today().isoformat()
    target_date_original = getattr(args, "target_date", None)
    target_date = target_date_original or next_trading_day(pred_date)
    target_is_trading_day = is_probably_trading_day(target_date)
    target_date_adjusted = False
    if not target_is_trading_day:
        # 显式传入的 target_date 落在周末/节假日时，顺延到下一交易日，
        # 但保留原值与 adjusted 标记，禁止静默改写。
        adjusted = next_trading_day(target_date)
        if adjusted:
            target_date = adjusted
            target_date_adjusted = True
            print(
                f"⚠️  目标日 {target_date_original} 非交易日，已顺延至 {target_date}",
                file=sys.stderr,
            )

    _validate_probabilities(args.base_prob, args.opt_prob, args.pes_prob)
    if target_date <= pred_date:
        raise SystemExit(
            f"❌ target_date({target_date}) 必须晚于预判日({pred_date})"
        )

    direction_label = getattr(args, "direction", None) or classify_prediction_type(
        args.base_scenario or args.summary or ""
    )
    if direction_label not in DIRECTION_LABELS:
        direction_label = "未分类"

    explicit_probability_source = getattr(args, "probability_source", None)
    if explicit_probability_source:
        probability_source = explicit_probability_source
    elif (args.base_prob, args.opt_prob, args.pes_prob) == (60, 20, 20):
        probability_source = "default_60_20_20"
    else:
        probability_source = "explicit_cli"

    target_file = DAILY_DIR / f"{pred_date}.json"
    existing: List[Dict[str, Any]] = []
    if target_file.exists():
        try:
            existing = _load_records(target_file)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"⚠️  现有预判文件损坏，保留原文件并拒绝覆盖: {exc}", file=sys.stderr)
            raise SystemExit(2)

    same_type = [r for r in existing if r.get("type") == args.type]
    revision = 1 + max((int(r.get("revision", 1)) for r in same_type), default=0)
    record_id = f"pred_{pred_date.replace('-', '')}_{args.type}_{revision:02d}"

    record = {
        "id": record_id,
        "schema_version": SCHEMA_VERSION,
        "rubric_version": getattr(args, "rubric_version", None) or RUBRIC_VERSION,
        "revision": revision,
        "supersedes": same_type[-1].get("id") if same_type else None,
        "date": pred_date,
        "target_date": target_date,
        "target_date_original": target_date_original,
        "target_date_adjusted": target_date_adjusted,
        "target_date_is_trading_day": is_probably_trading_day(target_date),
        "trading_day": is_probably_trading_day(pred_date),
        "calendar_version": "1.0.0",
        "captured_at": datetime.now().isoformat(timespec="minutes"),
        "type": args.type,
        "direction_label": direction_label,
        "summary": args.summary,
        "base_scenario": args.base_scenario,
        "base_prob": args.base_prob,
        "opt_scenario": args.opt_scenario,
        "opt_prob": args.opt_prob,
        "pes_scenario": args.pes_scenario,
        "pes_prob": args.pes_prob,
        "probability_source": probability_source,
        "scenarios": {
            "base": {"text": args.base_scenario, "prob": args.base_prob},
            "opt": {"text": args.opt_scenario, "prob": args.opt_prob},
            "pes": {"text": args.pes_scenario, "prob": args.pes_prob},
        },
        "support": args.support,
        "resistance": args.resistance,
        "observation": args.observation,
        "provider": args.provider,
        "data_source": getattr(args, "data_source", None) or "manual",
        "reviewed": False,
        "review_score": None,
        "review_notes": None,
        "actual_outcome": None,
    }

    for old in same_type:
        if not old.get("superseded_by"):
            old["superseded_by"] = record_id
    existing.append(record)
    _write_records(target_file, existing)

    print(f"✅ 预判记录已追加 ({pred_date} → {target_date}, type={args.type}, "
          f"revision={revision}): {target_file}")


def _latest_records(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """报告/列表默认只看未被替代的最新版本。"""
    return [r for r in records if not r.get("superseded_by")]


def list_predictions(limit: int = 10, show_history: bool = False) -> None:
    """列出最近的预判记录；损坏文件显式报警，不静默跳过。"""
    files = sorted(DAILY_DIR.glob("*.json"), reverse=True)[:limit]
    if not files:
        print("ℹ️ 暂无预判记录")
        return

    scope = "含历史版本" if show_history else "仅最新版本"
    print(f"📋 最近预判记录 (共 {len(files)} 个日期文件，{scope}):")
    print(f"{'日期':12s} {'目标日':12s} {'类型':8s} {'方向':6s} "
          f"{'版本':4s} {'口径':6s} {'基准情景':20s} {'复盘状态'}")
    print("-" * 110)
    for f in files:
        try:
            records = _load_records(f)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            print(f"⚠️  预判文件损坏，已跳过但需处理: {f} ({exc})", file=sys.stderr)
            continue
        selected = records if show_history else _latest_records(records)
        for r in selected:
            status = (
                f"已评分 ({r['review_score']}分)"
                if r.get("reviewed") and r.get("review_score") is not None
                else "待复盘"
            )
            if r.get("superseded_by"):
                status += f" / 已被 {r['superseded_by']} 替代"
            base = (r.get("base_scenario") or r.get("summary") or "")[:18]
            print(f"{r.get('date',''):12s} {r.get('target_date',''):12s} "
                  f"{r.get('type',''):8s} {_record_direction(r):6s} "
                  f"{str(r.get('revision', 1)):4s} "
                  f"{str(r.get('rubric_version', '?')):6s} {base:20s} {status}")


# ── 复盘评分（唯一 rubric，RUBRIC_VERSION = 2.0） ──────────────────────────

DIRECTION_WEIGHT = 50
RANGE_WEIGHT = 30
SCENARIO_WEIGHT = 20

# 方向误差阈值：与 prediction-error-classification.md 统一为 0.5% / 1.5%
DIRECTION_THRESHOLD_PCT = 0.5
OSCILLATION_THRESHOLD_PCT = 1.5


def _actual_bucket(change_pct: float) -> str:
    if change_pct >= DIRECTION_THRESHOLD_PCT:
        return "up"
    if change_pct <= -DIRECTION_THRESHOLD_PCT:
        return "down"
    return "flat"


def _direction_bucket(label: str) -> str:
    if label == "偏多":
        return "up"
    if label == "偏空":
        return "down"
    if label in ("震荡", "未分类"):
        return "flat"
    return "up"  # 弱反弹归入 up 桶


def _scenario_items(pred: Dict[str, Any]) -> List[Dict[str, Any]]:
    """兼容结构化 scenarios 与扁平 base/opt/pes 字段。"""
    structured = pred.get("scenarios")
    if isinstance(structured, dict):
        items = []
        for key in ("base", "opt", "pes"):
            item = structured.get(key) or {}
            if isinstance(item, dict):
                items.append({
                    "key": key,
                    "text": item.get("text", ""),
                    "prob": item.get("prob"),
                })
        if items:
            return items
    return [
        {"key": "base", "text": pred.get("base_scenario", ""), "prob": pred.get("base_prob")},
        {"key": "opt", "text": pred.get("opt_scenario", ""), "prob": pred.get("opt_prob")},
        {"key": "pes", "text": pred.get("pes_scenario", ""), "prob": pred.get("pes_prob")},
    ]


def _score_scenarios(pred: Dict[str, Any], actual_change_pct: float) -> Dict[str, Any]:
    """
    按三情景概率计算多分类 Brier 分数，并折合为 0-20 的情景分。
    BS = sum_c (p_c - o_c)^2，取值范围 0-2；情景分 = 20 * (1 - BS/2)。
    """
    items = _scenario_items(pred)
    try:
        probs = [float(item.get("prob")) for item in items]
    except (TypeError, ValueError):
        return {
            "status": "invalid_probabilities",
            "score": None,
            "brier_score": None,
            "probabilities_valid": False,
            "reason": "情景概率不是数值",
        }
    if any(p < 0 or p > 100 for p in probs) or abs(sum(probs) - 100.0) > 1e-6:
        return {
            "status": "invalid_probabilities",
            "score": None,
            "brier_score": None,
            "probabilities_valid": False,
            "reason": f"三情景概率之和必须为100，当前为{sum(probs):g}",
        }

    pred_probs = {"up": 0.0, "down": 0.0, "flat": 0.0}
    for item, prob in zip(items, probs):
        bucket = _direction_bucket(
            classify_prediction_type(item.get("text", ""))
        )
        pred_probs[bucket] += prob / 100.0

    actual = _actual_bucket(actual_change_pct)
    outcome = {key: 1.0 if key == actual else 0.0 for key in pred_probs}
    brier = sum((pred_probs[key] - outcome[key]) ** 2 for key in pred_probs)
    scenario_score = round(max(0.0, min(SCENARIO_WEIGHT, SCENARIO_WEIGHT * (1 - brier / 2.0))), 2)
    return {
        "status": "scored",
        "score": scenario_score,
        "brier_score": round(brier, 4),
        "probabilities_valid": True,
        "predicted_probabilities": {k: round(v, 4) for k, v in pred_probs.items()},
        "actual_bucket": actual,
    }


def evaluate_prediction(
    pred: Dict[str, Any],
    actual_change_pct: float,
    actual_high: Optional[float] = None,
    actual_low: Optional[float] = None,
) -> Dict[str, Any]:
    """
    唯一预判评分口径（RUBRIC_VERSION = 2.0）：

      - 方向分 0-50：偏多/偏空以 ±0.5% 为界；震荡以 ±1.5% 为界。
      - 区间分 0-30：支撑/阻力缺失时记为 N/A（不默认给分）。
      - 情景分 0-20：按三情景概率与 Brier 分数计算，概率和必须为 100。
      - 总分 = 已评分维度原始分之和；同时输出 score_coverage 表示覆盖度，
        避免“只判方向也拿高分”的虚高。
    """
    pred_type = _record_direction(pred)

    dir_score = 0
    err_type = "正确"
    if pred_type == "偏多":
        if actual_change_pct > DIRECTION_THRESHOLD_PCT:
            dir_score = 50
        elif actual_change_pct >= -DIRECTION_THRESHOLD_PCT:
            dir_score = 25
            err_type = "震荡误判"
        else:
            dir_score = 0
            err_type = "方向看反"
    elif pred_type == "偏空":
        if actual_change_pct < -DIRECTION_THRESHOLD_PCT:
            dir_score = 50
        elif actual_change_pct <= DIRECTION_THRESHOLD_PCT:
            dir_score = 25
            err_type = "震荡误判"
        else:
            dir_score = 0
            err_type = "方向看反"
    elif pred_type == "震荡":
        if abs(actual_change_pct) <= OSCILLATION_THRESHOLD_PCT:
            dir_score = 50
        else:
            dir_score = 15
            err_type = "震荡误判"
    elif pred_type == "未分类":
        dir_score = 0
        err_type = "未分类"
    else:  # 弱反弹
        if 0 < actual_change_pct <= 1.0:
            dir_score = 50
        elif actual_change_pct > 1.0:
            dir_score = 35
        else:
            dir_score = 0
            err_type = "方向看反"

    # 区间分：缺失即 N/A，不默认 20 分
    range_status = "N/A"
    range_score: Optional[float] = None
    sup = pred.get("support")
    res = pred.get("resistance")
    if sup is not None and res is not None and actual_high is not None and actual_low is not None:
        try:
            sup_val, res_val = float(sup), float(res)
            if res_val <= sup_val:
                range_status = "invalid_levels"
            else:
                range_status = "scored"
                if actual_low >= sup_val * 0.995 and actual_high <= res_val * 1.005:
                    range_score = 30
                elif actual_low < sup_val * 0.99 or actual_high > res_val * 1.01:
                    range_score = 0
                else:
                    range_score = 15
        except (TypeError, ValueError):
            range_status = "invalid_levels"

    scenario = _score_scenarios(pred, actual_change_pct)
    scenario_score = scenario.get("score")

    scored_parts = [dir_score]
    max_points = DIRECTION_WEIGHT
    if range_score is not None:
        scored_parts.append(range_score)
        max_points += RANGE_WEIGHT
    if scenario_score is not None:
        scored_parts.append(scenario_score)
        max_points += SCENARIO_WEIGHT

    total_score = round(sum(scored_parts), 2)
    coverage = round(max_points / 100.0, 2)
    confidence = "low" if coverage < 0.7 else ("medium" if coverage < 0.9 else "high")

    return {
        "pred_type": pred_type,
        "error_type": err_type,
        "score": total_score,
        "score_coverage": coverage,
        "max_points": max_points,
        "confidence": confidence,
        "dir_score": dir_score,
        "range_score": range_score,
        "range_status": range_status,
        "scenario_score": scenario_score,
        "scenario_status": scenario.get("status"),
        "brier_score": scenario.get("brier_score"),
        "probabilities_valid": scenario.get("probabilities_valid", False),
        "actual_bucket": scenario.get("actual_bucket", _actual_bucket(actual_change_pct)),
        "rubric_version": RUBRIC_VERSION,
    }


# ── 实际行情获取 ────────────────────────────────────────────────────────────

def _parse_sina_klines(raw: Any) -> List[Dict[str, Any]]:
    """解析新浪 K 线；统一委托 market_data_adapter。"""
    if parse_sina_kline_payload is None:
        return []
    return parse_sina_kline_payload(raw)


def _parse_tencent_klines(payload: Dict[str, Any], code_full: str) -> List[Dict[str, Any]]:
    """解析腾讯 K 线；统一委托 market_data_adapter。"""
    if parse_tencent_kline_payload is None:
        return []
    return parse_tencent_kline_payload(payload, code_full, period="day")


def fetch_index_klines(days: int = 300) -> Tuple[List[Dict[str, Any]], str]:
    """拉取上证指数日K，返回 (klines, source)；失败返回 ([], error)。"""
    sina_url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={INDEX_SYMBOL}&scale=240&ma=no&datalen={days}"
    )
    try:
        req = urllib.request.Request(
            sina_url,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = json.loads(resp.read().decode("utf-8", errors="replace"))
        rows = _parse_sina_klines(raw)
        if rows:
            return rows, "sina"
    except (urllib.error.URLError, ValueError, OSError, TimeoutError):
        pass

    tencent_url = (
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
        f"param={INDEX_SYMBOL},day,,,{days},qfq"
    )
    try:
        req = urllib.request.Request(tencent_url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
        rows = _parse_tencent_klines(payload, INDEX_SYMBOL)
        if rows:
            return rows, "tencent"
    except (urllib.error.URLError, ValueError, OSError, TimeoutError):
        pass
    return [], "unavailable"


def fetch_actual_index_day(target_date: str) -> Dict[str, Any]:
    """获取目标交易日的实际指数数据（含前收与真实涨跌幅）。"""
    klines, source = fetch_index_klines()
    if not klines:
        return {"status": "error", "reason": "指数K线获取失败", "target_date": target_date}
    klines = sorted(klines, key=lambda k: k["date"])
    index = next((i for i, k in enumerate(klines) if k["date"] == target_date), None)
    if index is None:
        return {
            "status": "missing",
            "reason": f"目标日 {target_date} 不在指数K线中（可能非交易日）",
            "target_date": target_date,
            "source": source,
        }
    day = klines[index]
    prev_close = klines[index - 1]["close"] if index > 0 else None
    change_pct = (
        (day["close"] - prev_close) / prev_close * 100
        if prev_close and prev_close > 0 else None
    )
    return {
        "status": "ok",
        "target_date": target_date,
        "as_of": day["date"],
        "open": day["open"],
        "close": day["close"],
        "high": day["high"],
        "low": day["low"],
        "prev_close": prev_close,
        "change_pct": round(change_pct, 4) if change_pct is not None else None,
        "source": source,
    }


def _migrate_legacy_accuracy(record: Dict[str, Any]) -> bool:
    """把旧的 accuracy 字典迁移为规范字段；返回是否发生迁移。"""
    accuracy = record.get("accuracy")
    if not isinstance(accuracy, dict):
        return False
    if record.get("reviewed") and record.get("review_score") is not None:
        return False
    overall = accuracy.get("overall")
    if overall is None:
        return False
    # 旧 overall 可能是 0-1 或 0-100
    score = float(overall) * 100 if float(overall) <= 1 else float(overall)
    record["reviewed"] = True
    record["review_score"] = round(score, 1)
    record["dir_score"] = accuracy.get("direction_score")
    record["range_score"] = accuracy.get("level_score")
    record["scenario_score"] = None
    record["error_type"] = record.get("error_type") or "旧口径迁移"
    record["actual_outcome"] = record.get("actual_outcome") or record.get("actual")
    record["review_notes"] = "由旧 accuracy 字段迁移，口径与当前 rubric 不完全可比"
    return True


# ── review 命令 ─────────────────────────────────────────────────────────────

def review_predictions(review_date: Optional[str] = None, dry_run: bool = False) -> int:
    """
    复盘指定日期文件中尚未复盘的预判。

    - `review_date` 是预判做出日（文件名日期）；实际验证日优先用记录里的
      `target_date`，缺失时用下一交易日。
    - 复盘写回规范字段：reviewed/review_score/error_type/actual_outcome/
      dir_score/range_score/scenario_score/reviewed_at。
    """
    target_file = DAILY_DIR / f"{review_date}.json" if review_date else None
    if target_file is None:
        # 默认：最近一个存在未复盘记录的文件
        files = sorted(DAILY_DIR.glob("*.json"), reverse=True)
        for candidate in files:
            try:
                records = _load_records(candidate)
            except (OSError, json.JSONDecodeError, ValueError) as exc:
                print(
                    f"⚠️  复盘扫描跳过损坏文件，但需人工处理: {candidate} ({exc})",
                    file=sys.stderr,
                )
                continue
            if any(not r.get("reviewed") for r in records):
                target_file = candidate
                break
    if target_file is None or not target_file.exists():
        print("ℹ️ 没有可复盘的预判文件")
        return 0

    try:
        records = _load_records(target_file)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"❌ 预判文件损坏: {target_file} ({exc})", file=sys.stderr)
        return 2

    migrated = 0
    for record in records:
        if _migrate_legacy_accuracy(record):
            migrated += 1

    pending = [r for r in records if not r.get("reviewed")]
    if not pending:
        print(f"ℹ️ {target_file.stem} 的预判均已复盘")
        return 0

    print(f"🔍 复盘 {target_file.stem} 的预判（待复盘 {len(pending)} 条）")
    reviewed_count = 0
    errors = 0
    for record in pending:
        actual_date = record.get("target_date") or next_trading_day(record.get("date", ""))
        actual = fetch_actual_index_day(actual_date)
        if actual.get("status") != "ok" or actual.get("change_pct") is None:
            print(f"⚠️  {record.get('id', '')} 无法获取 {actual_date} 实际行情: "
                  f"{actual.get('reason', actual.get('status'))}", file=sys.stderr)
            errors += 1
            continue

        result = evaluate_prediction(
            record,
            actual_change_pct=actual["change_pct"],
            actual_high=actual["high"],
            actual_low=actual["low"],
        )
        record["reviewed"] = True
        record["review_score"] = result["score"]
        record["score_coverage"] = result["score_coverage"]
        record["max_points"] = result["max_points"]
        record["confidence"] = result["confidence"]
        record["error_type"] = result["error_type"]
        record["actual_outcome"] = actual
        record["dir_score"] = result["dir_score"]
        record["range_score"] = result["range_score"]
        record["range_status"] = result["range_status"]
        record["scenario_score"] = result.get("scenario_score")
        record["scenario_status"] = result.get("scenario_status")
        record["brier_score"] = result.get("brier_score")
        record["probabilities_valid"] = result.get("probabilities_valid")
        record["actual_bucket"] = result.get("actual_bucket")
        record["reviewed_at"] = datetime.now().isoformat(timespec="minutes")
        record["review_rubric_version"] = RUBRIC_VERSION
        record["review_schema_version"] = SCHEMA_VERSION
        record["actual_data_as_of"] = actual.get("as_of") or actual_date
        record["review_data_source"] = actual.get("source")
        record["review_data_freshness"] = {
            "as_of": actual.get("as_of") or actual_date,
            "target_date": actual_date,
            "status": "fresh" if (actual.get("as_of") or actual_date) == actual_date else "stale",
            "is_stale": (actual.get("as_of") or actual_date) != actual_date,
        }
        reviewed_count += 1
        range_note = (
            f" 区间={result['range_score']}" if result["range_score"] is not None
            else " 区间=N/A"
        )
        print(
            f"   {record.get('id', ''):28s} 预判={result['pred_type']:4s} "
            f"实际={actual['change_pct']:+.2f}% 评分={result['score']}/100 "
            f"覆盖={result['score_coverage']:.2f}{range_note} "
            f"错误={result['error_type']}"
        )

    if not dry_run:
        _write_records(target_file, records)
        print(f"✅ 已写回 {reviewed_count} 条复盘结果"
              f"{f'，迁移旧 accuracy {migrated} 条' if migrated else ''}")
    else:
        print(f"🧪 dry-run：未写回文件（可写回 {reviewed_count} 条）")
    return 0 if errors == 0 else 1


# ── report 命令 ─────────────────────────────────────────────────────────────

def generate_report(by_type: bool = False) -> None:
    """生成历史预判准确率与错误归因报告；未复盘记录单独显示，不静默计入。"""
    files = sorted(DAILY_DIR.glob("*.json"))
    all_records: List[Dict[str, Any]] = []
    corrupt_files: List[str] = []
    migrated = 0
    for f in files:
        try:
            data = _load_records(f)
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            corrupt_files.append(f"{f.name}: {exc}")
            continue
        migrated_here = 0
        for record in data:
            if _migrate_legacy_accuracy(record):
                migrated += 1
                migrated_here += 1
            all_records.append(record)
        if migrated_here:
            # 迁移结果落盘，避免每次 report 重复迁移且不持久化
            try:
                _write_records(f, data)
            except OSError as exc:
                print(f"⚠️  旧 accuracy 迁移写回失败: {f} ({exc})", file=sys.stderr)

    if not all_records:
        print("ℹ️ 暂无历史预判数据")
        if corrupt_files:
            print(f"⚠️  {len(corrupt_files)} 个损坏文件：", file=sys.stderr)
            for item in corrupt_files:
                print(f"   - {item}", file=sys.stderr)
        return

    effective = _latest_records(all_records)
    reviewed = [
        r for r in effective
        if r.get("reviewed") and r.get("review_score") is not None
    ]
    pending = [r for r in effective if not (r.get("reviewed") and r.get("review_score") is not None)]
    average = (
        sum(float(r["review_score"]) for r in reviewed) / len(reviewed)
        if reviewed else 0.0
    )

    print("📈 预判能力分析报告")
    print(f"   有效记录: {len(effective)} 条 | 已复盘: {len(reviewed)} 条 | "
          f"待复盘: {len(pending)} 条 | 综合均分: {average:.1f}/100")
    if migrated:
        print(f"   ℹ️ 本次迁移旧 accuracy 记录: {migrated} 条（口径可能不完全可比）")
    if reviewed:
        rubric_counts = Counter(
            str(r.get("review_rubric_version") or r.get("rubric_version") or "unknown")
            for r in reviewed
        )
        schema_counts = Counter(
            str(r.get("schema_version") or "unknown") for r in reviewed
        )
        print("   评分口径分布（用于避免跨版本直接比较）: "
              + ", ".join(f"{key}:{value}" for key, value in sorted(rubric_counts.items())))
        print("   数据结构版本分布: "
              + ", ".join(f"{key}:{value}" for key, value in sorted(schema_counts.items())))
    if corrupt_files:
        print(f"   ⚠️ 损坏文件 {len(corrupt_files)} 个（详见 stderr）", file=sys.stderr)
        for item in corrupt_files:
            print(f"      - {item}", file=sys.stderr)
    if pending:
        print("   待复盘记录（不参与均分）:")
        for r in pending[:10]:
            print(f"      {r.get('date','')} → {r.get('target_date','?')} "
                  f"{r.get('id', '')} {_record_direction(r)}")

    if by_type:
        type_stats: Dict[str, Dict[str, Any]] = {}
        error_dist: Dict[str, int] = {}

        for r in effective:
            t = _record_direction(r)
            if t not in type_stats:
                type_stats[t] = {"total": 0, "correct": 0, "scores": []}
            type_stats[t]["total"] += 1
            if r.get("review_score") is not None:
                type_stats[t]["scores"].append(float(r["review_score"]))
                if float(r["review_score"]) >= 65:
                    type_stats[t]["correct"] += 1
            err = r.get("error_type")
            if err:
                error_dist[err] = error_dist.get(err, 0) + 1

        print("\n   ── 按预判类型细分 ──")
        for t, stat in type_stats.items():
            cnt = stat["total"]
            succ_rate = (
                stat["correct"] / len(stat["scores"]) * 100
                if stat["scores"] else 0.0
            )
            tag = "← 强项" if succ_rate >= 70 else (
                "← 弱项/需校准" if succ_rate <= 50 and cnt >= 3 else ""
            )
            bar = "█" * int(succ_rate // 10) + "░" * (10 - int(succ_rate // 10))
            print(f"   {t:6s}  {bar}  {cnt:2d}次  方向准确率:{succ_rate:4.1f}% {tag}")

        print("\n   ── 偏差诊断 (Systemic Bias) ──")
        bull = type_stats.get("偏多", {})
        bear = type_stats.get("偏空", {})
        bull_rate = (
            bull.get("correct", 0) / len(bull.get("scores", [1])) * 100
            if bull.get("scores") else 0
        )
        bear_rate = (
            bear.get("correct", 0) / len(bear.get("scores", [1])) * 100
            if bear.get("scores") else 0
        )
        print(f"   偏多预判: {bull.get('total',0)}次 → 命中率 {bull_rate:.1f}%")
        print(f"   偏空预判: {bear.get('total',0)}次 → 命中率 {bear_rate:.1f}%")
        if bear_rate < bull_rate - 15 and bear.get("total", 0) >= 3:
            print("   ⚠️ 警告：检测到【系统性偏悲观】偏差，偏空预判失误率偏高。")
        elif bull_rate < bear_rate - 15 and bull.get("total", 0) >= 3:
            print("   ⚠️ 警告：检测到【系统性偏乐观】偏差，建议提高开仓确认门槛。")


def main() -> None:
    parser = argparse.ArgumentParser(description="预判闭环追踪与错误归因引擎")
    subparsers = parser.add_subparsers(dest="action")

    rec = subparsers.add_parser("record", help="记录预判（追加式）")
    rec.add_argument("--type", default="recap", help="预判类型 (默认 recap)")
    rec.add_argument("--summary", default="", help="预判简述")
    rec.add_argument("--direction", default=None, choices=DIRECTION_LABELS,
                     help="结构化方向标签；缺省时按 base_scenario 关键词分类")
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
    rec.add_argument("--data-source", default="manual", help="数据来源说明")
    rec.add_argument("--rubric-version", default=RUBRIC_VERSION, help="评分口径版本")
    rec.add_argument("--probability-source", default=None,
                     help="概率来源（默认按 60/20/20 标记为 default_60_20_20）")
    rec.add_argument("--date", default=None, help="预判做出日 YYYY-MM-DD")
    rec.add_argument("--target-date", default=None, help="预判所指向的交易日 YYYY-MM-DD")

    lst = subparsers.add_parser("list", help="列出历史预判")
    lst.add_argument("--history", action="store_true",
                     help="包含被 revision 替代的历史版本")

    rev = subparsers.add_parser("review", help="拉取实际行情并写回复盘字段")
    rev.add_argument("--date", default=None, help="要复盘的预判文件日期 YYYY-MM-DD")
    rev.add_argument("--dry-run", action="store_true", help="只计算不写回")

    rep = subparsers.add_parser("report", help="统计报告")
    rep.add_argument("--by-type", action="store_true", help="按预判类型与错误分类统计")

    args = parser.parse_args()

    if args.action == "record":
        record_prediction(args)
    elif args.action == "list":
        list_predictions(show_history=args.history)
    elif args.action == "review":
        raise SystemExit(review_predictions(args.date, args.dry_run))
    elif args.action == "report":
        generate_report(by_type=args.by_type)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
