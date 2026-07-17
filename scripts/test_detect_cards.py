# -*- coding: utf-8 -*-
"""Unit tests for card match disambiguation rules."""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.detect_cards import (  # noqa: E402
    DETECTION_PARAMS,
    VISUAL_CARD_GROUPS,
    build_match_details,
    detect_cards,
    diagnose_card_match,
    normalize_template_label,
    select_card_match,
)
from src.card_rules import resolve_card_label  # noqa: E402
from src.layout import NUM_CARDS, NUM_PLAYERS  # noqa: E402

THRESHOLD = float(DETECTION_PARAMS["threshold"])
MIN_GAP = float(DETECTION_PARAMS["min_gap"])
CANDIDATES_JSON = ROOT / "data" / "template_candidates" / "candidates.json"


def _detail(
    name: str,
    *,
    combined: float,
    shape: float,
    color: float,
    chroma: float = 0.75,
) -> dict:
    return {
        "name": name,
        "combined": combined,
        "shape": shape,
        "color": color,
        "chroma": chroma,
    }


class SelectCardMatchTests(unittest.TestCase):
    def _select(self, details: list[dict]):
        return select_card_match(details, threshold=THRESHOLD, min_gap=MIN_GAP)

    def test_shape_family_color_rescue_card_bag(self) -> None:
        details = [
            _detail("白·最后的波纹.jpg", combined=0.82, shape=0.856, color=0.75),
            _detail("彩·卡牌宝袋.jpg", combined=0.76, shape=0.749, color=0.986),
            _detail("蓝·波纹利己.jpg", combined=0.78, shape=0.80, color=0.70),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "彩·卡牌宝袋")
        self.assertEqual(decision.debug["match_path"], "shape_family_color_rescue")
        self.assertEqual(decision.debug["reject_reason"], "accepted")

    def test_known_family_low_gap_accept_white_wave(self) -> None:
        details = [
            _detail("白·最后的波纹.jpg", combined=0.89, shape=0.85, color=0.95),
            _detail("彩·卡牌宝袋.jpg", combined=0.78, shape=0.75, color=0.98),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "白·最后的波纹")
        self.assertEqual(decision.debug["match_path"], "known_family_low_gap_accept")

    def test_clone_technology_color_rescue(self) -> None:
        details = [
            _detail("蓝·克隆技术.jpg", combined=0.80, shape=0.90, color=0.775),
            _detail("白·克隆技术.jpg", combined=0.76, shape=0.85, color=0.983),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "白·克隆技术")
        self.assertEqual(decision.debug["reject_reason"], "accepted")
        self.assertIn(
            decision.debug["match_path"],
            {"shape_family_color_rescue", "shape_cluster_color"},
        )

    def test_super_pack_allows_lower_shape(self) -> None:
        details = [
            _detail("蓝·延时礼物.jpg", combined=0.82, shape=0.995, color=0.70),
            _detail("黄·超级卡包.jpg", combined=0.78, shape=0.70, color=0.989),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "黄·超级卡包")
        self.assertEqual(decision.debug["match_path"], "shape_family_color_rescue")

    def test_monopoly_color_rescue(self) -> None:
        details = [
            _detail("蓝·带不走.jpg", combined=0.81, shape=0.88, color=0.72),
            _detail("彩·大富翁.jpg", combined=0.77, shape=0.76, color=0.998),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "彩·大富翁")
        self.assertEqual(decision.debug["match_path"], "shape_family_color_rescue")

    def test_merged_full_level_player_alias(self) -> None:
        self.assertEqual(
            normalize_template_label("蓝·半步满级.jpg"),
            "蓝·半步满级+满级玩家",
        )
        self.assertEqual(
            normalize_template_label("蓝·满级玩家.jpg"),
            "蓝·半步满级+满级玩家",
        )
        details = [
            _detail("白·满级玩家.jpg", combined=0.80, shape=0.86, color=0.72),
            _detail("蓝·半步满级.jpg", combined=0.78, shape=0.84, color=0.95),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "蓝·半步满级+满级玩家")
        self.assertEqual(decision.debug["reject_reason"], "accepted")
        self.assertIn(
            decision.debug["match_path"],
            {"shape_family_color_rescue", "shape_cluster_color"},
        )

    def test_matthew_max_color_rescue(self) -> None:
        details = [
            _detail("白·马太效应.jpg", combined=0.84, shape=0.90, color=0.962),
            _detail("彩·马太效应max.jpg", combined=0.79, shape=0.78, color=0.995),
            _detail("黄·马太效应pro.jpg", combined=0.83, shape=0.95, color=0.98),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "彩·马太效应max")
        self.assertEqual(decision.debug["match_path"], "shape_family_color_rescue")

    def test_wo_lai_zhu_ni_pro_color_rescue(self) -> None:
        details = [
            _detail("黄·我来助你pro.jpg", combined=0.87, shape=0.84, color=0.96),
            _detail("白·我来助你.jpg", combined=0.84, shape=0.86, color=0.78),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "黄·我来助你pro")
        self.assertEqual(decision.debug["reject_reason"], "accepted")
        self.assertIn(
            decision.debug["match_path"],
            {"shape_family_color_rescue", "shape_cluster_color"},
        )

    def test_heat_conduction_near_threshold(self) -> None:
        details = [
            _detail("黄·热传导.jpg", combined=0.737, shape=0.80, color=0.969),
            _detail("彩·热传导pro.jpg", combined=0.72, shape=0.82, color=0.75),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "黄·热传导")
        self.assertEqual(decision.debug["reject_reason"], "accepted")
        self.assertIn(
            decision.debug["match_path"],
            {"shape_family_color_rescue", "shape_cluster_color"},
        )

    def test_jin_shang_tian_hua_low_gap_accept(self) -> None:
        details = [
            _detail("彩·锦上添花pro.jpg", combined=0.88, shape=0.84, color=0.95),
            _detail("蓝·刷宝专家.jpg", combined=0.862, shape=0.839, color=0.70),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "彩·锦上添花pro")
        self.assertEqual(decision.debug["reject_reason"], "accepted")
        self.assertIn(
            decision.debug["match_path"],
            {
                "known_family_low_gap_accept",
                "shape_family_color_rescue",
                "shape_cluster_color",
                "combined",
            },
        )

    def test_attack_defense_narrow_low_gap(self) -> None:
        details = [
            _detail("蓝·攻防联合.jpg", combined=0.89, shape=0.85, color=0.80),
            _detail("蓝·友谊连接.jpg", combined=0.875, shape=0.83, color=0.81),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "蓝·攻防联合")
        self.assertEqual(decision.debug["match_path"], "known_family_low_gap_accept")

    def test_egg_transform_color_rescue(self) -> None:
        details = [
            _detail("白·蛋仔变变变.jpg", combined=0.82, shape=0.90, color=0.74),
            _detail("彩·装备变变变.jpg", combined=0.78, shape=0.79, color=0.986),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "彩·装备变变变")
        self.assertEqual(decision.debug["match_path"], "shape_family_color_rescue")
        self.assertEqual(decision.debug["reject_reason"], "accepted")

    def test_egg_transform_keeps_white_when_color_clear(self) -> None:
        details = [
            _detail("白·蛋仔变变变.jpg", combined=0.88, shape=0.91, color=0.97),
            _detail("彩·装备变变变.jpg", combined=0.79, shape=0.78, color=0.80),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "白·蛋仔变变变")
        self.assertEqual(decision.debug["reject_reason"], "accepted")
        self.assertIn(
            decision.debug["match_path"],
            {"shape_family_color_rescue", "shape_cluster_color", "combined"},
        )

    def test_fighter_color_rescue(self) -> None:
        details = [
            _detail("蓝·打手.jpg", combined=0.81, shape=0.89, color=0.73),
            _detail("白·打手.jpg", combined=0.77, shape=0.79, color=0.982),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "白·打手")
        self.assertEqual(decision.debug["match_path"], "shape_family_color_rescue")
        self.assertEqual(decision.debug["reject_reason"], "accepted")

    def test_fighter_keeps_blue_when_color_clear(self) -> None:
        details = [
            _detail("蓝·打手.jpg", combined=0.87, shape=0.90, color=0.96),
            _detail("白·打手.jpg", combined=0.78, shape=0.78, color=0.79),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "蓝·打手")
        self.assertEqual(decision.debug["reject_reason"], "accepted")
        self.assertIn(
            decision.debug["match_path"],
            {"shape_family_color_rescue", "shape_cluster_color", "combined"},
        )

    def test_unrelated_card_stays_on_combined_path(self) -> None:
        details = [
            _detail("白·法力专注.jpg", combined=0.90, shape=0.88, color=0.90),
            _detail("黄·下雨了.jpg", combined=0.70, shape=0.70, color=0.70),
        ]
        decision = self._select(details)
        self.assertEqual(decision.label, "白·法力专注")
        self.assertEqual(decision.debug["match_path"], "combined")

    def test_color_rescue_rejects_insufficient_color_gap(self) -> None:
        details = [
            _detail("白·克隆技术.jpg", combined=0.80, shape=0.85, color=0.90),
            _detail("蓝·克隆技术.jpg", combined=0.79, shape=0.88, color=0.88),
        ]
        decision = self._select(details)
        self.assertIsNone(decision.label)
        self.assertEqual(decision.debug["match_path"], "combined")
        self.assertEqual(decision.debug["reject_reason"], "below_min_gap")

    def test_below_threshold_without_family_rule(self) -> None:
        details = [
            _detail("白·法力专注.jpg", combined=0.70, shape=0.80, color=0.80),
            _detail("黄·下雨了.jpg", combined=0.60, shape=0.70, color=0.70),
        ]
        decision = self._select(details)
        self.assertIsNone(decision.label)
        self.assertEqual(decision.debug["reject_reason"], "below_threshold")


