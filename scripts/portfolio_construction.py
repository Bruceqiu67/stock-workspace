#!/usr/bin/env python3
"""
组合构建与风险预算层 (Portfolio Construction & Risk Budget)

目的（A1）：
  个股评分只回答“买什么”，不回答“买多少”。本模块在候选评分之上增加：
    - 按市场状态调整的总暴露（正常 80% / 反弹 70% / 恐慌 30% / 未知 0%）
    - 单票上限、方向（相关性代理）上限、最大持仓数
    - 基于评分与波动率的权重分配
    - 与当前持仓比较后的调仓建议（ENTER/ADD/HOLD/REDUCE/EXIT）
    - 可单元测试的约束校验

说明：候选快照未保存完整收益序列，相关性用“同方向”作为保守代理
（correlation_proxy=0.8），并由方向上限约束；文档明确标注这是代理而非精确相关系数。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

PORTFOLIO_BUILDER_VERSION = "1.0.0"
REGIME_EXPOSURE = {
    "NORMAL_OSCILLATION": 0.80,
    "REBOUND_RIGHT_SIDE": 0.70,
    "PANIC_DOWNTREND": 0.30,
    "UNKNOWN": 0.0,
}
DEFAULT_CONSTRAINTS = {
    "max_single_weight": 0.15,
    "max_sector_weight": 0.30,
    "max_positions": 8,
    "min_score": 50.0,
    "min_position": 0.02,
    "max_correlated_exposure": 0.30,
    "cash_min": 0.10,
}
CORRELATION_PROXY_SAME_SECTOR = 0.8
CORRELATION_PROXY_DIFFERENT_SECTOR = 0.2
NOT_EXECUTABLE_LABELS = ("AVOID_DEFENSE", "DATA_DEGRADED", "ABSTAIN_NO_DATA")


def _to_float(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number or number in (float("inf"), float("-inf")):
        return None
    return number


def _canonical_score(candidate: Dict[str, Any]) -> Optional[float]:
    score = _to_float(candidate.get("canonical_score"))
    if score is not None:
        return score
    combined = _to_float(candidate.get("combined_score"))
    if combined is not None:
        return combined * 20.0
    return None


def _volatility_proxy(candidate: Dict[str, Any]) -> float:
    """波动率代理：优先 ATR%，其次 5 日振幅，最后默认 3%。"""
    operation = candidate.get("operation") or {}
    for value in (
        operation.get("atr_pct"),
        candidate.get("atr_pct"),
        (candidate.get("tech_details") or {}).get("avg_amp_5d"),
    ):
        number = _to_float(value)
        if number is not None and number > 0:
            return max(1.0, min(15.0, number))
    return 3.0


def _sector_of(candidate: Dict[str, Any]) -> str:
    return str(
        candidate.get("primary_sector")
        or (candidate.get("sectors") or ["未知"])[0]
        or "未知"
    )


def _not_executable(candidate: Dict[str, Any], regime: str) -> Optional[str]:
    if not candidate.get("rankable"):
        return "不可排名（数据降级/弃权）"
    if candidate.get("direction_veto"):
        return "方向级淘汰（全部空头排列）"
    if candidate.get("buy_signal_suppressed"):
        return "买入标签已被风控/数据否决"
    label = str(candidate.get("action_label") or "")
    if label in NOT_EXECUTABLE_LABELS or label.startswith("OBSERVE"):
        return f"研究评级不可执行: {label}"
    if regime == "UNKNOWN":
        return "市场状态未知，不建立方向性暴露"
    if regime == "PANIC_DOWNTREND":
        stabilization = candidate.get("stabilization") or {}
        if not stabilization.get("passed"):
            return "恐慌态企稳三条件未通过"
    return None


def _normalize_current(current_positions: Optional[Any]) -> Dict[str, float]:
    if not current_positions:
        return {}
    if isinstance(current_positions, dict):
        result: Dict[str, float] = {}
        for code, value in current_positions.items():
            if isinstance(value, dict):
                weight = _to_float(value.get("weight"))
            else:
                weight = _to_float(value)
            if weight is not None and weight > 0:
                result[str(code)] = weight
        return result
    if isinstance(current_positions, (list, tuple)):
        result = {}
        for item in current_positions:
            if not isinstance(item, dict):
                continue
            code = item.get("code")
            weight = _to_float(item.get("weight"))
            if code and weight is not None and weight > 0:
                result[str(code)] = weight
        return result
    return {}


def _allocate_weights(raw_weights: Dict[str, float], target_exposure: float,
                      sector_map: Dict[str, str],
                      max_single: float, max_sector: float
                      ) -> Dict[str, float]:
    """在单票/方向上限下做迭代水填；无法分配的部分留作现金。"""
    if target_exposure <= 0 or not raw_weights:
        return {}
    total_raw = sum(max(0.0, value) for value in raw_weights.values())
    if total_raw <= 0:
        return {}
    weights = {
        code: target_exposure * max(0.0, value) / total_raw
        for code, value in raw_weights.items()
    }
    sectors: Dict[str, List[str]] = {}
    for code in weights:
        sectors.setdefault(sector_map.get(code, "未知"), []).append(code)

    epsilon = 1e-9
    for _ in range(50):
        excess = 0.0
        for code in list(weights):
            if weights[code] > max_single + epsilon:
                excess += weights[code] - max_single
                weights[code] = max_single
        for members in sectors.values():
            sector_total = sum(weights[code] for code in members)
            if sector_total > max_sector + epsilon:
                scale = max_sector / sector_total if sector_total > 0 else 0.0
                for code in members:
                    weights[code] *= scale
                excess += sector_total - max_sector
        if excess <= epsilon:
            break

        capacities: Dict[str, float] = {}
        for code in weights:
            if weights[code] >= max_single - epsilon:
                continue
            sector = sector_map.get(code, "未知")
            sector_total = sum(weights[member] for member in sectors[sector])
            sector_headroom = max_sector - sector_total
            if sector_headroom <= epsilon:
                continue
            capacities[code] = min(max_single - weights[code], sector_headroom)
        total_capacity = sum(capacities.values())
        if total_capacity <= epsilon:
            break
        for code, capacity in capacities.items():
            weights[code] += excess * capacity / total_capacity

    # 最后做一次保守截断：只保证上限不被突破，剩余额度留作现金。
    for _ in range(10):
        changed = False
        for code in list(weights):
            if weights[code] > max_single + epsilon:
                weights[code] = max_single
                changed = True
        for members in sectors.values():
            sector_total = sum(weights[code] for code in members)
            if sector_total > max_sector + epsilon:
                scale = max_sector / sector_total if sector_total > 0 else 0.0
                for code in members:
                    weights[code] *= scale
                changed = True
        if not changed:
            break
    return weights


def _adjustments(weights: Dict[str, float], current: Dict[str, float],
                 names: Dict[str, str]) -> List[Dict[str, Any]]:
    adjustments: List[Dict[str, Any]] = []
    for code in sorted(set(weights) | set(current)):
        target = weights.get(code, 0.0)
        existing = current.get(code, 0.0)
        delta = target - existing
        if existing <= 0 and target > 0:
            action = "ENTER"
        elif target <= 0 and existing > 0:
            action = "EXIT"
        elif delta > 0.02:
            action = "ADD"
        elif delta < -0.02:
            action = "REDUCE"
        else:
            action = "HOLD"
        adjustments.append({
            "code": code,
            "name": names.get(code, ""),
            "current_weight": round(existing, 4),
            "target_weight": round(target, 4),
            "delta_weight": round(delta, 4),
            "action": action,
        })
    return adjustments


def build_portfolio(candidates: List[Dict[str, Any]],
                     market_regime: str = "NORMAL_OSCILLATION",
                     current_positions: Optional[Any] = None,
                     constraints: Optional[Dict[str, Any]] = None,
                     as_of: Optional[str] = None) -> Dict[str, Any]:
    """由候选评分、当前持仓、市场状态生成目标组合与调仓建议。"""
    rules = dict(DEFAULT_CONSTRAINTS)
    rules.update(constraints or {})
    regime = market_regime if market_regime in REGIME_EXPOSURE else "NORMAL_OSCILLATION"
    target_exposure = float(REGIME_EXPOSURE[regime])
    if constraints and "target_exposure" in constraints:
        target_exposure = max(0.0, min(1.0, float(constraints["target_exposure"])))
    max_single = float(rules["max_single_weight"])
    max_sector = float(rules["max_sector_weight"])
    min_score = float(rules["min_score"])
    min_position = float(rules["min_position"])
    max_positions = int(rules["max_positions"])

    dropped: List[Dict[str, Any]] = []
    eligible: List[Dict[str, Any]] = []
    for candidate in candidates:
        reason = _not_executable(candidate, regime)
        if reason:
            dropped.append({"code": candidate.get("code"), "reason": reason})
            continue
        score = _canonical_score(candidate)
        if score is None:
            dropped.append({
                "code": candidate.get("code"),
                "reason": "缺少规范分，无法分配权重",
            })
            continue
        if score < min_score:
            dropped.append({
                "code": candidate.get("code"),
                "reason": f"规范分 {score:.1f} 低于组合最低分 {min_score:.0f}",
            })
            continue
        eligible.append(candidate)

    eligible.sort(key=lambda item: _canonical_score(item) or 0.0, reverse=True)
    if len(eligible) > max_positions:
        for candidate in eligible[max_positions:]:
            dropped.append({
                "code": candidate.get("code"),
                "reason": f"超出最大持仓数 {max_positions}",
            })
        eligible = eligible[:max_positions]

    sector_map = {candidate.get("code"): _sector_of(candidate) for candidate in eligible}
    raw_weights: Dict[str, float] = {}
    for candidate in eligible:
        score = _canonical_score(candidate) or 0.0
        volatility = _volatility_proxy(candidate)
        # 评分越高、波动越低，权重越大；只做相对排序，不做绝对下注。
        raw_weights[candidate.get("code")] = (
            max(score - min_score, 1.0) / max(volatility, 1.0)
        )

    weights = _allocate_weights(
        raw_weights, target_exposure, sector_map, max_single, max_sector
    )
    for code, weight in list(weights.items()):
        if weight < min_position:
            dropped.append({
                "code": code,
                "reason": f"分配权重 {weight:.2%} 低于最小仓位 {min_position:.0%}",
            })
            weights[code] = 0.0
    weights = {code: weight for code, weight in weights.items() if weight > 0}

    position_map = {candidate.get("code"): candidate for candidate in eligible}
    positions: List[Dict[str, Any]] = []
    for code, weight in sorted(weights.items(), key=lambda item: item[1], reverse=True):
        candidate = position_map.get(code, {})
        positions.append({
            "code": code,
            "name": candidate.get("name", ""),
            "sector": sector_map.get(code, "未知"),
            "target_weight": round(weight, 4),
            "canonical_score": round(_canonical_score(candidate) or 0.0, 1),
            "research_rating": candidate.get("action_label"),
            "correlation_cluster": sector_map.get(code, "未知"),
            "correlation_proxy": CORRELATION_PROXY_SAME_SECTOR,
            "volatility_proxy_pct": round(_volatility_proxy(candidate), 2),
        })

    sector_weights: Dict[str, float] = {}
    for position in positions:
        sector = position.get("sector", "未知")
        sector_weights[sector] = round(
            sector_weights.get(sector, 0.0) + position.get("target_weight", 0.0), 4
        )

    current = _normalize_current(current_positions)
    names = {candidate.get("code"): candidate.get("name", "") for candidate in candidates}
    # 用落盘后的四舍五入权重计算总权重/现金，保证 positions + cash = 100%。
    total_weight = round(sum(
        position.get("target_weight", 0.0) for position in positions
    ), 4)
    cash_pct = round(max(0.0, 1.0 - total_weight), 4)

    plan = {
        "builder_version": PORTFOLIO_BUILDER_VERSION,
        "as_of": as_of,
        "regime": regime,
        "target_exposure": round(target_exposure, 4),
        "total_weight": total_weight,
        "cash_pct": cash_pct,
        "positions": positions,
        "sector_weights": sector_weights,
        "correlation_proxy": {
            "same_sector": CORRELATION_PROXY_SAME_SECTOR,
            "different_sector": CORRELATION_PROXY_DIFFERENT_SECTOR,
            "note": "候选快照未保存完整收益序列，同方向作为高相关代理",
        },
        "constraints": rules,
        "dropped": dropped,
        "adjustments": _adjustments(weights, current, names),
        "eligible_count": len(eligible),
    }
    plan["validation_errors"] = validate_portfolio(plan)
    return plan


def validate_portfolio(plan: Dict[str, Any]) -> List[str]:
    """校验组合约束，返回错误列表（空列表表示通过）。"""
    errors: List[str] = []
    constraints = plan.get("constraints") or {}
    max_single = float(constraints.get("max_single_weight", 0.15))
    max_sector = float(constraints.get("max_sector_weight", 0.30))
    max_positions = int(constraints.get("max_positions", 8))
    positions = plan.get("positions") or []
    epsilon = 1e-3
    total = 0.0
    sector_totals: Dict[str, float] = {}
    for position in positions:
        weight = float(position.get("target_weight") or 0.0)
        if weight < 0 or weight > max_single + epsilon:
            errors.append(
                f"{position.get('code')} 单票权重 {weight:.2%} 超过上限 {max_single:.0%}"
            )
        total += weight
        sector = position.get("sector") or "未知"
        sector_totals[sector] = sector_totals.get(sector, 0.0) + weight
    if len(positions) > max_positions:
        errors.append(f"持仓数 {len(positions)} 超过上限 {max_positions}")
    for sector, weight in sector_totals.items():
        if weight > max_sector + epsilon:
            errors.append(f"方向 {sector} 权重 {weight:.2%} 超过上限 {max_sector:.0%}")
    if total > 1.0 + epsilon:
        errors.append(f"总权重 {total:.2%} 超过 100%")
    if total > float(plan.get("target_exposure") or 0.0) + epsilon:
        errors.append("总权重超过市场状态允许的目标暴露")
    if float(plan.get("cash_pct") or 0.0) < -epsilon:
        errors.append("现金比例不能为负")
    if abs((total + float(plan.get("cash_pct") or 0.0)) - 1.0) > epsilon:
        errors.append("总权重 + 现金必须等于 100%")
    return errors


def portfolio_from_candidates(candidates: List[Dict[str, Any]],
                              market_regime: str,
                              current_positions: Optional[Any] = None,
                              as_of: Optional[str] = None
                              ) -> Dict[str, Any]:
    """扫描器入口的薄封装，便于调用方只依赖一个函数。"""
    return build_portfolio(
        candidates,
        market_regime=market_regime,
        current_positions=current_positions,
        as_of=as_of,
    )
