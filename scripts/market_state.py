#!/usr/bin/env python3
"""
市场状态机与仲裁总线 (Market State Machine & Arbitration Bus)

负责识别当前市场宏观状态（震荡 / 恐慌下行 / 右侧反弹 / 未知），
并动态仲裁各专家子系统（Serenity产业链 vs mi-skill资金风控）的决策权重与硬性规则。

设计约束：
  - 数据不足时返回 UNKNOWN，绝不默认 NORMAL_OSCILLATION。
  - 每个状态输出 as_of / data_completeness / stale / missing_inputs。
  - REBOUND_RIGHT_SIDE 的判定必须真正读取指数 K 线（above_ma20），
    不允许出现“永远不可达”的状态。
  - 恐慌企稳三条件由 check_stabilization() 真实计算，而不是只写字符串。
"""
from typing import Dict, List, Optional, Any
from enum import Enum
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import date, datetime
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


class MarketRegime(str, Enum):
    NORMAL_OSCILLATION = "NORMAL_OSCILLATION"  # 正常/震荡市
    PANIC_DOWNTREND    = "PANIC_DOWNTREND"     # 恐慌/趋势下行市
    REBOUND_RIGHT_SIDE = "REBOUND_RIGHT_SIDE"  # 反弹/右侧启动市
    UNKNOWN            = "UNKNOWN"             # 数据不足/过期，禁止默认正常


try:
    from trading_calendar import freshness as calendar_freshness
except ImportError:  # 日历模块缺失时退回日历日近似，并标记 unavailable
    calendar_freshness = None

try:
    from market_data_adapter import fetch_tencent_klines
except ImportError:  # 适配器缺失时下面会显式返回空 K 线
    fetch_tencent_klines = None

try:
    from scoring_config import REGIME_WEIGHTS
except ImportError:  # 配置缺失时的兜底，正常路径只引用单一事实源
    REGIME_WEIGHTS = {
        "NORMAL_OSCILLATION": {"technical": 0.40, "industry": 0.40, "catalyst": 0.20},
        "PANIC_DOWNTREND":    {"technical": 0.60, "industry": 0.20, "catalyst": 0.20},
        "REBOUND_RIGHT_SIDE": {"technical": 0.35, "industry": 0.40, "catalyst": 0.25},
        "UNKNOWN":            {"technical": 0.40, "industry": 0.40, "catalyst": 0.20},
    }

REGIME_TONES = {
    MarketRegime.NORMAL_OSCILLATION.value:
        "正常/震荡轮动模式：指数在安全区间，执行标准三框架选股，看长做短，量价配合。",
    MarketRegime.PANIC_DOWNTREND.value:
        "恐慌/趋势下行模式：以守为主，严禁盲目开仓抄底，优先评估持仓相对强度，严防两头挨打。",
    MarketRegime.REBOUND_RIGHT_SIDE.value:
        "反弹/右侧启动模式：右侧信号初显，优先顺势加仓持仓相对强势股，精选卡脖子主线。",
    MarketRegime.UNKNOWN.value:
        "数据不足模式：市场状态无法可靠判定，只输出观察与数据缺口，不输出方向性开仓结论。",
}


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _index_change(index: Optional[Dict[str, Any]]) -> Optional[float]:
    if not index:
        return None
    return _to_float(index.get("change_pct"))


def _above_ma20(klines: Optional[List[Dict[str, Any]]]) -> Optional[bool]:
    if not klines or len(klines) < 20:
        return None
    closes = [_to_float(k.get("close")) for k in klines]
    if any(c is None for c in closes[-20:]):
        return None
    ma20 = sum(closes[-20:]) / 20.0
    return closes[-1] >= ma20


