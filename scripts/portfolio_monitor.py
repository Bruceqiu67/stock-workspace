#!/usr/bin/env python3
"""
统一持仓监控脚本 — 消费市场状态与候选池相对强度（S4 / A1）

关键变化：
  - 不再只用固定止损线产生告警。恐慌态（PANIC_DOWNTREND）下，机械止损会让
    用户在“两头挨打”中反复割肉；因此改为“绝对风险底线 + 相对强度 + 企稳条件”
    三层判断，并显示距止损、相对排名、是否缩量三项依据。
  - 正常/反弹态仍保留硬止损纪律；绝对风险底线（跌破止损缓冲或浮亏≥20%）
    在任何状态下都告警，不因市场状态而取消。
  - 读取最新候选池 JSON 的 market_state / relative_strength / stabilization，
    并把 A1 组合层的目标暴露、目标权重与调仓建议写入持仓报告。

输出仍写入 ~/.hermes/data/portfolio/current.json；退出码固定 0（告警不是错误）。
"""
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from market_data_adapter import fetch_tencent_quotes
except ImportError:
    fetch_tencent_quotes = None
try:
    from market_state import load_latest_market_state
except ImportError:
    load_latest_market_state = None
try:
    from portfolio_construction import build_portfolio, REGIME_EXPOSURE
except ImportError:
    build_portfolio = None
    REGIME_EXPOSURE = {}

DATA_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "portfolio"
CANDIDATES_DIR = (
    Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes"))
    / "data" / "candidates"
)
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── 持仓配置 ──────────────────────────────────
# 格式: code: {name, cost, shares, stop_loss}
# ⚠️ 示例数据，使用前请替换为你的真实持仓
PORTFOLIO = {
    "sz000001": {"name": "示例股票A", "cost": 10.00, "shares": 1000, "stop_loss": 9.00},
    "sh600000": {"name": "示例股票B", "cost": 20.00, "shares": 500, "stop_loss": 18.00},
}

DEFAULT_EXPOSURE = {
    "NORMAL_OSCILLATION": 0.80,
    "REBOUND_RIGHT_SIDE": 0.70,
    "PANIC_DOWNTREND": 0.30,
    "UNKNOWN": 0.0,
}
ABSOLUTE_FLOOR_PCT = -20.0       # 任何状态下浮亏达到 -20% 都触发绝对底线
ABSOLUTE_STOP_BUFFER = 0.08      # 跌破止损线 8% 以上视为绝对底线
PANIC_STRONG_RANK = 66.7         # 候选池相对强度前 1/3
PANIC_WEAK_RANK = 33.3           # 候选池相对强度后 1/3


def fetch_prices(codes):
    """批量获取行情（解析统一走 market_data_adapter）。"""
    if fetch_tencent_quotes is None:
        return {}
    return fetch_tencent_quotes(codes, min_fields=40)


def _load_latest_candidates(today):
    """读取日期 <= today 的最新候选池快照。"""
    if not CANDIDATES_DIR.exists():
        return None, None
    files = sorted(
        path for path in CANDIDATES_DIR.glob("*.json")
        if not path.name.startswith("refresh_state")
    )
    files = [path for path in files if path.stem[:10] <= today]
    if not files:
        return None, None
    path = files[-1]
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None, None
    return data, path.stem[:10]


def _candidate_map(latest):
    if not latest:
        return {}
    return {
        candidate.get("code"): candidate
        for candidate in latest.get("candidates", [])
        if candidate.get("code")
    }


def _market_context(latest, target_date):
    """优先复用候选池落盘的市场状态，避免持仓脚本再拉一次指数。"""
    if latest and isinstance(latest.get("market_state"), dict):
        return latest["market_state"]
    if load_latest_market_state:
        try:
            return load_latest_market_state(target_date=target_date)
        except Exception:
            pass
    return {"regime": "UNKNOWN", "missing_inputs": ["market_state"]}


