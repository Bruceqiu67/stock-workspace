#!/usr/bin/env python3
"""
双体系量化打分引擎 (Deterministic Scoring Engine)
基于 0.40技术形态分 + 0.40产业链价值分 + 0.20催化情绪分

提供确定性数学评分，消除大模型数值心算幻觉与漂移。
"""
from typing import Dict, List, Optional, Any
import json
import math
import sys

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── 1. 行业基准权重与战略属性配置 ──────────────────────────────────────────
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


def calc_technical_score(klines: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    计算技术形态分 (0.0 - 5.0)
    维度与权重：
      - 52周相对位置 (0.30)
      - 缩量程度 (0.25)
      - MA排列 (0.20)
      - 5日涨跌幅 (0.15)
      - 振幅收敛度 (0.10)
    """
    if not klines or len(klines) < 5:
        return {"score": 2.5, "signals": ["K线数据不足"], "details": {}}

    closes = [float(k["close"]) for k in klines]
    volumes = [float(k.get("volume", 0)) for k in klines]
    highs = [float(k.get("high", k["close"])) for k in klines]
    lows = [float(k.get("low", k["close"])) for k in klines]
    amplitudes = [
        float(k.get("amplitude", ((h - l) / c * 100 if c > 0 else 0)))
        for k, h, l, c in zip(klines, highs, lows, closes)
    ]

    curr_close = closes[-1]
    signals = []

    # 1. 相对位置 (52周 / 周期内高低点)
    high_all = max(highs)
    low_all = min(lows)
    if high_all > low_all:
        rel_pos = (curr_close - low_all) / (high_all - low_all)
    else:
        rel_pos = 0.5
    
    score_pos = max(0.0, min(5.0, (1.0 - rel_pos) * 5.0))
    if rel_pos <= 0.25:
        signals.append("低位安全区")
    elif rel_pos >= 0.75:
        signals.append("高位风险区")

    # 2. 缩量程度 (当日量 vs 近20日均量)
    lookback_vol = min(20, len(volumes))
    avg_vol_20 = sum(volumes[-lookback_vol:]) / lookback_vol if lookback_vol > 0 else 1.0
    vol_ratio = (volumes[-1] / avg_vol_20) if avg_vol_20 > 0 else 1.0

    if vol_ratio < 0.4:
        score_vol = 5.0
        signals.append("极端地量")
    elif vol_ratio < 0.7:
        score_vol = 4.2
        signals.append("良性缩量")
    elif vol_ratio <= 1.3:
        score_vol = 3.0
    elif vol_ratio <= 2.0:
        score_vol = 2.2
    else:
        score_vol = 1.0
        signals.append("放量异动")

    # 3. MA 排列 (MA5 / MA10 / MA20)
    ma5 = sum(closes[-min(5, len(closes)):]) / min(5, len(closes))
    ma10 = sum(closes[-min(10, len(closes)):]) / min(10, len(closes))
    ma20 = sum(closes[-min(20, len(closes)):]) / min(20, len(closes))

    if ma5 >= ma10 >= ma20:
        score_ma = 5.0
        signals.append("均线多头排列")
    elif curr_close >= ma5 >= ma10:
        score_ma = 4.0
        signals.append("短均线金叉")
    elif ma5 >= ma10 and ma10 < ma20:
        score_ma = 3.2
    elif curr_close < ma5 < ma10 < ma20:
        score_ma = 1.0
        signals.append("均线空头压制")
    else:
        score_ma = 2.5

    # 4. 5日涨跌幅动量
    lookback_5d = min(6, len(closes))
    ref_close = closes[-lookback_5d] if lookback_5d > 1 else curr_close
    chg_5d = (curr_close - ref_close) / ref_close if ref_close > 0 else 0.0

    if chg_5d > 0.05:
        score_chg = 4.5
        signals.append("短期强势")
    elif chg_5d > 0.0:
        score_chg = 3.8
    elif chg_5d > -0.05:
        score_chg = 2.8
    elif chg_5d > -0.10:
        score_chg = 2.0
    else:
        score_chg = 1.0
        signals.append("短期超跌")

    # 5. 振幅收敛度 (近5日均振幅)
    recent_amps = amplitudes[-min(5, len(amplitudes)):]
    avg_amp = sum(recent_amps) / len(recent_amps) if recent_amps else 5.0

    if avg_amp < 3.0:
        score_amp = 5.0
        signals.append("振幅极限收敛")
    elif avg_amp < 5.0:
        score_amp = 4.0
    elif avg_amp < 8.0:
        score_amp = 3.0
    elif avg_amp < 12.0:
        score_amp = 2.0
    else:
        score_amp = 1.0
        signals.append("剧烈震荡")

    total_tech = (
        score_pos * 0.30 +
        score_vol * 0.25 +
        score_ma  * 0.20 +
        score_chg * 0.15 +
        score_amp * 0.10
    )
    final_score = round(max(0.5, min(5.0, total_tech)), 2)

    return {
        "score": final_score,
        "signals": signals,
        "details": {
            "score_pos": round(score_pos, 2),
            "score_vol": round(score_vol, 2),
            "score_ma": round(score_ma, 2),
            "score_chg": round(score_chg, 2),
            "score_amp": round(score_amp, 2),
            "rel_pos_pct": round(rel_pos * 100, 1),
            "vol_ratio": round(vol_ratio, 2),
            "chg_5d_pct": round(chg_5d * 100, 2),
            "avg_amp_5d": round(avg_amp, 2),
        }
    }


def calc_industry_score(
    candidate: Dict[str, Any],
    market_signal: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    计算产业链价值分 (0.0 - 5.0)
    结合卡脖子等级、宏观环境催化（如关税/地缘）、主力流向与相对位置修正。
    """
    sector = candidate.get("primary_sector", candidate.get("sector", ""))
    attr = SECTOR_ATTRIBUTES.get(sector)
    
    # 模糊匹配
    if not attr:
        for k, v in SECTOR_ATTRIBUTES.items():
            if sector and (sector in k or k in sector):
                attr = v
                break
    if not attr:
        attr = {"卡脖子": 2, "关税受益": 1, "长期确定性": 2, "base": 2.0}

    score = float(attr["base"])
    signals = []

    if attr["卡脖子"] >= 4:
        signals.append("卡脖子稀缺层")
    if attr["长期确定性"] >= 4:
        signals.append("长期高确定性")

    market_signal = market_signal or {}

    # 1. 关税/地缘激活加分
    if market_signal.get("tariff_active", False):
        if attr["关税受益"] >= 4:
            score += 1.0
            signals.append("关税/国产替代核心受益")
        elif attr["关税受益"] >= 2:
            score += 0.5

    # 2. 板块资金主流向加分
    top_inflow_sectors = market_signal.get("top_capital_flow_sectors", [])
    if any(s in sector for s in top_inflow_sectors):
        score += 0.5
        signals.append("主力主攻方向")

    # 3. 相对位置修正 (低位加分，高位扣分)
    rel_pos = candidate.get("rel_position_52w")
    if rel_pos is not None:
        if rel_pos < 30:
            score += 0.3
        elif rel_pos > 70:
            score -= 0.5
            signals.append("估值/位置偏高")

    final_score = round(max(1.0, min(5.0, score)), 2)
    return {
        "score": final_score,
        "signals": signals,
        "details": {
            "sector": sector,
            "base_score": attr["base"],
            "chokepoint_rank": attr["卡脖子"],
        }
    }


def calc_catalyst_score(
    candidate: Dict[str, Any],
    sector_flow_data: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    计算催化情绪分 (0.0 - 5.0)
    结合板块净流入金额、板块涨跌幅与个股逆势抗跌表现。
    """
    sector = candidate.get("primary_sector", candidate.get("sector", ""))
    score = 3.0
    signals = []

    # 匹配板块资金流
    flow_matched = None
    if sector_flow_data:
        for f in sector_flow_data:
            s_name = f.get("name", "")
            if s_name and (s_name in sector or sector in s_name):
                flow_matched = f
                break

    if flow_matched:
        net_inflow_str = str(flow_matched.get("main_net_inflow", "0"))
        inflow_val = 0.0
        try:
            if "亿" in net_inflow_str:
                inflow_val = float(net_inflow_str.replace("亿", "").strip())
            elif "万" in net_inflow_str:
                inflow_val = float(net_inflow_str.replace("万", "").strip()) / 10000.0
            else:
                inflow_val = float(net_inflow_str)
        except ValueError:
            inflow_val = 0.0

        if inflow_val >= 10.0:
            score += 1.5
            signals.append(f"板块百亿主力抢筹(+{inflow_val:.1f}亿)")
        elif inflow_val >= 5.0:
            score += 1.0
            signals.append(f"板块主力大幅净流入(+{inflow_val:.1f}亿)")
        elif inflow_val >= 2.0:
            score += 0.5
        elif inflow_val <= -5.0:
            score -= 1.0
            signals.append(f"板块主力大幅出逃({inflow_val:.1f}亿)")

        sec_chg = float(flow_matched.get("change_pct", 0.0))
        if sec_chg >= 2.0:
            score += 0.5
        elif sec_chg <= -2.0:
            score -= 0.5

    # 个股逆势抗跌特征加分
    stock_chg = float(candidate.get("change_pct", 0.0))
    if stock_chg > 0:
        if flow_matched and float(flow_matched.get("change_pct", 0.0)) < -1.0:
            score += 0.6
            signals.append("逆板块强势收红")
        else:
            score += 0.2

    final_score = round(max(1.0, min(5.0, score)), 2)
    return {
        "score": final_score,
        "signals": signals,
        "details": {
            "flow_matched": flow_matched is not None,
        }
    }


def calculate_combined_score(
    candidate: Dict[str, Any],
    klines: Optional[List[Dict[str, Any]]] = None,
    market_signal: Optional[Dict[str, Any]] = None,
    sector_flow_data: Optional[List[Dict[str, Any]]] = None,
    weights: Optional[Dict[str, float]] = None
) -> Dict[str, Any]:
    """
    计算综合评分 (0.0 - 5.0)
    默认权重: 技术 0.40, 产业链 0.40, 催化 0.20
    """
    w = weights or {"technical": 0.40, "industry": 0.40, "catalyst": 0.20}
    w_tech = w.get("technical", 0.40)
    w_ind = w.get("industry", 0.40)
    w_cat = w.get("catalyst", 0.20)

    tech_res = calc_technical_score(klines or [])
    ind_res = calc_industry_score(candidate, market_signal)
    cat_res = calc_catalyst_score(candidate, sector_flow_data)

    combined = (
        tech_res["score"] * w_tech +
        ind_res["score"] * w_ind +
        cat_res["score"] * w_cat
    )
    combined = round(max(1.0, min(5.0, combined)), 2)

    all_signals = list(dict.fromkeys(
        tech_res["signals"] + ind_res["signals"] + cat_res["signals"]
    ))

    if combined >= 4.0:
        action_label = "STRONG_BUY_WATCH"
    elif combined >= 3.3:
        action_label = "ACCUMULATE_ON_DIP"
    elif combined >= 2.5:
        action_label = "NEUTRAL_OBSERVE"
    else:
        action_label = "AVOID_DEFENSE"

    return {
        "code": candidate.get("code", ""),
        "name": candidate.get("name", ""),
        "price": candidate.get("price", 0.0),
        "change_pct": candidate.get("change_pct", 0.0),
        "primary_sector": candidate.get("primary_sector", ""),
        "combined_score": combined,
        "action_label": action_label,
        "scores": {
            "technical": tech_res["score"],
            "industry": ind_res["score"],
            "catalyst": cat_res["score"],
        },
        "signals": all_signals,
        "details": {
            "tech_details": tech_res["details"],
            "weights_used": {"tech": w_tech, "industry": w_ind, "catalyst": w_cat},
        }
    }


if __name__ == "__main__":
    mock_candidate = {
        "code": "sz300054",
        "name": "鼎龙股份",
        "price": 71.52,
        "change_pct": -1.2,
        "rel_position_52w": 25.0,
        "primary_sector": "半导体/封测/材料",
    }
    mock_klines = [
        {"close": 70 + i * 0.1, "volume": 10000, "high": 72, "low": 69, "amplitude": 2.5}
        for i in range(30)
    ]
    res = calculate_combined_score(
        mock_candidate,
        mock_klines,
        market_signal={"tariff_active": True},
        sector_flow_data=[{"name": "半导体", "main_net_inflow": "12.5亿", "change_pct": 1.8}]
    )
    print(json.dumps(res, ensure_ascii=False, indent=2))
