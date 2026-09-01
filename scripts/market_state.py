#!/usr/bin/env python3
"""
市场状态机与仲裁总线 (Market State Machine & Arbitration Bus)
负责识别当前市场宏观状态（震荡 / 恐慌下行 / 右侧反弹），
并动态仲裁各专家子系统（Serenity产业链 vs mi-skill资金风控）的决策权重与硬性规则。
"""
from typing import Dict, List, Optional, Any
from enum import Enum
import json
import os
import sys
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

class MarketRegime(str, Enum):
    NORMAL_OSCILLATION = "NORMAL_OSCILLATION"  # 正常/震荡市
    PANIC_DOWNTREND    = "PANIC_DOWNTREND"     # 恐慌/趋势下行市
    REBOUND_RIGHT_SIDE = "REBOUND_RIGHT_SIDE"  # 反弹/右侧启动市


def evaluate_market_state(
    sh_index: Optional[Dict[str, Any]] = None,
    cy_index: Optional[Dict[str, Any]] = None,
    candidate_summary: Optional[Dict[str, Any]] = None,
    klines_sh: Optional[List[Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    状态识别核心算法：
    1. 恐慌模式判定：
       - 创业板跌幅 <= -5.0%
       - 或 候选池收红比例 < 20% 且 上证跌幅 <= -2.0%
       - 或 连续放量长阴
    2. 反弹启动判定：
       - 缩量企稳后首根中大阳线 (> +1.5%)
       - 且 候选池收红比例 > 70%
    3. 其余判定为正常/震荡市。
    """
    sh_chg = float(sh_index.get("change_pct", 0.0)) if sh_index else 0.0
    cy_chg = float(cy_index.get("change_pct", 0.0)) if cy_index else 0.0

    # 候选池上涨比例
    candidate_green_ratio = 0.5
    if candidate_summary and candidate_summary.get("total", 0) > 0:
        passed = candidate_summary.get("passed", 0)
        up_count = candidate_summary.get("up_count", 0)
        total = candidate_summary.get("total", 1)
        candidate_red_ratio = up_count / total
    else:
        candidate_red_ratio = 0.5

    # 均线位置
    above_ma20 = True
    if klines_sh and len(klines_sh) >= 20:
        closes = [float(k["close"]) for k in klines_sh]
        ma20 = sum(closes[-20:]) / 20.0
        above_ma20 = closes[-1] >= ma20

    # 状态仲裁
    if cy_chg <= -5.0 or (sh_chg <= -2.0 and candidate_red_ratio <= 0.20):
        regime = MarketRegime.PANIC_DOWNTREND
        tone = "恐慌/趋势下行模式：以守为主，严禁盲目开仓抄底，优先评估持仓相对强度，严防两头挨打。"
        weights = {"technical": 0.60, "industry": 0.20, "catalyst": 0.20}
        arbitration_rules = [
            "一票否决：未满足【缩量+不创新低+站上5日线】企稳三条件的开仓信号一律作废",
            "风控接管：长线价值与产业估值降权，防守与相对强度指标成为第一裁判",
            "持仓守则：持仓若相对抗跌（排名前1/3），守仓优于割肉换股",
        ]
    elif (sh_chg >= 1.5 or cy_chg >= 2.5) and candidate_red_ratio >= 0.70 and not above_ma20:
        regime = MarketRegime.REBOUND_RIGHT_SIDE
        tone = "反弹/右侧启动模式：右侧信号初显，优先顺势加仓持仓相对强势股，精选卡脖子主线。"
        weights = {"technical": 0.35, "industry": 0.40, "catalyst": 0.25}
        arbitration_rules = [
            "顺势跟随：主力大单净流入领先的板块享有优先推荐权",
            "聚焦龙头：重点配置卡脖子瓶颈层龙头，回避跟风杂毛",
        ]
    else:
        regime = MarketRegime.NORMAL_OSCILLATION
        tone = "正常/震荡轮动模式：指数在安全区间，执行标准三框架选股，看长做短，量价配合。"
        weights = {"technical": 0.40, "industry": 0.40, "catalyst": 0.20}
        arbitration_rules = [
            "三道硬过滤：绝对价格<100元 / 52周相对位置<70% / 均线多头",
            "双体系融合：产业链价值与资金流形态均衡决策",
        ]

    return {
        "regime": regime.value,
        "strategy_tone": tone,
        "weights": weights,
        "arbitration_rules": arbitration_rules,
        "macro_metrics": {
            "sh_change_pct": sh_chg,
            "cy_change_pct": cy_chg,
            "candidate_red_ratio": round(candidate_red_ratio * 100, 1),
            "above_ma20": above_ma20,
        }
    }


def load_latest_market_state() -> Dict[str, Any]:
    """
    自动从本地 market_snapshots / candidates 缓存中加载并推断最新市场状态
    """
    hermes_home = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    snapshots_dir = hermes_home / "data" / "market_snapshots"
    candidates_dir = hermes_home / "data" / "candidates"

    sh_idx = None
    cy_idx = None
    if snapshots_dir.exists():
        snap_files = sorted(snapshots_dir.glob("*.json"))
        if snap_files:
            try:
                with open(snap_files[-1], "r", encoding="utf-8") as f:
                    snap_data = json.load(f)
                    indices = snap_data.get("indices", {})
                    sh_idx = indices.get("上证指数") or indices.get("sh000001")
                    cy_idx = indices.get("创业板指") or indices.get("sz399006")
            except Exception:
                pass

    cand_summary = None
    if candidates_dir.exists():
        cand_files = sorted(candidates_dir.glob("*.json"))
        if cand_files:
            try:
                with open(cand_files[-1], "r", encoding="utf-8") as f:
                    cand_data = json.load(f)
                    cand_list = cand_data.get("candidates", [])
                    up_count = sum(1 for c in cand_list if float(c.get("change_pct", 0)) > 0)
                    cand_summary = {
                        "total": len(cand_list),
                        "up_count": up_count,
                        "passed": cand_data.get("passed", len(cand_list))
                    }
            except Exception:
                pass

    return evaluate_market_state(sh_index=sh_idx, cy_index=cy_idx, candidate_summary=cand_summary)


if __name__ == "__main__":
    state = load_latest_market_state()
    print(json.dumps(state, ensure_ascii=False, indent=2))
