# -*- coding: utf-8 -*-
"""Unit tests for meta analyzer recency weighting helpers."""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from collections import Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPEC = importlib.util.spec_from_file_location(
    "analyze_latest_meta",
    ROOT / ".cursor/skills/dzppq-meta-analysis/scripts/analyze_latest_meta.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

BACKTEST_SPEC = importlib.util.spec_from_file_location(
    "backtest_meta_analysis",
    ROOT / "scripts/backtest_meta_analysis.py",
)
BACKTEST = importlib.util.module_from_spec(BACKTEST_SPEC)
sys.modules[BACKTEST_SPEC.name] = BACKTEST
assert BACKTEST_SPEC.loader is not None
BACKTEST_SPEC.loader.exec_module(BACKTEST)


def make_feature(
    player_id: int,
    match_id: int,
    match_batch: str,
    *,
    rank: int = 4,
) -> MODULE.PlayerFeature:
    return MODULE.PlayerFeature(
        player_id=player_id,
        match_id=match_id,
        rank=rank,
        row_index=player_id - 1,
        partner_player=None,
        heroes=[],
        cards=[],
        trait_counts=Counter(),
        jiujiu_bonus=Counter(),
        trait_totals=Counter(),
        active_traits={},
        main_bond=None,
        main_carry=None,
        secondary_carry=None,
        hero_set=set(),
        level=8,
        match_batch=match_batch,
    )


class MetaRecencyTests(unittest.TestCase):
    def test_unpack_stat_item_defaults_weight(self) -> None:
        key, rank, weight = MODULE.unpack_stat_item(("card", 3))
        self.assertEqual((key, rank, weight), ("card", 3, 1.0))

    def test_rank_stats_weighted_rates(self) -> None:
        stats = MODULE.RankStats()
        stats.add(8, 1.0)
        stats.add(1, 2.0)
        row = stats.to_dict()
        self.assertEqual(row["appearances"], 2)
        self.assertAlmostEqual(row["weighted_appearances"], 3.0)
        self.assertAlmostEqual(row["avg_rank"], (8 + 2) / 3.0, places=2)
        self.assertAlmostEqual(row["top2_rate"], 200.0 / 3.0, places=1)
        self.assertAlmostEqual(row["top4_rate"], 200.0 / 3.0, places=1)

    def test_compute_sample_weights_latest_batch_is_heaviest(self) -> None:
        features = [
            make_feature(1, 1, "0701"),
            make_feature(2, 2, "0706"),
        ]
        MODULE.compute_sample_weights(features, half_life_days=2.0)
        self.assertGreater(features[1].sample_weight, features[0].sample_weight)
        self.assertGreaterEqual(features[0].sample_weight, MODULE.MIN_RECENCY_WEIGHT)

    def test_cross_year_batches_keep_january_latest(self) -> None:
        features = [
            make_feature(1, 1, "1231"),
            make_feature(2, 2, "0101"),
            make_feature(3, 3, "0102"),
        ]
        MODULE.compute_sample_weights(
            features,
            half_life_days=2.0,
            reference_date=date(2027, 1, 3),
        )
        self.assertEqual(
            MODULE.ordered_batches(features, date(2027, 1, 3)),
            ["1231", "0101", "0102"],
        )
        self.assertGreater(features[2].sample_weight, features[1].sample_weight)
        self.assertGreater(features[1].sample_weight, features[0].sample_weight)

    def test_old_samples_decay_below_legacy_permanent_floor(self) -> None:
        features = [
            make_feature(1, 1, "0101"),
            make_feature(2, 2, "0201"),
        ]
        MODULE.compute_sample_weights(
            features,
            half_life_days=2.0,
            reference_date=date(2027, 2, 2),
        )
        self.assertLess(features[0].sample_weight, 0.01)
        self.assertEqual(features[1].sample_weight, 1.0)

    def test_effective_sample_size_detects_concentrated_weights(self) -> None:
        stats = MODULE.RankStats()
        for weight in (10.0, 0.1, 0.1, 0.1):
            stats.add(1, weight)
        row = stats.to_dict()
        self.assertEqual(row["appearances"], 4)
        self.assertGreater(row["weighted_appearances"], 10)
        self.assertLess(row["n_eff"], 1.1)
        self.assertLess(row["n_eff"], row["appearances"])

    def test_extreme_small_sample_rates_are_shrunk(self) -> None:
        summary = MODULE.beta_posterior_summary(
            1.0, 2.0, 0.5, prior_strength=MODULE.COMPOSITION_RATE_PRIOR_STRENGTH
        )
        self.assertLess(summary["posterior_mean"], 0.6)
        self.assertLess(summary["lower_bound"], summary["posterior_mean"])

    def test_raw_ten_with_effective_three_is_not_recommendation_eligible(self) -> None:
        members = [
            make_feature(index, index, "0701" if index < 6 else "0702", rank=1)
            for index in range(1, 11)
        ]
        stats = MODULE.RankStats()
        for member in members:
            member.sample_weight = 0.3
            stats.add(member.rank, member.sample_weight)
        row = {
            "stats": stats.to_dict(),
            "play_style": "高费",
            "archetype_distribution": [{"archetype": "高费拼多多", "share": 100.0}],
        }
        evidence = MODULE.build_confidence_evidence(row, members)
        self.assertEqual(evidence["raw_n"], 10)
        self.assertAlmostEqual(evidence["weighted_n"], 3.0)
        self.assertFalse(evidence["recommendation_criteria"]["weighted_n"]["met"])
        self.assertFalse(evidence["recommendation_eligible"])

    def test_stable_multi_batch_sample_is_recommendation_eligible(self) -> None:
        members = [
            make_feature(
                index,
                index,
                f"070{(index - 1) % 3 + 1}",
                rank=1 if index <= 3 else 3,
            )
            for index in range(1, 13)
        ]
        stats = MODULE.RankStats()
        for member in members:
            stats.add(member.rank, member.sample_weight)
        row = {
            "stats": stats.to_dict(),
            "play_style": "高费",
            "archetype_distribution": [{"archetype": "高费拼多多", "share": 100.0}],
        }
        evidence = MODULE.build_confidence_evidence(row, members)
        self.assertTrue(evidence["recommendation_criteria"]["raw_n"]["met"])
        self.assertTrue(evidence["recommendation_criteria"]["n_eff"]["met"])
        self.assertTrue(evidence["recommendation_criteria"]["batch_coverage"]["met"])
        self.assertTrue(evidence["recommendation_eligible"])

    def test_high_sample_zero_win_comp_is_not_recommendation_eligible(self) -> None:
        members = [
            make_feature(index, index, f"070{(index - 1) % 3 + 1}", rank=3)
            for index in range(1, 25)
        ]
        stats = MODULE.RankStats()
        for member in members:
            stats.add(member.rank, member.sample_weight)
        row = {
            "stats": stats.to_dict(),
            "play_style": "高费",
            "archetype_distribution": [{"archetype": "高费拼多多", "share": 100.0}],
        }
        evidence = MODULE.build_confidence_evidence(
            row,
            members,
            {"avg_rank": 4.5, "top4_rate": 0.5, "win_rate": 0.125},
        )
        self.assertFalse(evidence["recommendation_criteria"]["observed_wins"]["met"])
        self.assertFalse(evidence["recommendation_eligible"])
        self.assertIn(
            "observed_wins",
            {item["criterion"] for item in evidence["recommendation_failure_reasons"]},
        )

    def test_comp_below_play_style_baseline_is_not_recommendation_eligible(self) -> None:
        members = [
            make_feature(
                index,
                index,
                f"070{(index - 1) % 3 + 1}",
                rank=1 if index == 1 else 6,
            )
            for index in range(1, 25)
        ]
        stats = MODULE.RankStats()
        for member in members:
            stats.add(member.rank, member.sample_weight)
        row = {
            "stats": stats.to_dict(),
            "play_style": "高费",
            "archetype_distribution": [{"archetype": "高费拼多多", "share": 100.0}],
        }
        evidence = MODULE.build_confidence_evidence(
            row,
            members,
            {"avg_rank": 3.8, "top4_rate": 0.65, "win_rate": 0.125},
        )
        criterion = evidence["recommendation_criteria"]["top4_vs_play_style_baseline"]
        self.assertFalse(criterion["met"])
        self.assertEqual(criterion["metric"], "shrunk_top4_90pct_lower_bound")
        self.assertFalse(evidence["recommendation_eligible"])

    def test_composition_trend_compares_recent_and_prior_windows(self) -> None:
        features = []
        strategy_ids = []
        player_id = 1
        for batch_index, batch in enumerate(("1230", "1231", "0101", "0102")):
            strategy_count = 4 if batch_index < 2 else 6
            for index in range(8):
                member = make_feature(
                    player_id,
                    player_id,
                    batch,
                    rank=(6 if batch_index < 2 else 2) if index < strategy_count else 5,
                )
                features.append(member)
                if index < strategy_count:
                    strategy_ids.append(player_id)
                player_id += 1
        stats = MODULE.RankStats()
        for feature in features:
            if feature.player_id in strategy_ids:
                stats.add(feature.rank)
        row = {
            "label": "趋势策略",
            "member_player_ids": strategy_ids,
            "play_style": "高费",
            "stats": stats.to_dict(),
            "confidence_evidence": {},
            "difficulty": {"score": 0.2},
        }
        metadata = MODULE.attach_composition_trends(
            [row],
            features,
            reference_date=date(2027, 1, 3),
        )
        self.assertEqual(metadata["recent_batches"], ["0101", "0102"])
        self.assertEqual(row["trend"]["label"], "上升")
        self.assertGreater(row["trend"]["changes"]["pick_rate"], 0)
        self.assertLess(row["trend"]["changes"]["shrunk_avg_rank"], 0)
        self.assertGreater(row["trend"]["changes"]["shrunk_top4_rate"], 0)

    def test_trend_is_insufficient_instead_of_guessing(self) -> None:
        recent = {
            "samples": 3,
            "population_samples": 20,
            "pick_rate": 15.0,
            "shrunk_avg_rank": 3.0,
            "shrunk_top4_rate": 70.0,
        }
        prior = {
            "samples": 3,
            "population_samples": 20,
            "pick_rate": 15.0,
            "shrunk_avg_rank": 5.0,
            "shrunk_top4_rate": 30.0,
        }
        label, reasons = MODULE.classify_trend(recent, prior)
        self.assertEqual(label, "insufficient")
        self.assertTrue(reasons)

    def test_explicit_balance_batch_changes_window_mode(self) -> None:
        features = [
            make_feature(index, index, batch)
            for index, batch in enumerate(("1230", "1231", "0101", "0102"), start=1)
        ]
        with tempfile.TemporaryDirectory() as directory:
            notes = Path(directory) / "balance.json"
            notes.write_text(json.dumps({"batch": "0101"}), encoding="utf-8")
            boundary = MODULE.extract_balance_boundary(notes, features)
        recent, prior, mode = MODULE.trend_window_batches(
            features,
            boundary_batch=boundary["batch"],
            reference_date=date(2027, 1, 3),
        )
        self.assertTrue(boundary["supported"])
        self.assertEqual(mode, "balance_boundary")
        self.assertEqual(recent, ["0101", "0102"])
        self.assertEqual(prior, ["1230", "1231"])

    def test_backtest_splits_have_no_future_leakage(self) -> None:
        features = [
            make_feature(index, index, batch)
            for index, batch in enumerate(("1230", "1231", "0101", "0102"), start=1)
        ]
        splits = BACKTEST.build_replay_splits(
            features,
            min_train_batches=2,
            max_splits=1,
        )
        self.assertEqual(len(splits), 1)
        split = splits[0]
        self.assertEqual(split["cutoff_batch"], "0101")
        self.assertEqual(split["validation_batch"], "0102")
        self.assertNotIn("0102", {feature.match_batch for feature in split["train"]})
        self.assertEqual(
            {feature.match_batch for feature in split["validation"]},
            {"0102"},
        )

    def test_backtest_reports_outcome_safety_metrics(self) -> None:
        validation = [
            make_feature(1, 1, "0703", rank=1),
            make_feature(2, 2, "0703", rank=6),
        ]
        for item in validation:
            item.archetype = "高费拼多多"
            item.hero_set = {"主C", "前排"}
        recommendation = {
            "label": "测试策略",
            "archetype": "高费拼多多",
            "core_heroes": [
                {"hero_name": "主C", "share": 100.0},
                {"hero_name": "前排", "share": 100.0},
            ],
            "confidence_evidence": {"raw_n": 12},
        }
        result = BACKTEST.evaluate_recommendations([recommendation], validation)
        self.assertEqual(result["matched_samples"], 2)
        self.assertEqual(result["recommended_top2_rate"], 50.0)
        self.assertEqual(result["recommended_win_rate"], 50.0)
        self.assertEqual(result["zero_win_recommendations"], 0)
        self.assertEqual(result["weak_recommendations"], 0)

    def test_backtest_keeps_regular_and_ceiling_tracks_distinct(self) -> None:
        features = [
            make_feature(index, index, batch, rank=1 if index % 2 else 3)
            for index, batch in enumerate(
                ("0701", "0701", "0702", "0702", "0703", "0703"), start=1
            )
        ]
        result = BACKTEST.run_backtest(
            features,
            min_train_batches=2,
            max_splits=1,
            min_comp_apps=5,
            recommendation_limit=2,
        )
        self.assertIn("current_quality_gate", result["summary"])
        self.assertIn("high_cost_ceiling", result["summary"])
        self.assertEqual(
            result["methodology"]["high_cost_ceiling"].split(";")[0],
            "separate final-board-only high-cost ceiling samples",
        )


    def test_ineligible_comps_still_enter_play_style_recommendations(self) -> None:
        row = {
            "label": "低样本赌狗 / 测试",
            "play_style": "赌狗",
            "recommendation_score": 3.0,
            "confidence_evidence": {"recommendation_eligible": False},
        }
        recommendations = MODULE.build_composition_recommendations([row])
        self.assertEqual(list(recommendations), ["赌狗", "高费"])
        self.assertEqual(recommendations["赌狗"], [row])
        self.assertEqual(recommendations["高费"], [])


if __name__ == "__main__":
    unittest.main()