class CardContextRuleTests(unittest.TestCase):
    def test_sss_defaults_to_normal_without_equipment_context(self) -> None:
        for label in (
            "一起刷刷刷",
            "蓝·天降啾啾pro",
            "蓝·一起刷刷刷+天降啾啾pro",
        ):
            self.assertEqual(
                resolve_card_label(label, 0, []),
                "蓝·一起刷刷刷",
            )
        self.assertEqual(
            resolve_card_label("蓝·一起刷刷刷+天降啾啾pro", 0, None),
            "蓝·一起刷刷刷",
        )
        self.assertEqual(
            resolve_card_label("蓝·一起刷刷刷+天降啾啾pro", 0),
            "蓝·一起刷刷刷",
        )

    def test_sss_uses_equipment_instance_count(self) -> None:
        cases = [
            ([], "蓝·一起刷刷刷"),
            ([{"equipments": ["火焰啾啾"]}], "蓝·一起刷刷刷"),
            (
                [{"equipments": ["火焰啾啾", "寒冰啾啾"]}],
                "蓝·天降啾啾pro",
            ),
            (
                [
                    {"equipments": ["核选火焰啾啾"]},
                    {"equipments": ["寒冰啾啾", "普通装备"]},
                ],
                "蓝·天降啾啾pro",
            ),
        ]
        for heroes, expected in cases:
            with self.subTest(heroes=heroes):
                self.assertEqual(
                    resolve_card_label("蓝·一起刷刷刷+天降啾啾pro", 0, heroes),
                    expected,
                )


