#!/usr/bin/env python3
"""
核心层工程化单元测试套件 (Core Layer Test Suite)
验证评分引擎、市场状态机与预判追踪器的数学确定性与边界用例。
"""
import unittest
import sys
from pathlib import Path

# 引入 scripts 目录
sys.path.insert(0, str(Path(__file__).resolve().parent))

from scoring_engine import (
    calc_technical_score,
    calc_industry_score,
    calc_catalyst_score,
    calculate_combined_score,
)
from market_state import evaluate_market_state, MarketRegime
from prediction_tracker import classify_prediction_type, evaluate_prediction


class TestScoringEngine(unittest.TestCase):

    def test_technical_score_low_position_and_low_volume(self):
        """测试低位+地量形态打分"""
        klines = [
            {"close": 10.0, "volume": 5000, "high": 10.5, "low": 9.8, "amplitude": 3.0}
            for _ in range(25)
        ]
        # 最近一天处于低位且缩量
        klines[-1] = {"close": 9.9, "volume": 1200, "high": 10.1, "low": 9.8, "amplitude": 2.0}
        res = calc_technical_score(klines)
        self.assertGreaterEqual(res["score"], 3.0)
        self.assertIn("极端地量", res["signals"])

    def test_industry_score_tariff_bonus(self):
        """测试关税/国产替代激活加分"""
        cand_semi = {"primary_sector": "半导体/封测/材料", "rel_position_52w": 20.0}
        res_normal = calc_industry_score(cand_semi, market_signal={"tariff_active": False})
        res_tariff = calc_industry_score(cand_semi, market_signal={"tariff_active": True})
        
        # 关税激活后半导体得分应显著提升
        self.assertGreater(res_tariff["score"], res_normal["score"])
        self.assertIn("卡脖子稀缺层", res_tariff["signals"])
        self.assertIn("关税/国产替代核心受益", res_tariff["signals"])

    def test_catalyst_score_counter_trend(self):
        """测试个股逆势抗跌加分"""
        cand = {"primary_sector": "机器人", "change_pct": 2.5}
        sector_flow = [{"name": "机器人", "main_net_inflow": "-8.5亿", "change_pct": -3.0}]
        res = calc_catalyst_score(cand, sector_flow)
        self.assertIn("逆板块强势收红", res["signals"])


class TestMarketStateMachine(unittest.TestCase):

    def test_panic_mode_trigger(self):
        """测试恐慌模式判定（创业板大跌）"""
        state = evaluate_market_state(
            sh_index={"change_pct": -2.8},
            cy_index={"change_pct": -5.6},
            candidate_summary={"total": 40, "up_count": 3, "passed": 5}
        )
        self.assertEqual(state["regime"], MarketRegime.PANIC_DOWNTREND.value)
        # 恐慌模式下技术/风控权重必须提升至 0.60
        self.assertEqual(state["weights"]["technical"], 0.60)
        self.assertEqual(state["weights"]["industry"], 0.20)

    def test_normal_oscillation_mode(self):
        """测试正常震荡模式"""
        state = evaluate_market_state(
            sh_index={"change_pct": 0.3},
            cy_index={"change_pct": 0.5},
            candidate_summary={"total": 40, "up_count": 22, "passed": 20}
        )
        self.assertEqual(state["regime"], MarketRegime.NORMAL_OSCILLATION.value)
        self.assertEqual(state["weights"]["technical"], 0.40)


class TestPredictionTracker(unittest.TestCase):

    def test_prediction_classification(self):
        """测试预判文本关键词分类"""
        self.assertEqual(classify_prediction_type("明日大概率延续放量上攻突破"), "偏多")
        self.assertEqual(classify_prediction_type("受外围拖累惯性低开弱势回调"), "偏空")
        self.assertEqual(classify_prediction_type("围绕3300点窄幅横盘蓄势"), "震荡")
        self.assertEqual(classify_prediction_type("探底后存在弱反弹需求"), "弱反弹")

    def test_prediction_review_evaluation(self):
        """测试自动化复盘评分与错误归因"""
        pred_bull = {"base_scenario": "放量上行，看多", "support": 3000, "resistance": 3100}
        # 实际大涨 -> 正确
        res_ok = evaluate_prediction(pred_bull, actual_change_pct=1.2, actual_high=3080, actual_low=3020)
        self.assertEqual(res_ok["error_type"], "正确")
        self.assertGreaterEqual(res_ok["score"], 80)

        # 实际大跌 -> 方向看反
        res_fail = evaluate_prediction(pred_bull, actual_change_pct=-1.5, actual_high=3020, actual_low=2950)
        self.assertEqual(res_fail["error_type"], "方向看反")
        self.assertLess(res_fail["score"], 40)


if __name__ == "__main__":
    unittest.main()
