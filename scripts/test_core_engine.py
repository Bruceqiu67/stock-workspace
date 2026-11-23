#!/usr/bin/env python3
"""
核心层工程化测试套件 (Core Layer Test Suite)

A4 约定：
  - 本文件只做“逻辑正确性 / 回归”测试：分数范围、缺失处理、状态边界、
    契约几何关系、组合约束、记录→复盘→报告闭环。
  - 预测有效性（前瞻收益、IC、分桶、回测）不在 unittest 中断言，由
    `scripts/forward_validation.py` 与 `skills/three-framework-analyst/scripts/backtest.py`
    用真实数据单独评估；两者产物必须带数据版本/日期，见 `docs/testing.md`。
  - 使用固定、可复现的 golden fixture（含真实快照字段形状），不依赖网络。
"""
import argparse
import contextlib
import io
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))

import prediction_tracker
from filter_policy import (
    apply_direction_health,
    direction_health,
    evaluate_quote_filters,
    resolve_filter_policy,
)
from market_data_adapter import (
    parse_sina_kline_payload,
    parse_tencent_kline_payload,
    parse_tencent_quote_payload,
)
from market_state import (
    MarketRegime,
    check_stabilization,
    evaluate_market_state,
)
from output_contract import (
    attach_operation_contract,
    derive_trade_plan,
    validate_trade_plan,
)
from portfolio_construction import build_portfolio, validate_portfolio
from research_depth import assess_research_depth, resolve_research_mode
from scoring_engine import (
    calc_catalyst_score,
    calc_industry_score,
    calc_technical_score,
    calculate_combined_score,
)
from trading_calendar import (
    freshness,
    is_trading_day,
    previous_trading_day,
    trading_days_between,
)


def make_daily(n=250, start=10.0, step=0.01, volume=10000,
               base=date(2025, 1, 1)):
    rows = []
    for index in range(n):
        close = start + step * index
        rows.append({
            "date": (base + timedelta(days=index)).isoformat(),
            "open": close * 0.995,
            "close": close,
            "high": close * 1.01,
            "low": close * 0.99,
            "volume": volume,
        })
    return rows


def make_weekly(n=30, start=10.0, step=0.05, base=date(2024, 6, 1)):
    rows = []
    for index in range(n):
        close = start + step * index
        rows.append({
            "date": (base + timedelta(days=7 * index)).isoformat(),
            "open": close * 0.99,
            "close": close,
            "high": close * 1.02,
            "low": close * 0.98,
            "volume": 50000,
        })
    return rows


def make_index(n=30, start=100.0, step=0.2, volume=1_000_000):
    return make_daily(n=n, start=start, step=step, volume=volume)


def analysis_context(tariff=True, flow=True, mainline=True):
    return {
        "as_of": "2026-01-05",
        "source": "golden_fixture",
        "tariff_active": tariff,
        "mainline_sectors": ["半导体/封测/材料"] if mainline else [],
        "top_capital_flow_sectors": ["半导体/封测/材料"] if mainline else [],
        "sector_flow_data": (
            [{"name": "半导体/封测/材料", "main_net_inflow": "12亿",
              "change_pct": 1.5}]
            if flow else []
        ),
        "main_fund_rank": [
            {"code": "sz300054", "main_net_inflow": "3亿"}
        ],
    }


def make_candidate(code="sz300054", name="鼎龙股份", sector="半导体/封测/材料",
                   price=11.2, relative=30.0, score=86.0):
    return {
        "code": code,
        "name": name,
        "price": price,
        "change_pct": 1.2,
        "rel_position_52w": relative,
        "sectors": [sector],
        "primary_sector": sector,
        "canonical_score": score,
        "combined_score": score / 20.0,
        "canonical_version": "1.0.0",
        "rankable": True,
        "scoring_status": "ok",
        "action_label": "STRONG_BUY_WATCH",
        "relative_strength_pct": 80.0,
        "tech_details": {"ma5": 10.2, "ma10": 10.0, "ma20": 9.8,
                         "avg_amp_5d": 3.0},
        "data_freshness": {"is_stale": False, "status": "fresh"},
        "stabilization": {"passed": True},
    }


