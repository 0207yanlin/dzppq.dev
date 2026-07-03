# -*- coding: utf-8 -*-
"""Tests for card icon disambiguation rules."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.card_rules import normalize_card_label, resolve_card_label  # noqa: E402


def test_normalize_aliases() -> None:
    assert normalize_card_label("装备共鸣法pro") == "装备共鸣pro"
    assert normalize_card_label("装备共鸣血") == "装备共鸣"
    assert normalize_card_label("巫术") == "大力巫术守护"
    assert normalize_card_label("福袋·蓝") == "福袋有钱"
    assert normalize_card_label("有钱同享") == "福袋有钱"


def test_quality_weight_pro_with_three_stars() -> None:
    heroes = [{"stars": 3}, {"stars": 3}, {"stars": 3}]
    assert (
        resolve_card_label("重质拍档支援", 0, heroes) == "蓝·重质也重量pro"
    )
    assert (
        resolve_card_label("最强支援", 2, heroes) == "蓝·重质也重量pro"
    )
    assert (
        resolve_card_label("蓝·重质拍档支援", 0, heroes) == "蓝·重质也重量pro"
    )


def test_quality_weight_pro_without_three_stars() -> None:
    heroes = [{"stars": 2}, {"stars": 2}]
    assert resolve_card_label("重质拍档支援", 0, heroes) == "蓝·拍档支援"
    assert resolve_card_label("最佳拍档", 0, heroes) == "蓝·拍档支援"
    assert resolve_card_label("蓝·重质拍档支援", 0, heroes) == "蓝·拍档支援"


def test_fast_xxb_second_slot() -> None:
    heroes = [{"stars": 1}]
    assert resolve_card_label("快速成型", 1, heroes) == "吸吸宝pro"
    assert resolve_card_label("快速成型", 0, heroes) == "快速成型"


def test_fast_xxb_merged_yellow_template() -> None:
    heroes = [{"stars": 1}]
    merged = "黄·吸吸宝pro快速成型"
    assert resolve_card_label(merged, 1, heroes) == "吸吸宝pro"
    assert resolve_card_label(merged, 0, heroes) == "快速成型"
    assert resolve_card_label(merged, 2, heroes) == "快速成型"
    assert normalize_card_label(merged) == "黄·快速成型"


def main() -> None:
    test_normalize_aliases()
    test_quality_weight_pro_with_three_stars()
    test_quality_weight_pro_without_three_stars()
    test_fast_xxb_second_slot()
    test_fast_xxb_merged_yellow_template()
    print("card_rules tests passed")


if __name__ == "__main__":
    main()
