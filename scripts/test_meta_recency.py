# -*- coding: utf-8 -*-
"""Unit tests for meta analyzer recency weighting helpers."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from collections import Counter
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


def make_feature(player_id: int, match_id: int, match_batch: str) -> MODULE.PlayerFeature:
    return MODULE.PlayerFeature(
        player_id=player_id,
        match_id=match_id,
        rank=player_id,
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
        self.assertAlmostEqual(row["top4_rate"], 200.0 / 3.0, places=1)

    def test_compute_sample_weights_latest_batch_is_heaviest(self) -> None:
        features = [
            make_feature(1, 1, "0701"),
            make_feature(2, 2, "0706"),
        ]
        MODULE.compute_sample_weights(features, half_life_days=2.0)
        self.assertGreater(features[1].sample_weight, features[0].sample_weight)
        self.assertGreaterEqual(features[0].sample_weight, MODULE.MIN_RECENCY_WEIGHT)


if __name__ == "__main__":
    unittest.main()
