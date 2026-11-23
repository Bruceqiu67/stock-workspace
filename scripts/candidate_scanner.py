#!/usr/bin/env python3
"""
每日候选池生成 — 交易日 15:15 自动运行 (no_agent cron)
对预定义候选列表批量查行情 → 三道硬过滤 → 输出候选池
"""
import json, os, subprocess, sys, re
from datetime import date, datetime
from pathlib import Path

# 兼容 Windows 终端 UTF-8 输出
if sys.platform == "win32" and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# 引入同级核心层模块
sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from scoring_engine import calculate_combined_score
    from market_state import load_latest_market_state, check_stabilization, MarketRegime
    from analysis_context import load_analysis_context
    from arbitration import arbitration_from_candidate
    from candidate_universe import (
        CODE_TO_SECTORS,
        SECTOR_ALIAS,
        WATCHLIST as CANDIDATES,
        build_scan_universe,
        candidate_metadata,
        universe_summary,
    )
    from trading_calendar import (
        calendar_summary,
        freshness as calendar_freshness,
        is_trading_day,
        previous_trading_day,
    )
    from market_data_adapter import (
        fetch_tencent_klines,
        kline_last_date,
        parse_tencent_quote_payload,
    )
    from filter_policy import (
        apply_direction_health,
        evaluate_quote_filters,
        resolve_filter_policy,
    )
    from portfolio_construction import build_portfolio
    from output_contract import (
        OUTPUT_CONTRACT_VERSION,
        attach_operation_contract,
        derive_trade_plan,
        validate_trade_plan,
    )
except ImportError:
    calculate_combined_score = None
    load_latest_market_state = None
    check_stabilization = None
    load_analysis_context = None
    arbitration_from_candidate = None
    MarketRegime = None
    CANDIDATES = {}
    SECTOR_ALIAS = {}
    CODE_TO_SECTORS = {}
    fetch_tencent_klines = None
    kline_last_date = None
    parse_tencent_quote_payload = None
    apply_direction_health = None
    evaluate_quote_filters = None
    resolve_filter_policy = None
    build_portfolio = None
    OUTPUT_CONTRACT_VERSION = "unavailable"
    attach_operation_contract = None
    derive_trade_plan = None
    validate_trade_plan = None

    def build_scan_universe(target_date=None):
        return {"status": "unavailable", "codes": [], "items": [],
                "watchlist_count": 0, "broad_universe_count": 0,
                "merged_count": 0, "selection_bias_risk": "unknown",
                "selection_bias_reason": "candidate_universe 导入失败"}

    def candidate_metadata(code):
        return {"metadata_status": "missing"}

    def universe_summary(universe):
        return {"status": (universe or {}).get("status", "unavailable")}

    def calendar_summary(target_date=None):
        return {"calendar_version": "unavailable", "target_date": target_date}

    def calendar_freshness(as_of, target_date=None, allow_previous=True):
        return {"status": "unknown", "is_stale": True,
                "as_of": as_of, "target_date": target_date,
                "data_age_days": None, "data_age_trading_days": None}

    def is_trading_day(day):
        return True

    def previous_trading_day(day):
        return None

DATA_DIR = Path(os.environ.get("HERMES_HOME", Path.home() / ".hermes")) / "data" / "candidates"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# 候选来源：观察池定义与元数据统一在 scripts/candidate_universe.py。
# 本文件只负责抓取、过滤、评分与输出，不再维护第二份 CANDIDATES 字典。


def fetch_all(codes):
    """批量获取腾讯行情（解析统一走 market_data_adapter）。"""
    results = {}
    if parse_tencent_quote_payload is None:
        return results
    # 分批次，每批最多 20 只
    batch_size = 20
    for i in range(0, len(codes), batch_size):
        batch = codes[i:i + batch_size]
        url = f"http://qt.gtimg.cn/q={','.join(batch)}"
        try:
            r = subprocess.run(
                ["curl", "-s", "--connect-timeout", "8", url],
                capture_output=True, timeout=15
            )
            raw = r.stdout.decode("gbk", errors="replace")
        except Exception as e:
            print(f"⚠️  批次 {i//batch_size+1} 失败: {e}", file=sys.stderr)
            continue
        results.update(parse_tencent_quote_payload(raw))
    return results