class TestTechnicalScore(unittest.TestCase):

    def test_full_window_uses_52w_and_ma60(self):
        result = calc_technical_score(make_daily(250), make_weekly(30))
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["details"]["position_window_days"], 250)
        self.assertIn("ma60", result["details"])

    def test_position_uses_52w_not_30d(self):
        klines = make_daily(250, start=10.0, step=0.001)
        for index in range(100):
            klines[index]["high"] = 100.0
            klines[index]["low"] = 90.0
        result = calc_technical_score(klines, make_weekly(30))
        self.assertLess(result["details"]["rel_pos_pct"], 5.0)
        self.assertEqual(result["details"]["position_window_days"], 250)

    def test_score_range_and_position_monotonicity(self):
        for step in (-0.02, 0.0, 0.02):
            result = calc_technical_score(
                make_daily(250, start=15.0, step=step), make_weekly(30)
            )
            self.assertGreaterEqual(result["score"], 0.5)
            self.assertLessEqual(result["score"], 5.0)
        high_position = calc_technical_score(
            make_daily(250, start=10.0, step=0.05), make_weekly(30)
        )
        low_position = calc_technical_score(
            make_daily(250, start=20.0, step=-0.03), make_weekly(30)
        )
        self.assertGreater(
            low_position["details"]["score_pos"],
            high_position["details"]["score_pos"],
        )

    def test_missing_and_invalid_klines_abstain(self):
        self.assertEqual(calc_technical_score([])["status"], "abstain")
        self.assertEqual(
            calc_technical_score(make_daily(4))["status"], "abstain"
        )
        nan_rows = make_daily(120)
        nan_rows[-1]["close"] = float("nan")
        self.assertEqual(
            calc_technical_score(nan_rows, make_weekly(30))["status"],
            "abstain",
        )
        zero_volume_rows = make_daily(120)
        zero_volume_rows[-1]["volume"] = 0
        self.assertEqual(
            calc_technical_score(zero_volume_rows, make_weekly(30))["status"],
            "abstain",
        )

    def test_degraded_without_weekly(self):
        result = calc_technical_score(make_daily(250), None)
        self.assertEqual(result["status"], "degraded")
        self.assertIsNotNone(result["score"])
        self.assertIn("weekly_klines(>=20)", result["missing_inputs"])


class TestIndustryAndCatalyst(unittest.TestCase):

    def test_tariff_and_mainline_change_industry_score(self):
        candidate = {"code": "sz300054", "name": "鼎龙股份",
                     "primary_sector": "半导体/封测/材料"}
        normal = calc_industry_score(candidate, {"tariff_active": False})
        tariff = calc_industry_score(candidate, {"tariff_active": True})
        self.assertGreater(tariff["score"], normal["score"])
        self.assertIn("卡脖子稀缺层", tariff["signals"])

    def test_unknown_sector_is_degraded(self):
        result = calc_industry_score(
            {"code": "sz000001", "primary_sector": "不存在方向"},
            {"tariff_active": True, "top_capital_flow_sectors": []},
        )
        self.assertEqual(result["status"], "degraded")
        self.assertIn("sector_profile", result["missing_inputs"])

    def test_company_alpha_differs_within_same_sector(self):
        context = {"tariff_active": True,
                   "top_capital_flow_sectors": ["半导体/封测/材料"]}
        first = calc_industry_score(
            {"code": "sz002156", "name": "通富微电",
             "primary_sector": "半导体/封测/材料"}, context,
        )
        second = calc_industry_score(
            {"code": "sz002185", "name": "华天科技",
             "primary_sector": "半导体/封测/材料"}, context,
        )
        self.assertIsNotNone(first["details"]["company_alpha"])
        self.assertIsNotNone(second["details"]["company_alpha"])
        self.assertNotEqual(first["score"], second["score"])

    def test_catalyst_missing_flow_abstains(self):
        result = calc_catalyst_score({"primary_sector": "机器人"})
        self.assertIsNone(result["score"])
        self.assertEqual(result["status"], "abstain")

    def test_catalyst_counter_trend_bonus(self):
        result = calc_catalyst_score(
            {"primary_sector": "机器人", "change_pct": 2.5},
            [{"name": "机器人", "main_net_inflow": "-8.5亿",
              "change_pct": -3.0}],
        )
        self.assertEqual(result["status"], "ok")
        self.assertIn("逆板块强势收红", result["signals"])


