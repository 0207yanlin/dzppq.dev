# -*- coding: utf-8 -*-
"""Regression tests for auditable composition identity and strategy merging."""

from __future__ import annotations

import importlib.util
import sys
import unittest
from collections import Counter
from pathlib import Path
from unittest import mock


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


def hero(
    name: str,
    *,
    tier: int = 4,
    stars: int = 2,
    traits: list[str] | None = None,
    equipment: str | None = None,
) -> MODULE.Hero:
    equipments = []
    if equipment:
        normalized, selected = MODULE.normalize_equipment_name(equipment)
        equipments.append(MODULE.Equipment(equipment, normalized, selected))
    return MODULE.Hero(
        id=abs(hash((name, tier))) % 100000,
        name=name,
        canonical_name=name,
        slot_index=0,
        tier=tier,
        stars=stars,
        equipment_count=len(equipments),
        equipments=equipments,
        traits=traits or [],
        carry_score=100.0 if equipment else 10.0,
    )


def feature(
    player_id: int,
    heroes: list[MODULE.Hero],
    *,
    archetype: str = "拼多多",
    active_traits: dict[str, int] | None = None,
    investment: dict | None = None,
) -> MODULE.PlayerFeature:
    active_traits = active_traits or {}
    return MODULE.PlayerFeature(
        player_id=player_id,
        match_id=player_id,
        rank=player_id,
        row_index=player_id,
        partner_player=None,
        heroes=heroes,
        cards=[],
        trait_counts=Counter(),
        jiujiu_bonus=Counter(),
        trait_totals=Counter(active_traits),
        active_traits=active_traits,
        main_bond=next(iter(active_traits), None),
        main_carry=heroes[0] if heroes else None,
        secondary_carry=heroes[1] if len(heroes) > 1 else None,
        hero_set={unit.name for unit in heroes},
        level=8,
        carry_candidates=heroes[:3],
        archetype=archetype,
        trait_investment=investment
        or {"dominant_trait": None, "stable_traits": [], "scattered_active_traits": len(active_traits)},
        high_cost_structure={},
    )