def _stabilization_text(candidate):
    stabilization = (candidate or {}).get("stabilization") or {}
    conditions = stabilization.get("conditions") or {}
    shrink = conditions.get("volume_shrink") or {}
    ratio = shrink.get("actual_ratio")
    try:
        ratio = float(ratio)
    except (TypeError, ValueError):
        ratio = None
    if ratio is None:
        return "缩量: 数据缺失"
    return f"缩量: {ratio:.2f}x（阈值≤0.50x）"


def _alert_for_holding(regime, cur, stop, pnl_pct, candidate):
    """按市场状态与相对强度生成告警；绝对风险底线始终优先。"""
    relative_rank = None
    if candidate:
        try:
            relative_rank = float(candidate.get("relative_strength_pct"))
        except (TypeError, ValueError):
            relative_rank = None
    stabilization = (candidate or {}).get("stabilization") or {}
    stabilized = bool(stabilization.get("passed"))
    distance_to_stop = (
        (cur - stop) / cur * 100.0 if cur > 0 and stop > 0 else None
    )
    bases = [
        f"距止损: {distance_to_stop:+.1f}%" if distance_to_stop is not None else "距止损: N/A",
        f"相对排名: {relative_rank:.0f}%" if relative_rank is not None else "相对排名: N/A",
        _stabilization_text(candidate),
    ]
    absolute_floor = (
        (stop > 0 and cur < stop * (1 - ABSOLUTE_STOP_BUFFER))
        or pnl_pct <= ABSOLUTE_FLOOR_PCT
    )
    if absolute_floor:
        return {
            "level": "critical",
            "text": " 🚨 绝对风险底线",
            "bases": bases,
            "reason": "跌破止损缓冲或浮亏达到绝对底线",
        }

    if regime == "PANIC_DOWNTREND":
        if candidate is None:
            return {
                "level": "warn",
                "text": " ⚠️ 恐慌态·无相对强度数据",
                "bases": bases,
                "reason": "候选池无该标的，按绝对底线管理",
            }
        if relative_rank is not None and relative_rank >= PANIC_STRONG_RANK and not stabilized:
            return {
                "level": "info",
                "text": " 🛡 恐慌态·相对强势，防守观察",
                "bases": bases,
                "reason": "相对强度前1/3，不机械止损；等企稳三条件",
            }
        if relative_rank is not None and relative_rank <= PANIC_WEAK_RANK:
            return {
                "level": "warn",
                "text": " 🔴 恐慌态·相对弱势，优先减仓",
                "bases": bases,
                "reason": "相对强度后1/3且未企稳，弱于候选池",
            }
        return {
            "level": "warn",
            "text": " ⚠️ 恐慌态·观察企稳",
            "bases": bases,
            "reason": "未通过企稳三条件，暂不机械止损也不加仓",
        }

    if regime == "UNKNOWN":
        return {
            "level": "warn",
            "text": " ⚠️ 数据不足·仅守绝对底线",
            "bases": bases,
            "reason": "市场状态未知，不输出方向性动作",
        }

    # 正常/反弹态：保留硬止损纪律。
    if cur < stop:
        return {
            "level": "critical",
            "text": " 🚨 破止损！",
            "bases": bases,
            "reason": "正常态硬止损纪律",
        }
    if pnl_pct < -10:
        return {
            "level": "critical",
            "text": " 🔴 浮亏>10%",
            "bases": bases,
            "reason": "正常态浮亏警戒",
        }
    if pnl_pct < -5:
        return {
            "level": "warn",
            "text": " ⚠️ 浮亏>5%",
            "bases": bases,
            "reason": "正常态浮亏观察",
        }
    if pnl_pct > 15:
        return {
            "level": "info",
            "text": " ✅ 浮盈>15%",
            "bases": bases,
            "reason": "盈利保护",
        }
    if pnl_pct > 0:
        return {
            "level": "info",
            "text": " ✅ 盈利",
            "bases": bases,
            "reason": "正常持有",
        }
    return {
        "level": "info",
        "text": " 🔸 微亏",
        "bases": bases,
        "reason": "正常持有",
    }