class TestCombinedScore(unittest.TestCase):

    def test_combined_score_consumes_dynamic_context(self):
        candidate = {"code": "sz300054", "name": "鼎龙股份",
                     "price": 11.2, "change_pct": 1.2,
                     "primary_sector": "半导体/封测/材料"}
        result = calculate_combined_score(
            candidate,
            klines=make_daily(250),
            weekly_klines=make_weekly(30),
            analysis_context=analysis_context(),
            market_regime="NORMAL_OSCILLATION",
            as_of="2026-01-05",
        )
        self.assertTrue(result["rankable"])
        self.assertEqual(result["scoring_status"], "ok")
        self.assertIsNotNone(result["canonical_score"])
        self.assertIn(result["action_label"],
                      ("STRONG_BUY_WATCH", "ACCUMULATE_ON_DIP",
                       "NEUTRAL_OBSERVE", "AVOID_DEFENSE"))
        self.assertEqual(result["as_of"], "2026-01-05")

        degraded = calculate_combined_score(
            candidate,
            klines=make_daily(250),
            weekly_klines=make_weekly(30),
            analysis_context=analysis_context(mainline=False),
            market_regime="NORMAL_OSCILLATION",
        )
        self.assertFalse(degraded["rankable"])
        self.assertEqual(degraded["scoring_status"], "degraded")

    def test_combined_abstains_without_sector_flow(self):
        result = calculate_combined_score(
            {"code": "sz300054", "primary_sector": "半导体/封测/材料"},
            klines=make_daily(250),
            weekly_klines=make_weekly(30),
            analysis_context=analysis_context(flow=False),
        )
        self.assertIsNone(result["combined_score"])
        self.assertEqual(result["scoring_status"], "abstain")
        self.assertEqual(result["action_label"], "ABSTAIN_NO_DATA")

    def test_invalid_weight_falls_back_with_warning(self):
        result = calculate_combined_score(
            {"code": "sz300054", "primary_sector": "半导体/封测/材料"},
            klines=make_daily(250),
            weekly_klines=make_weekly(30),
            analysis_context=analysis_context(),
            weights={"technical": 0.8, "industry": 0.8, "catalyst": 0.8},
        )
        self.assertIsNotNone(result["weight_warning"])


class TestMarketState(unittest.TestCase):

    def test_normal_state(self):
        state = evaluate_market_state(
            sh_index={"change_pct": 0.3},
            cy_index={"change_pct": 0.5},
            candidate_summary={"total": 40, "up_count": 22, "passed": 20},
            klines_sh=make_index(step=0.2),
        )
        self.assertEqual(state["regime"], MarketRegime.NORMAL_OSCILLATION.value)

    def test_panic_state(self):
        state = evaluate_market_state(
            sh_index={"change_pct": -2.8},
            cy_index={"change_pct": -5.6},
            candidate_summary={"total": 40, "up_count": 3, "passed": 5},
            klines_sh=make_index(step=0.2),
        )
        self.assertEqual(state["regime"], MarketRegime.PANIC_DOWNTREND.value)
        self.assertEqual(state["weights"]["technical"], 0.60)

    def test_rebound_state_reachable(self):
        state = evaluate_market_state(
            sh_index={"change_pct": 1.6},
            cy_index={"change_pct": 0.2},
            candidate_summary={"total": 40, "up_count": 30, "passed": 20},
            klines_sh=make_index(start=100.0, step=-0.2),
        )
        self.assertEqual(state["regime"], MarketRegime.REBOUND_RIGHT_SIDE.value)

    def test_unknown_when_key_input_missing(self):
        state = evaluate_market_state(
            sh_index={"change_pct": 0.3},
            cy_index={"change_pct": 0.5},
            candidate_summary={"total": 40, "up_count": 22, "passed": 20},
            klines_sh=[],
        )
        self.assertEqual(state["regime"], MarketRegime.UNKNOWN.value)
        self.assertIn("klines_sh(>=20)", state["missing_inputs"])

    def test_stale_data_forces_unknown(self):
        state = evaluate_market_state(
            sh_index={"change_pct": 0.3},
            cy_index={"change_pct": 0.5},
            candidate_summary={"total": 40, "up_count": 22, "passed": 20},
            klines_sh=make_index(step=0.2),
            as_of="2025-12-31",
            target_date="2026-01-05",
            freshness={"status": "stale", "is_stale": True,
                       "data_age_days": 5, "data_age_trading_days": 3},
        )
        self.assertEqual(state["regime"], MarketRegime.UNKNOWN.value)
        self.assertEqual(state["data_age_days"], 5)

    def test_breadth_extreme_triggers_panic(self):
        breadth = {
            "status": "ok", "advance_ratio": 0.15, "limit_down": 40,
            "sentiment_score": 18, "sample_size": 4800,
        }
        state = evaluate_market_state(
            sh_index={"change_pct": -0.5},
            cy_index={"change_pct": -1.0},
            candidate_summary={"total": 40, "up_count": 10, "passed": 20},
            klines_sh=make_index(step=0.2),
            breadth=breadth,
        )
        self.assertEqual(state["regime"], MarketRegime.PANIC_DOWNTREND.value)

    def test_stabilization_pass_and_fail(self):
        passing = []
        for index in range(10):
            passing.append({
                "date": f"2026-01-{index + 1:02d}",
                "close": 10.0 + index * 0.1,
                "low": 8.0 if index == 0 else 9.0 + index * 0.01,
                "volume": 10000 if index == 0 else 2000,
            })
        result = check_stabilization(passing)
        self.assertTrue(result["passed"])
        self.assertTrue(all(item["passed"] for item in result["conditions"].values()))

        failing = [dict(row) for row in passing]
        failing[-1]["low"] = 7.5
        failing[-1]["volume"] = 9000
        failed = check_stabilization(failing)
        self.assertFalse(failed["passed"])
        self.assertTrue(failed["reasons"])

    def test_stabilization_insufficient_data(self):
        result = check_stabilization(make_daily(5))
        self.assertEqual(result["status"], "abstain")
        self.assertIn("klines(>=6)", result["missing_inputs"])