def fetch_single_kline(code_full, limit=250, period="day"):
    """获取单只股票日K/周K；日K默认 250 根以覆盖真实 52 周窗口。"""
    if fetch_tencent_klines is None:
        return code_full, []
    return code_full, fetch_tencent_klines(
        code_full, limit=limit, period=period
    )


def fetch_all_klines(codes, limit=250, period="day"):
    """并发批量获取K线（日K/周K共用）"""
    from concurrent.futures import ThreadPoolExecutor
    kline_map = {}
    with ThreadPoolExecutor(max_workers=10) as executor:
        results = executor.map(
            lambda code: fetch_single_kline(code, limit=limit, period=period),
            codes,
        )
        for code, kl in results:
            kline_map[code] = kl
    return kline_map


def main():
    today = date.today().isoformat()
    # I3：周二到周五的目标日就是今天；周一/节假日盘前应指向上一交易日。
    today_is_trading = is_trading_day(today)
    target_day = (
        today if today_is_trading
        else (previous_trading_day(today) or today)
    )
    quote_as_of = today if today_is_trading else target_day

    # 宇宙分层：全市场宇宙（可选外部配置）+ 重点观察池。
    # 缺少全市场宇宙时显式标记 watchlist_only / selection_bias_risk=high。
    universe = build_scan_universe(target_date=target_day)
    universe_items = {
        item["code"]: item for item in universe.get("items", [])
    }
    all_codes = universe.get("codes") or list(dict.fromkeys(
        code for codes in CANDIDATES.values() for code in codes
    ))
    print(f"📊 候选池扫描 · {today}（目标交易日 {target_day}）")
    print(f"   总候选: {len(all_codes)} 只 | 观察池: {universe.get('watchlist_count', 0)} 只 | "
          f"全市场宇宙: {universe.get('broad_universe_count', 0)} 只 | "
          f"覆盖: {universe.get('coverage')} | 选择偏差风险: {universe.get('selection_bias_risk')}")
    if universe.get("selection_bias_risk") == "high":
        print("   ⚠️  缺少全市场宇宙配置，本次仅为观察池排序，不能称为全市场选股")

    quotes = fetch_all(all_codes)
    print(f"   行情获取: {len(quotes)}/{len(all_codes)} 成功")

    # I3：输出行情数据的新鲜度与交易日语义，报告可追溯“基于哪一天的数据”。
    quote_freshness = calendar_freshness(
        quote_as_of, target_day, allow_previous=False
    )
    calendar_info = calendar_summary(target_day)
    print(f"   数据日: {quote_freshness.get('as_of')} | 新鲜度: "
          f"{quote_freshness.get('status')} | 交易日: "
          f"{calendar_info.get('target_is_trading_day')} | "
          f"上一交易日: {calendar_info.get('previous_trading_day')}")

    # 相对强度排名：用于恐慌态的相对强度优先与持仓/候选判断。
    change_values = sorted(
        float(q.get("change_pct", 0) or 0) for q in quotes.values()
    )
    for q in quotes.values():
        chg = float(q.get("change_pct", 0) or 0)
        rank = sum(1 for value in change_values if value <= chg)
        q["relative_strength_pct"] = round(
            rank / max(1, len(change_values)) * 100, 1
        )
        q["relative_strength_rank"] = rank

    # 获取市场宏观状态：候选池广度必须来自当日行情，不能复用上一次候选文件。
    today_summary = {
        "total": len(quotes),
        "up_count": sum(
            1 for q in quotes.values()
            if float(q.get("change_pct", 0) or 0) > 0
        ),
        "passed": len(quotes),
        "as_of": quote_as_of,
    }
    mkt_state = (
        load_latest_market_state(target_date=target_day, candidate_summary=today_summary)
        if load_latest_market_state else {}
    )
    regime = mkt_state.get("regime", "UNKNOWN")
    tone = mkt_state.get("strategy_tone", "")
    weights = mkt_state.get("weights", {"technical": 0.40, "industry": 0.40, "catalyst": 0.20})

    # 读取统一分析上下文（板块资金流/主力排行/政策标记/主线板块）。
    # 缺失时显式降级，不再让评分引擎按静态默认值伪装成动态结论。
    analysis_ctx = load_analysis_context(target_day) if load_analysis_context else None
    if analysis_ctx:
        print(f"   分析上下文: {analysis_ctx.get('status')} "
              f"(完整度 {analysis_ctx.get('completeness', 0.0):.2f}, "
              f"as_of={analysis_ctx.get('as_of')})")
    else:
        print("   ⚠️  分析上下文模块不可用，评分将标记为 degraded/abstain")

    # 运行硬过滤（I5：FilterPolicy 统一阈值，硬排除与排序惩罚分离）
    filter_policy = (
        resolve_filter_policy(regime, purpose="watchlist")
        if resolve_filter_policy else None
    )
    if filter_policy:
        print(f"   过滤策略: {filter_policy.purpose}/{filter_policy.regime} "
              f"价格硬上限={filter_policy.max_price_hard:.0f} "
              f"位置硬上限={filter_policy.position_hard_cutoff:.0f}% "
              f"位置惩罚阈值={filter_policy.position_penalty_threshold:.0f}%")
    passed = []
    filtered_out = {"price": [], "position": [], "no_data": [], "near_miss": []}

    # 行情缺失也计入 no_data，避免统计行长期显示 0
    for code_full in all_codes:
        if code_full not in quotes:
            filtered_out["no_data"].append(f"{code_full}(行情缺失)")

    for code_full, q in quotes.items():
        price = q.get("price", 0) or 0
        filter_input = {
            "code": code_full,
            "name": q.get("name", ""),
            "price": price,
            "high_52w": q.get("high_52w"),
            "low_52w": q.get("low_52w"),
        }
        if filter_policy:
            filter_result = evaluate_quote_filters(filter_input, filter_policy)
        else:
            # 策略模块导入失败时保留最基本的绝对风控，但显式标记不可审计。
            hard = []
            if price <= 0:
                hard.append("价格缺失")
            elif price >= 100:
                hard.append("价格超过 100 元兜底上限")
            filter_result = {
                "decision": "exclude" if hard else "pass",
                "hard_exclusions": hard,
                "penalties": [],
                "reasons": hard,
                "score_adjustment": 0.0,
                "rel_position_52w": None,
                "preference": "neutral",
                "near_miss": None,
            }
        q["filter_result"] = filter_result

        if filter_result["decision"] == "exclude":
            reasons = filter_result.get("hard_exclusions") or filter_result.get("reasons", [])
            joined = "；".join(reasons)
            if "价格" in joined:
                filtered_out["price"].append(
                    f"{q.get('name', code_full)}({price:.0f}元)"
                )
            else:
                position = filter_result.get("rel_position_52w")
                position_text = f"{position:.0f}%" if position is not None else "?"
                filtered_out["position"].append(
                    f"{q.get('name', code_full)}({position_text})"
                )
            if filter_result.get("near_miss"):
                filtered_out["near_miss"].append(filter_result["near_miss"])
            continue

        rel_pos = filter_result.get("rel_position_52w")
        if filter_result["decision"] == "penalize" and filter_result.get("near_miss"):
            filtered_out["near_miss"].append(filter_result["near_miss"])

        # 通过过滤
        universe_meta = universe_items.get(code_full, {})
        sectors = CODE_TO_SECTORS.get(code_full)
        if not sectors and universe_meta.get("sector"):
            sectors = [universe_meta["sector"]]
        sectors = sectors or ["未知"]
        meta = candidate_metadata(code_full)
        cand_item = {
            "code": code_full,
            "name": q["name"],
            "price": price,
            "change_pct": q.get("change_pct", 0),
            "rel_position_52w": round(rel_pos, 1) if rel_pos is not None else None,
            "turnover": q.get("turnover", 0),
            "amount_wan": q.get("amount_wan", 0),
            "relative_strength_pct": q.get("relative_strength_pct"),
            "relative_strength_rank": q.get("relative_strength_rank"),
            "sectors": sectors,
            "primary_sector": sectors[0] if sectors else "未知",
            # I5：保留过滤决策以供审计，不再只输出“通过/淘汰”。
            "filter_decision": filter_result["decision"],
            "filter_penalty": filter_result.get("score_adjustment", 0.0),
            "filter_reasons": filter_result.get("reasons", []),
            "filter_preference": filter_result.get("preference"),
            "filter_policy_version": (
                filter_policy.version if filter_policy else "unavailable"
            ),
            # 每只标的保留纳入原因/纳入日期/退出条件，避免“只看到一个固定池”。
            "universe_layer": universe_meta.get("layer", meta.get("layer")),
            "inclusion_reason": (
                meta.get("inclusion_reason")
                or universe_meta.get("inclusion_reason", "")
            ),
            "included_at": meta.get("included_at") or universe_meta.get("included_at"),
            "exit_criteria": (
                meta.get("exit_criteria")
                or universe_meta.get("exit_criteria", "")
            ),
            "universe_metadata_status": (
                "ok" if (meta.get("metadata_status") == "ok"
                         or universe_meta.get("inclusion_reason")) else "sector_proxy"
            ),
        }
        passed.append(cand_item)

    # 批量并发拉取通过标的的日K（250根，真实52周窗口）与周K（60根，周线定方向）
    passed_codes = [c["code"] for c in passed]
    kline_map = fetch_all_klines(passed_codes, limit=250, period="day")
    weekly_kline_map = fetch_all_klines(passed_codes, limit=60, period="week")

    # 运行确定性评分引擎（显式传入分析上下文，禁止隐式默认值）
    for cand_item in passed:
        c_code = cand_item["code"]
        c_klines = kline_map.get(c_code, [])
        last_kline_date = kline_last_date(c_klines) if kline_last_date else None
        if last_kline_date:
            kline_freshness = calendar_freshness(
                last_kline_date, target_day, allow_previous=True
            )
        else:
            kline_freshness = {
                "status": "missing",
                "is_stale": True,
                "as_of": None,
                "target_date": target_day,
                "data_age_days": None,
                "data_age_trading_days": None,
            }
        cand_item["as_of"] = last_kline_date or target_day
        cand_item["kline_as_of"] = last_kline_date
        cand_item["data_freshness"] = kline_freshness
        if not c_klines:
            filtered_out["no_data"].append(f"{cand_item['name']}({c_code})")
        if calculate_combined_score:
            score_res = calculate_combined_score(
                cand_item,
                klines=c_klines,
                weekly_klines=weekly_kline_map.get(c_code, []),
                weights=weights,
                analysis_context=analysis_ctx,
                market_regime=regime,
                as_of=last_kline_date or target_day,
                data_freshness=kline_freshness,
            )
            cand_item["combined_score"] = score_res.get("combined_score")
            cand_item["canonical_score"] = score_res.get("canonical_score")
            cand_item["canonical_version"] = score_res.get("canonical_version")
            cand_item["canonical_breakdown"] = score_res.get("canonical_breakdown")
            cand_item["risk"] = score_res.get("risk")
            cand_item["action_label"] = score_res.get("action_label")
            cand_item["scoring_status"] = score_res.get("scoring_status", "degraded")
            cand_item["rankable"] = score_res.get("rankable", False)
            cand_item["missing_inputs"] = score_res.get("missing_inputs", [])
            cand_item["scores"] = score_res.get("scores", {})
            cand_item["signals"] = score_res.get("signals", [])
            cand_item["tech_details"] = score_res.get("details", {}).get("tech_details", {})
            cand_item["data_quality"] = score_res.get("data_quality", {})
            cand_item["analysis_context"] = score_res.get("analysis_context", {})

            # I3：K线过期时显式降级，绝不把旧数据当成当日评分继续给买入标签。
            if kline_freshness.get("is_stale") and cand_item.get("rankable"):
                cand_item["scoring_status"] = "degraded"
                cand_item["rankable"] = False
                cand_item["action_label"] = "DATA_STALE"
                cand_item["missing_inputs"] = list(dict.fromkeys(
                    cand_item.get("missing_inputs", []) + ["klines.stale"]
                ))
        else:
            # 导入失败不是“中性”，而是不可评分：显式 abstain，禁用操作标签。
            cand_item["combined_score"] = None
            cand_item["action_label"] = "ABSTAIN_IMPORT_FAILED"
            cand_item["scoring_status"] = "abstain"
            cand_item["rankable"] = False
            cand_item["missing_inputs"] = ["scoring_engine"]
            cand_item["scores"] = {}
            cand_item["signals"] = ["评分引擎导入失败"]

    # I5：方向级淘汰——方向内全部空头排列时整方向放弃，并抑制买入标签。
    direction_result = {"direction_health": {}, "direction_veto": [], "vetoed_sectors": []}
    if apply_direction_health:
        direction_result = apply_direction_health(passed, filter_policy)
    else:
        for cand_item in passed:
            cand_item["direction_veto"] = False
    direction_veto = direction_result.get("direction_veto", [])

    # 恐慌态：企稳三条件未通过时一票否决买入标签，只保留观察/防守。
    # UNKNOWN：数据不足，同样不输出方向性开仓标签。
    panic_veto = []
    if regime == "PANIC_DOWNTREND":
        for cand_item in passed:
            klines_for_stab = kline_map.get(cand_item["code"], [])
            stab = (
                check_stabilization(klines_for_stab)
                if check_stabilization else
                {"passed": False, "status": "abstain",
                 "reasons": ["check_stabilization 不可用"]}
            )
            cand_item["stabilization"] = stab
            already_suppressed = bool(cand_item.get("buy_signal_suppressed"))
            cand_item["buy_signal_suppressed"] = (
                already_suppressed or not stab.get("passed", False)
            )
            if not stab.get("passed", False):
                original = cand_item.get("action_label")
                if original in ("STRONG_BUY_WATCH", "ACCUMULATE_ON_DIP"):
                    cand_item["action_label"] = "OBSERVE_DEFENSE_PANIC_UNCONFIRMED"
                    panic_veto.append({
                        "code": cand_item["code"],
                        "name": cand_item["name"],
                        "original_label": original,
                        "reasons": stab.get("reasons", []),
                    })
            else:
                cand_item["buy_signal_suppressed"] = already_suppressed
    elif regime == "UNKNOWN":
        for cand_item in passed:
            original = cand_item.get("action_label")
            if original in ("STRONG_BUY_WATCH", "ACCUMULATE_ON_DIP"):
                cand_item["action_label"] = "OBSERVE_DATA_INSUFFICIENT"
                cand_item["buy_signal_suppressed"] = True
                panic_veto.append({
                    "code": cand_item["code"],
                    "name": cand_item["name"],
                    "original_label": original,
                    "reasons": mkt_state.get("missing_inputs", []),
                })
            else:
                cand_item["buy_signal_suppressed"] = bool(
                    cand_item.get("buy_signal_suppressed")
                )

    # 三框架冲突裁决：产业逻辑 vs 技术确认，按市场状态与持有周期分层。
    arbitration_veto = []
    for cand_item in passed:
        if arbitration_from_candidate:
            arbitration = arbitration_from_candidate(cand_item, regime)
        else:
            arbitration = {"decision": "ARBITRATION_UNAVAILABLE",
                           "conflict_type": "unknown", "basis": []}
        cand_item["framework_arbitration"] = arbitration
        if (arbitration.get("decision") in
                ("DEFEND_NO_NEW_BUY", "AVOID", "NO_DIRECTIONAL_ACTION")
                and cand_item.get("action_label") in
                ("STRONG_BUY_WATCH", "ACCUMULATE_ON_DIP")):
            original = cand_item.get("action_label")
            cand_item["action_label"] = "OBSERVE_ARBITRATION_VETO"
            arbitration_veto.append({
                "code": cand_item["code"],
                "name": cand_item["name"],
                "original_label": original,
                "decision": arbitration.get("decision"),
                "conflict_type": arbitration.get("conflict_type"),
                "basis": arbitration.get("basis", []),
            })

    # 排序：恐慌态优先相对强度，其余状态优先规范分；不可排名标的置后。
    # I5 的排序惩罚只影响排序，不改变研究评级与操作契约。
    def _canonical_key(item):
        value = item.get("canonical_score")
        if value is None:
            value = item.get("combined_score")
            return value * 20 if value is not None else -1
        return value

    def _sort_score(item):
        return _canonical_key(item) + float(item.get("filter_penalty") or 0.0)

    if regime == "PANIC_DOWNTREND":
        passed.sort(
            key=lambda x: (
                bool(x.get("rankable")),
                x.get("relative_strength_pct") or -1,
                _sort_score(x),
            ),
            reverse=True,
        )
    else:
        passed.sort(
            key=lambda x: (
                bool(x.get("rankable")),
                _sort_score(x),
                x.get("relative_strength_pct") or -1,
            ),
            reverse=True,
        )
    rankable_candidates = [c for c in passed if c.get("rankable")]
    non_rankable = [c for c in passed if not c.get("rankable")]

    # A1：组合构建与风险预算层。输入候选分数/市场状态，输出目标权重、现金比例、
    # 方向集中度与调仓建议；个股层面只消费组合层给出的仓位上限。
    portfolio_plan = {}
    if build_portfolio:
        portfolio_plan = build_portfolio(
            passed, market_regime=regime, as_of=target_day
        )
    weights_by_code = {
        position.get("code"): position.get("target_weight")
        for position in portfolio_plan.get("positions", [])
    }

    # A2：操作层输出契约（入场/止损/目标/仓位/失效条件/有效期）。
    operation_errors = []
    if derive_trade_plan and attach_operation_contract:
        for cand_item in passed:
            label = str(cand_item.get("action_label") or "")
            trade_allowed = bool(
                cand_item.get("rankable")
                and cand_item.get("scoring_status") == "ok"
                and not cand_item.get("direction_veto")
                and not cand_item.get("buy_signal_suppressed")
                and not label.startswith("OBSERVE")
                and label not in ("AVOID_DEFENSE", "DATA_DEGRADED",
                                  "ABSTAIN_NO_DATA", "DATA_STALE")
            )
            plan = derive_trade_plan(
                cand_item,
                klines=kline_map.get(cand_item["code"], []),
                market_regime=regime,
                trade_allowed=trade_allowed,
                position_weight=weights_by_code.get(cand_item["code"], 0.0),
                as_of=cand_item.get("as_of") or target_day,
            )
            attach_operation_contract(cand_item, plan)
            if validate_trade_plan:
                errors = validate_trade_plan(plan)
                for error in errors:
                    operation_errors.append({
                        "code": cand_item.get("code"),
                        "error": error,
                    })
    else:
        for cand_item in passed:
            cand_item["operation"] = {
                "schema_version": "unavailable",
                "trade_action": "NO_TRADE",
                "position_size": 0.0,
                "invalidation": ["output_contract 模块不可用"],
            }
            cand_item["trade_action"] = "NO_TRADE"
            cand_item["position_size"] = 0.0

    # 输出统计与状态
    print(f"\n   宏观市场状态: {regime}")
    if tone:
        print(f"   基调指引: {tone}")
    print(f"   通过过滤: {len(passed)} 只 | 可排名: {len(rankable_candidates)} | "
          f"降级/弃权: {len(non_rankable)} | 恐慌否决: {len(panic_veto)} | "
          f"方向淘汰: {len(direction_veto)} | 裁决否决: {len(arbitration_veto)} | "
          f"过滤惩罚: {len(filtered_out['near_miss'])} | "
          f"价格超标: {len(filtered_out['price'])} | "
          f"位置过高: {len(filtered_out['position'])} | 数据缺失: {len(filtered_out['no_data'])}")
    if portfolio_plan:
        print(f"   组合目标: 暴露 {portfolio_plan.get('total_weight', 0):.1%} / "
              f"现金 {portfolio_plan.get('cash_pct', 0):.1%} / "
              f"持仓 {len(portfolio_plan.get('positions', []))} 只 | "
              f"约束校验: {'OK' if not portfolio_plan.get('validation_errors') else 'FAIL'}")
    if panic_veto:
        print("   🛑 企稳三条件未通过，以下买入标签已抑制：")
        for item in panic_veto[:10]:
            print(f"      {item['code']} {item['name']} {item['original_label']} -> "
                  f"OBSERVE（{','.join(item.get('reasons', [])[:3])}）")
    if direction_veto:
        print("   🛑 方向级淘汰（方向内均线全部空头排列）：")
        for item in direction_veto[:10]:
            print(f"      {item['sector']} {item['code']} {item['name']} "
                  f"{item['original_label']} -> OBSERVE_DIRECTION_BEARISH")

    # 输出 TOP 10 规范评分榜单（只列可排名标的；0-100 为单一事实源）
    print(f"\n🏆 候选池规范评分 TOP 10 (0-100；子分技术/产业/催化，风险为诊断维度):")
    print(f"   {'排名':4s} {'代码':8s} {'名称':8s} {'现价':7s} {'涨跌幅':7s} {'52w':5s} {'规范分':6s} {'操作评级':20s} {'入场区':13s} {'止损':6s} {'仓位':6s} {'交易动作':12s} 核心特征")
    print("   " + "-" * 125)
    for idx, c in enumerate(rankable_candidates[:10], 1):
        rp = f"{c['rel_position_52w']:.0f}%" if c['rel_position_52w'] is not None else "?"
        sig = " | ".join(c.get("signals", [])[:2])
        canonical = _canonical_key(c)
        canonical_str = f"{canonical:.1f}" if canonical is not None and canonical >= 0 else "N/A"
        entry = c.get("entry_zone") or [None, None]
        entry_str = (
            f"{entry[0]:.2f}-{entry[1]:.2f}"
            if entry[0] is not None and entry[1] is not None else "N/A"
        )
        stop = f"{c['stop']:.2f}" if c.get("stop") else "N/A"
        position = c.get("position_size_pct")
        position_str = f"{position:.1f}%" if position is not None else "N/A"
        print(f"   #{idx:<3d} {c['code']:8s} {c['name']:8s} ¥{c['price']:<6.2f} "
              f"{c['change_pct']:+6.2f}% {rp:>5s} {canonical_str:>6s} "
              f"{c.get('action_label',''):20s} {entry_str:13s} {stop:>6s} "
              f"{position_str:>6s} {c.get('trade_action','NO_TRADE'):12s} {sig}")

    if non_rankable:
        print(f"\n⚠️  不可排名标的 {len(non_rankable)} 只（数据降级/弃权，不输出买卖标签）:")
        for c in non_rankable[:10]:
            print(f"   {c['code']:8s} {c['name']:8s} status={c.get('scoring_status')} "
                  f"missing={','.join(c.get('missing_inputs', [])[:3])}")

    # 按方向分组
    by_sector = {}
    for c in passed:
        for s in c["sectors"]:
            by_sector.setdefault(s, []).append(c)

    # 保存
    output = {
        "date": today,
        "target_date": target_day,
        "as_of": target_day,
        "data_freshness": quote_freshness,
        "trading_calendar": calendar_info,
        "captured_at": datetime.now().isoformat(timespec="minutes"),
        "score_snapshot_version": "1.0",
        "output_contract_version": OUTPUT_CONTRACT_VERSION,
        "forward_return_horizons": [1, 5, 20],
        "market_state": mkt_state,
        "analysis_context": analysis_ctx,
        "filter_policy": filter_policy.as_dict() if filter_policy else None,
        "universe": {
            **universe_summary(universe),
            "watchlist_sector_count": len(CANDIDATES),
        },
        "scoring_summary": {
            "canonical_version": next(
                (c.get("canonical_version") for c in passed
                 if c.get("canonical_version")), None
            ),
            "rankable": len(rankable_candidates),
            "non_rankable": len(non_rankable),
            "panic_veto": len(panic_veto),
            "direction_veto": len(direction_veto),
            "arbitration_veto": len(arbitration_veto),
            "filter_near_miss": len(filtered_out["near_miss"]),
            "portfolio_positions": len(portfolio_plan.get("positions", [])),
            "output_contract_errors": len(operation_errors),
            "status_counts": {
                status: sum(1 for c in passed if c.get("scoring_status") == status)
                for status in ("ok", "degraded", "abstain")
            },
        },
        "panic_veto": panic_veto,
        "direction_veto": direction_veto,
        "direction_health": direction_result.get("direction_health", {}),
        "arbitration_veto": arbitration_veto,
        "portfolio_plan": portfolio_plan,
        "operation_contract_errors": operation_errors,
        "total": len(all_codes),
        "fetched": len(quotes),
        "passed": len(passed),
        "filtered": {
            "price_high": filtered_out["price"],
            "position_high": filtered_out["position"],
            "no_data": filtered_out["no_data"],
            "near_miss": filtered_out["near_miss"],
        },
        "candidates": passed,
        "by_sector": {s: [c["code"] for c in stocks] for s, stocks in by_sector.items()},
    }

    out_path = DATA_DIR / f"{today}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    print(f"\n✅ 候选池与评分结果已落盘: {out_path}")

    # 退出码：没合格候选 = 异常
    if not passed:
        print("⚠️  今日无合格候选", file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()

