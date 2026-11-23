#!/usr/bin/env python3
"""
研究深度与证据覆盖契约 (Research Depth & Evidence Coverage)

目的（I4）：
  方法论层要求深度主题扫描至少 20 家公司、25 个来源；日更流程只有 4-5 次
  工具调用、30 秒预算。两者不是同一个任务，必须显式分档，禁止把快速扫描
  的输出写成“已验证”。

两档模式：
  - quick_scan：日更/盘中快速筛选，默认最多 5 次工具调用；只能声明
    research_depth=quick_scan、claim_level=initial_pass，并列出缺失证据。
  - deep_research：深度主题研究，最低 20 家公司、25 个来源；
    达标后才能声明 claim_level=verified。

输出字段：research_depth / evidence_coverage / claim_level /
missing_evidence / requirements / claim_allowed。
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

RESEARCH_DEPTH_VERSION = "1.0.0"

RESEARCH_MODES: Dict[str, Dict[str, Any]] = {
    "quick_scan": {
        "label": "快速扫描（初步）",
        "min_companies": 0,
        "target_companies": 8,
        "min_sources": 3,
        "target_sources": 5,
        "max_tool_calls": 5,
        "budget_seconds": 30,
        "can_claim_verified": False,
    },
    "deep_research": {
        "label": "深度研究",
        "min_companies": 20,
        "target_companies": 20,
        "min_sources": 25,
        "target_sources": 25,
        "max_tool_calls": 60,
        "budget_seconds": 1800,
        "can_claim_verified": True,
    },
}

CLAIM_LEVELS = ("insufficient", "initial_pass", "verified")


def resolve_research_mode(requested: Optional[str] = None,
                          tool_calls: Optional[int] = None,
                          budget_seconds: Optional[int] = None) -> Dict[str, Any]:
    """
    解析研究模式；预算不足时降级为 quick_scan，并给出原因。

    显式请求 deep_research 但预算只有 30 秒/5 次调用时，不静默按深度模式跑，
    而是返回 quick_scan + requested_mode=deep_research + downgrade_reason。
    """
    mode = requested if requested in RESEARCH_MODES else "quick_scan"
    reason = None
    if mode == "deep_research":
        deep = RESEARCH_MODES["deep_research"]
        if (tool_calls is not None and tool_calls < deep["min_sources"]) or (
            budget_seconds is not None and budget_seconds < 300
        ):
            mode = "quick_scan"
            reason = "预算不足以完成深度研究（需 >=25 次取数 / 至少 5 分钟）"
    return {
        "research_depth": mode,
        "requested_mode": requested,
        "budget": RESEARCH_MODES[mode],
        "downgrade_reason": reason,
        "version": RESEARCH_DEPTH_VERSION,
    }


def assess_research_depth(mode: str,
                          companies_reviewed: int = 0,
                          sources_inspected: int = 0,
                          evidence_grades: Optional[Iterable[str]] = None,
                          claimed_verified: bool = False) -> Dict[str, Any]:
    """
    计算证据覆盖度与可声明的结论等级。

    evidence_coverage 由公司覆盖（50%）与来源覆盖（50%）组成，均以目标值为分母；
    深度模式还必须同时满足最低公司数与最低来源数，才可声明 verified。
    """
    chosen = mode if mode in RESEARCH_MODES else "quick_scan"
    requirements = RESEARCH_MODES[chosen]
    companies = max(0, int(companies_reviewed or 0))
    sources = max(0, int(sources_inspected or 0))

    company_coverage = min(1.0, companies / requirements["target_companies"]) \
        if requirements["target_companies"] else 0.0
    source_coverage = min(1.0, sources / requirements["target_sources"]) \
        if requirements["target_sources"] else 0.0
    coverage = round(company_coverage * 0.5 + source_coverage * 0.5, 3)

    missing: List[str] = []
    if companies < requirements["min_companies"]:
        missing.append(
            f"公司覆盖 {companies}/{requirements['min_companies']}（深度研究最低要求）"
        )
    elif companies < requirements["target_companies"]:
        missing.append(
            f"公司覆盖 {companies}/{requirements['target_companies']}（目标值）"
        )
    if sources < requirements["min_sources"]:
        missing.append(
            f"来源覆盖 {sources}/{requirements['min_sources']}（深度研究最低要求）"
        )
    elif sources < requirements["target_sources"]:
        missing.append(
            f"来源覆盖 {sources}/{requirements['target_sources']}（目标值）"
        )

    if companies == 0 and sources == 0:
        claim_level = "insufficient"
    elif (requirements["can_claim_verified"]
          and companies >= requirements["min_companies"]
          and sources >= requirements["min_sources"]):
        claim_level = "verified"
    else:
        claim_level = "initial_pass"

    grades = list(evidence_grades or [])
    weak_grades = [
        grade for grade in grades
        if str(grade).lower() in ("weak", "rumor", "unverified", "弱", "未核实")
    ]
    claim_allowed = True
    claim_reason = None
    if claimed_verified and claim_level != "verified":
        claim_allowed = False
        claim_reason = (
            "当前为快速扫描/证据不足，不能把结论写成“已验证”；"
            "请标注为初步扫描并列出待核实来源"
        )

    return {
        "version": RESEARCH_DEPTH_VERSION,
        "research_depth": chosen,
        "research_label": requirements["label"],
        "evidence_coverage": coverage,
        "company_coverage": round(company_coverage, 3),
        "source_coverage": round(source_coverage, 3),
        "claim_level": claim_level,
        "claim_allowed": claim_allowed,
        "claim_reason": claim_reason,
        "missing_evidence": missing,
        "evidence_grades": {
            "total": len(grades),
            "weak_or_unverified": len(weak_grades),
        },
        "requirements": requirements,
    }


def enrich_output(payload: Dict[str, Any],
                  assessment: Dict[str, Any]) -> Dict[str, Any]:
    """把研究深度字段合并到报告/候选输出，保留已有字段。"""
    enriched = dict(payload)
    for key in (
        "research_depth", "research_label", "evidence_coverage",
        "claim_level", "claim_allowed", "claim_reason", "missing_evidence",
    ):
        enriched[key] = assessment.get(key)
    return enriched