class TestPredictionLogic(unittest.TestCase):

    def test_mixed_language_classification(self):
        self.assertEqual(prediction_tracker.classify_prediction_type("冲高回落"), "偏空")
        self.assertEqual(prediction_tracker.classify_prediction_type("低开高走"), "偏多")
        self.assertEqual(prediction_tracker.classify_prediction_type("先跌后涨"), "偏多")
        self.assertEqual(prediction_tracker.classify_prediction_type("弱反弹"), "弱反弹")

    def test_direction_range_and_brier_scoring(self):
        pred = {
            "base_scenario": "放量上行，看多", "base_prob": 60,
            "opt_scenario": "突破", "opt_prob": 20,
            "pes_scenario": "回调", "pes_prob": 20,
            "support": 3000, "resistance": 3100,
        }
        result = prediction_tracker.evaluate_prediction(
            pred, actual_change_pct=1.2,
            actual_high=3080, actual_low=3020,
        )
        self.assertEqual(result["error_type"], "正确")
        self.assertEqual(result["dir_score"], 50)
        self.assertEqual(result["range_score"], 30)
        self.assertIsNotNone(result["brier_score"])
        self.assertGreaterEqual(result["score"], 80)
        self.assertEqual(result["score_coverage"], 1.0)

    def test_range_missing_is_na_not_default(self):
        pred = {
            "base_scenario": "放量上行", "base_prob": 60,
            "opt_scenario": "突破", "opt_prob": 20,
            "pes_scenario": "回调", "pes_prob": 20,
        }
        result = prediction_tracker.evaluate_prediction(
            pred, actual_change_pct=1.2,
        )
        self.assertEqual(result["range_status"], "N/A")
        self.assertIsNone(result["range_score"])
        self.assertEqual(result["max_points"], 70)
        self.assertLess(result["score"], 85)

    def test_invalid_probabilities_not_scored(self):
        pred = {
            "base_scenario": "上行", "base_prob": 60,
            "opt_scenario": "突破", "opt_prob": 30,
            "pes_scenario": "回调", "pes_prob": 20,
        }
        result = prediction_tracker.evaluate_prediction(
            pred, actual_change_pct=1.2,
        )
        self.assertEqual(result["scenario_status"], "invalid_probabilities")
        self.assertIsNone(result["scenario_score"])
        self.assertFalse(result["probabilities_valid"])

    def test_direction_only_has_low_coverage(self):
        # 只有方向、没有情景概率：覆盖度应为 0.5（仅方向维度），置信度 low。
        # 注意：带完整 60/20/20 情景概率的预判不是“仅方向”，应计入情景维度
        #（见 test_range_missing_is_na_not_default，max_points 为 70）。
        pred = {"base_scenario": "上行"}
        result = prediction_tracker.evaluate_prediction(pred, actual_change_pct=0.0)
        self.assertEqual(result["score_coverage"], 0.5)
        self.assertEqual(result["confidence"], "low")


