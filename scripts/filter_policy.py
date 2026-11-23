#!/usr/bin/env python3
"""
硬过滤策略 (Filter Policy)

目的（I5）：
  - 把散落在脚本文本、SKILL、刷新文档中的价格/位置阈值收敛为一份可配置策略。
  - 观察池（watchlist）与交易池（trade）使用不同上限；按市场状态覆盖。
  - 区分“硬排除”和“排序惩罚”：高价值龙头不再被机械剔除，而是进入
    near-miss 列表并承担排序惩罚，便于审计。
  - 提供方向级均线检查：方向内全部空头排列时整方向放弃。

本模块只做纯计算，不抓行情、不写文件，便于单元测试。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

FILTER_POLICY_VERSION = "1.0.0"
BUY_ACTION_LABELS = ("STRONG_BUY_WATCH", "ACCUMULATE_ON_DIP")


@dataclass(frozen=True)
class FilterPolicy:
    purpose: str
    regime: str
    max_price_hard: float
    price_penalty_threshold: float
    price_penalty_points: float
    position_hard_cutoff: float
    position_penalty_threshold: float
    position_preferred: float
    position_penalty_points: float
    missing_position_penalty_points: float
    require_direction_ma_alignment: bool = True
    direction_ma_periods: tuple = (5, 10, 20)
    notes: str = ""
    version: str = FILTER_POLICY_VERSION

    def as_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["direction_ma_periods"] = list(self.direction_ma_periods)
        return data


_BASE_POLICIES: Dict[str, Dict[str, Any]] = {
    # 观察池：允许保留高价值龙头，>100 元只惩罚；>150 元才硬排除。
    "watchlist": {
        "max_price_hard": 150.0,
        "price_penalty_threshold": 100.0,
        "price_penalty_points": 4.0,
        "position_hard_cutoff": 70.0,
        "position_penalty_threshold": 60.0,
        "position_preferred": 40.0,
        "position_penalty_points": 4.0,
        "missing_position_penalty_points": 6.0,
        "notes": "观察池：高位只惩罚不淘汰，保留龙头观察价值",
    },
    # 交易池：严格执行 100 元上限与 70% 位置上限。
    "trade": {
        "max_price_hard": 100.0,
        "price_penalty_threshold": 80.0,
        "price_penalty_points": 6.0,
        "position_hard_cutoff": 70.0,
        "position_penalty_threshold": 60.0,
        "position_preferred": 40.0,
        "position_penalty_points": 6.0,
        "missing_position_penalty_points": 8.0,
        "notes": "交易池：价格/位置硬上限更严格",
    },
}

_REGIME_OVERRIDES: Dict[str, Dict[str, Any]] = {
    "PANIC_DOWNTREND": {
        "position_hard_cutoff": 60.0,
        "position_penalty_threshold": 50.0,
        "position_penalty_points": 8.0,
        "price_penalty_points": 8.0,
        "notes": "恐慌态：位置上限收紧，避免在下跌中继接飞刀",
    },
    "REBOUND_RIGHT_SIDE": {
        "position_hard_cutoff": 75.0,
        "position_penalty_threshold": 65.0,
        "position_penalty_points": 3.0,
        "notes": "反弹态：允许相对强度较高的标的适度放宽位置",
    },
    "UNKNOWN": {
        "notes": "未知态：过滤照常执行，但下游不输出方向性开仓标签",
    },
}


def resolve_filter_policy(regime: Optional[str] = None,
                          purpose: str = "watchlist") -> FilterPolicy:
    """按用途与市场状态解析过滤策略；未知用途回退观察池。"""
    chosen_purpose = purpose if purpose in _BASE_POLICIES else "watchlist"
    values = dict(_BASE_POLICIES[chosen_purpose])
    chosen_regime = regime if regime in _REGIME_OVERRIDES else "NORMAL_OSCILLATION"
    override = _REGIME_OVERRIDES.get(chosen_regime, {})
    notes = [values.get("notes", ""), override.get("notes", "")]
    values.update({key: value for key, value in override.items() if key != "notes"})
    values["notes"] = "；".join(item for item in notes if item)
    return FilterPolicy(
        purpose=chosen_purpose,
        regime=chosen_regime,
        require_direction_ma_alignment=True,
        **values,
    )


def _relative_position(candidate: Dict[str, Any]) -> Optional[float]:
    value = candidate.get("rel_position_52w")
    if value is not None:
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    price = candidate.get("price")
    high = candidate.get("high_52w")
    low = candidate.get("low_52w")
    try:
        price_value = float(price)
        high_value = float(high)
        low_value = float(low)
    except (TypeError, ValueError):
        return None
    if high_value <= low_value or price_value <= 0:
        return None
    return (price_value - low_value) / (high_value - low_value) * 100.0


def evaluate_quote_filters(candidate: Dict[str, Any],
                           policy: FilterPolicy) -> Dict[str, Any]:
    """
    对单只候选执行价格/位置过滤。

    返回：
      decision: pass / penalize / exclude
      hard_exclusions / penalties / reasons
      score_adjustment: 负分，供排序使用（不改变研究评级本身）
      near_miss: 审计记录（被惩罚或排除时非空）
    """
    hard_exclusions: List[str] = []
    penalties: List[str] = []
    reasons: List[str] = []
    score_adjustment = 0.0

    try:
        price = float(candidate.get("price"))
    except (TypeError, ValueError):
        price = None
    if price is None or price <= 0:
        hard_exclusions.append("价格缺失或非正数")
    else:
        if price > policy.max_price_hard:
            hard_exclusions.append(
                f"价格 {price:.2f} 超过{policy.purpose}硬上限 {policy.max_price_hard:.0f}"
            )
        elif price > policy.price_penalty_threshold:
            penalties.append(
                f"价格 {price:.2f} 超过惩罚阈值 {policy.price_penalty_threshold:.0f}"
            )
            score_adjustment -= policy.price_penalty_points

    relative_position = _relative_position(candidate)
    if relative_position is None:
        penalties.append("52周相对位置缺失，无法判断高位风险")
        score_adjustment -= policy.missing_position_penalty_points
    elif relative_position >= policy.position_hard_cutoff:
        hard_exclusions.append(
            f"52周位置 {relative_position:.1f}% 超过硬上限 "
            f"{policy.position_hard_cutoff:.0f}%"
        )
    elif relative_position >= policy.position_penalty_threshold:
        penalties.append(
            f"52周位置 {relative_position:.1f}% 超过惩罚阈值 "
            f"{policy.position_penalty_threshold:.0f}%"
        )
        score_adjustment -= policy.position_penalty_points

    if hard_exclusions:
        decision = "exclude"
        reasons = hard_exclusions
    elif penalties:
        decision = "penalize"
        reasons = penalties
    else:
        decision = "pass"
        if relative_position is not None and relative_position < policy.position_preferred:
            reasons = [f"52周位置 {relative_position:.1f}% 处于优先区间"]

    near_miss = None
    if decision != "pass":
        near_miss = {
            "code": candidate.get("code"),
            "name": candidate.get("name"),
            "price": price,
            "rel_position_52w": relative_position,
            "decision": decision,
            "reasons": reasons,
            "score_adjustment": round(score_adjustment, 2),
            "policy_version": policy.version,
        }

    return {
        "decision": decision,
        "hard_exclusions": hard_exclusions,
        "penalties": penalties,
        "reasons": reasons,
        "score_adjustment": round(score_adjustment, 2),
        "preference": (
            "preferred"
            if relative_position is not None
            and relative_position < policy.position_preferred
            else "neutral"
        ),
        "rel_position_52w": relative_position,
        "near_miss": near_miss,
        "policy": policy.as_dict(),
    }


def _ma_values(candidate: Dict[str, Any], periods: tuple) -> Optional[Dict[str, float]]:
    details = candidate.get("tech_details") or {}
    values: Dict[str, float] = {}
    for period in periods:
        raw = details.get(f"ma{period}")
        if raw is None:
            raw = candidate.get(f"ma{period}")
        try:
            values[f"ma{period}"] = float(raw)
        except (TypeError, ValueError):
            return None
    return values


def direction_health(candidates: List[Dict[str, Any]],
                     policy: Optional[FilterPolicy] = None
                     ) -> Dict[str, Any]:
    """
    方向级均线检查：方向内全部候选 MA5<MA10<MA20 时整方向放弃。

    只使用已经过技术评分的 `tech_details.ma5/ma10/ma20`；缺失均线的候选
    不计入“全部空头”的分母，避免把数据缺失当成看空。
    """
    active_policy = policy or resolve_filter_policy()
    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for candidate in candidates:
        sectors = candidate.get("sectors") or [candidate.get("primary_sector")]
        for sector in sectors:
            if not sector:
                continue
            grouped.setdefault(str(sector), []).append(candidate)

    sectors: Dict[str, Dict[str, Any]] = {}
    vetoed: List[str] = []
    for sector, members in grouped.items():
        valid = 0
        bearish = 0
        member_codes: List[str] = []
        for member in members:
            member_codes.append(member.get("code"))
            ma = _ma_values(member, active_policy.direction_ma_periods)
            if ma is None:
                continue
            valid += 1
            if (ma["ma5"] < ma["ma10"] < ma["ma20"]):
                bearish += 1
        if valid == 0:
            status = "unknown"
            is_vetoed = False
        elif bearish == valid:
            status = "all_bearish"
            is_vetoed = True
        elif valid >= 3 and bearish / valid >= 0.67:
            status = "mostly_bearish"
            is_vetoed = False
        else:
            status = "mixed"
            is_vetoed = False
        sectors[sector] = {
            "status": status,
            "member_count": len(members),
            "valid_ma_count": valid,
            "bearish_count": bearish,
            "vetoed": is_vetoed,
            "members": member_codes,
            "policy_version": active_policy.version,
        }
        if is_vetoed:
            vetoed.append(sector)

    return {
        "status": "ok",
        "sectors": sectors,
        "vetoed_sectors": vetoed,
        "policy_version": active_policy.version,
    }


def apply_direction_health(candidates: List[Dict[str, Any]],
                           policy: Optional[FilterPolicy] = None
                           ) -> Dict[str, Any]:
    """
    把方向级健康度写回候选，并抑制被淘汰方向的买入标签。

    返回方向级 veto 明细，供扫描结果落盘。
    """
    health = direction_health(candidates, policy)
    veto_records: List[Dict[str, Any]] = []
    for candidate in candidates:
        sector = candidate.get("primary_sector")
        sector_health = health["sectors"].get(sector)
        candidate["direction_health"] = sector_health
        if not sector_health or not sector_health.get("vetoed"):
            candidate["direction_veto"] = False
            continue
        candidate["direction_veto"] = True
        original = candidate.get("action_label")
        if original in BUY_ACTION_LABELS:
            candidate["action_label"] = "OBSERVE_DIRECTION_BEARISH"
            candidate["buy_signal_suppressed"] = True
            veto_records.append({
                "code": candidate.get("code"),
                "name": candidate.get("name"),
                "sector": sector,
                "original_label": original,
                "reasons": [
                    f"{sector} 方向内 {sector_health['valid_ma_count']} 只"
                    f"均线全部空头排列（MA5<MA10<MA20）"
                ],
            })
    return {
        "direction_health": health,
        "direction_veto": veto_records,
        "vetoed_sectors": health.get("vetoed_sectors", []),
    }