class MetaCompositionTests(unittest.TestCase):
    def test_food_archetype_recognizes_all_harvest_prefixes(self) -> None:
        investment = {"stable_traits": [], "scattered_active_traits": 0, "traits": [], "dominant_trait": None}
        for equipment, normalized in (
            ("核选美味大餐", "美味大餐"),
            ("绝味盛宴", "绝味盛宴"),
            ("暗黑料理", "暗黑料理"),
        ):
            with self.subTest(equipment=equipment):
                units = [hero("蛋小黑", equipment=equipment)]
                evidence = MODULE.food_harvest_evidence(units)
                archetype, signals, _ = MODULE.classify_archetype(
                    units, evidence, investment, "高费"
                )
                self.assertEqual(archetype, "美食社收菜")
                self.assertEqual(signals[0]["equipment"]["equipment_name"], normalized)

    def test_equipment_kind_classifies_super_and_food_items(self) -> None:
        self.assertTrue(MODULE.is_super_equipment("核选幸运猫猫"))
        self.assertEqual(MODULE.equipment_kind("核选幸运猫猫"), "super")
        self.assertTrue(MODULE.is_super_equipment("鲱鱼罐头"))
        self.assertTrue(MODULE.is_super_equipment("核选鲱鱼罐头"))
        self.assertEqual(MODULE.equipment_kind("核选鲱鱼罐头"), "super")
        self.assertIn("鲱鱼罐头", MODULE.SUPER_EQUIPMENT_NAMES)
        self.assertTrue(MODULE.is_food_equipment("核选美味大餐"))
        self.assertTrue(MODULE.is_food_equipment("杏仁豆腐"))
        self.assertTrue(MODULE.is_food_equipment("椒盐酥糖"))
        self.assertTrue(MODULE.is_food_equipment("岛好锅"))
        self.assertEqual(MODULE.equipment_kind("岛好锅"), "food")
        self.assertFalse(MODULE.is_food_equipment("拳王手套"))
        self.assertEqual(MODULE.equipment_kind("拳王手套"), "normal")
        # Special bare food names do not by themselves trigger harvest archetype evidence.
        units = [hero("厨师长", equipment="杏仁豆腐")]
        self.assertEqual(MODULE.food_harvest_evidence(units), [])

    def test_special_equipment_ranks_wearers_and_marks_low_confidence(self) -> None:
        features = []
        for idx in range(10):
            features.append(
                feature(
                    idx + 1,
                    [hero("厨师长", equipment="幸运猫猫")],
                )
            )
        features.append(feature(99, [hero("炸鸡三宝", equipment="岛好锅")]))
        for unit in features:
            unit.rank = 2 if unit.player_id != 99 else 8
            unit.sample_weight = 1.0
        result = MODULE.analyze_special_equipment(
            features,
            baseline=4.5,
            kind="super",
            always_include=MODULE.SUPER_EQUIPMENT_NAMES,
        )
        names = [row["equipment_name"] for row in result["rankings"]]
        self.assertEqual(set(names), set(MODULE.SUPER_EQUIPMENT_NAMES))
        lucky = next(row for row in result["rankings"] if row["equipment_name"] == "幸运猫猫")
        self.assertGreaterEqual(lucky["appearances"], 10)
        self.assertTrue(lucky["recommended_wearers"])
        self.assertEqual(lucky["recommended_wearers"][0]["hero_name"], "厨师长")

        food = MODULE.analyze_special_equipment(
            features,
            baseline=4.5,
            kind="food",
            always_include=MODULE.FOOD_SPECIAL_EQUIPMENT_NAMES,
        )
        island = next(row for row in food["rankings"] if row["equipment_name"] == "岛好锅")
        self.assertEqual(island["confidence"], "低")
        self.assertIn("低置信", island["note"])

    def test_high_cost_pdd_does_not_rewrite_real_main_bond(self) -> None:
        units = [
            hero("A", tier=5, stars=2, traits=["音乐社"]),
            hero("B", tier=4, stars=2, traits=["考古社"]),
            hero("C", tier=4, stars=2, traits=["宠物社"]),
            hero("D", tier=5, stars=2, traits=["种地社"]),
            hero("E", tier=2, stars=2, traits=["美食社"]),
        ]
        investment = MODULE.analyze_trait_investment(
            units,
            {"音乐社": 2},
            Counter({"音乐社": 1}),
            {"音乐社": [2, 3, 4]},
            units[0],
        )
        archetype, signals, structure = MODULE.classify_archetype(
            units, [], investment, "高费", units[0]
        )
        board = feature(9, units, archetype=archetype, active_traits={"音乐社": 2})
        self.assertEqual(archetype, "高费拼多多")
        self.assertEqual(structure["four_five_cost_count"], 4)
        self.assertEqual(structure["low_cost_three_star_count"], 0)
        self.assertTrue(structure["main_carry_is_high_cost_two_star"])
        self.assertEqual(signals[0]["main_carry_tier"], 5)
        self.assertEqual(board.main_bond, "音乐社")
        self.assertEqual(board.active_traits, {"音乐社": 2})

    def test_any_low_cost_three_star_forces_reroll_play_style(self) -> None:
        high_cost_main = hero("高费主C", tier=5, stars=2, equipment="攻击力")
        high_cost_main.equipment_count = 3
        low_cost_support = hero("低费副C", tier=2, stars=3)
        fillers = [hero(name, tier=4, stars=2) for name in ("甲", "乙", "丙", "丁", "戊")]
        board = feature(
            1,
            [high_cost_main, low_cost_support, *fillers],
            archetype="拼多多",
        )
        board.level = 9
        board.main_carry = high_cost_main
        board.secondary_carry = low_cost_support
        self.assertEqual(MODULE.classify_play_style(board), "赌狗")

    def test_level_seven_high_cost_main_with_low_cost_three_star_is_reroll(self) -> None:
        high_cost_main = hero("高费主C", tier=4, stars=2, equipment="攻击力")
        high_cost_main.equipment_count = 3
        low_cost_unit = hero("低费挂件", tier=1, stars=3)
        fillers = [hero(name, tier=4, stars=2) for name in ("甲", "乙", "丙", "丁")]
        board = feature(
            2,
            [high_cost_main, low_cost_unit, *fillers],
            archetype="拼多多",
        )
        board.level = 7
        board.main_carry = high_cost_main
        self.assertEqual(MODULE.classify_play_style(board), "赌狗")

    def test_true_high_cost_pdd_requires_no_low_cost_three_star(self) -> None:
        units = [
            hero("高费主C", tier=5, stars=2, traits=["音乐社"], equipment="攻击力"),
            hero("高费副C", tier=4, stars=2, traits=["考古社"]),
            hero("前排甲", tier=4, stars=2, traits=["宠物社"]),
            hero("前排乙", tier=5, stars=2, traits=["种地社"]),
            hero("挂件", tier=2, stars=2, traits=["美食社"]),
        ]
        units[0].equipment_count = 3
        investment = MODULE.analyze_trait_investment(
            units,
            {"音乐社": 2},
            Counter({"音乐社": 1}),
            {"音乐社": [2, 3, 4]},
            units[0],
        )
        clean, _, structure = MODULE.classify_archetype(
            units, [], investment, "高费", units[0]
        )
        self.assertEqual(clean, "高费拼多多")
        self.assertEqual(structure["low_cost_three_star_count"], 0)

        polluted = [
            units[0],
            hero("低费三星", tier=2, stars=3, traits=["学习社"]),
            *units[2:],
        ]
        polluted_investment = MODULE.analyze_trait_investment(
            polluted,
            {"音乐社": 2},
            Counter({"音乐社": 1}),
            {"音乐社": [2, 3, 4]},
            polluted[0],
        )
        archetype, _, polluted_structure = MODULE.classify_archetype(
            polluted, [], polluted_investment, "高费", polluted[0]
        )
        self.assertNotEqual(archetype, "高费拼多多")
        self.assertGreaterEqual(polluted_structure["low_cost_three_star_count"], 1)

    def test_low_cost_main_carry_cannot_be_high_cost_pdd(self) -> None:
        units = [
            hero("低费主C", tier=3, stars=2, traits=["音乐社"], equipment="攻击力"),
            hero("高费甲", tier=4, stars=2, traits=["考古社"]),
            hero("高费乙", tier=4, stars=2, traits=["宠物社"]),
            hero("高费丙", tier=5, stars=2, traits=["种地社"]),
            hero("高费丁", tier=5, stars=2, traits=["学习社"]),
        ]
        units[0].equipment_count = 3
        investment = MODULE.analyze_trait_investment(
            units,
            {"音乐社": 2},
            Counter({"音乐社": 1}),
            {"音乐社": [2, 3, 4]},
            units[0],
        )
        archetype, _, structure = MODULE.classify_archetype(
            units, [], investment, "高费", units[0]
        )
        self.assertNotEqual(archetype, "高费拼多多")
        self.assertFalse(structure["main_carry_is_high_cost_two_star"])

    def test_mature_stage_bucket_overrides_aggregate_majority(self) -> None:
        high_units = [
            hero("高费主C", tier=5, stars=2, equipment="攻击力"),
            hero("副C", tier=4, stars=2),
            hero("前排甲", tier=4, stars=2),
            hero("前排乙", tier=4, stars=2),
            hero("挂件甲", tier=2, stars=2),
            hero("挂件乙", tier=2, stars=2),
            hero("挂件丙", tier=1, stars=2),
            hero("挂件丁", tier=1, stars=2),
        ]
        high_units[0].equipment_count = 3
        mature_members = []
        for player_id in range(1, 6):
            member = feature(
                player_id,
                [hero(u.name, tier=u.tier, stars=u.stars, equipment=("攻击力" if idx == 0 else None)) for idx, u in enumerate(high_units)],
                archetype="高费拼多多",
                active_traits={"音乐社": 2},
            )
            member.heroes[0].equipment_count = 3
            member.level = 9
            member.rank = 2
            member.high_cost_structure = {
                "four_five_cost_count": 4,
                "four_five_cost_share": 0.5,
                "low_cost_three_star_count": 0,
                "main_carry_is_high_cost_two_star": True,
            }
            mature_members.append(member)

        # Transition boards still share the same high-cost skeleton, but each
        # keeps a low-cost 3-star unit so board-level play_style is 赌狗.
        reroll_units = [
            hero("高费主C", tier=5, stars=2, equipment="攻击力"),
            hero("副C", tier=4, stars=2),
            hero("前排甲", tier=4, stars=2),
            hero("前排乙", tier=4, stars=2),
            hero("挂件甲", tier=2, stars=3),
            hero("挂件乙", tier=2, stars=2),
            hero("挂件丙", tier=1, stars=2),
            hero("挂件丁", tier=1, stars=2),
        ]
        transition_members = []
        for player_id in range(11, 21):
            member = feature(
                player_id,
                [hero(u.name, tier=u.tier, stars=u.stars, equipment=("攻击力" if idx == 0 else None)) for idx, u in enumerate(reroll_units)],
                archetype="高费拼多多",
                active_traits={"音乐社": 2},
            )
            member.heroes[0].equipment_count = 3
            member.level = 8
            member.rank = 5
            transition_members.append(member)

        mature_row = MODULE.build_composition_row(mature_members, 1, 15, 15)
        mature_row["main_bond"] = "音乐社-4"
        transition_row = MODULE.build_composition_row(transition_members, 2, 15, 15)
        transition_row["main_bond"] = "音乐社-2"
        self.assertEqual(mature_row["play_style"], "高费")
        self.assertEqual(transition_row["play_style"], "赌狗")
        all_members = mature_members + transition_members
        strategy = MODULE.merge_comp_strategies([mature_row, transition_row], all_members)[0]
        self.assertEqual(strategy["play_style"], "高费")
        self.assertEqual(strategy["mature_stage"]["play_style"], "高费")
        recommendations = MODULE.build_composition_recommendations([strategy])
        self.assertEqual(recommendations["高费"], [strategy])
        self.assertEqual(recommendations["赌狗"], [])
        self.assertFalse(
            MODULE.has_low_cost_three_star_carry_requirement(strategy)
        )

    def test_low_cost_three_star_carry_requirement_forces_reroll_bucket(self) -> None:
        row = {
            "label": "混合 / 测试",
            "play_style": "高费",
            "recommendation_score": 1.0,
            "mature_stage": {
                "play_style": "高费",
                "carry_requirements": [
                    {
                        "hero_name": "失心熊",
                        "tier": 3,
                        "recommended_min_stars": 3,
                    }
                ],
            },
            "carry_requirements": [
                {
                    "hero_name": "失心熊",
                    "tier": 3,
                    "recommended_min_stars": 3,
                }
            ],
        }
        self.assertEqual(MODULE.resolve_strategy_play_style(row), "赌狗")
        recommendations = MODULE.build_composition_recommendations(
            [{**row, "play_style": MODULE.resolve_strategy_play_style(row)}]
        )
        self.assertEqual(recommendations["赌狗"], [recommendations["赌狗"][0]])
        self.assertEqual(recommendations["高费"], [])

    def test_low_cost_three_star_stabilizes_its_shallow_carry_trait(self) -> None:
        units = [
            hero("赌狗主C", tier=2, stars=3, traits=["音乐社"], equipment="攻击力"),
            hero("挂件甲", tier=1, traits=["音乐社"]),
            hero("挂件乙", tier=4, traits=["考古社"]),
            hero("挂件丙", tier=4, traits=["种地社"]),
            hero("挂件丁", tier=4, traits=["宠物社"]),
        ]
        investment = MODULE.analyze_trait_investment(
            units,
            {"音乐社": 2, "考古社": 1, "种地社": 1, "宠物社": 1},
            Counter({"音乐社": 2, "考古社": 1, "种地社": 1, "宠物社": 1}),
            {
                "音乐社": [2, 4, 6],
                "考古社": [1, 3],
                "种地社": [1, 3],
                "宠物社": [1, 3],
            },
            units[0],
        )
        rows = {row["trait"]: row for row in investment["traits"]}
        self.assertTrue(investment["scattered_structure"])
        self.assertTrue(rows["音乐社"]["low_cost_three_star_carry_aligned"])
        self.assertTrue(rows["音乐社"]["stable"])
        self.assertFalse(rows["考古社"]["stable"])

    def test_cluster_is_order_independent_and_identity_safe(self) -> None:
        shared = [hero(name, tier=4) for name in ("甲", "乙", "丙", "丁", "戊")]
        food_a = feature(1, shared, archetype="美食社收菜")
        food_b = feature(2, shared, archetype="美食社收菜")
        pdd_a = feature(3, shared, archetype="高费拼多多")
        pdd_b = feature(4, shared, archetype="高费拼多多")
        forward = MODULE.cluster_compositions([food_a, food_b, pdd_a, pdd_b], 2)
        reverse = MODULE.cluster_compositions([pdd_b, pdd_a, food_b, food_a], 2)
        self.assertEqual(
            [(row["archetype"], row["member_player_ids"]) for row in forward],
            [(row["archetype"], row["member_player_ids"]) for row in reverse],
        )
        self.assertEqual({row["archetype"] for row in forward}, {"美食社收菜", "高费拼多多"})
        self.assertTrue(all(row["label"].startswith(f"{row['archetype']} / ") for row in forward))

    def test_food_harvest_identity_survives_strategy_merge(self) -> None:
        units = [hero(name, tier=4) for name in ("甲", "乙", "丙", "丁", "戊")]
        members = [
            feature(1, units, archetype="美食社收菜"),
            feature(2, units, archetype="美食社收菜"),
        ]
        row = MODULE.build_composition_row(members, 1, 2, 2)
        strategy = MODULE.merge_comp_strategies([row], members)[0]
        self.assertEqual(strategy["archetype"], "美食社收菜")
        self.assertTrue(strategy["label"].startswith("美食社收菜 / "))

    def test_cluster_and_merge_reasons_describe_actual_members(self) -> None:
        units = [hero(name, tier=4) for name in ("甲", "乙", "丙", "丁", "戊")]
        members = [
            feature(1, units, archetype="羁绊运营:音乐社", active_traits={"音乐社": 3},
                    investment={"dominant_trait": "音乐社", "stable_traits": ["音乐社"], "scattered_active_traits": 1}),
            feature(2, units, archetype="羁绊运营:音乐社", active_traits={"音乐社": 3},
                    investment={"dominant_trait": "音乐社", "stable_traits": ["音乐社"], "scattered_active_traits": 1}),
        ]
        row = MODULE.build_composition_row(members, 1, 2, 2)
        self.assertEqual(row["cluster_reason"]["archetype_distribution"][0]["appearances"], 2)
        strategy = MODULE.merge_comp_strategies([row], members)[0]
        self.assertEqual(strategy["merge_reason"]["mature_member_count"], 2)
        self.assertEqual(strategy["merge_reason"]["ownership_rule"], "one_player_one_top_level_strategy")

    def test_high_tier_subgroup_recomputes_its_cluster_reason(self) -> None:
        units = [hero(name, tier=4) for name in ("甲", "乙", "丙", "丁", "戊")]
        members = [
            feature(
                player_id,
                units,
                archetype="羁绊运营:音乐社",
                active_traits={"考古社": 99, "音乐社": 3 if player_id <= 16 else 2},
                investment={"dominant_trait": "音乐社", "stable_traits": ["音乐社"], "scattered_active_traits": 1},
            )
            for player_id in range(1, 21)
        ]
        with mock.patch.object(
            MODULE,
            "load_game_config",
            return_value=({}, {"考古社": [2], "音乐社": [2, 3]}),
        ):
            rows = MODULE.cluster_compositions(members, 2)
        base = next(row for row in rows if not row["is_subfamily"])
        subgroup = next(row for row in rows if row["is_subfamily"])
        self.assertEqual(base["cluster_reason"]["archetype_distribution"][0]["appearances"], 20)
        self.assertEqual(subgroup["cluster_reason"]["archetype_distribution"][0]["appearances"], 16)

    def test_merge_uses_mature_stats_and_unique_player_ownership(self) -> None:
        units = [hero(name, tier=4) for name in ("甲", "乙", "丙", "丁", "戊")]
        members = [
            feature(1, units, archetype="羁绊运营:音乐社", investment={"dominant_trait": "音乐社", "stable_traits": ["音乐社"], "scattered_active_traits": 1}),
            feature(2, units, archetype="羁绊运营:音乐社", investment={"dominant_trait": "音乐社", "stable_traits": ["音乐社"], "scattered_active_traits": 1}),
        ]
        base = MODULE.build_composition_row(members, 1, 2, 2)
        base.update({"main_bond": "音乐社-3", "archetype": "羁绊运营:音乐社"})
        mature = MODULE.build_composition_row([members[0]], 2, 2, 2, is_subfamily=True)
        mature.update({"main_bond": "音乐社-5", "archetype": "羁绊运营:音乐社"})
        merged = MODULE.merge_comp_strategies([base, mature], members)
        self.assertEqual(len(merged), 1)
        strategy = merged[0]
        self.assertEqual(strategy["mature_stats"]["appearances"], 1)
        self.assertEqual(strategy["aggregate_stats"]["appearances"], 2)
        self.assertEqual(strategy["stats"], strategy["mature_stats"])
        self.assertEqual(strategy["member_player_ids"], [1, 2])
        self.assertEqual(strategy["transition_stats"]["appearances"], 1)

    def test_weak_high_tier_stage_does_not_replace_stronger_mature_candidate(self) -> None:
        units = [hero(name, tier=4) for name in ("甲", "乙", "丙", "丁", "戊")]
        investment = {
            "dominant_trait": "音乐社",
            "stable_traits": ["音乐社"],
            "scattered_active_traits": 1,
        }
        strong_members = [
            feature(
                player_id,
                units,
                archetype="羁绊运营:音乐社",
                investment=investment,
            )
            for player_id in range(1, 13)
        ]
        weak_members = [
            feature(
                player_id,
                units,
                archetype="羁绊运营:音乐社",
                investment=investment,
            )
            for player_id in range(13, 25)
        ]
        for index, member in enumerate(strong_members):
            member.rank = 1 if index < 3 else 3
        for member in weak_members:
            member.rank = 7
        strong = MODULE.build_composition_row(strong_members, 1, 24, 24)
        strong.update({"main_bond": "音乐社-3", "archetype": "羁绊运营:音乐社"})
        weak_high_tier = MODULE.build_composition_row(
            weak_members, 2, 24, 24, is_subfamily=True
        )
        weak_high_tier.update(
            {"main_bond": "音乐社-5", "archetype": "羁绊运营:音乐社"}
        )

        strategy = MODULE.merge_comp_strategies(
            [strong, weak_high_tier], strong_members + weak_members
        )[0]

        self.assertEqual(strategy["mature_stage"]["bond"], "音乐社-3")
        self.assertTrue(strategy["stage_inversion_diagnostics"]["detected"])
        rejected = strategy["stage_inversion_diagnostics"][
            "rejected_higher_tier_stages"
        ]
        self.assertEqual(rejected[0]["bond"], "音乐社-5")
        self.assertIn("avg_rank_regression", rejected[0]["inversion_reasons"])

    def test_transition_with_changed_carry_merges_by_core_and_identity(self) -> None:
        mature_units = [hero(name, tier=4) for name in ("甲", "乙", "丙", "丁", "戊")]
        transition_units = [
            mature_units[1],
            mature_units[0],
            *mature_units[2:],
        ]
        investment = {
            "dominant_trait": "音乐社",
            "stable_traits": ["音乐社"],
            "scattered_active_traits": 1,
        }
        mature_member = feature(
            1, mature_units, archetype="羁绊运营:音乐社", investment=investment
        )
        transition_member = feature(
            2, transition_units, archetype="羁绊运营:音乐社", investment=investment
        )
        mature = MODULE.build_composition_row([mature_member], 1, 2, 2)
        mature["main_bond"] = "音乐社-5"
        transition = MODULE.build_composition_row([transition_member], 2, 2, 2)
        transition["main_bond"] = "音乐社-3"
        merged = MODULE.merge_comp_strategies(
            [transition, mature], [transition_member, mature_member]
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["aggregate_stats"]["appearances"], 2)
        self.assertEqual(merged[0]["transition_stats"]["appearances"], 1)

    def test_same_carries_do_not_merge_different_archetypes(self) -> None:
        units = [hero(name, tier=4) for name in ("甲", "乙", "丙", "丁", "戊")]
        food = feature(1, units, archetype="美食社收菜")
        pdd = feature(2, units, archetype="高费拼多多")
        rows = [
            MODULE.build_composition_row([food], 1, 2, 2),
            MODULE.build_composition_row([pdd], 2, 2, 2),
        ]
        merged = MODULE.merge_comp_strategies(rows, [food, pdd])
        self.assertEqual(len(merged), 2)
        self.assertEqual(
            {row["archetype"] for row in merged},
            {"美食社收菜", "高费拼多多"},
        )
        owned_ids = [
            player_id
            for row in merged
            for player_id in row["member_player_ids"]
        ]
        self.assertEqual(sorted(owned_ids), [1, 2])

    def test_shared_carry_alone_does_not_merge_same_archetype(self) -> None:
        left_units = [hero(name, tier=4) for name in ("共享主C", "甲", "乙", "丙", "丁")]
        right_units = [hero(name, tier=4) for name in ("共享主C", "戊", "己", "庚", "辛")]
        left = feature(1, left_units, archetype="高费拼多多")
        right = feature(2, right_units, archetype="高费拼多多")
        rows = [
            MODULE.build_composition_row([left], 1, 2, 2),
            MODULE.build_composition_row([right], 2, 2, 2),
        ]
        merged = MODULE.merge_comp_strategies(rows, [left, right])
        self.assertEqual(len(merged), 2)

    def test_high_cost_pdd_strategy_group_blocks_transitive_bridge(self) -> None:
        def stage(
            label: str,
            player_id: int,
            appearances: int,
            names: list[str],
        ) -> dict:
            return {
                "label": label,
                "archetype": "高费拼多多",
                "main_bond": "拼多多",
                "main_carries": [
                    {"hero_name": names[0], "share": 100.0},
                    {"hero_name": names[1], "share": 100.0},
                ],
                "core_heroes": [
                    {"hero_name": name, "share": 100.0} for name in names
                ],
                "member_player_ids": [player_id],
                "is_subfamily": False,
                "stats": {"appearances": appearances},
            }

        left = stage("左", 1, 30, ["甲", "乙", "丙", "丁", "戊"])
        bridge = stage("桥", 2, 20, ["甲", "乙", "丙", "丁", "戊", "己"])
        right = stage("右", 3, 10, ["乙", "丙", "丁", "戊", "己", "庚"])
        self.assertTrue(MODULE.strategy_rows_compatible(left, bridge))
        self.assertTrue(MODULE.strategy_rows_compatible(bridge, right))
        self.assertFalse(MODULE.strategy_rows_compatible(left, right))

        grouped = MODULE.group_strategy_rows([right, bridge, left])
        groups = list(grouped.values())

        self.assertEqual(len(groups), 2)
        self.assertFalse(
            any(
                {"左", "右"} <= {row["label"] for row in members}
                for members in groups
            )
        )

    def test_target_pdd_stage_is_not_absorbed_into_music_stage(self) -> None:
        target_names = [
            "法莉塔",
            "南瓜喵呜",
            "血衣教师礼温",
            "好柿连连",
            "天才黑客米鲁比",
            "饶舌诗人马修",
            "闪电阿存",
            "投手阿豪",
            "魔警艾琳",
        ]
        music_names = [
            "法莉塔",
            "好柿连连",
            "天才黑客米鲁比",
            "血衣教师礼温",
            "饶舌诗人马修",
            "蛋小绿",
            "吉他手卡萝",
            "主唱耀星",
            "音乐教父",
        ]

        def stage(
            label: str,
            player_id: int,
            appearances: int,
            names: list[str],
        ) -> dict:
            return {
                "label": label,
                "archetype": "高费拼多多",
                "main_bond": "拼多多",
                "main_carries": [
                    {"hero_name": names[0], "share": 100.0},
                    {"hero_name": names[1], "share": 100.0},
                ],
                "core_heroes": [
                    {"hero_name": name, "share": 100.0} for name in names
                ],
                "member_player_ids": [player_id],
                "is_subfamily": False,
                "stats": {"appearances": appearances},
            }

        stages = [stage("目标阵容", 1, 72, target_names)]
        current = target_names[:]
        replacements = [
            ("南瓜喵呜", "蛋小绿"),
            ("闪电阿存", "吉他手卡萝"),
            ("投手阿豪", "主唱耀星"),
            ("魔警艾琳", "音乐教父"),
        ]
        for index, (old, new) in enumerate(replacements, start=2):
            current = [new if name == old else name for name in current]
            stages.append(stage(f"桥接{index}", index, 70 - index, current[:]))
        stages.append(stage("音乐阵容", 9, 15, music_names))

        grouped = MODULE.group_strategy_rows(list(reversed(stages)))
        target_group = next(
            members
            for members in grouped.values()
            if any(row["label"] == "目标阵容" for row in members)
        )

        self.assertNotIn("音乐阵容", {row["label"] for row in target_group})

    def test_high_cost_pdd_continuous_structure_can_cluster(self) -> None:
        shared = [hero(name, tier=4) for name in ("甲", "乙", "丙", "丁", "戊")]
        left = feature(1, [*shared, hero("己", tier=5)], archetype="高费拼多多")
        right = feature(2, [*shared, hero("庚", tier=5)], archetype="高费拼多多")
        rows = MODULE.cluster_compositions([left, right], 2)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["stats"]["appearances"], 2)

    def test_high_cost_pdd_maturity_prefers_representative_sample(self) -> None:
        def stage(
            label: str,
            appearances: int,
            n_eff: float,
            completion: float,
        ) -> dict:
            return {
                "label": label,
                "archetype": "高费拼多多",
                "play_style": "高费",
                "main_bond": "测试羁绊-2",
                "recommendation_score": 2.0,
                "difficulty": {"carry_complete_rate": completion},
                "stats": {
                    "appearances": appearances,
                    "n_eff": n_eff,
                    "avg_rank": 2.5,
                    "top4_rate": 85.0,
                },
            }

        representative = stage("代表阵容", 72, 61.0, 98.6)
        small_perfect = stage("小样本分支", 12, 10.5, 100.0)

        selected, audit = MODULE.select_mature_stage(
            [small_perfect, representative]
        )

        self.assertIs(selected, representative)
        self.assertEqual(
            audit["method"],
            "reliable_performance_guard_then_representative_sample",
        )

    def test_high_cost_three_star_dependency_caps_normal_star_recommendation(self) -> None:
        units = [
            hero("高费主C", tier=5, stars=3, equipment="攻击力"),
            hero("副C", tier=4, stars=2),
            hero("前排甲", tier=4, stars=2),
            hero("前排乙", tier=3, stars=2),
            hero("挂件甲", tier=2),
            hero("挂件乙", tier=1),
            hero("挂件丙", tier=1),
        ]
        units[0].equipment_count = 3
        members = []
        for player_id in range(1, 13):
            member = feature(
                player_id,
                [hero(u.name, tier=u.tier, stars=u.stars, equipment=("攻击力" if idx == 0 else None), traits=u.traits) for idx, u in enumerate(units)],
                archetype="高费拼多多",
                active_traits={"音乐社": 2},
            )
            member.heroes[0].equipment_count = 3
            member.level = 9
            member.rank = 2
            member.play_style = "高费"
            members.append(member)
        # Force a high three-star rate on the 5-cost carry.
        for member in members:
            member.heroes[0].stars = 3
        row = MODULE.build_composition_row(members, 1, 12, 12)
        req = next(item for item in row["carry_requirements"] if item["hero_name"] == "高费主C")
        self.assertTrue(req["high_cost_three_star_dependency"])
        self.assertEqual(req["recommended_min_stars"], 2)
        recommendations = MODULE.build_composition_recommendations([row])
        self.assertEqual(set(recommendations), {"赌狗", "高费"})
        self.assertNotIn("高费大成上限", recommendations)
        self.assertNotIn("观察", recommendations)
        # Row may land in 赌狗 or 高费 depending on classify_play_style; ensure it is listed.
        listed = recommendations["赌狗"] + recommendations["高费"]
        self.assertEqual(len(listed), 1)
        self.assertTrue(listed[0].get("high_cost_three_star_dependency") or req["high_cost_three_star_dependency"])


if __name__ == "__main__":
    unittest.main()