class TestPredictionPipeline(unittest.TestCase):

    def _record_args(self, target_date="2026-01-06"):
        return argparse.Namespace(
            type="recap", summary="放量上行", direction="偏多",
            base_scenario="放量上行，看多", base_prob=60,
            opt_scenario="突破", opt_prob=20,
            pes_scenario="回调", pes_prob=20,
            support=3000.0, resistance=3100.0, observation="量能",
            provider="test", data_source="golden_fixture",
            rubric_version=prediction_tracker.RUBRIC_VERSION,
            probability_source=None,
            date="2026-01-05", target_date=target_date,
        )

    def test_record_review_report_round_trip_and_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            daily_dir = Path(tmp)
            with patch.object(prediction_tracker, "DAILY_DIR", daily_dir):
                with patch.object(
                    prediction_tracker, "is_probably_trading_day",
                    return_value=True,
                ):
                    with contextlib.redirect_stdout(io.StringIO()):
                        prediction_tracker.record_prediction(self._record_args())
                        prediction_tracker.record_prediction(self._record_args())
                records = prediction_tracker._load_records(
                    daily_dir / "2026-01-05.json"
                )
                self.assertEqual(len(records), 2)
                self.assertEqual(records[1]["revision"], 2)
                self.assertEqual(records[0]["superseded_by"], records[1]["id"])
                self.assertIn("target_date", records[1])
                self.assertIn("probability_source", records[1])

                actual = {
                    "status": "ok", "target_date": "2026-01-06",
                    "as_of": "2026-01-06", "change_pct": 1.2,
                    "high": 3080.0, "low": 3020.0,
                    "open": 3050.0, "close": 3070.0,
                    "prev_close": 3033.0, "source": "golden_fixture",
                }
                with patch.object(
                    prediction_tracker, "fetch_actual_index_day",
                    return_value=actual,
                ):
                    with contextlib.redirect_stdout(io.StringIO()):
                        code = prediction_tracker.review_predictions("2026-01-05")
                self.assertEqual(code, 0)
                reviewed = prediction_tracker._load_records(
                    daily_dir / "2026-01-05.json"
                )
                self.assertTrue(all(record["reviewed"] for record in reviewed))
                self.assertTrue(all(
                    record["review_score"] is not None for record in reviewed
                ))
                self.assertEqual(
                    reviewed[0]["review_data_freshness"]["status"], "fresh"
                )
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    prediction_tracker.generate_report(by_type=True)
                report = output.getvalue()
                self.assertIn("评分口径分布", report)
                self.assertIn("已复盘: 1 条", report)

    def test_corrupt_file_is_reported_not_silently_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            daily_dir = Path(tmp)
            (daily_dir / "2026-01-05.json").write_text("{bad json", encoding="utf-8")
            stderr = io.StringIO()
            with patch.object(prediction_tracker, "DAILY_DIR", daily_dir):
                with contextlib.redirect_stderr(stderr):
                    with contextlib.redirect_stdout(io.StringIO()):
                        prediction_tracker.list_predictions()
            self.assertIn("损坏", stderr.getvalue())


