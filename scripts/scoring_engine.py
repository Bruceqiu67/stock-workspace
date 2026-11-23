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

try:
    from scoring_config import (
        ACTION_THRESHOLDS,
        CONFIG_VERSION,
        DEFAULT_WEIGHTS,
        SECTOR_ATTRIBUTES,
        action_label_from_canonical,
        canonical_from_components,
        scale_mappings,
    )
except ImportError:  # 配置缺失时显式降级，不在本文件复制第二套权重表
    ACTION_THRESHOLDS = {"strong_buy_watch": 80.0, "accumulate_on_dip": 66.0,
                         "neutral_observe": 50.0}
    CONFIG_VERSION = "unavailable"
    DEFAULT_WEIGHTS = {"technical": 0.40, "industry": 0.40, "catalyst": 0.20}
    SECTOR_ATTRIBUTES = {}

    def canonical_from_components(technical_0_5, industry_0_5, catalyst_0_5, weights=None):
        return None

    def action_label_from_canonical(canonical_score):
        return "ABSTAIN_NO_DATA" if canonical_score is None else "NEUTRAL_OBSERVE"

    def scale_mappings(canonical_score):
        return {"canonical_0_100": canonical_score, "config_status": "unavailable"}

try:
    from company_profiles import (
        company_alpha_score,
        profile_summary,
        resolve_company_profile,
    )
except ImportError:  # 单独运行时的兜底：公司画像缺失，产业 alpha 弃权
    def resolve_company_profile(code, sector, name="", external=None):
        return {"code": code, "source": "missing", "specificity": "none"}

    def company_alpha_score(profile):
        return {"score": None, "status": "abstain",
                "missing_inputs": ["company_profile"], "confidence": "none",
                "details": {}}

    def profile_summary(profile):
        return {"status": "missing"}

try:
    from analysis_context import context_summary
except ImportError:  # 单独运行时的兜底，不改变契约语义
    def context_summary(context: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "status": (context or {}).get("status", "missing"),
            "as_of": (context or {}).get("as_of"),
            "source": (context or {}).get("source"),
            "completeness": (context or {}).get("completeness", 0.0),
            "stale": (context or {}).get("stale", True),
        }

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

SCORING_SCHEMA_VERSION = CONFIG_VERSION


def _parse_net_inflow(value: Any) -> float:
    """把 '8.5亿' / '3200万' / '-1.2亿' 统一解析为「亿元」。"""
    text = str(value or "0").strip()
    if not text:
        return 0.0
    sign = -1.0 if text.startswith("-") else 1.0
    text = text.lstrip("+-")
    try:
        if "亿" in text:
            return sign * float(text.replace("亿", "").strip())
        if "万" in text:
            return sign * float(text.replace("万", "").strip()) / 10000.0
        return sign * float(text)
    except ValueError:
        return 0.0

