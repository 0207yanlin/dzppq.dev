# -*- coding: utf-8 -*-
"""Unit tests for card pick recommendation helpers and runtime paths."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.card_pick_recommend import (  # noqa: E402
    CardMatchResult,
    CardStatsIndex,
    build_recommendation,
    build_sort_dimensions,
    clean_ocr_text,
    display_card_name,
    fuzzy_match_card,
    format_recommendation,
)
from src.card_stats_db import build_card_stats_payload  # noqa: E402
from src.layout import HAND_CARD_BOXES, hand_card_roi  # noqa: E402
from src.match_db import import_ground_truth, init_match_db, insert_match_entry  # noqa: E402
from src.runtime_paths import (  # noqa: E402
    MATCH_DB_NAME,
    app_base_dir,
    resolve_match_db,
    resolve_meta_json,
    runtime_build_label,
)


def _sample_stats() -> CardStatsIndex:
    payload = {
        "generated_at": "2026-07-04T17:16:44+00:00",
        "data_source": "data/matches_test.db",
        "overview": {"quality": {"matches": 100, "cards": 1000}},
        "rankings": {
            "cards": {
                "single_cards_by_prefix": {
                    "白": [
                        {
                            "key": "白·克隆技术",
                            "appearances": 88,
                            "adjusted_avg_rank": 3.23,
                            "avg_rank": 3.18,
                            "top4_rate": 64.8,
                            "avg_appearances_per_match": 0.2,
                        },
                        {
                            "key": "白·摇盒",
                            "appearances": 142,
                            "adjusted_avg_rank": 4.17,
                            "avg_rank": 4.19,
                            "top4_rate": 52.1,
                            "avg_appearances_per_match": 0.33,
                        },
                    ],
                    "蓝": [
                        {
                            "key": "蓝·克隆技术",
                            "appearances": 173,
                            "adjusted_avg_rank": 3.94,
                            "avg_rank": 3.95,
                            "top4_rate": 60.7,
                            "avg_appearances_per_match": 0.4,
                        },
                        {
                            "key": "蓝·福袋",
                            "appearances": 196,
                            "adjusted_avg_rank": 3.67,
                            "avg_rank": 3.67,
                            "top4_rate": 63.3,
                            "avg_appearances_per_match": 0.46,
                        },
                    ],
                },
                "blue_cards_team_rank_by_prefix": {
                    "蓝": [
                        {
                            "key": "蓝·克隆技术",
                            "avg_rank": 2.18,
                            "team_top2_rate": 61.8,
                            "appearances": 173,
                        },
                        {
                            "key": "蓝·福袋",
                            "avg_rank": 2.12,
                            "team_top2_rate": 61.7,
                            "appearances": 196,
                        },
                    ]
                },
            }
        },
    }
    return CardStatsIndex(payload)


def _concrete_card_stats() -> CardStatsIndex:
    """Stats with 0727+ concrete shared-icon card keys."""
    base_keys = {
        "蓝": [
            "蓝·重质也重量pro",
            "蓝·最佳拍档",
            "蓝·最强支援",
            "蓝·我们全都要",
            "蓝·一起刷刷刷",
            "蓝·天降揪揪pro",
            "蓝·我是老大",
            "蓝·快速成长",
            "蓝·专业打手",
            "蓝·冒险",
            "蓝·开攒",
            "蓝·大亨",
            "蓝·福袋",
            "蓝·有钱同享",
            "蓝·利己主义",
            "蓝·最后的波纹",
        ],
        "黄": [
            "黄·装备共鸣法",
            "黄·装备共鸣攻",
            "黄·装备共鸣血",
            "黄·大力",
            "黄·巫术",
            "黄·守护",
        ],
        "彩": [
            "彩·装备共鸣法pro",
            "彩·装备共鸣攻pro",
            "彩·装备共鸣血pro",
            "彩·法师礼包",
            "彩·坦克礼包",
            "彩·射手礼包",
        ],
        "白": ["白·最后的波纹"],
    }
    single_by_prefix: dict[str, list[dict]] = {}
    for prefix, keys in base_keys.items():
        single_by_prefix[prefix] = [
            {
                "key": key,
                "appearances": 50,
                "adjusted_avg_rank": 3.5,
                "avg_rank": 3.5,
                "top4_rate": 55.0,
                "avg_appearances_per_match": 0.3,
            }
            for key in keys
        ]
    payload = {
        "generated_at": "2026-07-04T17:16:44+00:00",
        "data_source": "data/matches_test.db",
        "overview": {"quality": {"matches": 100, "cards": 1000}},
        "rankings": {
            "cards": {
                "single_cards_by_prefix": single_by_prefix,
                "blue_cards_team_rank_by_prefix": {"蓝": []},
            }
        },
    }
    return CardStatsIndex(payload)


def _make_test_match_entry(
    *,
    screenshot_name: str,
    path: str,
    players: list[dict[str, object]],
    pairs: list[list[int]] | None = None,
) -> dict[str, object]:
    return {
        "path": path,
        "captured_at": "2026-07-27T12:00:00",
        "pairs": pairs or [],
        "players": players,
    }


def _build_card_stats_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "match_latest.db"
    conn = init_match_db(db_path)
    try:
        insert_match_entry(
            conn,
            "match_a.png",
            _make_test_match_entry(
                screenshot_name="match_a.png",
                path="screenshots.0727/match_a.png",
                players=[
                    {
                        "rank": 1,
                        "row_index": 0,
                        "partner_player": 2,
                        "heroes": [
                            {
                                "slot_index": 0,
                                "hero_name": "测试英雄",
                                "stars": 3,
                                "equipment_count": "2",
                                "equipments": ["核选火焰啾啾", "寒冰啾啾"],
                            }
                        ],
                        "cards": [
                            {"slot_index": 0, "card_name": "白·克隆技术"},
                            {"slot_index": 1, "card_name": "蓝·福袋"},
                            {
                                "slot_index": 2,
                                "card_name": "蓝·天降揪揪pro",
                            },
                        ],
                    },
                    {
                        "rank": 2,
                        "row_index": 1,
                        "partner_player": 1,
                        "heroes": [
                            {
                                "slot_index": 0,
                                "hero_name": "测试英雄",
                                "stars": 2,
                                "equipment_count": "1",
                                "equipments": ["火焰啾啾"],
                            }
                        ],
                        "cards": [
                            {"slot_index": 0, "card_name": "蓝·克隆技术"},
                            {
                                "slot_index": 1,
                                "card_name": "蓝·一起刷刷刷",
                            },
                        ],
                    },
                    {
                        "rank": 3,
                        "row_index": 2,
                        "heroes": [],
                        "cards": [{"slot_index": 0, "card_name": "白·摇盒"}],
                    },
                    {
                        "rank": 4,
                        "row_index": 3,
                        "heroes": [],
                        "cards": [
                            {"slot_index": 0, "card_name": "白·克隆技术"},
                            {"slot_index": 1, "card_name": "白·摇盒"},
                        ],
                    },
                ],
            ),
        )
        insert_match_entry(
            conn,
            "match_b.png",
            _make_test_match_entry(
                screenshot_name="match_b.png",
                path="screenshots.0728/match_b.png",
                players=[
                    {
                        "rank": 1,
                        "row_index": 0,
                        "partner_player": 2,
                        "heroes": [],
                        "cards": [{"slot_index": 0, "card_name": "蓝·福袋"}],
                    },
                    {
                        "rank": 2,
                        "row_index": 1,
                        "partner_player": 1,
                        "heroes": [],
                        "cards": [{"slot_index": 0, "card_name": "蓝·克隆技术"}],
                    },
                    {
                        "rank": 5,
                        "row_index": 4,
                        "heroes": [],
                        "cards": [{"slot_index": 0, "card_name": "白·摇盒"}],
                    },
                    {
                        "rank": 6,
                        "row_index": 5,
                        "heroes": [],
                        "cards": [{"slot_index": 0, "card_name": "白·克隆技术"}],
                    },
                ],
            ),
        )
    finally:
        conn.close()
    return db_path


CONCRETE_CARD_MATCH_CASES: list[tuple[str, str, str]] = [
    ("蓝", "半步满级", "蓝·半步满级"),
    ("蓝", "蓝·半步满级", "蓝·半步满级"),
    ("蓝", "满级玩家", "蓝·满级玩家"),
    ("蓝", "蓝·满级玩家", "蓝·满级玩家"),
    ("蓝", "最佳拍档", "蓝·最佳拍档"),
    ("蓝", "蓝·最佳拍档", "蓝·最佳拍档"),
    ("蓝", "最强支援", "蓝·最强支援"),
    ("蓝", "蓝·最强支援", "蓝·最强支援"),
    ("蓝", "一起刷刷刷", "蓝·一起刷刷刷"),
    ("蓝", "蓝·一起刷刷刷", "蓝·一起刷刷刷"),
    ("蓝", "我们全都要", "蓝·我们全都要"),
    ("蓝", "蓝·我们全都要", "蓝·我们全都要"),
    ("蓝", "我是老大", "蓝·我是老大"),
    ("蓝", "快速成长", "蓝·快速成长"),
    ("蓝", "专业打手", "蓝·专业打手"),
    ("蓝", "冒险", "蓝·冒险"),
    ("蓝", "天降揪揪pro", "蓝·天降揪揪pro"),
    ("蓝", "蓝·天降揪揪pro", "蓝·天降揪揪pro"),
    ("蓝", "天降啾啾pro", "蓝·天降揪揪pro"),
    ("蓝", "蓝·天降啾啾pro", "蓝·天降揪揪pro"),
    ("蓝", "天降啾啾", "蓝·天降揪揪pro"),
    ("蓝", "开攒", "蓝·开攒"),
    ("蓝", "蓝·开攒", "蓝·开攒"),
    ("蓝", "开赞", "蓝·开攒"),
    ("蓝", "蓝·开赞", "蓝·开攒"),
    ("蓝", "大亨", "蓝·大亨"),
    ("蓝", "蓝·大亨", "蓝·大亨"),
    ("蓝", "福袋", "蓝·福袋"),
    ("蓝", "蓝·福袋", "蓝·福袋"),
    ("蓝", "有钱同享", "蓝·有钱同享"),
    ("蓝", "蓝·有钱同享", "蓝·有钱同享"),
    ("蓝", "利己主义", "蓝·利己主义"),
    ("蓝", "蓝·利己主义", "蓝·利己主义"),
    ("蓝", "蓝·最后的波纹", "蓝·最后的波纹"),
    ("黄", "装备共鸣法", "黄·装备共鸣法"),
    ("黄", "黄·装备共鸣法", "黄·装备共鸣法"),
    ("黄", "装备共鸣攻", "黄·装备共鸣攻"),
    ("黄", "装备共鸣血", "黄·装备共鸣血"),
    ("黄", "大力", "黄·大力"),
    ("黄", "巫术", "黄·巫术"),
    ("黄", "守护", "黄·守护"),
    ("彩", "装备共鸣法pro", "彩·装备共鸣法pro"),
    ("彩", "彩·装备共鸣法pro", "彩·装备共鸣法pro"),
    ("彩", "装备共鸣攻pro", "彩·装备共鸣攻pro"),
    ("彩", "装备共鸣血pro", "彩·装备共鸣血pro"),
    ("彩", "法师礼包", "彩·法师礼包"),
    ("彩", "射手礼包", "彩·射手礼包"),
    ("彩", "坦克礼包", "彩·坦克礼包"),
]


@pytest.mark.parametrize("prefix,ocr_text,expected_key", CONCRETE_CARD_MATCH_CASES)
def test_fuzzy_match_concrete_card_names(
    prefix: str, ocr_text: str, expected_key: str
) -> None:
    stats = _concrete_card_stats()
    catalog = stats.prefix_catalog(prefix)
    matched_key, score, _ = fuzzy_match_card(ocr_text, prefix, catalog)
    assert matched_key == expected_key, f"OCR={ocr_text!r} prefix={prefix}"
    assert score >= 0.98


def test_fuzzy_match_last_ripple_stays_white_when_prefix_white() -> None:
    stats = _concrete_card_stats()
    catalog = stats.prefix_catalog("白")
    matched_key, score, _ = fuzzy_match_card("最后的波纹", "白", catalog)
    assert matched_key == "白·最后的波纹"
    assert score >= 0.9


@pytest.mark.parametrize(
    "prefix,logical_name",
    [
        ("蓝", "蓝·半步满级"),
        ("蓝", "蓝·满级玩家"),
        ("蓝", "蓝·一起刷刷刷"),
        ("蓝", "蓝·天降揪揪pro"),
        ("蓝", "蓝·重质也重量pro"),
        ("蓝", "蓝·最佳拍档"),
        ("黄", "黄·快速成型"),
        ("黄", "黄·吸吸宝pro"),
        ("黄", "黄·巨神兵"),
        ("黄", "黄·迅迅迅捷双剑"),
        ("黄", "黄·死亡摇滚"),
        ("黄", "黄·摇盒高手"),
    ],
)
def test_logical_catalog_matches_zero_sample_cards(prefix: str, logical_name: str) -> None:
    stats = CardStatsIndex(
        {
            "overview": {"quality": {"matches": 0, "cards": 0}},
            "rankings": {
                "cards": {
                    "single_cards_by_prefix": {},
                    "blue_cards_team_rank_by_prefix": {},
                }
            },
        }
    )
    matched_key, score, _ = fuzzy_match_card(logical_name, prefix, stats.prefix_catalog(prefix))
    assert matched_key == logical_name
    assert score == 1.0
    metrics = stats.get_metrics(matched_key, prefix)
    assert metrics is not None
    assert not metrics.has_stats

    card = CardMatchResult(
        slot=0,
        raw_text=logical_name,
        cleaned_text=logical_name.split("·", 1)[1],
        matched_key=matched_key,
        match_score=score,
        metrics=metrics,
        metrics_lookup_key=stats.metrics_lookup_key(matched_key),
    )
    rendered = format_recommendation(build_recommendation(prefix, [card], stats))
    assert "暂无统计" in rendered
    assert "未匹配" not in rendered


@pytest.mark.parametrize("logical_name", ["黄·大力", "黄·巫术", "黄·守护"])
def test_shared_template_uses_concrete_stats(logical_name: str) -> None:
    stats = _concrete_card_stats()
    matched_key, score, _ = fuzzy_match_card(logical_name, "黄", stats.prefix_catalog("黄"))
    assert matched_key == logical_name
    lookup_key = stats.metrics_lookup_key(matched_key)
    assert lookup_key == logical_name
    metrics = stats.get_metrics(matched_key, "黄")
    assert metrics is not None
    assert metrics.key == logical_name

    card = CardMatchResult(
        slot=0,
        raw_text=logical_name,
        cleaned_text=logical_name.split("·", 1)[1],
        matched_key=matched_key,
        match_score=score,
        metrics=metrics,
        metrics_lookup_key=lookup_key,
    )
    rendered = format_recommendation(build_recommendation("黄", [card], stats))
    assert logical_name in rendered
    assert "共享统计" not in rendered


def test_fuzzy_match_last_ripple_blue_uses_concrete_name() -> None:
    stats = _concrete_card_stats()
    catalog = stats.prefix_catalog("蓝")
    matched_key, score, _ = fuzzy_match_card("最后的波纹", "蓝", catalog)
    assert matched_key == "蓝·最后的波纹"
    assert score == 1.0


def test_hand_card_roi_boxes() -> None:
    assert len(HAND_CARD_BOXES) == 3
    assert hand_card_roi(0) == (510, 490, 1050, 650)
    assert hand_card_roi(2) == (1590, 490, 2130, 650)


def test_load_stats_and_sample_weight() -> None:
    stats = _sample_stats()
    white_clone = stats.get_metrics("白·克隆技术", "白")
    assert white_clone is not None
    assert white_clone.appearances == 88
    assert white_clone.sample_weight_pct == 8.8
    assert white_clone.team_top2_rate is None


def test_blue_team_metrics_merge() -> None:
    stats = _sample_stats()
    blue_clone = stats.get_metrics("蓝·克隆技术", "蓝")
    assert blue_clone is not None
    assert blue_clone.team_avg_rank == 2.18
    assert blue_clone.team_top2_rate == 61.8


def test_fuzzy_match_same_name_different_prefix() -> None:
    stats = _sample_stats()
    white_catalog = stats.prefix_catalog("白")
    blue_catalog = stats.prefix_catalog("蓝")

    white_key, white_score, _ = fuzzy_match_card("克隆技术", "白", white_catalog)
    blue_key, blue_score, _ = fuzzy_match_card("克隆技术", "蓝", blue_catalog)

    assert white_key == "白·克隆技术"
    assert blue_key == "蓝·克隆技术"
    assert white_score >= 0.9
    assert blue_score >= 0.9


def test_clean_ocr_text_strips_prefix() -> None:
    assert clean_ocr_text("蓝·克隆技术") == "克隆技术"
    assert clean_ocr_text("  克隆 技术 ") == "克隆技术"


def test_recommendation_prefers_better_white_card() -> None:
    stats = _sample_stats()
    cards = [
        CardMatchResult(
            slot=0,
            raw_text="摇盒",
            cleaned_text="摇盒",
            matched_key="白·摇盒",
            match_score=1.0,
            metrics=stats.get_metrics("白·摇盒", "白"),
        ),
        CardMatchResult(
            slot=1,
            raw_text="克隆技术",
            cleaned_text="克隆技术",
            matched_key="白·克隆技术",
            match_score=1.0,
            metrics=stats.get_metrics("白·克隆技术", "白"),
        ),
        CardMatchResult(
            slot=2,
            raw_text="未知",
            cleaned_text="未知",
            matched_key=None,
            match_score=0.0,
            metrics=None,
        ),
    ]
    result = build_recommendation("白", cards, stats)
    assert result.recommended_slot == 1
    text = format_recommendation(result)
    assert "建议选取: 卡2 · 白·克隆技术" in text
    assert text.index("建议选取:") < text.index("对比速览")
    assert "对比速览" in text
    assert "多维排序" in text
    assert "克隆技术 > 摇盒" in text or "克隆技术 < 摇盒" in text
    assert "诊断信息" in text
    assert "样本比例权重" in text


def test_format_multi_sort_uses_card_names_not_slots() -> None:
    stats = _sample_stats()
    cards = [
        CardMatchResult(
            slot=0,
            raw_text="福袋",
            cleaned_text="福袋",
            matched_key="蓝·福袋",
            match_score=1.0,
            metrics=stats.get_metrics("蓝·福袋", "蓝"),
        ),
        CardMatchResult(
            slot=1,
            raw_text="克隆技术",
            cleaned_text="克隆技术",
            matched_key="蓝·克隆技术",
            match_score=1.0,
            metrics=stats.get_metrics("蓝·克隆技术", "蓝"),
        ),
        CardMatchResult(
            slot=2,
            raw_text="未知",
            cleaned_text="未知",
            matched_key=None,
            match_score=0.0,
            metrics=None,
        ),
    ]
    result = build_recommendation("蓝", cards, stats)
    text = format_recommendation(result)
    dims = build_sort_dimensions(result)
    assert dims
    sort_section = text.split("多维排序")[1].split("诊断信息")[0]
    assert "卡1" not in sort_section
    assert "卡2" not in sort_section
    assert "卡3" not in sort_section
    assert "福袋" in sort_section
    assert "克隆技术" in sort_section
    for dim in dims:
        for card in dim.cards:
            assert display_card_name(card) in sort_section


def test_compact_line_marks_low_confidence() -> None:
    stats = _sample_stats()
    cards = [
        CardMatchResult(
            slot=0,
            raw_text="一起刷刷刷一",
            cleaned_text="一起刷刷刷",
            matched_key="蓝·一起刷刷刷",
            match_score=0.62,
            metrics=stats.get_metrics("蓝·福袋", "蓝"),
        ),
        CardMatchResult(
            slot=1,
            raw_text="友谊连接",
            cleaned_text="友谊连接",
            matched_key="蓝·克隆技术",
            match_score=1.0,
            metrics=stats.get_metrics("蓝·克隆技术", "蓝"),
        ),
        CardMatchResult(
            slot=2,
            raw_text="虾饺",
            cleaned_text="虾饺",
            matched_key="蓝·福袋",
            match_score=1.0,
            metrics=stats.get_metrics("蓝·福袋", "蓝"),
        ),
    ]
    text = format_recommendation(build_recommendation("蓝", cards, stats))
    assert "低置信" in text.split("诊断信息")[0]


def test_recommendation_blue_uses_team_rank() -> None:
    stats = _sample_stats()
    cards = [
        CardMatchResult(
            slot=0,
            raw_text="福袋",
            cleaned_text="福袋",
            matched_key="蓝·福袋",
            match_score=1.0,
            metrics=stats.get_metrics("蓝·福袋", "蓝"),
        ),
        CardMatchResult(
            slot=1,
            raw_text="克隆技术",
            cleaned_text="克隆技术",
            matched_key="蓝·克隆技术",
            match_score=1.0,
            metrics=stats.get_metrics("蓝·克隆技术", "蓝"),
        ),
        CardMatchResult(
            slot=2,
            raw_text="未知",
            cleaned_text="未知",
            matched_key=None,
            match_score=0.0,
            metrics=None,
        ),
    ]
    result = build_recommendation("蓝", cards, stats)
    assert result.recommended_slot == 0
    rendered = format_recommendation(result)
    assert "对比速览" in rendered
    assert "福袋" in rendered
    assert "队伍前二率" in rendered
    assert "队伍平均名次" in rendered


def test_resolve_match_db_explicit_override(tmp_path: Path) -> None:
    db_path = _build_card_stats_db(tmp_path)
    assert resolve_match_db(db_path) == db_path.resolve()


def test_resolve_match_db_next_to_exe(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = _build_card_stats_db(data_dir)
    fake_exe = tmp_path / "DZPPQCardRecommender.exe"
    fake_exe.write_bytes(b"")

    with patch("src.runtime_paths.is_frozen", return_value=True), patch(
        "src.runtime_paths.sys.executable", str(fake_exe)
    ):
        resolved = resolve_match_db()
    assert resolved == db_path.resolve()


def test_card_stats_index_from_db_path(tmp_path: Path) -> None:
    db_path = _build_card_stats_db(tmp_path)
    stats = CardStatsIndex.from_db_path(db_path)
    assert stats.total_matches == 2
    assert stats.total_card_records > 0


    white_clone = stats.get_metrics("白·克隆技术", "白")
    white_shake = stats.get_metrics("白·摇盒", "白")
    assert white_clone is not None
    assert white_shake is not None
    assert white_clone.appearances >= 2
    assert white_clone.adjusted_avg_rank is not None
    assert white_clone.appearances < white_shake.appearances or (
        white_clone.adjusted_avg_rank <= white_shake.adjusted_avg_rank
    )

    blue_clone = stats.get_metrics("蓝·克隆技术", "蓝")
    blue_bag = stats.get_metrics("蓝·福袋", "蓝")
    assert blue_clone is not None
    assert blue_bag is not None
    assert blue_clone.team_avg_rank is not None
    assert blue_clone.team_top2_rate is not None

    normal_sss = stats.get_metrics("蓝·一起刷刷刷", "蓝")
    pro_sss = stats.get_metrics("蓝·天降揪揪pro", "蓝")
    assert normal_sss is not None
    assert pro_sss is not None
    assert normal_sss.appearances == 1
    assert pro_sss.appearances == 1
    assert stats.get_metrics("蓝·一起刷刷刷+天降揪揪pro", "蓝") is None


def test_detail_ocr_gt_survives_import_and_card_stats(tmp_path: Path) -> None:
    db_path = tmp_path / "authoritative.db"
    conn = init_match_db(db_path)
    gt_data = {
        "screenshots": {
            "detail.png": _make_test_match_entry(
                screenshot_name="detail.png",
                path="screenshots.0727/detail.png",
                players=[
                    {
                        "rank": 1,
                        "row_index": 0,
                        "heroes": [
                            {"slot_index": 0, "hero_name": "测试英雄", "stars": 3}
                            for _ in range(3)
                        ],
                        "cards": [
                            {
                                "slot_index": 0,
                                "card_name": "蓝·最佳拍档",
                                "source": "detail_ocr",
                            },
                            {
                                "slot_index": 1,
                                "card_name": "蓝·最强支援",
                            },
                        ],
                    }
                ],
            )
        }
    }
    import_ground_truth(conn, gt_data, dedupe_similar=False)
    conn.close()

    payload = build_card_stats_payload(db_path)
    rows = payload["rankings"]["cards"]["single_cards_by_prefix"]["蓝"]
    names = {row["key"] for row in rows}
    assert "蓝·最佳拍档" in names
    assert "蓝·最强支援" in names
    assert "蓝·重质也重量pro" not in names


@pytest.mark.parametrize(
    "actual_name",
    [
        "蓝·我们全都要",
        "蓝·一起刷刷刷",
        "蓝·我是老大",
        "蓝·快速成长",
        "蓝·专业打手",
        "蓝·冒险",
    ],
)
def test_exe_actual_blue_names_use_concrete_statistics(actual_name: str) -> None:
    stats = _concrete_card_stats()
    actual = stats.get_metrics(actual_name, "蓝")
    assert actual is not None
    assert actual.key == actual_name
    assert actual.appearances == 50


def _build_jsb_xj_stats_db(tmp_path: Path) -> Path:
    db_path = tmp_path / "jsb_xj.db"
    conn = init_match_db(db_path)
    try:
        insert_match_entry(
            conn,
            "jsb_match.png",
            _make_test_match_entry(
                screenshot_name="jsb_match.png",
                path="screenshots.0727/jsb_match.png",
                players=[
                    {
                        "rank": 1,
                        "row_index": 0,
                        "heroes": [
                            {
                                "slot_index": 0,
                                "hero_name": "测试英雄",
                                "stars": 2,
                                "equipment_count": "1",
                                "equipments": ["巨神兵之斧"],
                            }
                        ],
                        # Stored template labels intentionally swapped vs equipment.
                        "cards": [{"slot_index": 0, "card_name": "黄·迅迅迅捷双剑"}],
                    },
                    {
                        "rank": 2,
                        "row_index": 1,
                        "heroes": [
                            {
                                "slot_index": 0,
                                "hero_name": "测试英雄",
                                "stars": 2,
                                "equipment_count": "1",
                                "equipments": ["迅捷双剑"],
                            }
                        ],
                        "cards": [{"slot_index": 0, "card_name": "黄·巨神兵"}],
                    },
                    {
                        "rank": 3,
                        "row_index": 2,
                        "heroes": [
                            {
                                "slot_index": 0,
                                "hero_name": "测试英雄",
                                "stars": 2,
                                "equipment_count": "1",
                                "equipments": ["巨神兵之斧"],
                            }
                        ],
                        "cards": [{"slot_index": 0, "card_name": "黄·巨神兵"}],
                    },
                    {
                        "rank": 4,
                        "row_index": 3,
                        "heroes": [
                            {
                                "slot_index": 0,
                                "hero_name": "测试英雄",
                                "stars": 1,
                                "equipment_count": "2",
                                "equipments": ["巨神兵之斧", "迅捷双剑"],
                            }
                        ],
                        "cards": [{"slot_index": 0, "card_name": "黄·迅迅迅捷双剑"}],
                    },
                ],
            ),
        )
    finally:
        conn.close()
    return db_path


def test_card_stats_jsb_xj_preserves_concrete_labels(tmp_path: Path) -> None:
    db_path = _build_jsb_xj_stats_db(tmp_path)
    first = CardStatsIndex.from_db_path(db_path)
    second = CardStatsIndex.from_db_path(db_path)

    jsb = first.get_metrics("黄·巨神兵", "黄")
    xj = first.get_metrics("黄·迅迅迅捷双剑", "黄")
    assert jsb is not None
    assert xj is not None
    assert jsb.appearances + xj.appearances == 4
    assert jsb.appearances == 2
    assert xj.appearances == 2
    assert first.get_metrics("黄·巨神兵+迅迅迅捷双剑", "黄") is None

    jsb2 = second.get_metrics("黄·巨神兵", "黄")
    xj2 = second.get_metrics("黄·迅迅迅捷双剑", "黄")
    assert jsb2 is not None and xj2 is not None
    assert jsb.appearances == jsb2.appearances
    assert xj.appearances == xj2.appearances


def test_load_card_stats_prefers_db(tmp_path: Path) -> None:
    from scripts.card_pick_recommender import build_parser, load_card_stats

    db_path = _build_card_stats_db(tmp_path)
    args = build_parser().parse_args(["--db", str(db_path)])
    stats, data_path, mode = load_card_stats(args)
    assert mode == "db"
    assert data_path.resolve() == db_path.resolve()
    assert stats.get_metrics("白·克隆技术", "白") is not None


def test_load_card_stats_json_fallback(tmp_path: Path) -> None:
    from scripts.card_pick_recommender import build_parser, load_card_stats

    meta = tmp_path / "fallback.json"
    meta.write_text(
        '{"generated_at":"t","data_source":"x","overview":{"quality":{"matches":1,"cards":10}},'
        '"rankings":{"cards":{"single_cards_by_prefix":{"白":[]},'
        '"blue_cards_team_rank_by_prefix":{"蓝":[]}}}}',
        encoding="utf-8",
    )
    args = build_parser().parse_args(["--data-json", str(meta)])
    with patch("scripts.card_pick_recommender.resolve_match_db", side_effect=FileNotFoundError("no db")):
        stats, data_path, mode = load_card_stats(args)
    assert mode == "json"
    assert data_path.resolve() == meta.resolve()
    assert stats.total_card_records == 10


def test_ocr_helper_warmup_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.adb_capture import OcrHelper, WarmupResult

    calls = {"count": 0}

    class FakeOcrHelper(OcrHelper):
        def _run_warmup_once(self) -> WarmupResult:
            calls["count"] += 1
            self._backend = "rapidocr"
            return WarmupResult(backend="rapidocr", elapsed_ms=12.5, success=True)

    helper = FakeOcrHelper(use_cls=False)
    helper.start_warmup_async()
    result = helper.ensure_ready(timeout=5.0)
    assert result.success is True
    assert result.backend == "rapidocr"
    assert calls["count"] == 1

    second = helper.warmup(blocking=True, timeout=1.0)
    assert second is not None
    assert second.success is True
    assert calls["count"] == 1


def test_ocr_helper_reuses_worker_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.adb_capture import OcrHelper

    init_count = {"count": 0}

    class FakeEngine:
        def __call__(self, patch: object, **kwargs: object) -> tuple[list, None]:
            return [], None

    def fake_create(self: OcrHelper) -> FakeEngine:
        init_count["count"] += 1
        self._backend = "rapidocr"
        return FakeEngine()

    monkeypatch.setattr(OcrHelper, "_create_engine", fake_create)

    helper = OcrHelper(use_cls=False)
    helper.start_warmup_async()
    helper.ensure_ready(timeout=5.0)
    assert init_count["count"] == 1

    def ocr_job() -> str:
        import numpy as np

        dummy = np.zeros((32, 128, 3), dtype=np.uint8)
        return helper.ocr_text(dummy)

    helper.run_on_ocr_thread(ocr_job)
    assert init_count["count"] == 1

    helper.run_on_ocr_thread(ocr_job)
    assert init_count["count"] == 1


def test_resolve_meta_json_from_project_root() -> None:
    path = resolve_meta_json()
    assert path.is_file()
    assert path.name == "latest_meta_analysis.json"


def test_resolve_meta_json_explicit_override(tmp_path: Path) -> None:
    custom = tmp_path / "custom_meta.json"
    custom.write_text('{"generated_at":"","data_source":"","overview":{"quality":{}},"rankings":{"cards":{}}}', encoding="utf-8")
    assert resolve_meta_json(custom) == custom.resolve()


def test_resolve_meta_json_next_to_exe(tmp_path: Path) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    meta = data_dir / "latest_meta_analysis.json"
    meta.write_text('{"generated_at":"","data_source":"","overview":{"quality":{}},"rankings":{"cards":{}}}', encoding="utf-8")
    fake_exe = tmp_path / "DZPPQCardRecommender.exe"
    fake_exe.write_bytes(b"")

    with patch("src.runtime_paths.is_frozen", return_value=True), patch(
        "src.runtime_paths.sys.executable", str(fake_exe)
    ):
        resolved = resolve_meta_json()
    assert resolved == meta.resolve()


def test_load_real_meta_json_if_present() -> None:
    path = ROOT / "data" / "latest_meta_analysis.json"
    if not path.exists():
        return
    stats = CardStatsIndex.from_json_path(path)
    assert stats.total_card_records > 0
    assert "白" in stats.by_prefix
    assert len(stats.prefix_catalog("白")) > 0


def test_build_release_validation_rejects_stale_or_merged_data(tmp_path: Path) -> None:
    import json

    import scripts.build_card_pick_recommender_exe as build_script

    source_dir = tmp_path / "source"
    output_dir = tmp_path / "dist"
    source_dir.mkdir()
    (output_dir / "data").mkdir(parents=True)
    (output_dir / "_internal" / "data").mkdir(parents=True)
    source_db = _build_card_stats_db(source_dir)
    source_json = source_dir / "latest_meta_analysis.json"

    rows = [
        {"key": key, "appearances": 0}
        for key in build_script.REQUIRED_CONCRETE_CARD_KEYS
    ]
    payload = {
        "rankings": {
            "cards": {
                "single_cards_by_prefix": {"蓝": rows, "黄": []},
                "blue_cards_team_rank_by_prefix": {"蓝": []},
            }
        }
    }
    source_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    for data_dir in (output_dir / "data", output_dir / "_internal" / "data"):
        (data_dir / source_db.name).write_bytes(source_db.read_bytes())
        (data_dir / source_json.name).write_bytes(source_json.read_bytes())

    with patch.object(build_script, "MATCH_DB", source_db), patch.object(
        build_script, "META_JSON", source_json
    ):
        build_script.validate_runtime_data(output_dir, check_bundled=True)

        (output_dir / "data" / source_db.name).write_bytes(b"stale-db")
        with pytest.raises(SystemExit, match="stale copied runtime data"):
            build_script.validate_runtime_data(output_dir, check_bundled=True)

        (output_dir / "data" / source_db.name).write_bytes(source_db.read_bytes())
        (output_dir / "_internal" / "data" / source_json.name).write_bytes(b"stale-json")
        with pytest.raises(SystemExit, match="stale copied runtime data"):
            build_script.validate_runtime_data(output_dir, check_bundled=True)

        (output_dir / "_internal" / "data" / source_json.name).write_bytes(
            source_json.read_bytes()
        )
        payload["rankings"]["cards"]["single_cards_by_prefix"]["蓝"].append(
            {"key": "蓝·一起刷刷刷+天降揪揪pro", "appearances": 1}
        )
        source_json.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        (output_dir / "data" / source_json.name).write_bytes(source_json.read_bytes())
        with pytest.raises(SystemExit, match="stale merged ranking keys"):
            build_script.validate_runtime_data(output_dir)


def test_runtime_build_label_source_mode() -> None:
    label = runtime_build_label(
        entry_script=ROOT / "scripts" / "card_pick_recommender.py",
    )
    assert label.startswith("运行: source | 脚本:")


def test_app_base_dir_points_to_project_root_in_source_mode() -> None:
    assert app_base_dir().resolve() == ROOT.resolve()


def test_adb_options_default_auto_connect() -> None:
    from scripts.card_pick_recommender import AdbRuntimeOptions, adb_options_from_args, build_parser

    args = build_parser().parse_args([])
    options = adb_options_from_args(args)
    assert options.auto_connect is True
    assert options.mumu_port == 16384

    args = build_parser().parse_args(["--no-auto-connect"])
    options = adb_options_from_args(args)
    assert options.auto_connect is False


def test_ensure_adb_session_auto_connects() -> None:
    from scripts.card_pick_recommender import AdbRuntimeOptions, ensure_adb_session

    calls: list[str] = []

    class FakeAdb:
        device_serial = None

        def connect(self, host: str = "127.0.0.1", port: int = 16384) -> str:
            self.device_serial = f"{host}:{port}"
            return self.device_serial

        def check_device(self, prefer: str | None = None) -> str:
            return self.device_serial or "127.0.0.1:16384"

        def validate_resolution(self) -> tuple:
            calls.append("validate")
            return ((1600, 2160), (2160, 1600))

        def validate_wm_size(self) -> tuple[int, int]:
            calls.append("validate_wm")
            return 1600, 2160

    fake = FakeAdb()
    options = AdbRuntimeOptions(auto_connect=True, skip_resolution_check=False)
    serial = ensure_adb_session(fake, options)  # type: ignore[arg-type]
    assert serial == "127.0.0.1:16384"
    assert "validate" not in calls
    assert "validate_wm" not in calls


def test_ensure_adb_session_skips_connect_when_disabled() -> None:
    from scripts.card_pick_recommender import AdbRuntimeOptions, ensure_adb_session

    calls: list[str] = []

    class FakeAdb:
        device_serial = "emulator-5554"

        def connect(self, host: str = "127.0.0.1", port: int = 16384) -> str:
            calls.append("connect")
            return f"{host}:{port}"

        def check_device(self, prefer: str | None = None) -> str:
            calls.append("check")
            return self.device_serial

        def validate_resolution(self) -> tuple:
            calls.append("validate")
            return ((1600, 2160), (2160, 1600))

        def validate_wm_size(self) -> tuple[int, int]:
            calls.append("validate_wm")
            return 1600, 2160

    fake = FakeAdb()
    options = AdbRuntimeOptions(auto_connect=False)
    serial = ensure_adb_session(fake, options)  # type: ignore[arg-type]
    assert serial == "emulator-5554"
    assert calls == ["check"]


def test_resolve_adb_bin_with_source_cli_priority(tmp_path: Path) -> None:
    from scripts.card_pick_recommender import resolve_adb_bin_with_source

    cli_adb = tmp_path / "cli_adb.exe"
    cli_adb.write_bytes(b"")
    saved_adb = tmp_path / "saved_adb.exe"
    saved_adb.write_bytes(b"")
    default_adb = tmp_path / "default_adb.exe"
    default_adb.write_bytes(b"")

    with patch("scripts.card_pick_recommender.get_saved_adb_bin", return_value=str(saved_adb)), patch(
        "scripts.card_pick_recommender.DEFAULT_ADB_BIN", str(default_adb)
    ):
        resolved, source = resolve_adb_bin_with_source(str(cli_adb))
    assert resolved == str(cli_adb.resolve())
    assert source == "命令行"


def test_resolve_adb_bin_with_source_saved_config(tmp_path: Path) -> None:
    from scripts.card_pick_recommender import resolve_adb_bin_with_source

    saved_adb = tmp_path / "saved_adb.exe"
    saved_adb.write_bytes(b"")
    default_adb = tmp_path / "default_adb.exe"
    default_adb.write_bytes(b"")

    with patch("scripts.card_pick_recommender.get_saved_adb_bin", return_value=str(saved_adb)), patch(
        "scripts.card_pick_recommender.DEFAULT_ADB_BIN", str(default_adb)
    ):
        resolved, source = resolve_adb_bin_with_source(None)
    assert resolved == str(saved_adb.resolve())
    assert source == "已保存配置"


def test_resolve_adb_bin_with_source_default_fallback(tmp_path: Path) -> None:
    from scripts.card_pick_recommender import resolve_adb_bin_with_source

    default_adb = tmp_path / "default_adb.exe"
    default_adb.write_bytes(b"")

    with patch("scripts.card_pick_recommender.get_saved_adb_bin", return_value=None), patch(
        "scripts.card_pick_recommender.DEFAULT_ADB_BIN", str(default_adb)
    ):
        resolved, source = resolve_adb_bin_with_source(None)
    assert resolved == str(default_adb.resolve())
    assert source == "默认路径"


def test_resolve_adb_bin_with_source_invalid_paths(tmp_path: Path) -> None:
    from scripts.card_pick_recommender import resolve_adb_bin_with_source

    with patch("scripts.card_pick_recommender.get_saved_adb_bin", return_value=str(tmp_path / "missing.exe")), patch(
        "scripts.card_pick_recommender.DEFAULT_ADB_BIN", str(tmp_path / "also_missing.exe")
    ):
        resolved, source = resolve_adb_bin_with_source(None)
    assert resolved is None
    assert source == "已保存配置（无效）"

    resolved, source = resolve_adb_bin_with_source(str(tmp_path / "missing_cli.exe"))
    assert resolved is None
    assert source == "命令行（无效）"


def test_user_settings_save_and_clear_adb_bin(tmp_path: Path) -> None:
    from src.user_settings import clear_saved_adb_bin, get_saved_adb_bin, save_adb_bin, settings_path

    fake_exe = tmp_path / "DZPPQCardRecommender.exe"
    fake_exe.write_bytes(b"")

    with patch("src.user_settings.app_base_dir", return_value=tmp_path):
        assert get_saved_adb_bin() is None
        save_adb_bin(r"D:\MuMu\nx_main\adb.exe")
        assert get_saved_adb_bin() == r"D:\MuMu\nx_main\adb.exe"
        assert settings_path().is_file()
        clear_saved_adb_bin()
        assert get_saved_adb_bin() is None
        assert not settings_path().exists()


if __name__ == "__main__":
    import tempfile

    test_hand_card_roi_boxes()
    test_load_stats_and_sample_weight()
    test_blue_team_metrics_merge()
    test_fuzzy_match_same_name_different_prefix()
    test_clean_ocr_text_strips_prefix()
    test_recommendation_prefers_better_white_card()
    test_recommendation_blue_uses_team_rank()
    test_resolve_meta_json_from_project_root()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        test_resolve_meta_json_explicit_override(tmp_path)
        test_resolve_meta_json_next_to_exe(tmp_path)
    test_app_base_dir_points_to_project_root_in_source_mode()
    test_adb_options_default_auto_connect()
    test_ensure_adb_session_auto_connects()
    test_ensure_adb_session_skips_connect_when_disabled()
    test_load_real_meta_json_if_present()
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        test_resolve_adb_bin_with_source_cli_priority(tmp_path)
        test_resolve_adb_bin_with_source_saved_config(tmp_path)
        test_resolve_adb_bin_with_source_default_fallback(tmp_path)
        test_resolve_adb_bin_with_source_invalid_paths(tmp_path)
        test_user_settings_save_and_clear_adb_bin(tmp_path)
    print("all tests passed")
