#!/usr/bin/env python3
"""
评分体系单一事实源 (Scoring Single Source of Truth)

所有评分权重、行业基准、动作阈值、量表换算集中在本文件，并带版本号。
其他模块（scoring_engine / market_state / 文档）只引用这里，避免出现
多套互相冲突的权重表。

规范量表（canonical）：0-100
  technical / industry / catalyst 三个子分（0-5）按 0.40/0.40/0.20 加权后
  乘以 20；risk 作为诊断子分单独输出，不改变既有排名权重。
其他量表（0-5、0-20、0-100 底部检查、五星制、Serenity 0-100）均为展示层，
通过本文件的显式换算函数得到。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

CONFIG_VERSION = "1.0.0"
CANONICAL_SCALE = (0, 100)

# ── 行业基准（方向 beta） ───────────────────────────────────────────────────
SECTOR_ATTRIBUTES: Dict[str, Dict[str, Any]] = {
    "半导体/封测/材料": {"卡脖子": 5, "关税受益": 5, "长期确定性": 5, "base": 3.8},
    "AI算力/信创":     {"卡脖子": 4, "关税受益": 4, "长期确定性": 4, "base": 3.2},
    "功率半导体":      {"卡脖子": 3, "关税受益": 2, "长期确定性": 3, "base": 3.0},
    "电网设备":        {"卡脖子": 3, "关税受益": 2, "长期确定性": 4, "base": 3.0},
    "机器人":          {"卡脖子": 3, "关税受益": 2, "长期确定性": 4, "base": 2.8},
    "锂电/新能源":     {"卡脖子": 2, "关税受益": 1, "长期确定性": 2, "base": 2.0},
    "消费电子":        {"卡脖子": 2, "关税受益": 1, "长期确定性": 2, "base": 1.8},
    "创新药":          {"卡脖子": 2, "关税受益": 1, "长期确定性": 2, "base": 1.8},
    "证券":            {"卡脖子": 1, "关税受益": 1, "长期确定性": 1, "base": 1.0},
}

# ── 权重 ───────────────────────────────────────────────────────────────────
DEFAULT_WEIGHTS = {"technical": 0.40, "industry": 0.40, "catalyst": 0.20}

REGIME_WEIGHTS: Dict[str, Dict[str, float]] = {
    "NORMAL_OSCILLATION": {"technical": 0.40, "industry": 0.40, "catalyst": 0.20},
    "PANIC_DOWNTREND":    {"technical": 0.60, "industry": 0.20, "catalyst": 0.20},
    "REBOUND_RIGHT_SIDE": {"technical": 0.35, "industry": 0.40, "catalyst": 0.25},
    "UNKNOWN":            {"technical": 0.40, "industry": 0.40, "catalyst": 0.20},
}

# ── 动作阈值（规范 0-100 量表） ─────────────────────────────────────────────
ACTION_THRESHOLDS = {
    "strong_buy_watch": 80.0,   # 0-5 量表的 4.0
    "accumulate_on_dip": 66.0,  # 0-5 量表的 3.3
    "neutral_observe": 50.0,    # 0-5 量表的 2.5
}

# 各展示量表的显式换算说明
DISPLAY_SCALES = {
    "engine_0_5": "canonical / 20",
    "dual_framework_0_20": "canonical / 5",
    "bottom_check_0_100": "canonical（仅作展示对齐，维度定义不同）",
    "stars_1_5": "round(engine_0_5)",
    "serenity_0_100": "canonical（公司级分数映射，需标注 mapping_confidence=low）",
}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def engine_score_to_canonical(combined_0_5: Optional[float]) -> Optional[float]:
    if combined_0_5 is None:
        return None
    return round(_clamp(float(combined_0_5), 0.0, 5.0) * 20.0, 1)


def canonical_from_components(
    technical_0_5: Optional[float],
    industry_0_5: Optional[float],
    catalyst_0_5: Optional[float],
    weights: Optional[Dict[str, float]] = None,
) -> Optional[float]:
    """由三个 0-5 子分直接得到规范 0-100 分；任一缺失则返回 None。"""
    if technical_0_5 is None or industry_0_5 is None or catalyst_0_5 is None:
        return None
    w = weights or DEFAULT_WEIGHTS
    total = (
        float(technical_0_5) * w.get("technical", 0.40)
        + float(industry_0_5) * w.get("industry", 0.40)
        + float(catalyst_0_5) * w.get("catalyst", 0.20)
    )
    return round(_clamp(total, 0.0, 5.0) * 20.0, 1)


def action_label_from_canonical(canonical_score: Optional[float]) -> str:
    if canonical_score is None:
        return "ABSTAIN_NO_DATA"
    if canonical_score >= ACTION_THRESHOLDS["strong_buy_watch"]:
        return "STRONG_BUY_WATCH"
    if canonical_score >= ACTION_THRESHOLDS["accumulate_on_dip"]:
        return "ACCUMULATE_ON_DIP"
    if canonical_score >= ACTION_THRESHOLDS["neutral_observe"]:
        return "NEUTRAL_OBSERVE"
    return "AVOID_DEFENSE"


def to_engine_0_5(canonical_score: Optional[float]) -> Optional[float]:
    if canonical_score is None:
        return None
    return round(canonical_score / 20.0, 2)


def to_dual_framework_0_20(canonical_score: Optional[float]) -> Optional[float]:
    if canonical_score is None:
        return None
    return round(canonical_score / 5.0, 1)


def to_stars_1_5(canonical_score: Optional[float]) -> Optional[int]:
    if canonical_score is None:
        return None
    stars = int(round(canonical_score / 20.0))
    return max(1, min(5, stars))


def scale_mappings(canonical_score: Optional[float]) -> Dict[str, Any]:
    """输出所有展示量表的换算结果，供报告/复盘统一引用。"""
    return {
        "canonical_version": CONFIG_VERSION,
        "canonical_0_100": canonical_score,
        "engine_0_5": to_engine_0_5(canonical_score),
        "dual_framework_0_20": to_dual_framework_0_20(canonical_score),
        "bottom_check_0_100": canonical_score,
        "stars_1_5": to_stars_1_5(canonical_score),
        "display_scales": DISPLAY_SCALES,
    }