class TestFilterPolicy(unittest.TestCase):

    def test_price_is_penalty_not_hard_exclusion_in_watchlist(self):
        policy = resolve_filter_policy("NORMAL_OSCILLATION", "watchlist")
        result = evaluate_quote_filters(
            {"code": "sh600000", "price": 120.0, "rel_position_52w": 30.0},
            policy,
        )
        self.assertEqual(result["decision"], "penalize")
        self.assertLess(result["score_adjustment"], 0)

    def test_price_hard_cap_differs_by_purpose(self):
        watch = evaluate_quote_filters(
            {"code": "sh600000", "price": 160.0, "rel_position_52w": 30.0},
            resolve_filter_policy("NORMAL_OSCILLATION", "watchlist"),
        )
        trade = evaluate_quote_filters(
            {"code": "sh600000", "price": 120.0, "rel_position_52w": 30.0},
            resolve_filter_policy("NORMAL_OSCILLATION", "trade"),
        )
        self.assertEqual(watch["decision"], "exclude")
        self.assertEqual(trade["decision"], "exclude")

    def test_position_hard_and_penalty_thresholds(self):
        policy = resolve_filter_policy("NORMAL_OSCILLATION", "watchlist")
        hard = evaluate_quote_filters(
            {"code": "sh600000", "price": 20.0, "rel_position_52w": 72.0},
            policy,
        )
        penalty = evaluate_quote_filters(
            {"code": "sh600000", "price": 20.0, "rel_position_52w": 65.0},
            policy,
        )
        self.assertEqual(hard["decision"], "exclude")
        self.assertEqual(penalty["decision"], "penalize")

    def test_panic_tightens_position_cutoff(self):
        policy = resolve_filter_policy("PANIC_DOWNTREND", "watchlist")
        result = evaluate_quote_filters(
            {"code": "sh600000", "price": 20.0, "rel_position_52w": 62.0},
            policy,
        )
        self.assertEqual(result["decision"], "exclude")

    def test_direction_all_bearish_veto(self):
        candidates = [
            {"code": "a", "name": "A", "primary_sector": "机器人",
             "action_label": "STRONG_BUY_WATCH",
             "tech_details": {"ma5": 9.0, "ma10": 10.0, "ma20": 11.0}},
            {"code": "b", "name": "B", "primary_sector": "机器人",
             "action_label": "ACCUMULATE_ON_DIP",
             "tech_details": {"ma5": 8.0, "ma10": 9.0, "ma20": 10.0}},
        ]
        health = direction_health(candidates)
        self.assertEqual(health["sectors"]["机器人"]["status"], "all_bearish")
        result = apply_direction_health(candidates)
        self.assertEqual(result["vetoed_sectors"], ["机器人"])
        self.assertEqual(
            candidates[0]["action_label"], "OBSERVE_DIRECTION_BEARISH"
        )


class TestOutputContract(unittest.TestCase):

    def test_operation_geometry_and_position_size(self):
        candidate = make_candidate(price=11.2)
        plan = derive_trade_plan(
            candidate,
            klines=make_daily(120),
            market_regime="NORMAL_OSCILLATION",
            trade_allowed=True,
            as_of="2026-01-05",
        )
        self.assertEqual(validate_trade_plan(plan), [])
        low, high = plan["entry_zone"]
        self.assertLess(plan["stop"], low)
        self.assertLessEqual(low, high)
        self.assertGreater(plan["target_1"], high)
        self.assertGreater(plan["target_2"], plan["target_1"])
        self.assertGreaterEqual(plan["position_size"], 0.0)
        self.assertLessEqual(plan["position_size"], 0.15)
        self.assertTrue(plan["invalidation"])
        self.assertTrue(plan["valid_until"])
        self.assertIn(plan["trade_action"], ("BUY_ZONE", "WAIT_ENTRY"))

    def test_veto_plan_is_no_trade(self):
        plan = derive_trade_plan(
            make_candidate(),
            klines=make_daily(120),
            market_regime="PANIC_DOWNTREND",
            trade_allowed=False,
            as_of="2026-01-05",
        )
        self.assertEqual(plan["trade_action"], "NO_TRADE")
        self.assertEqual(plan["position_size"], 0)
        self.assertEqual(validate_trade_plan(plan), [])

    def test_attach_contract_sets_top_level_fields(self):
        candidate = make_candidate()
        plan = derive_trade_plan(
            candidate, klines=make_daily(120),
            trade_allowed=True, as_of="2026-01-05",
        )
        attach_operation_contract(candidate, plan)
        self.assertEqual(candidate["stop"], plan["stop"])
        self.assertEqual(candidate["entry_zone"], plan["entry_zone"])
        self.assertEqual(candidate["operation"]["schema_version"],
                         plan["schema_version"])