class DetectCardsRoiTests(unittest.TestCase):
    def test_detect_cards_returns_three_slots_per_player(self) -> None:
        import numpy as np

        img = np.zeros((1600, 2160, 3), dtype=np.uint8)
        rows = detect_cards(img, template_sigs={})
        self.assertEqual(len(rows), NUM_PLAYERS)
        for row in rows:
            self.assertEqual(len(row["cards"]), NUM_CARDS)
            for card in row["cards"]:
                self.assertIn("slot_index", card)
                self.assertIn("label", card)
                self.assertIn("score", card)


class CandidateRecomputeTests(unittest.TestCase):
    @unittest.skipUnless(CANDIDATES_JSON.exists(), "candidates.json not present")
    def test_recompute_confusing_groups_from_candidates(self) -> None:
        data = json.loads(CANDIDATES_JSON.read_text(encoding="utf-8"))
        group_labels = {label for group in VISUAL_CARD_GROUPS for label in group["labels"]}
        interesting: list[dict] = []

        for item in data.get("candidates", []):
            if item.get("kind") != "card":
                continue
            debug = item.get("match_debug") or {}
            top1 = debug.get("top1_label")
            top2 = debug.get("top2_label")
            if top1 not in group_labels and top2 not in group_labels:
                continue
            reason = debug.get("reject_reason")
            if reason not in {"below_min_gap", "below_threshold", "accepted"}:
                continue
            if top1 in group_labels and top2 in group_labels:
                interesting.append(item)

        self.assertTrue(interesting, "expected confusing-group card candidates in candidates.json")

        from src.detect_cards import load_template_sigs

        sigs = load_template_sigs()
        if not sigs:
            self.skipTest("card templates not available")

        improved = 0
        for item in interesting:
            crop_path = ROOT / item["crop_path"]
            if not crop_path.exists():
                continue
            import cv2

            crop = cv2.imread(str(crop_path))
            if crop is None:
                continue
            old_reason = (item.get("match_debug") or {}).get("reject_reason")
            new_debug = diagnose_card_match(crop, sigs)
            if old_reason in {"below_min_gap", "below_threshold"} and new_debug.get(
                "reject_reason"
            ) == "accepted":
                improved += 1

        self.assertGreater(
            improved,
            0,
            "expected at least one previously rejected confusing-group sample to be accepted",
        )


if __name__ == "__main__":
    unittest.main()
