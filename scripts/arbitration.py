#!/usr/bin/env python3
"""
三框架冲突裁决协议 (Framework Arbitration Protocol)

输入：
  - industry_view:  Serenity / 产业链视角（stance, score, evidence）
  - technical_view: mi-skill 资金流/技术确认（stance, score, broken）
  - regime:         market_state 输出的市场状态
  - holding_horizon: long（3-5年）/ swing（5日级）/ research（研究）
  - relative_strength_pct: 候选/持仓在当日候选池中的相对强度百分位
  - stabilization:  check_stabilization() 的结果（恐慌态必需）

输出：
  - conflict_type / decision / basis / priority / confidence / evidence_needed

原则：
  - 恐慌态：风控一票否决，产业逻辑不能覆盖破位/未企稳。
  - 正常态：产业逻辑做准入，技术形态做时机。
  - 反弹态：相对强度优先。
  - 未知态：不输出方向性动作。
  - 长周期：产业逻辑权重上升，但技术破位仍需标注；短周期：技术优先。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

ARBITRATION_VERSION = "1.0"

STANCE_STRONG = 3.6
STANCE_WEAK = 2.4


def stance_from_score(score: Optional[float]) -> str:
    if score is None:
        return "unknown"
    if score >= STANCE_STRONG:
        return "bullish"
    if score <= STANCE_WEAK:
        return "bearish"
    return "neutral"


def detect_conflict(industry_stance: str, technical_stance: str,
                    technical_broken: bool = False) -> str:
    if industry_stance == "unknown" or technical_stance == "unknown":
        return "insufficient_data"
    if technical_broken and industry_stance == "bullish":
        return "industry_bull_technical_broken"
    if industry_stance == "bullish" and technical_stance == "bearish":
        return "industry_bull_technical_bearish"
    if industry_stance == "bearish" and technical_stance == "bullish":
        return "industry_bearish_technical_bullish"
    if industry_stance == "bullish" and technical_stance == "bullish":
        return "consensus_bullish"
    if industry_stance == "bearish" and technical_stance == "bearish":
        return "consensus_bearish"
    return "mixed"


def resolve_conflict(
    industry_view: Dict[str, Any],
    technical_view: Dict[str, Any],
    regime: str,
    holding_horizon: str = "swing",
    relative_strength_pct: Optional[float] = None,
    stabilization: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    industry_stance = industry_view.get("stance") or stance_from_score(
        industry_view.get("score")
    )
    technical_stance = technical_view.get("stance") or stance_from_score(
        technical_view.get("score")
    )
    technical_broken = bool(
        technical_view.get("broken")
        or "均线空头压制" in (technical_view.get("signals") or [])
    )
    conflict = detect_conflict(industry_stance, technical_stance, technical_broken)
    evidence_needed = []

    if regime == "UNKNOWN":
        return _result(
            conflict, "NO_DIRECTIONAL_ACTION", "unknown",
            ["市场状态未知，禁止方向性开仓"],
            evidence_needed=["补齐指数MA20/量能/候选池广度"],
            confidence="low",
        )

    if regime == "PANIC_DOWNTREND":
        passed = bool((stabilization or {}).get("passed"))
        if not passed:
            return _result(
                conflict, "DEFEND_NO_NEW_BUY", "risk_control",
                ["恐慌态企稳三条件未通过，风控一票否决"],
                evidence_needed=["缩量", "不创新低", "站上MA5"],
                confidence="medium",
            )
        if technical_stance == "bearish" or technical_broken:
            return _result(
                conflict, "HOLD_ONLY_RELATIVE_STRENGTH", "risk_control",
                ["企稳条件通过但技术仍弱，只保留相对强度持仓"],
                evidence_needed=["相对强度保持前1/3", "不再创新低"],
                confidence="medium",
            )
        return _result(
            conflict, "OBSERVE_STABILIZATION_CONFIRMED", "risk_control",
            ["企稳条件已通过，仍需等待趋势确认"],
            evidence_needed=["缩量持续", "站上MA10/MA20"],
            confidence="medium",
        )

    if regime == "REBOUND_RIGHT_SIDE":
        if relative_strength_pct is not None and relative_strength_pct >= 66:
            return _result(
                conflict, "ADD_RELATIVE_STRENGTH", "relative_strength",
                ["反弹态相对强度优先（候选池前1/3）"],
                evidence_needed=["板块资金流延续", "不跌破MA5"],
                confidence="medium",
            )
        return _result(
            conflict, "WAIT_CONFIRMATION", "relative_strength",
            ["反弹态但相对强度不足，等待确认"],
            evidence_needed=["相对强度进入前1/3", "主力净流入"],
            confidence="low",
        )

    # NORMAL_OSCILLATION：产业准入、技术择时
    if holding_horizon == "long" and industry_stance == "bullish":
        if technical_broken:
            return _result(
                conflict, "WATCH_WAIT_TECH_REPAIR", "industry_admission",
                ["长周期产业逻辑成立，但技术破位，先观察修复"],
                evidence_needed=["站回MA20", "缩量止跌"],
                confidence="medium",
            )
        return _result(
            conflict, "LONG_HORIZON_ACCUMULATE", "industry_admission",
            ["长周期产业逻辑优先，技术未破位"],
            evidence_needed=["业绩/订单验证", "估值安全边际"],
            confidence="medium",
        )

    if conflict == "consensus_bullish":
        return _result(
            conflict, "BUY_ON_PULLBACK", "both",
            ["产业与技术共振，回踩不破可参与"],
            evidence_needed=["回踩MA10/MA20不破", "量价配合"],
            confidence="high",
        )
    if conflict == "industry_bull_technical_broken":
        return _result(
            conflict, "WATCH_WAIT_TECH_REPAIR", "industry_admission",
            ["产业逻辑做准入，但技术已破位，不立即买入"],
            evidence_needed=["站回MA20", "缩量止跌"],
            confidence="medium",
        )
    if conflict == "industry_bull_technical_bearish":
        return _result(
            conflict, "WATCH_WAIT_TECH_REPAIR", "industry_admission",
            ["产业看多但技术空头，等待时机"],
            evidence_needed=["均线修复", "资金流转正"],
            confidence="medium",
        )
    if conflict == "industry_bearish_technical_bullish":
        return _result(
            conflict, "TACTICAL_ONLY_SMALL", "technical_timing",
            ["技术强但产业逻辑不成立，只允许小仓战术参与"],
            evidence_needed=["产业链证据补齐", "严格止损"],
            confidence="low",
        )
    if conflict == "consensus_bearish":
        return _result(
            conflict, "AVOID", "both",
            ["产业与技术同时走弱"],
            evidence_needed=["等待基本面或技术面反转"],
            confidence="high",
        )
    return _result(
        conflict, "NEUTRAL_WAIT", "mixed",
        ["信号混合，等待更明确的方向"],
        evidence_needed=["产业证据", "技术确认"],
        confidence="low",
    )


def _result(conflict: str, decision: str, priority: str,
            basis: list, evidence_needed: list,
            confidence: str) -> Dict[str, Any]:
    return {
        "arbitration_version": ARBITRATION_VERSION,
        "conflict_type": conflict,
        "decision": decision,
        "priority": priority,
        "basis": basis,
        "evidence_needed": evidence_needed,
        "confidence": confidence,
    }


def arbitration_from_candidate(candidate: Dict[str, Any], regime: str) -> Dict[str, Any]:
    """从候选评分结果直接构造仲裁输入，供候选扫描调用。"""
    scores = candidate.get("scores") or {}
    signals = candidate.get("signals") or []
    tech_details = candidate.get("tech_details") or {}
    return resolve_conflict(
        industry_view={
            "stance": stance_from_score(scores.get("industry")),
            "score": scores.get("industry"),
        },
        technical_view={
            "stance": stance_from_score(scores.get("technical")),
            "score": scores.get("technical"),
            "broken": "均线空头压制" in signals,
            "signals": signals,
        },
        regime=regime,
        holding_horizon=candidate.get("holding_horizon", "swing"),
        relative_strength_pct=candidate.get("relative_strength_pct"),
        stabilization=candidate.get("stabilization"),
    )
