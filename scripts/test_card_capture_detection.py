# -*- coding: utf-8 -*-
"""Regression tests for summary-card capture detection."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.detect_cards import (  # noqa: E402
    CARD_PRESENCE_PARAMS,
    CardDetectionResult,
    build_match_details,
    classify_card_presence,
    match_card_roi_detailed,
    should_confirm_card_detail,
)


def _detail(
    name: str,
    combined: float,
    *,
    shape: float = 0.8,
    color: float = 0.8,
    chroma: float = 0.8,
) -> dict:
    return {
        "name": name,
        "combined": combined,
        "shape": shape,
        "color": color,
        "chroma": chroma,
    }


class SingleScanTests(unittest.TestCase):
    def test_build_details_scores_each_template_once(self) -> None:
        roi = np.zeros((12, 12, 3), dtype=np.uint8)
        fg = np.zeros((12, 12), dtype=bool)
        sig = {"icon": roi, "fg": fg, "shape_gray": None, "chroma": None}
        templates = {"raw-a.jpg": sig, "raw-b.jpg": sig}

        with (
            patch("src.detect_cards.shape_score", return_value=0.8) as shape,
            patch("src.detect_cards.color_score", return_value=0.7) as color,
            patch("src.detect_cards.chroma_score", return_value=0.6) as chroma,
            patch(
                "src.detect_cards.combined_score",
                side_effect=AssertionError("must not rescan"),
            ),
        ):
            details = build_match_details(
                roi, fg, templates, padding=4, margin_ratio=0.12
            )

        self.assertEqual(len(details), 2)
        self.assertEqual(shape.call_count, 2)
        self.assertEqual(color.call_count, 2)
        self.assertEqual(chroma.call_count, 2)

    def test_detailed_match_ranks_independent_of_template_insertion_order(self) -> None:
        roi = np.zeros((12, 12, 3), dtype=np.uint8)
        fg = np.zeros((12, 12), dtype=bool)
        low = _detail("inserted_first.jpg", 0.20)
        high = _detail("actual_best.jpg", 0.91)

        for details in ([low, high], [high, low]):
            with self.subTest(order=[item["name"] for item in details]):
                with (
                    patch("src.detect_cards.prepare_card_icon", return_value=(roi, fg)),
                    patch(
                        "src.detect_cards.build_match_details",
                        return_value=details,
                    ) as scan,
                ):
                    result = match_card_roi_detailed(roi, {"unused": {}})

                scan.assert_called_once()
                self.assertEqual(
                    result.debug["top1_raw_asset_stem"], "actual_best"
                )
                self.assertEqual(
                    result.debug["top_candidates"][0]["raw_asset_stem"],
                    "actual_best",
                )
                self.assertEqual(
                    result.debug["presence_signals"]["best_template_score"],
                    0.91,
                )
                self.assertEqual(result.presence, "occupied")
                self.assertEqual(result.candidates[0]["name"], "actual_best.jpg")


class PresenceTests(unittest.TestCase):
    def test_flat_unmatched_slot_is_empty(self) -> None:
        roi = np.full((45, 45, 3), 25, dtype=np.uint8)
        fg = np.zeros((45, 45), dtype=bool)
        presence, signals = classify_card_presence(roi, fg, 0.1)
        self.assertEqual(presence, "empty")
        self.assertLessEqual(
            signals["gray_variance"],
            CARD_PRESENCE_PARAMS["empty_max_gray_variance"],
        )

    def test_strong_template_score_is_occupied(self) -> None:
        roi = np.full((45, 45, 3), 25, dtype=np.uint8)
        fg = np.zeros((45, 45), dtype=bool)
        presence, _ = classify_card_presence(roi, fg, 0.9)
        self.assertEqual(presence, "occupied")

    def test_middle_signal_band_is_uncertain(self) -> None:
        roi = np.full((45, 45, 3), 25, dtype=np.uint8)
        fg = np.zeros((45, 45), dtype=bool)
        presence, _ = classify_card_presence(roi, fg, 0.5)
        self.assertEqual(presence, "uncertain")


class DetailTriggerTests(unittest.TestCase):
    def test_presence_confidence_and_workbook_mapping_matrix(self) -> None:
        for presence in ("empty", "uncertain", "occupied"):
            for score, reason in ((0.99, "accepted"), (0.40, "below_threshold")):
                for mapped in (False, True):
                    with self.subTest(
                        presence=presence, score=score, mapped=mapped
                    ):
                        result = CardDetectionResult(
                            label=(
                                "白·法力专注"
                                if reason == "accepted"
                                else "unknown"
                            ),
                            score=score,
                            presence=presence,
                            debug={
                                "top1_raw_asset_stem": "workbook_asset",
                                "gap": 0.01,
                                "gap_threshold": 0.08,
                                "reject_reason": reason,
                            },
                        )
                        workbook_stems = (
                            {"workbook_asset.jpg"} if mapped else {"other_asset"}
                        )
                        expected = presence == "occupied" and mapped
                        self.assertEqual(
                            should_confirm_card_detail(result, workbook_stems),
                            expected,
                        )

    def test_known_visual_group_does_not_trigger_without_workbook_mapping(self) -> None:
        result = CardDetectionResult(
            label="unknown",
            score=0.79,
            presence="occupied",
            debug={
                "top1_raw_asset_stem": "raw_a",
                "gap": 0.01,
                "gap_threshold": 0.08,
                "reject_reason": "below_min_gap",
                "top_candidates": [
                    {"raw_asset_stem": "raw_a", "label": "蓝·攻防联合"},
                    {"raw_asset_stem": "raw_b", "label": "蓝·友谊连接"},
                ],
            },
        )
        self.assertFalse(should_confirm_card_detail(result, set()))


if __name__ == "__main__":
    unittest.main()
