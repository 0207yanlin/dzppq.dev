# -*- coding: utf-8 -*-
"""Regression tests for business-level primary-bond classification."""

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
    "analyze_latest_meta_primary_bond",
    ROOT / ".cursor/skills/dzppq-meta-analysis/scripts/analyze_latest_meta.py",
)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def hero_with_equipment(equipment_name: str) -> MODULE.Hero:
    normalized, selected = MODULE.normalize_equipment_name(equipment_name)
    return MODULE.Hero(
        id=1,
        name="蛋小黑",
        canonical_name="蛋小黑",
        slot_index=0,
        tier=1,
        stars=2,
        equipment_count=1,
        equipments=[MODULE.Equipment(equipment_name, normalized, selected)],
        traits=["美食社"],
        carry_score=10.0,
    )


def feature(
    player_id: int,
    *,
    totals: dict[str, int] | None = None,
    active_traits: dict[str, int] | None = None,
    archetype: str = "拼多多",
    heroes: list[MODULE.Hero] | None = None,
    jiujiu_bonus: dict[str, int] | None = None,
    main_bond: str | None = None,
) -> MODULE.PlayerFeature:
    totals = totals or {}
    active_traits = active_traits or {}
    return MODULE.PlayerFeature(
        player_id=player_id,
        match_id=player_id,
        rank=((player_id - 1) % 8) + 1,
        row_index=player_id,
        partner_player=None,
        heroes=heroes or [],
        cards=[],
        trait_counts=Counter(
            {
                trait: count - (jiujiu_bonus or {}).get(trait, 0)
                for trait, count in totals.items()
            }
        ),
        jiujiu_bonus=Counter(jiujiu_bonus or {}),
        trait_totals=Counter(totals),
        active_traits=active_traits,
        main_bond=main_bond or next(iter(active_traits), None),
        main_carry=None,
        secondary_carry=None,
        hero_set=set(),
        level=8,
        archetype=archetype,
    )


class PrimaryBondBusinessClassificationTests(unittest.TestCase):
    def test_food_harvest_merges_into_food_bond_with_auditable_sources(self) -> None:
        harvest = feature(
            1,
            totals={"美食社": 1},
            active_traits={},
            heroes=[hero_with_equipment("美味大餐")],
        )
        harvest_by_archetype = feature(
            2,
            totals={"美食社": 2},
            active_traits={},
            archetype="美食社收菜",
        )
        qualified = feature(
            3,
            totals={"美食社": 5},
            active_traits={"美食社": 5},
            main_bond="美食社",
        )

        result = MODULE.analyze_primary_bond_strength(
            [harvest, harvest_by_archetype, qualified], min_apps=1, baseline=4.5
        )

        self.assertEqual([row["bond"] for row in result["rows"]], ["美食社"])
        self.assertEqual(
            result["rows"][0]["source_distribution"],
            {"food_harvest": 2, "qualified_bond": 1},
        )
        self.assertEqual(harvest.main_bond, None)
        self.assertEqual(qualified.main_bond, "美食社")

    def test_normal_bond_must_reach_second_configured_threshold(self) -> None:
        first_tier = feature(
            1, totals={"音乐社": 2}, active_traits={"音乐社": 2}
        )
        second_tier = feature(
            2, totals={"音乐社": 3}, active_traits={"音乐社": 3}
        )

        self.assertEqual(MODULE.primary_bonds_by_count(first_tier), [])
        self.assertEqual(
            MODULE.primary_bonds_by_count(second_tier),
            [("音乐社", 3, 3)],
        )

    def test_jiujiu_count_can_reach_second_threshold(self) -> None:
        board = feature(
            1,
            totals={"音乐社": 3},
            active_traits={"音乐社": 3},
            jiujiu_bonus={"音乐社": 1},
        )

        self.assertEqual(board.trait_counts["音乐社"], 2)
        self.assertEqual(MODULE.primary_bonds_by_count(board), [("音乐社", 3, 3)])

    def test_qualified_bonds_tied_by_activation_count_all_count(self) -> None:
        board = feature(
            1,
            totals={"音乐社": 4, "种地社": 4, "电玩社": 2},
            active_traits={"音乐社": 4, "种地社": 4, "电玩社": 2},
        )

        self.assertEqual(
            set(MODULE.primary_bonds_by_count(board)),
            {("音乐社", 4, 4), ("种地社", 4, 4)},
        )

    def test_study_tier4_excludes_other_qualified_bonds(self) -> None:
        board = feature(
            1,
            totals={"学习社": 4, "种地社": 4, "座位更换者": 4},
            active_traits={"学习社": 4, "种地社": 4, "座位更换者": 4},
        )

        self.assertEqual(MODULE.primary_bonds_by_count(board), [("学习社", 4, 4)])
        self.assertEqual(
            MODULE.primary_bond_business_selections(board)[0]["source"],
            "study_override",
        )

    def test_study_tier4_overrides_higher_farming_count(self) -> None:
        board = feature(
            1,
            totals={"学习社": 4, "种地社": 6},
            active_traits={"学习社": 4, "种地社": 6},
        )

        self.assertEqual(MODULE.primary_bonds_by_count(board), [("学习社", 4, 4)])

    def test_study_tier3_does_not_trigger_override(self) -> None:
        board = feature(
            1,
            totals={"学习社": 3, "种地社": 4},
            active_traits={"学习社": 3, "种地社": 4},
        )

        self.assertEqual(MODULE.primary_bonds_by_count(board), [("种地社", 4, 4)])

    def test_study_override_covers_food_harvest(self) -> None:
        board = feature(
            1,
            totals={"学习社": 4, "美食社": 2},
            active_traits={"学习社": 4},
            archetype="美食社收菜",
            heroes=[hero_with_equipment("美味大餐")],
        )

        self.assertEqual(MODULE.primary_bonds_by_count(board), [("学习社", 4, 4)])
        self.assertEqual(
            MODULE.primary_bond_business_selections(board)[0]["source"],
            "study_override",
        )

    def test_study_override_before_high_cost_pdd(self) -> None:
        board = feature(
            1,
            totals={"学习社": 4},
            active_traits={"学习社": 4},
            archetype="高费拼多多",
        )

        self.assertEqual(MODULE.primary_bonds_by_count(board), [("学习社", 4, 4)])

    def test_high_cost_pdd_is_fallback_without_qualified_bond(self) -> None:
        board = feature(
            1,
            totals={"音乐社": 2},
            active_traits={"音乐社": 2},
            archetype="高费拼多多",
            main_bond="音乐社",
        )

        self.assertEqual(
            MODULE.primary_bonds_by_count(board),
            [("高费拼多多", 0, 0)],
        )
        self.assertEqual(board.main_bond, "音乐社")

    def test_first_tier_scattered_board_is_excluded(self) -> None:
        board = feature(
            1,
            totals={"音乐社": 2, "电玩社": 2, "考古社": 3},
            active_traits={"音乐社": 2, "电玩社": 2, "考古社": 3},
            archetype="拼多多",
        )

        self.assertEqual(MODULE.primary_bonds_by_count(board), [])


if __name__ == "__main__":
    unittest.main()
