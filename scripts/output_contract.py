#!/usr/bin/env python3
"""
操作层输出契约 (Execution Output Contract)

目的（A2）：
  候选扫描此前只输出分数/评级/信号，用户仍需自行补入场、止损、目标与仓位。
  本模块把研究评级与交易指令分开，并输出统一 schema：

    entry_zone / stop / target_1 / target_2 / position_size /
    invalidation / valid_until / confidence / research_rating / trade_action

推导依据（全部来自确定性数据，不调用 LLM）：
  - 入场区间：现价、近 20 日低点、ATR
  - 止损：入场区下沿、近 20 日低点、MA20、ATR 上限
  - 目标：近 20 日高点与 ATR 倍数
  - 仓位：单笔风险预算 / 止损距离，再受组合层权重、市场状态与置信度约束

重要：`research_rating` 只表示研究排序结论；`trade_action` 才是“是否可执行”。
非可排名、恐慌未企稳、方向淘汰、数据过期时，trade_action=NO_TRADE/OBSERVE，
position_size=0，不输出买卖指令。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List, Optional

try:
    from trading_calendar import next_trading_day as calendar_next_trading_day
except ImportError:  # 日历缺失时只做日历日顺延，并保留 valid_until_basis 标记
    calendar_next_trading_day = None

OUTPUT_CONTRACT_VERSION = "1.0.0"
RISK_BUDGET_PCT = 0.5          # 单笔最大组合风险 0.5%
MAX_SINGLE_POSITION = 0.15     # 单票上限 15%
MIN_POSITION = 0.005           # 低于 0.5% 视为不建仓
REGIME_POSITION_MULTIPLIER = {
    "NORMAL_OSCILLATION": 1.0,
    "REBOUND_RIGHT_SIDE": 0.9,
    "PANIC_DOWNTREND": 0.3,
    "UNKNOWN": 0.0,
}
CONFIDENCE_MULTIPLIER = {"high": 1.0, "medium": 0.75, "low": 0.4}
RESEARCH_BUY_LABELS = ("STRONG_BUY_WATCH", "ACCUMULATE_ON_DIP")
NO_TRADE_LABELS = ("AVOID_DEFENSE", "DATA_DEGRADED", "ABSTAIN_NO_DATA")


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _atr_pct(klines: Optional[List[Dict[str, Any]]], period: int = 14) -> Optional[float]:
    if not klines or len(klines) < 2:
        return None
    ranges: List[float] = []
    previous_close: Optional[float] = None
    for row in klines:
        high = _to_float(row.get("high"))
        low = _to_float(row.get("low"))
        close = _to_float(row.get("close"))
        if high is None or low is None or close is None or close <= 0:
            previous_close = close
            continue
        if previous_close is None:
            true_range = high - low
        else:
            true_range = max(high - low, abs(high - previous_close),
                             abs(low - previous_close))
        ranges.append(max(0.0, true_range))
        previous_close = close
    if not ranges:
        return None
    window = ranges[-min(period, len(ranges)):]
    atr = sum(window) / len(window)
    last_close = _to_float(klines[-1].get("close"))
    if not last_close or last_close <= 0:
        return None
    return atr / last_close * 100.0


def _ma(klines: Optional[List[Dict[str, Any]]], period: int) -> Optional[float]:
    if not klines or len(klines) < period:
        return None
    closes = [_to_float(row.get("close")) for row in klines[-period:]]
    if any(value is None for value in closes):
        return None
    return sum(closes) / period  # type: ignore[arg-type]


def _recent_extreme(klines: Optional[List[Dict[str, Any]]], key: str,
                    lookback: int = 20) -> Optional[float]:
    if not klines:
        return None
    values = [_to_float(row.get(key)) for row in klines[-lookback:]]
    values = [value for value in values if value is not None]
    if not values:
        return None
    return max(values) if key == "high" else min(values)


def _valid_until(as_of: str, valid_days: int) -> Dict[str, Any]:
    if calendar_next_trading_day:
        result = calendar_next_trading_day(as_of, n=valid_days)
        if result:
            return {"valid_until": result, "valid_until_basis": "trading_calendar"}
    try:
        base = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        base = date.today()
    target = base + timedelta(days=valid_days)
    while target.weekday() >= 5:
        target += timedelta(days=1)
    return {
        "valid_until": target.isoformat(),
        "valid_until_basis": "weekday_approximation",
    }


def _trade_action(candidate: Dict[str, Any], regime: str,
                  trade_allowed: bool, price: float,
                  entry_low: float, entry_high: float,
                  trade_action_override: Optional[str]) -> str:
    if trade_action_override:
        return trade_action_override
    label = candidate.get("action_label")
    if not trade_allowed or not candidate.get("rankable", False):
        return "NO_TRADE"
    if regime == "UNKNOWN":
        return "NO_TRADE"
    if regime == "PANIC_DOWNTREND":
        return "OBSERVE"
    if label in NO_TRADE_LABELS or str(label or "").startswith("OBSERVE"):
        return "NO_TRADE"
    if label in RESEARCH_BUY_LABELS:
        relative_strength = _to_float(candidate.get("relative_strength_pct"))
        if regime == "REBOUND_RIGHT_SIDE" and (
            relative_strength is None or relative_strength < 66
        ):
            return "WAIT_CONFIRMATION"
        if price > entry_high:
            return "WAIT_ENTRY"
        if price < entry_low:
            return "WAIT_ENTRY"
        return "BUY_ZONE"
    return "WAIT_ENTRY"


def derive_trade_plan(candidate: Dict[str, Any],
                      klines: Optional[List[Dict[str, Any]]] = None,
                      market_regime: str = "NORMAL_OSCILLATION",
                      trade_allowed: bool = True,
                      position_weight: Optional[float] = None,
                      valid_days: int = 5,
                      trade_action_override: Optional[str] = None,
                      as_of: Optional[str] = None) -> Dict[str, Any]:
    """由候选、K线、市场状态与组合权重推导统一操作契约。"""
    last_close = None
    if klines:
        last_close = _to_float(klines[-1].get("close"))
    price = _to_float(candidate.get("price")) or last_close
    if price is None or price <= 0:
        price = 0.0

    data_date = (
        str(as_of or candidate.get("as_of") or
            (klines[-1].get("date") if klines else date.today().isoformat()))[:10]
    )
    atr = _atr_pct(klines)
    atr_value = _clamp(atr if atr is not None else 3.0, 1.0, 12.0)
    recent_low = _recent_extreme(klines, "low")
    recent_high = _recent_extreme(klines, "high")
    ma20 = _ma(klines, 20)

    if price > 0:
        entry_low = max(
            (recent_low * 0.995) if recent_low else price * 0.98,
            price * (1 - atr_value / 100.0 * 0.6),
        )
        if entry_low >= price:
            entry_low = price * 0.99
        entry_high = min(
            price * 1.01,
            entry_low * (1 + atr_value / 100.0 * 0.5),
        )
        if entry_high <= entry_low:
            entry_high = entry_low * 1.005
    else:
        entry_low = entry_high = 0.0

    entry_mid = (entry_low + entry_high) / 2.0
    stop_candidates = [entry_low * (1 - atr_value / 100.0)]
    if recent_low:
        stop_candidates.append(recent_low * 0.985)
    if ma20:
        stop_candidates.append(ma20 * 0.97)
    stop = min(stop_candidates) if entry_low > 0 else 0.0
    if entry_low > 0:
        stop = max(stop, entry_low * 0.85)
        if stop >= entry_low:
            stop = entry_low * 0.95
    risk_pct = (
        (entry_mid - stop) / entry_mid * 100.0
        if entry_mid > 0 and stop > 0 else None
    )

    target_1 = 0.0
    target_2 = 0.0
    risk_reward = None
    if entry_high > 0:
        target_1 = max(
            entry_high * (1 + atr_value / 100.0 * 1.2),
            recent_high if recent_high else entry_high * 1.05,
        )
        target_1 = min(target_1, entry_mid * (1 + atr_value / 100.0 * 4.0))
        # 至少保证 1.2R，否则“目标价”无法覆盖止损风险，不应建仓。
        if stop > 0:
            target_1 = max(target_1, entry_mid + 1.2 * (entry_mid - stop))
        if target_1 <= entry_high:
            target_1 = entry_high * 1.03
        target_2 = max(
            target_1 * (1 + atr_value / 100.0 * 1.2),
            entry_mid * (1 + atr_value / 100.0 * 2.5),
        )
        # 若 1.2R 目标仍低于 target_1，保证 target_2 不低于 target_1 + 0.3R。
        if stop > 0:
            target_2 = max(target_2, target_1 + 0.3 * (entry_mid - stop))
        if risk_pct and risk_pct > 0:
            risk_reward = round(
                (target_1 - entry_mid) / (entry_mid - stop), 2
            )

    confidence = candidate.get("confidence")
    if confidence not in ("high", "medium", "low"):
        if candidate.get("scoring_status") == "ok" and candidate.get("rankable"):
            confidence = "high"
        elif candidate.get("rankable"):
            confidence = "medium"
        else:
            confidence = "low"

    action = _trade_action(
        candidate, market_regime, trade_allowed, price,
        entry_low, entry_high, trade_action_override,
    )

    position_size = 0.0
    if action in ("BUY_ZONE", "WAIT_ENTRY") and risk_pct and risk_pct > 0:
        risk_based = (RISK_BUDGET_PCT / risk_pct)
        position_size = min(risk_based, MAX_SINGLE_POSITION)
        position_size *= REGIME_POSITION_MULTIPLIER.get(market_regime, 0.0)
        position_size *= CONFIDENCE_MULTIPLIER.get(confidence, 0.4)
        if position_weight is not None:
            position_size = min(position_size, max(0.0, float(position_weight)))
    if position_size < MIN_POSITION:
        position_size = 0.0
        if action in ("BUY_ZONE", "WAIT_ENTRY"):
            action = "NO_TRADE"
    position_size = round(_clamp(position_size, 0.0, 1.0), 4)

    invalidation: List[str] = []
    if stop > 0:
        invalidation.append(f"收盘价跌破止损 {stop:.2f}")
    if entry_high > 0:
        invalidation.append(f"有效期内未站稳入场区上沿 {entry_high:.2f}")
    if candidate.get("direction_veto"):
        invalidation.append("所属方向内均线全部空头排列，方向级淘汰")
    if market_regime == "PANIC_DOWNTREND":
        invalidation.append("恐慌企稳三条件未通过或市场再度放量下跌")
    if market_regime == "UNKNOWN":
        invalidation.append("市场状态无法判定（数据缺失/过期）")
    if candidate.get("data_freshness", {}).get("is_stale"):
        invalidation.append("行情/K线数据已过期")
    stabilization = candidate.get("stabilization") or {}
    if stabilization and not stabilization.get("passed", True):
        invalidation.append("企稳三条件未通过")
    if candidate.get("scoring_status") in ("degraded", "abstain"):
        invalidation.append("评分数据降级/弃权，研究结论不可执行")
    if not invalidation:
        invalidation.append("核心假设被新信息证伪")
    if action in ("NO_TRADE", "OBSERVE") and not trade_allowed:
        invalidation.insert(0, "未通过可执行性校验（降级/否决/组合约束）")

    valid = _valid_until(data_date, valid_days)
    plan = {
        "schema_version": OUTPUT_CONTRACT_VERSION,
        "as_of": data_date,
        "entry_zone": [round(entry_low, 2), round(entry_high, 2)],
        "stop": round(stop, 2) if stop else None,
        "target_1": round(target_1, 2) if target_1 else None,
        "target_2": round(target_2, 2) if target_2 else None,
        "position_size": position_size,
        "position_size_pct": round(position_size * 100, 1),
        "risk_pct": round(risk_pct, 2) if risk_pct is not None else None,
        "risk_reward": risk_reward,
        "invalidation": invalidation,
        **valid,
        "confidence": confidence,
        "research_rating": candidate.get("action_label"),
        "trade_action": action,
        "atr_pct": round(atr_value, 2),
        "derivation": {
            "recent_low": round(recent_low, 2) if recent_low else None,
            "recent_high": round(recent_high, 2) if recent_high else None,
            "ma20": round(ma20, 2) if ma20 else None,
            "risk_budget_pct": RISK_BUDGET_PCT,
            "max_single_position": MAX_SINGLE_POSITION,
            "regime_multiplier": REGIME_POSITION_MULTIPLIER.get(market_regime),
            "position_weight_cap": position_weight,
        },
    }
    return plan


def validate_trade_plan(plan: Dict[str, Any]) -> List[str]:
    """校验操作契约的内部一致性，返回错误列表（空列表表示通过）。"""
    errors: List[str] = []
    entry = plan.get("entry_zone") or []
    if not isinstance(entry, list) or len(entry) != 2:
        return ["entry_zone 必须是 [low, high]"]
    low, high = entry
    stop = plan.get("stop")
    target_1 = plan.get("target_1")
    target_2 = plan.get("target_2")
    position = plan.get("position_size")

    if position is None or not (0 <= float(position) <= 1):
        errors.append("position_size 必须在 0-1 之间")
    if float(position or 0) > 0:
        if stop is None or target_1 is None or target_2 is None:
            errors.append("可执行计划必须包含 stop/target_1/target_2")
        else:
            if not (float(stop) < float(low) <= float(high) < float(target_1)):
                errors.append("价格几何关系必须满足 stop < entry_low <= entry_high < target_1")
            if not (float(target_2) > float(target_1)):
                errors.append("target_2 必须高于 target_1")
        risk_reward = plan.get("risk_reward")
        if risk_reward is not None and float(risk_reward) < 1.0:
            errors.append("风险收益比低于 1.0，不应建仓")
    if plan.get("trade_action") in ("NO_TRADE", "OBSERVE") and float(position or 0) > 0:
        errors.append("NO_TRADE/OBSERVE 的 position_size 必须为 0")
    if not plan.get("invalidation"):
        errors.append("必须给出失效条件")
    if not plan.get("valid_until"):
        errors.append("必须给出有效期")
    return errors


def attach_operation_contract(candidate: Dict[str, Any],
                              plan: Dict[str, Any]) -> Dict[str, Any]:
    """把操作契约同时写到 candidate["operation"] 与顶层字段，便于下游兼容。"""
    candidate["operation"] = plan
    for key in (
        "entry_zone", "stop", "target_1", "target_2", "position_size",
        "position_size_pct", "invalidation", "valid_until", "confidence",
        "research_rating", "trade_action", "risk_reward",
    ):
        candidate[key] = plan.get(key)
    return candidate