class TestPortfolioConstruction(unittest.TestCase):

    def _candidates(self):
        sectors = ["半导体/封测/材料", "机器人", "电网设备"]
        result = []
        for index in range(9):
            result.append(make_candidate(
                code=f"sz{index:06d}", name=f"股票{index}",
                sector=sectors[index % 3],
                score=90.0 - index * 3,
                relative=20.0 + index,
            ))
        return result

    def test_constraints_and_cash(self):
        plan = build_portfolio(self._candidates(), "NORMAL_OSCILLATION")
        self.assertEqual(validate_portfolio(plan), [])
        self.assertLessEqual(plan["total_weight"], 0.80 + 1e-6)
        self.assertGreaterEqual(plan["cash_pct"], 0.10 - 1e-6)
        self.assertLessEqual(len(plan["positions"]), 8)
        for position in plan["positions"]:
            self.assertLessEqual(position["target_weight"], 0.15 + 1e-6)
        for weight in plan["sector_weights"].values():
            self.assertLessEqual(weight, 0.30 + 1e-6)

    def test_regime_exposure(self):
        panic = build_portfolio(self._candidates(), "PANIC_DOWNTREND")
        unknown = build_portfolio(self._candidates(), "UNKNOWN")
        self.assertLessEqual(panic["total_weight"], 0.30 + 1e-6)
        self.assertEqual(unknown["positions"], [])
        self.assertEqual(unknown["cash_pct"], 1.0)

    def test_adjustments_against_current_positions(self):
        candidates = self._candidates()[:2]
        current = {candidates[0]["code"]: 0.20}
        plan = build_portfolio(
            candidates, "NORMAL_OSCILLATION", current_positions=current
        )
        actions = {item["code"]: item["action"] for item in plan["adjustments"]}
        self.assertIn(actions[candidates[0]["code"]], ("REDUCE", "HOLD"))
        self.assertEqual(actions[candidates[1]["code"]], "ENTER")


class TestTradingCalendar(unittest.TestCase):

    def test_previous_trading_day_skips_weekend(self):
        self.assertEqual(
            previous_trading_day("2026-01-05", holidays=[]), "2026-01-02"
        )
        self.assertEqual(
            trading_days_between("2026-01-02", "2026-01-05", holidays=[]), 1
        )

    def test_holidays_and_extra_trading_days(self):
        self.assertFalse(is_trading_day("2026-01-01",
                                        holidays=["2026-01-01"]))
        self.assertTrue(is_trading_day("2026-01-03",
                                       extra_trading_days=["2026-01-03"]))
        self.assertFalse(is_trading_day("2026-01-03", holidays=[]))

    def test_freshness_semantics(self):
        fresh = freshness(
            "2026-01-05", "2026-01-05", allow_previous=False, holidays=[]
        )
        previous = freshness(
            "2026-01-02", "2026-01-05", allow_previous=True, holidays=[]
        )
        stale = freshness(
            "2025-12-31", "2026-01-05", allow_previous=True, holidays=[]
        )
        self.assertEqual(fresh["status"], "fresh")
        self.assertEqual(previous["status"], "previous_trading_day")
        self.assertEqual(stale["status"], "stale")
        self.assertTrue(stale["is_stale"])


class TestMarketDataAdapter(unittest.TestCase):

    def _quote_line(self):
        parts = ["v_sh600000", "浦发银行", "600000", "9.50"]
        parts.extend(["0"] * 46)
        parts[32] = "1.20"
        parts[37] = "12345"
        parts[38] = "0.80"
        parts[43] = "2.10"
        parts[47] = "12.00"
        parts[48] = "7.00"
        return "~".join(parts)

    def test_parse_tencent_quote_payload(self):
        raw = f'{self._quote_line()};'
        quotes = parse_tencent_quote_payload(raw)
        self.assertIn("sh600000", quotes)
        quote = quotes["sh600000"]
        self.assertEqual(quote["name"], "浦发银行")
        self.assertAlmostEqual(quote["price"], 9.5)
        self.assertAlmostEqual(quote["change_pct"], 1.2)
        self.assertAlmostEqual(quote["high_52w"], 12.0)

    def test_parse_tencent_kline_payload(self):
        payload = {
            "data": {
                "sh600000": {
                    "qfqday": [
                        ["2026-01-05", "9.0", "9.5", "9.6", "8.9", "1000"]
                    ]
                }
            }
        }
        rows = parse_tencent_kline_payload(payload, "sh600000")
        self.assertEqual(rows[0]["date"], "2026-01-05")
        self.assertAlmostEqual(rows[0]["close"], 9.5)
        self.assertAlmostEqual(rows[0]["volume"], 1000.0)

    def test_parse_sina_kline_payload(self):
        rows = parse_sina_kline_payload([
            {"day": "2026-01-05", "open": "9", "close": "9.5",
             "high": "9.6", "low": "8.9", "volume": "1000"}
        ])
        self.assertEqual(rows[0]["date"], "2026-01-05")
        self.assertAlmostEqual(rows[0]["close"], 9.5)