# ── 1. 行业基准权重与战略属性配置（单一事实源：scoring_config.py） ─────────
def calc_technical_score(
    klines: List[Dict[str, Any]],
    weekly_klines: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    计算技术形态分 (0.0 - 5.0)

    维度与权重（Q2 起使用真实 52 周窗口与周线）：
      - 52周相对位置 (0.25)  ← 使用 >=120 日（目标 250 日）日K高低点
      - 缩量程度 (0.20)
      - MA5/10/20/60 排列 (0.25)
      - 5日涨跌幅 (0.10)
      - 振幅收敛度 (0.10)
      - 周线趋势 (0.10)      ← 周线定方向

    数据窗口不足（<120日）或缺周线时 status=degraded，不把 30 日窗口
    伪装成 52 周，也不把缺失的周线当成中性。
    """
    if not klines or len(klines) < 5:
        return {
            "score": None,
            "status": "abstain",
            "status_reason": "K线数据不足（<5 根），技术分不参与排名",
            "signals": ["K线数据不足"],
            "details": {"data_window_days": len(klines or [])},
            "missing_inputs": ["klines"],
        }

    # 显式拒绝 NaN / inf / 非正价格 / 零成交量，避免静默污染评分
    closes: List[float] = []
    volumes: List[float] = []
    highs: List[float] = []
    lows: List[float] = []
    for k in klines:
        try:
            c = float(k["close"])
            v = float(k.get("volume", 0) or 0)
            h = float(k.get("high", c))
            l = float(k.get("low", c))
        except (KeyError, TypeError, ValueError):
            return {
                "score": None,
                "status": "abstain",
                "status_reason": "K线存在非数值字段，技术分弃权",
                "signals": ["K线字段非法"],
                "details": {"data_window_days": len(klines)},
                "missing_inputs": ["klines.numeric"],
            }
        if not all(math.isfinite(x) for x in (c, v, h, l)) or c <= 0 or h <= 0 or l <= 0:
            return {
                "score": None,
                "status": "abstain",
                "status_reason": "K线包含 NaN/inf/非正价格，技术分弃权",
                "signals": ["K线数值异常"],
                "details": {"data_window_days": len(klines)},
                "missing_inputs": ["klines.valid"],
            }
        if v < 0:
            return {
                "score": None,
                "status": "abstain",
                "status_reason": "K线成交量存在负值，技术分弃权",
                "signals": ["成交量异常"],
                "details": {"data_window_days": len(klines)},
                "missing_inputs": ["klines.volume"],
            }
        closes.append(c)
        volumes.append(v)
        highs.append(h)
        lows.append(l)

    if volumes[-1] <= 0:
        return {
            "score": None,
            "status": "abstain",
            "status_reason": "最新交易日成交量为0，疑似停牌/数据缺失，技术分弃权",
            "signals": ["最新成交量缺失"],
            "details": {"data_window_days": len(klines)},
            "missing_inputs": ["klines.volume.latest"],
        }
    if sum(volumes) <= 0:
        return {
            "score": None,
            "status": "abstain",
            "status_reason": "区间成交量全为0，技术分弃权",
            "signals": ["成交量全缺失"],
            "details": {"data_window_days": len(klines)},
            "missing_inputs": ["klines.volume"],
        }

    amplitudes: List[float] = []
    for k, h, l, c in zip(klines, highs, lows, closes):
        fallback_amp = (h - l) / c * 100 if c > 0 else 0.0
        try:
            amp = float(k.get("amplitude", fallback_amp))
        except (TypeError, ValueError):
            amp = fallback_amp
        if not math.isfinite(amp) or amp < 0:
            amp = fallback_amp
        amplitudes.append(amp)

    curr_close = closes[-1]
    signals = []

    # 1. 52周相对位置（最多取最近 250 个交易日，绝不把 30 日窗口当成 52 周）
    position_window_size = min(len(klines), 250)
    position_window = klines[-position_window_size:]
    position_highs = highs[-position_window_size:]
    position_lows = lows[-position_window_size:]
    high_all = max(position_highs)
    low_all = min(position_lows)
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

    # 3. MA 排列 (MA5 / MA10 / MA20 / MA60)
    def _ma(period: int) -> float:
        window = closes[-min(period, len(closes)):]
        return sum(window) / len(window)

    ma5, ma10, ma20, ma60 = _ma(5), _ma(10), _ma(20), _ma(60)
    if ma5 >= ma10 >= ma20 >= ma60:
        score_ma = 5.0
        signals.append("均线多头排列(含MA60)")
    elif ma5 >= ma10 >= ma20:
        score_ma = 4.2
        signals.append("均线多头排列")
    elif curr_close >= ma5 >= ma10:
        score_ma = 3.8
        signals.append("短均线金叉")
    elif ma5 >= ma10 and ma10 < ma20:
        score_ma = 3.0
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

    # 6. 周线趋势（周线定方向）
    weekly_status = "missing"
    score_weekly = 2.5
    weekly_details: Dict[str, Any] = {
        "weekly_window_days": len(weekly_klines or []),
    }
    if weekly_klines and len(weekly_klines) >= 20:
        wcloses = []
        for k in weekly_klines:
            value = k.get("close")
            try:
                wcloses.append(float(value))
            except (TypeError, ValueError):
                wcloses.append(None)
        if all(v is not None for v in wcloses[-20:]):
            wclose = wcloses[-1]
            wma5 = sum(wcloses[-5:]) / 5.0
            wma10 = sum(wcloses[-10:]) / 10.0
            wma20 = sum(wcloses[-20:]) / 20.0
            weekly_status = "ok"
            if wclose >= wma20 and wma5 >= wma10:
                score_weekly = 5.0
                signals.append("周线趋势向上")
            elif wclose >= wma20:
                score_weekly = 3.5
                signals.append("周线站上MA20")
            elif wma5 >= wma10:
                score_weekly = 2.5
            else:
                score_weekly = 1.0
                signals.append("周线趋势向下")
            weekly_details.update({
                "weekly_close": round(wclose, 2),
                "weekly_ma5": round(wma5, 2),
                "weekly_ma10": round(wma10, 2),
                "weekly_ma20": round(wma20, 2),
            })
        else:
            signals.append("周线数据存在非数值")
    else:
        signals.append("周线数据缺失")

    status = "ok"
    status_reason = "技术窗口完整（52周+周线）"
    missing_inputs: List[str] = []
    if len(klines) < 120:
        status = "degraded"
        missing_inputs.append("klines(>=120 for 52w)")
    if weekly_status != "ok":
        status = "degraded"
        missing_inputs.append("weekly_klines(>=20)")
    if missing_inputs:
        status_reason = "技术窗口不足: " + ", ".join(missing_inputs)

    total_tech = (
        score_pos   * 0.25 +
        score_vol   * 0.20 +
        score_ma    * 0.25 +
        score_chg   * 0.10 +
        score_amp   * 0.10 +
        score_weekly * 0.10
    )
    final_score = round(max(0.5, min(5.0, total_tech)), 2)

    return {
        "score": final_score,
        "status": status,
        "status_reason": status_reason,
        "signals": signals,
        "missing_inputs": missing_inputs,
        "details": {
            "score_pos": round(score_pos, 2),
            "score_vol": round(score_vol, 2),
            "score_ma": round(score_ma, 2),
            "score_chg": round(score_chg, 2),
            "score_amp": round(score_amp, 2),
            "score_weekly": round(score_weekly, 2),
            "rel_pos_pct": round(rel_pos * 100, 1),
            "vol_ratio": round(vol_ratio, 2),
            "chg_5d_pct": round(chg_5d * 100, 2),
            "avg_amp_5d": round(avg_amp, 2),
            "ma5": round(ma5, 2),
            "ma10": round(ma10, 2),
            "ma20": round(ma20, 2),
            "ma60": round(ma60, 2),
            "data_window_days": len(klines),
            "position_window_days": len(position_window),
            "weekly_status": weekly_status,
            **weekly_details,
        }
    }


def calc_industry_score(
    candidate: Dict[str, Any],
    market_signal: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """
    计算产业链价值分 (0.0 - 5.0)
    结合卡脖子等级、宏观环境催化（如关税/地缘）与主力主攻方向。

    market_signal 既可以是 analysis_context 契约对象，也可以是旧的
    扁平 market_signal。动态字段缺失时返回 status=degraded，并在
    missing_inputs 中列出缺项，禁止把「没有数据」当成「没有催化」。
    """
    context: Dict[str, Any] = market_signal or {}
    signal = context.get("market_signal") if isinstance(
        context.get("market_signal"), dict
    ) else context

    sector = candidate.get("primary_sector", candidate.get("sector", ""))
    attr = SECTOR_ATTRIBUTES.get(sector)

    # 模糊匹配
    if not attr:
        for k, v in SECTOR_ATTRIBUTES.items():
            if sector and (sector in k or k in sector):
                attr = v
                break
    sector_known = attr is not None
    if not attr:
        attr = {"卡脖子": 2, "关税受益": 1, "长期确定性": 2, "base": 2.0}

    score = float(attr["base"])
    signals: List[str] = []
    missing_inputs: List[str] = []
    dynamic_used: List[str] = []
    if not sector_known:
        missing_inputs.append("sector_profile")

    if attr["卡脖子"] >= 4:
        signals.append("卡脖子稀缺层")
    if attr["长期确定性"] >= 4:
        signals.append("长期高确定性")

    # 1. 关税/地缘激活加分：None 表示未知，不等于未激活
    tariff_state = signal.get("tariff_active")
    if tariff_state is None:
        missing_inputs.append("tariff_active")
    else:
        dynamic_used.append("tariff_active")
        if tariff_state:
            if attr["关税受益"] >= 4:
                score += 1.0
                signals.append("关税/国产替代核心受益")
            elif attr["关税受益"] >= 2:
                score += 0.5

    # 2. 板块资金主流向加分
    top_inflow_sectors = (
        signal.get("top_capital_flow_sectors")
        or signal.get("mainline_sectors")
        or []
    )
    if top_inflow_sectors:
        dynamic_used.append("top_capital_flow_sectors")
        if any(s in sector or sector in s for s in top_inflow_sectors):
            score += 0.5
            signals.append("主力主攻方向")
    else:
        missing_inputs.append("top_capital_flow_sectors")

    # 相对位置只在技术分的 52周位置因子里计分，产业分不再重复奖励/惩罚。

    # 产业分 = 板块 beta（60%）+ 公司 alpha（40%），公司级画像按代码解析。
    sector_beta_score = round(max(1.0, min(5.0, score)), 2)
    company_profile = resolve_company_profile(
        candidate.get("code", ""), sector, candidate.get("name", "")
    )
    alpha = company_alpha_score(company_profile)
    if alpha.get("score") is None:
        final_score = sector_beta_score
        missing_inputs.append("company_profile")
        alpha_confidence = "none"
    else:
        final_score = round(max(1.0, min(5.0,
            sector_beta_score * 0.60 + alpha["score"] * 0.40)), 2)
        alpha_confidence = alpha.get("confidence", "low")

    status = "ok" if not missing_inputs else "degraded"
    return {
        "score": final_score,
        "status": status,
        "status_reason": (
            "产业动态字段与公司画像齐全" if status == "ok"
            else "产业输入降级: " + ", ".join(missing_inputs)
        ),
        "signals": signals,
        "missing_inputs": missing_inputs,
        "dynamic_inputs_used": dynamic_used,
        "details": {
            "sector": sector,
            "sector_beta": sector_beta_score,
            "base_score": attr["base"],
            "chokepoint_rank": attr["卡脖子"],
            "company_alpha": alpha.get("score"),
            "alpha_weight": 0.40,
            "alpha_confidence": alpha_confidence,
            "company_profile": profile_summary(company_profile),
            "market_signal_used": bool(dynamic_used),
        },
    }


def calc_catalyst_score(
    candidate: Dict[str, Any],
    sector_flow_data: Optional[List[Dict[str, Any]]] = None,
    main_fund_rank: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    计算催化情绪分 (0.0 - 5.0)
    结合板块净流入金额、板块涨跌幅与个股逆势抗跌表现。

    板块资金流缺失或无法匹配到候选方向时返回 score=None + status=abstain。
    催化分不能从 3.0 的“中性起点”继续累加，否则缺失数据会被当成中性偏上。
    """
    sector = candidate.get("primary_sector", candidate.get("sector", ""))

    if not sector_flow_data:
        return {
            "score": None,
            "status": "abstain",
            "status_reason": "板块资金流数据缺失，催化分不参与排名",
            "signals": ["板块资金流缺失"],
            "missing_inputs": ["sector_flow_data"],
            "dynamic_inputs_used": [],
            "details": {"flow_matched": False},
        }

    # 匹配板块资金流
    flow_matched = None
    for f in sector_flow_data:
        if not isinstance(f, dict):
            continue
        s_name = f.get("name", "")
        if s_name and (s_name in sector or sector in s_name):
            flow_matched = f
            break

    if not flow_matched:
        return {
            "score": None,
            "status": "abstain",
            "status_reason": f"未在板块资金流中匹配到方向「{sector}」",
            "signals": ["板块资金流未匹配"],
            "missing_inputs": ["sector_flow_data.match"],
            "dynamic_inputs_used": ["sector_flow_data"],
            "details": {"flow_matched": False},
        }

    score = 3.0
    signals: List[str] = []
    dynamic_used = ["sector_flow_data"]

    inflow_val = _parse_net_inflow(flow_matched.get("main_net_inflow", "0"))
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

    sec_chg = float(flow_matched.get("change_pct", 0.0) or 0.0)
    if sec_chg >= 2.0:
        score += 0.5
    elif sec_chg <= -2.0:
        score -= 0.5

    # 个股逆势抗跌特征加分
    stock_chg = float(candidate.get("change_pct", 0.0) or 0.0)
    if stock_chg > 0:
        if sec_chg < -1.0:
            score += 0.6
            signals.append("逆板块强势收红")
        else:
            score += 0.2

    # 个股主力资金排行（可选）只做小幅修正，不替代板块资金流
    code = str(candidate.get("code", ""))
    stock_flow = None
    for row in (main_fund_rank or []):
        if not isinstance(row, dict):
            continue
        row_code = str(row.get("code", ""))
        if row_code and (row_code == code or row_code in code):
            stock_flow = row
            break
    if stock_flow:
        dynamic_used.append("main_fund_rank")
        stock_inflow = _parse_net_inflow(stock_flow.get("main_net_inflow", "0"))
        if stock_inflow >= 2.0:
            score += 0.3
            signals.append("个股主力净流入")
        elif stock_inflow <= -2.0:
            score -= 0.3
            signals.append("个股主力净流出")

    final_score = round(max(1.0, min(5.0, score)), 2)
    return {
        "score": final_score,
        "status": "ok",
        "status_reason": "板块资金流已匹配",
        "signals": signals,
        "missing_inputs": [],
        "dynamic_inputs_used": dynamic_used,
        "details": {
            "flow_matched": True,
            "sector": flow_matched.get("name", ""),
            "main_net_inflow": inflow_val,
            "sector_change_pct": sec_chg,
        },
    }


def calc_risk_score(
    technical_result: Dict[str, Any],
    market_regime: Optional[str] = None,
) -> Dict[str, Any]:
    """
    风险子分 (0-5，越高越安全)。作为诊断维度输出，不改变既有
    technical/industry/catalyst 的排名权重，但会显式反映：
      - 位置越高、波动越大、数据越不完整、市场状态越差，风险分越低。
    """
    if technical_result.get("status") == "abstain":
        return {
            "score": None,
            "status": "abstain",
            "status_reason": "技术数据缺失，风险分弃权",
            "signals": ["风险不可评估"],
        }
    details = technical_result.get("details", {})
    score = 3.0
    signals: List[str] = []
    missing: List[str] = []

    rel_pos = details.get("rel_pos_pct")
    if rel_pos is None:
        missing.append("rel_pos_pct")
    elif rel_pos >= 75:
        score -= 0.8
        signals.append("高位风险")
    elif rel_pos <= 25:
        score += 0.4
        signals.append("低位安全边际")
    elif rel_pos >= 60:
        score -= 0.3

    vol_ratio = details.get("vol_ratio")
    if vol_ratio is None:
        missing.append("vol_ratio")
    elif vol_ratio > 2.0:
        score -= 0.5
        signals.append("放量风险")
    elif vol_ratio < 0.7:
        score += 0.2

    if details.get("weekly_status") != "ok":
        score -= 0.4
        missing.append("weekly_trend")

    if technical_result.get("status") == "degraded":
        score -= 0.5

    if market_regime == "PANIC_DOWNTREND":
        score -= 0.7
        signals.append("恐慌态风险溢价")
    elif market_regime == "REBOUND_RIGHT_SIDE":
        score += 0.2
    elif market_regime == "UNKNOWN":
        score -= 0.8
        signals.append("市场状态未知")
    elif market_regime is None:
        missing.append("market_regime")
        score -= 0.3

    final = round(max(1.0, min(5.0, score)), 2)
    return {
        "score": final,
        "status": "ok" if not missing else "degraded",
        "status_reason": "风险输入完整" if not missing else "风险输入缺失: " + ", ".join(missing),
        "signals": signals,
        "missing_inputs": missing,
    }


def calculate_combined_score(
    candidate: Dict[str, Any],
    klines: Optional[List[Dict[str, Any]]] = None,
    market_signal: Optional[Dict[str, Any]] = None,
    sector_flow_data: Optional[List[Dict[str, Any]]] = None,
    weights: Optional[Dict[str, float]] = None,
    analysis_context: Optional[Dict[str, Any]] = None,
    main_fund_rank: Optional[List[Dict[str, Any]]] = None,
    weekly_klines: Optional[List[Dict[str, Any]]] = None,
    market_regime: Optional[str] = None,
    as_of: Optional[str] = None,
    data_freshness: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    计算综合评分 (0.0 - 5.0)
    默认权重: 技术 0.40, 产业链 0.40, 催化 0.20

    analysis_context 是唯一推荐的动态输入入口；直接传 market_signal /
    sector_flow_data 仅用于兼容旧调用。动态数据缺失时：
      - 关键分缺失 -> combined_score=None, scoring_status=abstain
      - 非关键动态字段缺失 -> 仍给出诊断分，但 rankable=False，
        且不输出买入标签（DATA_DEGRADED）。
    """
    ctx = analysis_context or {}
    if ctx:
        # 上下文对象本身就是 market_signal 的完整形态
        if market_signal is None:
            market_signal = ctx
        if not sector_flow_data:
            sector_flow_data = ctx.get("sector_flow_data") or []
        if not main_fund_rank:
            main_fund_rank = ctx.get("main_fund_rank") or []

    w = dict(DEFAULT_WEIGHTS)
    w.update(weights or {})
    w_tech = w.get("technical", 0.40)
    w_ind = w.get("industry", 0.40)
    w_cat = w.get("catalyst", 0.20)
    total_weight = w_tech + w_ind + w_cat
    if abs(total_weight - 1.0) > 1e-6:
        # 权重非法时不悄悄归一化，显式标记并回退默认权重
        weight_warning = f"权重之和={total_weight:.4f}，已回退默认 0.40/0.40/0.20"
        w_tech, w_ind, w_cat = 0.40, 0.40, 0.20
    else:
        weight_warning = None

    tech_res = calc_technical_score(klines or [], weekly_klines)
    ind_res = calc_industry_score(candidate, market_signal)
    cat_res = calc_catalyst_score(candidate, sector_flow_data, main_fund_rank)

    sub_scores = {
        "technical": tech_res.get("score"),
        "industry": ind_res.get("score"),
        "catalyst": cat_res.get("score"),
    }
    sub_status = {
        "technical": tech_res.get("status", "ok"),
        "industry": ind_res.get("status", "ok"),
        "catalyst": cat_res.get("status", "ok"),
    }
    risk_res = calc_risk_score(tech_res, market_regime)

    missing_inputs: List[str] = []
    for name, res in (("technical", tech_res), ("industry", ind_res),
                      ("catalyst", cat_res)):
        if res.get("score") is None or res.get("status") == "abstain":
            missing_inputs.extend(res.get("missing_inputs") or [name])

    if (any(value is None for value in sub_scores.values())
            or any(status == "abstain" for status in sub_status.values())):
        combined = None
        canonical_score = None
        action_label = "ABSTAIN_NO_DATA"
        scoring_status = "abstain"
        rankable = False
    else:
        combined = (
            sub_scores["technical"] * w_tech +
            sub_scores["industry"] * w_ind +
            sub_scores["catalyst"] * w_cat
        )
        combined = round(max(1.0, min(5.0, combined)), 2)
        canonical_score = canonical_from_components(
            sub_scores["technical"],
            sub_scores["industry"],
            sub_scores["catalyst"],
            weights={"technical": w_tech, "industry": w_ind, "catalyst": w_cat},
        )
        if canonical_score is None:
            # 配置不可用时退回 0-5 的机械换算，仍保持同一量表
            canonical_score = round(combined * 20.0, 1)
        if any(status == "degraded" for status in sub_status.values()):
            scoring_status = "degraded"
            rankable = False
            action_label = "DATA_DEGRADED"
        else:
            scoring_status = "ok"
            rankable = True
            action_label = action_label_from_canonical(canonical_score)

    all_signals = list(dict.fromkeys(
        tech_res.get("signals", [])
        + ind_res.get("signals", [])
        + cat_res.get("signals", [])
    ))

    return {
        "schema_version": SCORING_SCHEMA_VERSION,
        "as_of": as_of,
        "data_freshness": data_freshness,
        "code": candidate.get("code", ""),
        "name": candidate.get("name", ""),
        "price": candidate.get("price", 0.0),
        "change_pct": candidate.get("change_pct", 0.0),
        "primary_sector": candidate.get("primary_sector", ""),
        "combined_score": combined,
        "canonical_score": canonical_score,
        "canonical_version": CONFIG_VERSION,
        "canonical_breakdown": {
            "technical_0_100": round(sub_scores["technical"] * 20, 1) if sub_scores["technical"] is not None else None,
            "industry_0_100": round(sub_scores["industry"] * 20, 1) if sub_scores["industry"] is not None else None,
            "catalyst_0_100": round(sub_scores["catalyst"] * 20, 1) if sub_scores["catalyst"] is not None else None,
            "risk_0_100": round(risk_res["score"] * 20, 1) if risk_res.get("score") is not None else None,
        },
        "score_scales": scale_mappings(canonical_score),
        "action_thresholds": ACTION_THRESHOLDS,
        "risk": risk_res,
        "weight_warning": weight_warning,
        "action_label": action_label,
        "scoring_status": scoring_status,
        "rankable": rankable,
        "missing_inputs": list(dict.fromkeys(missing_inputs)),
        "scores": sub_scores,
        "data_quality": {
            name: {
                "status": res.get("status", "ok"),
                "reason": res.get("status_reason", ""),
                "missing_inputs": res.get("missing_inputs", []),
            }
            for name, res in (("technical", tech_res), ("industry", ind_res),
                              ("catalyst", cat_res))
        },
        "signals": all_signals,
        "details": {
            "tech_details": tech_res.get("details", {}),
            "industry_details": ind_res.get("details", {}),
            "catalyst_details": cat_res.get("details", {}),
            "weights_used": {"tech": w_tech, "industry": w_ind, "catalyst": w_cat},
        },
        "analysis_context": context_summary(ctx),
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