def _volume_metrics(klines: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    """计算量能指标；数据不足返回 None，不伪造 1.0。"""
    if not klines or len(klines) < 21:
        return {
            "volume_ratio": None,
            "consecutive_volume_down": None,
            "volume_window_days": len(klines or []),
        }
    closes = [_to_float(k.get("close")) for k in klines]
    opens = [_to_float(k.get("open", k.get("close"))) for k in klines]
    volumes = [_to_float(k.get("volume", 0)) for k in klines]
    if any(v is None for v in volumes[-21:]) or any(c is None for c in closes[-3:]):
        return {
            "volume_ratio": None,
            "consecutive_volume_down": None,
            "volume_window_days": len(klines),
        }

    avg20 = sum(volumes[-21:-1]) / 20.0
    volume_ratio = volumes[-1] / avg20 if avg20 > 0 else None

    # 连续放量长阴：最近两日收盘低于前收，且两日量能均 > 20日均量 * 1.3
    consecutive = False
    if len(klines) >= 3 and avg20 > 0:
        down_today = closes[-1] < closes[-2]
        down_prev = closes[-2] < closes[-3]
        vol_expand_today = volumes[-1] > avg20 * 1.3
        vol_expand_prev = volumes[-2] > avg20 * 1.3
        body_today = opens[-1] is not None and opens[-1] > 0 and (
            (opens[-1] - closes[-1]) / opens[-1] > 0.01
        )
        consecutive = bool(
            down_today and down_prev and vol_expand_today
            and vol_expand_prev and body_today
        )

    return {
        "volume_ratio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "consecutive_volume_down": consecutive,
        "volume_window_days": len(klines),
    }


def _breadth_metrics(breadth: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not breadth:
        return {"status": "missing", "reason": "未提供市场宽度数据"}
    return {
        "status": breadth.get("status", "ok"),
        "reason": breadth.get("reason"),
        "advance_ratio": breadth.get("advance_ratio"),
        "decline_count": breadth.get("decline_count"),
        "advance_count": breadth.get("advance_count"),
        "flat_count": breadth.get("flat_count"),
        "limit_up": breadth.get("limit_up"),
        "limit_down": breadth.get("limit_down"),
        "failed_limit_up_rate": breadth.get("failed_limit_up_rate"),
        "median_change_pct": breadth.get("median_change_pct"),
        "total_amount_yi": breadth.get("total_amount_yi"),
        "sentiment_score": breadth.get("sentiment_score"),
        "sentiment_tier": breadth.get("sentiment_tier"),
        "sample_size": breadth.get("sample_size"),
        "formula_version": breadth.get("formula_version"),
    }


def evaluate_market_state(
    sh_index: Optional[Dict[str, Any]] = None,
    cy_index: Optional[Dict[str, Any]] = None,
    candidate_summary: Optional[Dict[str, Any]] = None,
    klines_sh: Optional[List[Dict[str, Any]]] = None,
    breadth: Optional[Dict[str, Any]] = None,
    as_of: Optional[str] = None,
    target_date: Optional[str] = None,
    stale: Optional[bool] = None,
    freshness: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    状态识别核心算法：

    1. 恐慌 PANIC_DOWNTREND：
       - 创业板跌幅 <= -5.0%
       - 或 上证跌幅 <= -2.0% 且 候选池收红比例 <= 20%
       - 或 连续两日放量长阴
       - 或 市场宽度极弱（上涨家数占比 <= 20% 且 跌停家数 >= 30）
    2. 反弹 REBOUND_RIGHT_SIDE：
       - 指数仍在 MA20 下方（右侧启动初期）
       - 且 上证 >= +1.5% 或 创业板 >= +2.5%
       - 且 候选池收红比例 >= 70%
    3. 其余为 NORMAL_OSCILLATION。
    4. 关键输入缺失时 UNKNOWN；不确定性不会被当成正常。
    """
    sh_chg = _index_change(sh_index)
    cy_chg = _index_change(cy_index)

    candidate_red_ratio: Optional[float] = None
    if candidate_summary and candidate_summary.get("total", 0) > 0:
        total = _to_float(candidate_summary.get("total"))
        up_count = _to_float(candidate_summary.get("up_count"))
        if total and total > 0 and up_count is not None:
            candidate_red_ratio = up_count / total

    above_ma20 = _above_ma20(klines_sh)
    vol_metrics = _volume_metrics(klines_sh)
    breadth_metrics = _breadth_metrics(breadth)
    if breadth_metrics.get("status") == "ok" and candidate_red_ratio is None:
        ratio = _to_float(breadth.get("advance_ratio"))
        if ratio is not None:
            candidate_red_ratio = ratio

    missing_inputs: List[str] = []
    if sh_chg is None:
        missing_inputs.append("sh_index.change_pct")
    if cy_chg is None:
        missing_inputs.append("cy_index.change_pct")
    if candidate_red_ratio is None:
        missing_inputs.append("candidate_summary.up_ratio")
    if above_ma20 is None:
        missing_inputs.append("klines_sh(>=20)")
    if vol_metrics.get("volume_ratio") is None:
        missing_inputs.append("klines_sh.volume")

    # 完整度：关键输入各占权重，宽度为加分项
    completeness = 0.0
    completeness += 0.20 if sh_chg is not None else 0.0
    completeness += 0.20 if cy_chg is not None else 0.0
    completeness += 0.20 if candidate_red_ratio is not None else 0.0
    completeness += 0.25 if above_ma20 is not None else 0.0
    completeness += 0.10 if vol_metrics.get("volume_ratio") is not None else 0.0
    completeness += 0.05 if breadth_metrics.get("status") == "ok" else 0.0
    completeness = round(completeness, 2)

    required_missing = {
        "sh_index.change_pct", "cy_index.change_pct",
        "candidate_summary.up_ratio", "klines_sh(>=20)",
    }
    regime_reasons: List[str] = []
    panic_reasons: List[str] = []
    rebound_reasons: List[str] = []
    if required_missing.intersection(missing_inputs):
        regime = MarketRegime.UNKNOWN
        regime_reasons = ["关键输入缺失，无法可靠判定市场状态"]
    else:
        panic = False
        if cy_chg is not None and cy_chg <= -5.0:
            panic = True
            panic_reasons.append("创业板单日跌幅<=-5.0%")
        if sh_chg is not None and sh_chg <= -2.0 and candidate_red_ratio is not None and candidate_red_ratio <= 0.20:
            panic = True
            panic_reasons.append("上证跌幅<=-2.0%且候选池收红比例<=20%")
        if vol_metrics.get("consecutive_volume_down"):
            panic = True
            panic_reasons.append("连续两日放量长阴")
        if breadth_metrics.get("status") in ("ok", "degraded"):
            adv = _to_float(breadth.get("advance_ratio"))
            limit_down = _to_float(breadth.get("limit_down"))
            sentiment = _to_float(breadth.get("sentiment_score"))
            if adv is not None and adv <= 0.20 and limit_down is not None and limit_down >= 30:
                panic = True
                panic_reasons.append("市场宽度极弱（上涨占比<=20%且跌停>=30家）")
            elif sentiment is not None and sentiment <= 20 and adv is not None and adv <= 0.25:
                panic = True
                panic_reasons.append(
                    f"情绪冰点（sentiment={sentiment:.1f}，上涨占比={adv*100:.1f}%）"
                )

        rebound = False
        if above_ma20 is False:
            if ((sh_chg is not None and sh_chg >= 1.5)
                    or (cy_chg is not None and cy_chg >= 2.5)):
                if candidate_red_ratio is not None and candidate_red_ratio >= 0.70:
                    rebound = True
                    rebound_reasons.append("指数仍在MA20下方但单日强势且候选池普涨")

        if panic:
            regime = MarketRegime.PANIC_DOWNTREND
            regime_reasons = panic_reasons
        elif rebound:
            regime = MarketRegime.REBOUND_RIGHT_SIDE
            regime_reasons = rebound_reasons
        else:
            regime = MarketRegime.NORMAL_OSCILLATION
            regime_reasons = ["关键输入齐全且未触发恐慌/反弹阈值"]

    # 状态附加信息
    arbitration_rules = _arbitration_rules(regime)
    macro_metrics = {
        "sh_change_pct": sh_chg,
        "cy_change_pct": cy_chg,
        "candidate_red_ratio": round(candidate_red_ratio * 100, 1) if candidate_red_ratio is not None else None,
        "above_ma20": above_ma20,
        "volume_ratio": vol_metrics.get("volume_ratio"),
        "consecutive_volume_down": vol_metrics.get("consecutive_volume_down"),
        "breadth": breadth_metrics,
    }

    as_of_value = as_of or target_date
    freshness_supplied = freshness is not None
    as_of_supplied = as_of is not None
    if freshness is None and calendar_freshness:
        freshness = calendar_freshness(
            as_of_value, target_date or date.today().isoformat(),
            allow_previous=True,
        )
    if freshness is None:
        freshness = {
            "status": "unknown",
            "is_stale": True,
            "as_of": as_of_value,
            "target_date": target_date,
            "data_age_days": None,
            "data_age_trading_days": None,
            "calendar_version": "unavailable",
        }
    stale_value = stale
    if stale_value is None:
        stale_value = freshness.get("is_stale", True)

    # I3：只有调用方显式提供 as_of/freshness 时才据此降级，避免
    # 单元测试里“没有日期但输入齐全”的纯函数调用被误判为过期。
    freshness_informative = bool(as_of_supplied or freshness_supplied)
    if regime != MarketRegime.UNKNOWN and freshness_informative and stale_value:
        regime = MarketRegime.UNKNOWN
        regime_reasons = [
            "数据日期与目标交易日不匹配（stale/future/missing），降级为 UNKNOWN"
        ]
        arbitration_rules = _arbitration_rules(regime)

    return {
        "regime": regime.value,
        "strategy_tone": REGIME_TONES[regime.value],
        "weights": REGIME_WEIGHTS[regime.value],
        "arbitration_rules": arbitration_rules,
        "regime_reasons": regime_reasons,
        "as_of": as_of_value,
        "data_completeness": completeness,
        "data_age_days": freshness.get("data_age_days"),
        "data_age_trading_days": freshness.get("data_age_trading_days"),
        "stale": bool(stale_value) if stale_value is not None else True,
        "freshness": freshness,
        "missing_inputs": missing_inputs,
        "macro_metrics": macro_metrics,
        "breadth": breadth_metrics,
    }


def _arbitration_rules(regime: MarketRegime) -> List[str]:
    if regime == MarketRegime.PANIC_DOWNTREND:
        return [
            "一票否决：未满足【缩量+不创新低+站上5日线】企稳三条件的开仓信号一律作废",
            "风控接管：长线价值与产业估值降权，防守与相对强度指标成为第一裁判",
            "持仓守则：持仓若相对抗跌（排名前1/3），守仓优于割肉换股",
        ]
    if regime == MarketRegime.REBOUND_RIGHT_SIDE:
        return [
            "顺势跟随：主力大单净流入领先的板块享有优先推荐权",
            "相对强度优先：优先加仓持仓中相对强势的个股",
            "聚焦龙头：重点配置卡脖子瓶颈层龙头，回避跟风杂毛",
        ]
    if regime == MarketRegime.UNKNOWN:
        return [
            "数据不足：不输出方向性开仓结论，只输出观察项与数据缺口",
            "禁止把缺失数据当作正常震荡处理",
        ]
    return [
        "产业准入、技术择时：产业逻辑决定是否进入观察池，技术形态决定买点",
        "双体系融合：产业链价值与资金流形态均衡决策",
    ]


def check_stabilization(
    klines: Optional[List[Dict[str, Any]]],
    panic_low: Optional[float] = None,
    panic_volume: Optional[float] = None,
    lookback: int = 20,
) -> Dict[str, Any]:
    """
    恐慌后企稳三条件（真实计算）：
      1. 缩量：最近两日均量 <= 恐慌日成交量 * 50%
      2. 不创新低：最近两日最低价均高于恐慌日最低价
      3. 站上5日线：最新收盘价 >= MA5

    未显式提供 panic_low/panic_volume 时，用近 lookback 日的最低点日作为恐慌日。
    数据不足时 passed=False，并给出 missing_inputs，不做“默认企稳”。
    """
    if not klines or len(klines) < 6:
        return {
            "passed": False,
            "status": "abstain",
            "conditions": {},
            "missing_inputs": ["klines(>=6)"],
            "reasons": ["K线不足，无法计算企稳三条件"],
        }

    closes = [_to_float(k.get("close")) for k in klines]
    lows = [_to_float(k.get("low", k.get("close"))) for k in klines]
    volumes = [_to_float(k.get("volume", 0)) for k in klines]
    if any(v is None for v in closes[-6:]) or any(v is None for v in lows[-6:]) or any(v is None for v in volumes[-6:]):
        return {
            "passed": False,
            "status": "abstain",
            "conditions": {},
            "missing_inputs": ["klines.close/low/volume"],
            "reasons": ["K线存在非数值字段，无法计算企稳三条件"],
        }

    window = klines[-min(lookback, len(klines)):]
    window_lows = [_to_float(k.get("low", k.get("close"))) for k in window]
    window_vols = [_to_float(k.get("volume", 0)) for k in window]
    if any(v is None for v in window_lows) or any(v is None for v in window_vols):
        return {
            "passed": False,
            "status": "abstain",
            "conditions": {},
            "missing_inputs": ["klines.lookback"],
            "reasons": ["回看窗口存在非数值字段，无法计算企稳三条件"],
        }
    panic_index = min(range(len(window_lows)), key=lambda i: window_lows[i])
    if panic_low is None:
        panic_low = window_lows[panic_index]
    if panic_volume is None:
        panic_volume = window_vols[panic_index]

    recent_vol = (volumes[-1] + volumes[-2]) / 2.0
    shrink = panic_volume > 0 and recent_vol <= panic_volume * 0.5
    no_new_low = lows[-1] > panic_low and lows[-2] > panic_low
    ma5 = sum(closes[-5:]) / 5.0
    above_ma5 = closes[-1] >= ma5

    conditions = {
        "volume_shrink": {
            "passed": bool(shrink),
            "recent_avg_volume": round(recent_vol, 2),
            "panic_volume": round(panic_volume, 2),
            "required_ratio": 0.5,
            "actual_ratio": round(recent_vol / panic_volume, 3) if panic_volume > 0 else None,
        },
        "no_new_low": {
            "passed": bool(no_new_low),
            "panic_low": round(panic_low, 2),
            "recent_lows": [round(lows[-2], 2), round(lows[-1], 2)],
        },
        "above_ma5": {
            "passed": bool(above_ma5),
            "close": round(closes[-1], 2),
            "ma5": round(ma5, 2),
        },
    }
    reasons = [
        name for name, detail in conditions.items() if not detail["passed"]
    ]
    passed = all(detail["passed"] for detail in conditions.values())
    return {
        "passed": passed,
        "status": "ok" if passed else "not_confirmed",
        "conditions": conditions,
        "missing_inputs": [],
        "reasons": reasons,
        "as_of": klines[-1].get("date"),
    }


def _fetch_index_klines(code_full: str = "sh000001", days: int = 120,
                        end_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """尽力获取指数日K；解析统一走 market_data_adapter，失败返回空列表。

    `end_date` 用于防止把目标日之后的 K 线算进状态；不传则使用最新。
    """
    if fetch_tencent_klines is None:
        return []
    rows = fetch_tencent_klines(code_full, limit=days, period="day")
    if end_date:
        rows = [row for row in rows if str(row.get("date", ""))[:10] <= end_date]
    return rows


def _latest_file(directory: Path, target_date: Optional[str] = None) -> Optional[Path]:
    if not directory.exists():
        return None
    files = sorted(directory.glob("*.json"))
    if not files:
        return None
    if target_date:
        eligible = [
            f for f in files
            if f.stem <= target_date and not f.stem.startswith("refresh_state")
        ]
        return eligible[-1] if eligible else None
    return files[-1]


def load_latest_market_state(
    target_date: Optional[str] = None,
    klines_sh: Optional[List[Dict[str, Any]]] = None,
    candidate_summary: Optional[Dict[str, Any]] = None,
    breadth: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    从本地 market_snapshots / candidates 缓存加载并推断最新市场状态。

    - 只读取日期 <= target_date 的快照，并校验日期；不匹配时 stale=True。
    - 不再复用上一次候选文件里的“广度”；调用方应传入当日行情计算的
      candidate_summary。未传入时才回退到最近候选文件并标记 stale。
    - 指数 K 线缺失时尝试拉取；仍缺失则 UNKNOWN。
    """
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    snapshots_dir = hermes_home / "data" / "market_snapshots"
    candidates_dir = hermes_home / "data" / "candidates"
    target = target_date or date.today().isoformat()

    sh_idx = None
    cy_idx = None
    as_of = None
    snap_stale = True
    candidate_stale = False
    kline_freshness: Optional[Dict[str, Any]] = None
    snap_file = _latest_file(snapshots_dir, target)
    if snap_file:
        try:
            with open(snap_file, "r", encoding="utf-8") as f:
                snap_data = json.load(f)
            indices = snap_data.get("indices", {})
            sh_idx = indices.get("上证指数") or indices.get("sh000001")
            cy_idx = indices.get("创业板指") or indices.get("sz399006")
            as_of = snap_data.get("date") or snap_file.stem
            snap_stale = str(as_of)[:10] != target
            if breadth is None:
                snapshot_breadth = snap_data.get("breadth")
                if isinstance(snapshot_breadth, dict):
                    breadth = snapshot_breadth
        except (OSError, json.JSONDecodeError):
            sh_idx = None
            cy_idx = None
            as_of = None

    if candidate_summary is None:
        cand_file = _latest_file(candidates_dir, target)
        if cand_file:
            try:
                with open(cand_file, "r", encoding="utf-8") as f:
                    cand_data = json.load(f)
                cand_list = cand_data.get("candidates", [])
                up_count = sum(
                    1 for c in cand_list
                    if _to_float(c.get("change_pct")) is not None
                    and _to_float(c.get("change_pct")) > 0
                )
                candidate_as_of = cand_data.get("date") or cand_file.stem
                candidate_summary = {
                    "total": len(cand_list),
                    "up_count": up_count,
                    "passed": cand_data.get("passed", len(cand_list)),
                    "as_of": candidate_as_of,
                }
                # 回退读取的候选广度如果早于上一交易日，视为过期，不能
                # 与当日指数拼成一个“当日状态”。
                if calendar_freshness:
                    cand_fresh = calendar_freshness(
                        candidate_as_of, target, allow_previous=True
                    )
                    candidate_stale = bool(cand_fresh.get("is_stale"))
                else:
                    candidate_stale = str(candidate_as_of)[:10] != target
            except (OSError, json.JSONDecodeError):
                candidate_summary = None

    if klines_sh is None:
        klines_sh = _fetch_index_klines("sh000001", end_date=target)
    if klines_sh and calendar_freshness:
        kline_freshness = calendar_freshness(
            klines_sh[-1].get("date"), target, allow_previous=True
        )

    freshness_info = (
        calendar_freshness(as_of, target, allow_previous=True)
        if calendar_freshness else None
    )
    if freshness_info is not None:
        snap_stale = freshness_info.get("is_stale", snap_stale)
    if kline_freshness is not None and kline_freshness.get("is_stale"):
        snap_stale = True

    state = evaluate_market_state(
        sh_index=sh_idx,
        cy_index=cy_idx,
        candidate_summary=candidate_summary,
        klines_sh=klines_sh,
        breadth=breadth,
        as_of=as_of,
        target_date=target,
        stale=bool(snap_stale or candidate_stale),
        freshness=freshness_info,
    )
    state["kline_freshness"] = kline_freshness
    state["candidate_freshness"] = {
        "as_of": (candidate_summary or {}).get("as_of"),
        "stale": candidate_stale,
    }
    return state


if __name__ == "__main__":
    state = load_latest_market_state()
    print(json.dumps(state, ensure_ascii=False, indent=2))