class TestResearchDepth(unittest.TestCase):

    def test_quick_scan_cannot_claim_verified(self):
        result = assess_research_depth(
            "quick_scan", companies_reviewed=8, sources_inspected=5,
            claimed_verified=True,
        )
        self.assertEqual(result["claim_level"], "initial_pass")
        self.assertFalse(result["claim_allowed"])
        self.assertIn("不能把结论写成", result["claim_reason"])

    def test_deep_research_verified_thresholds(self):
        result = assess_research_depth(
            "deep_research", companies_reviewed=20, sources_inspected=25,
        )
        self.assertEqual(result["claim_level"], "verified")
        self.assertEqual(result["evidence_coverage"], 1.0)
        self.assertTrue(result["claim_allowed"])

    def test_deep_research_insufficient_is_initial_pass(self):
        result = assess_research_depth(
            "deep_research", companies_reviewed=5, sources_inspected=5,
        )
        self.assertEqual(result["claim_level"], "initial_pass")
        self.assertTrue(result["missing_evidence"])

    def test_budget_downgrades_deep_request(self):
        mode = resolve_research_mode(
            "deep_research", tool_calls=5, budget_seconds=30
        )
        self.assertEqual(mode["research_depth"], "quick_scan")
        self.assertIn("预算不足", mode["downgrade_reason"])


class TestGoldenPipelineIntegration(unittest.TestCase):
    """扫描→状态→评分→方向淘汰→组合→操作契约的集成路径。"""

    def test_end_to_end_pipeline(self):
        context = analysis_context()
        candidates = []
        for code, name, sector in (
            ("sz300054", "鼎龙股份", "半导体/封测/材料"),
            ("sz002156", "通富微电", "半导体/封测/材料"),
            ("sz300124", "汇川技术", "机器人"),
        ):
            candidate = {
                "code": code, "name": name, "price": 11.2,
                "change_pct": 1.2, "primary_sector": sector,
                "sectors": [sector], "relative_strength_pct": 75.0,
            }
            score = calculate_combined_score(
                candidate,
                klines=make_daily(250),
                weekly_klines=make_weekly(30),
                analysis_context=context,
                market_regime="NORMAL_OSCILLATION",
                as_of="2026-01-05",
            )
            candidate.update({
                "canonical_score": score["canonical_score"],
                "combined_score": score["combined_score"],
                "canonical_version": score["canonical_version"],
                "rankable": score["rankable"],
                "scoring_status": score["scoring_status"],
                "action_label": score["action_label"],
                "scores": score["scores"],
                "signals": score["signals"],
                "tech_details": score["details"]["tech_details"],
                "data_freshness": {"is_stale": False, "status": "fresh"},
            })
            candidates.append(candidate)

        direction = apply_direction_health(candidates)
        self.assertIsInstance(direction["direction_health"], dict)
        portfolio = build_portfolio(candidates, "NORMAL_OSCILLATION")
        self.assertIsInstance(portfolio["positions"], list)
        for candidate in candidates:
            plan = derive_trade_plan(
                candidate,
                klines=make_daily(250),
                market_regime="NORMAL_OSCILLATION",
                trade_allowed=bool(candidate["rankable"]),
                as_of="2026-01-05",
            )
            attach_operation_contract(candidate, plan)
            self.assertIn("entry_zone", candidate)
            self.assertIn("stop", candidate)
            self.assertIn("invalidation", candidate)
            self.assertIn("trade_action", candidate)


if __name__ == "__main__":
    unittest.main()