def main():
    codes = list(PORTFOLIO.keys())
    quotes = fetch_prices(codes)

    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    latest, latest_date = _load_latest_candidates(today)
    market_state = _market_context(latest, today)
    regime = market_state.get("regime", "UNKNOWN")
    candidate_by_code = _candidate_map(latest)

    total_cost = sum(s["cost"] * s["shares"] for s in PORTFOLIO.values())
    total_market = 0
    rows = []

    # 先计算当前市值权重，供组合层做调仓建议。
    market_values = {}
    for code, info in PORTFOLIO.items():
        quote = quotes.get(code)
        if quote and float(quote.get("price") or 0) > 0:
            market_values[code] = float(quote.get("price") or 0) * info["shares"]
    total_equity = sum(market_values.values()) or 0.0
    current_weights = {
        code: (value / total_equity if total_equity > 0 else 0.0)
        for code, value in market_values.items()
    }

    portfolio_plan = {}
    if build_portfolio and latest:
        portfolio_plan = build_portfolio(
            latest.get("candidates", []),
            market_regime=regime,
            current_positions=current_weights,
            as_of=latest_date or today,
        )
    target_exposure = (
        portfolio_plan.get("total_weight")
        if portfolio_plan else DEFAULT_EXPOSURE.get(regime, 0.0)
    )
    cash_pct = (
        portfolio_plan.get("cash_pct")
        if portfolio_plan else round(1.0 - target_exposure, 4)
    )
    plan_weights = {
        position.get("code"): position.get("target_weight")
        for position in portfolio_plan.get("positions", [])
    }
    adjustments = {
        item.get("code"): item
        for item in portfolio_plan.get("adjustments", [])
    }

    print(f"📊 持仓监控 · {today} {now}")
    print(f"  市场状态: {regime}（候选池数据日 {latest_date or 'N/A'}）")
    print(f"  组合目标暴露: {target_exposure:.0%} / 现金: {cash_pct:.0%}")
    if regime == "PANIC_DOWNTREND":
        print("  🛡 恐慌态：不机械执行止损；按绝对底线+相对强度+企稳条件判断")
    print()

    alarm_count = 0
    for code, info in PORTFOLIO.items():
        quote = quotes.get(code)
        name = info["name"]
        cost_p = info["cost"]
        shares = info["shares"]
        stop = info["stop_loss"]
        candidate = candidate_by_code.get(code)

        if quote and float(quote.get("price") or 0) > 0:
            cur = float(quote.get("price") or 0)
            chg = float(quote.get("change_pct") or 0)
            pnl_pct = (cur - cost_p) / cost_p * 100 if cost_p else 0.0
            pnl_yuan = (cur - cost_p) * shares
            market_val = cur * shares
            total_market += market_val

            alert = _alert_for_holding(regime, cur, stop, pnl_pct, candidate)
            if alert["level"] in ("warn", "critical"):
                alarm_count += 1
        else:
            cur = chg = pnl_pct = pnl_yuan = market_val = 0
            alert = {
                "level": "warn",
                "text": " ⚠️ 数据缺失",
                "bases": ["距止损: N/A", "相对排名: N/A", "缩量: N/A"],
                "reason": "行情缺失",
            }
            alarm_count += 1

        cost_total = cost_p * shares
        rows.append({
            "name": name, "code": code,
            "cost": cost_p, "cur": cur,
            "chg": chg, "pnl_pct": pnl_pct,
            "pnl_yuan": pnl_yuan,
            "shares": shares,
            "market_val": market_val,
            "cost_total": cost_total,
            "stop": stop,
            "alarm": alert.get("text", ""),
            "alarm_level": alert.get("level"),
            "alarm_reason": alert.get("reason"),
            "alarm_bases": alert.get("bases", []),
            "relative_strength_pct": (
                candidate.get("relative_strength_pct") if candidate else None
            ),
            "stabilization": candidate.get("stabilization") if candidate else None,
            "target_weight": plan_weights.get(code, 0.0),
            "rebalance_action": (
                adjustments.get(code, {}).get("action", "NONE")
            ),
        })

    # 输出表格
    header = (
        f"{'股票':10s} {'现价':>6s} {'成本':>6s} {'今日':>6s} {'浮亏%':>7s} "
        f"{'浮亏¥':>9s} {'止损':>6s} {'目标仓':>6s} 状态"
    )
    print(header)
    print("-" * len(header))

    total_pnl = 0
    for row in rows:
        pnl_pct_str = f"{row['pnl_pct']:+.1f}%" if row['cur'] else "N/A"
        chg_str = f"{row['chg']:+.2f}%" if row['cur'] else "N/A"
        cur_str = f"{row['cur']:.2f}" if row['cur'] else "N/A"
        total_pnl += row['pnl_yuan'] if row['cur'] else 0
        print(f"{row['name']:8s} {cur_str:>6} {row['cost']:>6.2f} {chg_str:>6} "
              f"{pnl_pct_str:>7} {row['pnl_yuan']:>+8.0f} {row['stop']:>6.2f} "
              f"{row['target_weight']:>6.1%}{row['alarm']}")
        if row["alarm_level"] in ("warn", "critical"):
            print(f"   依据: {' | '.join(row['alarm_bases'])} | "
                  f"调仓: {row['rebalance_action']} | {row['alarm_reason']}")

    # 组合层调仓建议
    if portfolio_plan:
        print("\n  组合层调仓建议:")
        if portfolio_plan.get("validation_errors"):
            print(f"    ⚠️ 约束校验失败: {portfolio_plan['validation_errors']}")
        for item in portfolio_plan.get("adjustments", []):
            if item.get("action") == "HOLD":
                continue
            print(f"    {item.get('action'):6s} {item.get('code')} "
                  f"{item.get('name', '')} "
                  f"{item.get('current_weight', 0):.1%} -> "
                  f"{item.get('target_weight', 0):.1%}")

    # 汇总
    print()
    print(f"  总投入: ¥{total_cost:,.0f}")
    print(f"  总市值: ¥{total_market:,.0f}")
    pnl_ratio = total_pnl / total_cost * 100 if total_cost else 0.0
    print(f"  总浮盈: ¥{total_pnl:+,.0f} ({pnl_ratio:+.1f}%)")
    print(f"  目标暴露: {target_exposure:.0%} / 现金: {cash_pct:.0%}")

    if alarm_count > 0:
        print(f"\n  ⚠️  {alarm_count} 个标的需关注！")

    # 保存状态
    state = {
        "date": today,
        "time": now,
        "as_of": latest_date or today,
        "market_state": market_state,
        "market_regime": regime,
        "candidate_data_freshness": (latest or {}).get("data_freshness"),
        "total_cost": total_cost,
        "total_market": total_market,
        "total_pnl": total_pnl,
        "total_pnl_pct": round(pnl_ratio, 2),
        "target_exposure": target_exposure,
        "cash_pct": cash_pct,
        "target_portfolio": portfolio_plan,
        "stocks": [
            {"code": row["code"], "name": row["name"], "price": row["cur"],
             "cost": row["cost"], "pnl_pct": round(row["pnl_pct"], 2),
             "pnl_yuan": round(row["pnl_yuan"]), "market_val": round(row["market_val"]),
             "stop": row["stop"], "alarm_level": row["alarm_level"],
             "alarm_reason": row["alarm_reason"], "alarm_bases": row["alarm_bases"],
             "relative_strength_pct": row["relative_strength_pct"],
             "stabilization": row["stabilization"],
             "target_weight": row["target_weight"],
             "rebalance_action": row["rebalance_action"]}
            for row in rows
        ],
    }
    with open(DATA_DIR / "current.json", "w", encoding="utf-8") as handle:
        json.dump(state, handle, ensure_ascii=False, indent=2)

    sys.exit(0)  # Always exit clean — alarms are output, not errors


if __name__ == "__main__":
    main()
