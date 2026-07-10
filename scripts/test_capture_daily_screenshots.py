# -*- coding: utf-8 -*-
"""Unit tests for adb_capture parsing helpers."""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adb_capture import (  # noqa: E402
    AdbClient,
    EXPECTED_SCREENSHOT_SIZE,
    EXPECTED_WM_SIZE,
    RankingEntry,
    SWIPE_RANKING_ONE_PLAYER,
    all_dates_before_target,
    build_next_rank_spec,
    build_scroll_player_specs_from_entries,
    compute_next_target_rank,
    extract_match_entries,
    extract_ranking_entries,
    extract_visible_match_dates,
    filter_new_match_entries,
    has_target_date_on_page,
    is_before_target_date,
    is_partial_rank_ocr,
    make_global_match_id,
    make_match_dedup_key,
    make_mumu_filename,
    normalize_match_datetime,
    normalize_match_duration,
    page_date_summary,
    page_has_before_target_date,
    parse_capture_entry_date,
    parse_capture_target_date,
    parse_visible_ranks,
    parse_ranking_entry_rank,
    parse_wm_size_output,
    should_exit_before_target_after_today_done,
)


def test_normalize_match_datetime_variants() -> None:
    assert normalize_match_datetime("07-04 00:52") == "07-04 00:52"
    assert normalize_match_datetime("07-0400:52") == "07-04 00:52"
    assert normalize_match_datetime("07-04  00:52") == "07-04 00:52"


def test_normalize_match_duration_variants() -> None:
    assert normalize_match_duration("32:09") == "32:09"
    assert normalize_match_duration("32：09") == "32:09"
    assert normalize_match_duration("时长32:09") == "32:09"
    assert normalize_match_duration("3:9") == "03:09"


def test_make_global_match_id() -> None:
    a = make_global_match_id("07-05 12:42", "32:09")
    b = make_global_match_id("07-05 12:42", "32:09")
    assert a == b == "07-05 12:42|32:09"


def test_parse_visible_ranks() -> None:
    assert parse_visible_ranks("4 5") == [4, 5]
    assert parse_visible_ranks("45") == [4, 5]
    assert parse_visible_ranks("第4第5") == [4, 5]
    assert parse_visible_ranks("10 11") == [10, 11]


def test_extract_match_entries_pairs_duo_peak_and_time() -> None:
    roi = (400, 500, 900, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    details = [
        {"text": "蛋仔碰碰棋", "score": 0.99, "box": quad(30)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(60)},
        {"text": "07-0400:52", "score": 0.98, "box": quad(90)},
        {"text": "蛋仔碰碰棋", "score": 0.99, "box": quad(150)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(180)},
        {"text": "07-04 01:10", "score": 0.98, "box": quad(210)},
    ]
    entries = extract_match_entries(details, roi, rank=4, player_index=1)
    assert len(entries) == 2
    assert entries[0].normalized_datetime == "07-04 00:52"
    assert entries[0].duo_peak_y == 562
    assert entries[0].time_y == 592
    assert entries[0].tap_y == 577
    assert entries[1].normalized_datetime == "07-04 01:10"
    assert entries[0].dedup_key != entries[1].dedup_key


def test_extract_match_entries_skips_duo_peak_without_ppq() -> None:
    roi = (400, 500, 900, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    details = [
        {"text": "双人巅峰", "score": 0.99, "box": quad(60)},
        {"text": "07-0400:52", "score": 0.98, "box": quad(90)},
    ]
    entries = extract_match_entries(details, roi, rank=4, player_index=1)
    assert entries == []

    filtered_entries = extract_match_entries(
        details,
        roi,
        rank=4,
        player_index=1,
        require_ppq=False,
    )
    assert len(filtered_entries) == 1
    assert filtered_entries[0].normalized_datetime == "07-04 00:52"


def test_extract_match_entries_mixed_ppq_and_other_modes() -> None:
    roi = (400, 500, 900, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    details = [
        {"text": "蛋仔碰碰棋", "score": 0.99, "box": quad(30)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(60)},
        {"text": "07-05 12:42", "score": 0.98, "box": quad(90)},
        {"text": "其他玩法", "score": 0.99, "box": quad(150)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(180)},
        {"text": "07-04 22:45", "score": 0.98, "box": quad(210)},
    ]
    entries = extract_match_entries(details, roi, rank=3, player_index=1)
    assert len(entries) == 1
    assert entries[0].normalized_datetime == "07-05 12:42"


def test_extract_match_entries_merged_ocr_line() -> None:
    """OCR may merge mode label into one line: 蛋仔碰碰棋一双人巅峰."""
    roi = (400, 500, 900, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    details = [
        {"text": "蛋仔碰碰棋一双人巅峰", "score": 0.99, "box": quad(60)},
        {"text": "07-0605:35", "score": 0.98, "box": quad(90)},
        {"text": "蛋仔碰碰棋一双人巅峰", "score": 0.99, "box": quad(180)},
        {"text": "07-0604:15", "score": 0.98, "box": quad(210)},
    ]
    entries = extract_match_entries(details, roi, rank=17, player_index=1)
    assert len(entries) == 2
    assert entries[0].normalized_datetime == "07-06 05:35"
    assert entries[1].normalized_datetime == "07-06 04:15"


def test_extract_match_entries_fuzzy_ocr_typo() -> None:
    """Common OCR typos like 蛋仔碰碰供双火额峰 should still match PPQ duo peak."""
    roi = (400, 500, 900, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    details = [
        {"text": "蛋仔碰碰供双火额峰", "score": 0.99, "box": quad(60)},
        {"text": "07-0602:51", "score": 0.98, "box": quad(90)},
    ]
    entries = extract_match_entries(details, roi, rank=17, player_index=1)
    assert len(entries) == 1
    assert entries[0].normalized_datetime == "07-06 02:51"


def test_party_page_date_scan_without_duo_peak() -> None:
    details = [
        {"text": "07-05 12:42", "score": 0.99, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        {"text": "07-04 22:45", "score": 0.98, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    ]
    assert has_target_date_on_page(details, "07-05") is True
    assert all_dates_before_target(details, "07-05", year=2026) is False
    summary = page_date_summary(details, "07-05", year=2026)
    assert summary.today_dates == ["07-05 12:42"]
    assert summary.before_target_dates == ["07-04 22:45"]
    assert page_has_before_target_date(details, "07-05", year=2026) is True

    old_only = [
        {"text": "07-04 22:45", "score": 0.98, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        {"text": "07-03 18:00", "score": 0.98, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    ]
    assert all_dates_before_target(old_only, "07-05", year=2026) is True
    assert extract_visible_match_dates(old_only) == ["07-04 22:45", "07-03 18:00"]
    assert page_has_before_target_date(old_only, "07-05", year=2026) is True


def test_cross_year_capture_date_resolution() -> None:
    new_year_ref = datetime(2027, 1, 1)

    assert parse_capture_target_date(
        "12-31",
        reference=new_year_ref,
    ) == datetime(2026, 12, 31).date()
    assert parse_capture_entry_date(
        "01-01",
        "12-31",
        reference=new_year_ref,
    ) == datetime(2027, 1, 1).date()
    assert parse_capture_entry_date(
        "12-30",
        "12-31",
        reference=new_year_ref,
    ) == datetime(2026, 12, 30).date()

    assert is_before_target_date("12-30", "12-31", reference=new_year_ref) is True
    assert is_before_target_date("01-01", "12-31", reference=new_year_ref) is False
    assert is_before_target_date("12-31", "12-31", reference=new_year_ref) is False

    new_year_only = [
        {"text": "01-01 00:52", "score": 0.99, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        {"text": "01-01 01:10", "score": 0.98, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    ]
    assert all_dates_before_target(new_year_only, "12-31", reference=new_year_ref) is False

    old_year_only = [
        {"text": "12-30 22:45", "score": 0.98, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        {"text": "12-29 18:00", "score": 0.98, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    ]
    assert all_dates_before_target(old_year_only, "12-31", reference=new_year_ref) is True

    mixed_cross_year = [
        {"text": "01-01 00:52", "score": 0.99, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
        {"text": "12-30 22:45", "score": 0.98, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    ]
    summary = page_date_summary(mixed_cross_year, "12-31", reference=new_year_ref)
    assert summary.today_dates == []
    assert summary.before_target_dates == ["12-30 22:45"]
    assert "01-01 00:52" not in summary.before_target_dates


def test_same_year_backfill_date_resolution() -> None:
    run_ref = datetime(2026, 7, 6)

    assert parse_capture_target_date("07-05", reference=run_ref) == datetime(2026, 7, 5).date()
    assert parse_capture_entry_date("07-06", "07-05", reference=run_ref) == datetime(
        2026, 7, 6
    ).date()
    assert parse_capture_entry_date("07-04", "07-05", reference=run_ref) == datetime(
        2026, 7, 4
    ).date()

    assert is_before_target_date("07-04", "07-05", reference=run_ref) is True
    assert is_before_target_date("07-06", "07-05", reference=run_ref) is False

    future_only = [
        {"text": "07-06 00:52", "score": 0.99, "box": [[0, 0], [1, 0], [1, 1], [0, 1]]},
    ]
    assert all_dates_before_target(future_only, "07-05", reference=run_ref) is False


def test_should_exit_before_target_after_today_done() -> None:
    roi = (400, 500, 900, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    mixed_details = [
        {"text": "蛋仔碰碰棋", "score": 0.99, "box": quad(30)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(60)},
        {"text": "07-05 12:42", "score": 0.98, "box": quad(90)},
        {"text": "其他玩法", "score": 0.99, "box": quad(150)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(180)},
        {"text": "07-04 22:45", "score": 0.98, "box": quad(210)},
    ]
    entries = extract_match_entries(mixed_details, roi, rank=3, player_index=1)
    summary = page_date_summary(mixed_details, "07-05", year=2026)
    seen: set[str] = set()
    processed: set[str] = set()

    assert should_exit_before_target_after_today_done(
        summary, entries, "07-05", seen, processed
    ) is False

    seen.add(entries[0].dedup_key)
    processed.add(entries[0].dedup_key)
    assert should_exit_before_target_after_today_done(
        summary, entries, "07-05", seen, processed
    ) is True

    today_only_details = [
        {"text": "07-05 12:42", "score": 0.98, "box": quad(90)},
        {"text": "07-04 22:45", "score": 0.98, "box": quad(210)},
    ]
    today_summary = page_date_summary(today_only_details, "07-05", year=2026)
    assert should_exit_before_target_after_today_done(
        today_summary, [], "07-05", set(), set()
    ) is False


def test_dedup_key_stable_for_y_jitter() -> None:
    key_a = make_match_dedup_key(4, 1, "07-04 00:52", 562)
    key_b = make_match_dedup_key(4, 1, "07-04 00:52", 565)
    assert key_a == key_b


def test_resolution_constants() -> None:
    assert EXPECTED_WM_SIZE == (1600, 2160)
    assert EXPECTED_SCREENSHOT_SIZE == (2160, 1600)


def test_extract_ranking_entries_with_screen_coordinates() -> None:
    roi = (500, 850, 600, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    details = [
        {"text": "4", "score": 0.99, "box": quad(20)},
        {"text": "5", "score": 0.99, "box": quad(120)},
    ]
    entries = extract_ranking_entries(details, roi)
    assert len(entries) == 2
    assert entries[0].rank == 4
    assert entries[0].tap_y == 872
    assert entries[1].rank == 5
    assert entries[1].tap_y == 972


def test_build_scroll_player_specs_skips_processed_ranks() -> None:
    entries = [
        RankingEntry(rank=4, tap_y=933, raw_text="4"),
        RankingEntry(rank=5, tap_y=1080, raw_text="5"),
    ]
    specs, skipped = build_scroll_player_specs_from_entries(
        entries,
        next_expected_rank=5,
        end_rank=100,
        processed_ranks={4},
        tap_x=700,
    )
    assert specs == [(5, 1, 700, 1080)]
    assert skipped == []


def test_build_next_rank_spec_marks_duplicate_visible_rank() -> None:
    entries = [RankingEntry(rank=4, tap_y=933, raw_text="4")]
    specs, skipped, wait_reason = build_next_rank_spec(
        entries,
        next_target_rank=4,
        end_rank=100,
        processed_ranks={4},
        tap_x=700,
    )
    assert specs == []
    assert skipped == [(4, "duplicate_rank_visible_after_swipe")]
    assert wait_reason is None


def test_build_scroll_player_specs_uses_ocr_tap_y() -> None:
    entries = [
        RankingEntry(rank=6, tap_y=950, raw_text="6"),
        RankingEntry(rank=7, tap_y=1105, raw_text="7"),
    ]
    specs, skipped = build_scroll_player_specs_from_entries(
        entries,
        next_expected_rank=6,
        end_rank=100,
        processed_ranks=set(),
        tap_x=700,
    )
    assert specs == [(6, 1, 700, 950)]
    assert skipped == []


def test_filter_new_match_entries_skips_duplicate_start_time() -> None:
    roi = (400, 500, 900, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    page_one = [
        {"text": "蛋仔碰碰棋", "score": 0.99, "box": quad(30)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(60)},
        {"text": "07-05 12:42", "score": 0.98, "box": quad(90)},
    ]
    page_two = [
        {"text": "蛋仔碰碰棋", "score": 0.99, "box": quad(150)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(180)},
        {"text": "07-05 12:42", "score": 0.98, "box": quad(210)},
    ]
    first_entries = extract_match_entries(page_one, roi, rank=3, player_index=1)
    second_entries = extract_match_entries(page_two, roi, rank=3, player_index=1)
    assert first_entries[0].dedup_key != second_entries[0].dedup_key

    seen_start_times = {first_entries[0].normalized_datetime}
    filtered = filter_new_match_entries(
        second_entries,
        set(),
        set(),
        seen_start_times,
    )
    assert filtered == []


def test_should_exit_before_target_respects_start_times() -> None:
    roi = (400, 500, 900, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    mixed_details = [
        {"text": "蛋仔碰碰棋", "score": 0.99, "box": quad(30)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(60)},
        {"text": "07-05 12:42", "score": 0.98, "box": quad(90)},
        {"text": "其他玩法", "score": 0.99, "box": quad(150)},
        {"text": "双人巅峰", "score": 0.99, "box": quad(180)},
        {"text": "07-04 22:45", "score": 0.98, "box": quad(210)},
    ]
    entries = extract_match_entries(mixed_details, roi, rank=3, player_index=1)
    summary = page_date_summary(mixed_details, "07-05", year=2026)
    seen: set[str] = set()
    processed: set[str] = set()
    start_times = {entries[0].normalized_datetime}

    assert should_exit_before_target_after_today_done(
        summary, entries, "07-05", seen, processed, start_times
    ) is True


def test_parse_ranking_entry_rank_two_digit() -> None:
    assert parse_ranking_entry_rank("12") == 12
    assert parse_ranking_entry_rank("13") == 13
    assert parse_ranking_entry_rank("第12") == 12
    assert parse_ranking_entry_rank("12.") == 12
    assert parse_ranking_entry_rank("45") == 45


def test_parse_ranking_entry_rank_differs_from_visible_ranks_split() -> None:
    assert parse_visible_ranks("45") == [4, 5]
    assert parse_ranking_entry_rank("45") == 45


def test_extract_ranking_entries_parses_two_digit_ranks() -> None:
    roi = (500, 850, 600, 1100)

    def quad(y: int) -> list[list[int]]:
        return [[0, y], [10, y], [10, y + 5], [0, y + 5]]

    details = [
        {"text": "12", "score": 0.99, "box": quad(20)},
        {"text": "13", "score": 0.99, "box": quad(120)},
    ]
    entries = extract_ranking_entries(details, roi)
    assert [entry.rank for entry in entries] == [12, 13]
    assert entries[0].tap_y == 872
    assert entries[1].tap_y == 972


def test_build_scroll_player_specs_for_rank_12_and_13() -> None:
    entries = [
        RankingEntry(rank=12, tap_y=875, raw_text="12"),
        RankingEntry(rank=13, tap_y=1026, raw_text="13"),
    ]
    specs, skipped = build_scroll_player_specs_from_entries(
        entries,
        next_expected_rank=12,
        end_rank=20,
        processed_ranks=set(),
        tap_x=700,
    )
    assert specs == [(12, 1, 700, 875)]
    assert skipped == []


def test_swipe_ranking_one_player_constant() -> None:
    assert SWIPE_RANKING_ONE_PLAYER == (1000, 1000, 1000, 870, 500)


def test_build_next_rank_spec_target_ahead_keeps_scrolling() -> None:
    entries = [
        RankingEntry(rank=65, tap_y=900, raw_text="65"),
        RankingEntry(rank=66, tap_y=1000, raw_text="66"),
    ]
    specs, skipped, wait_reason = build_next_rank_spec(
        entries,
        next_target_rank=90,
        end_rank=100,
        processed_ranks=set(),
        tap_x=700,
    )
    assert specs == []
    assert skipped == []
    assert wait_reason == "ranking_target_ahead"


def test_build_next_rank_spec_overshoot_returns_missing() -> None:
    entries = [
        RankingEntry(rank=91, tap_y=900, raw_text="91"),
        RankingEntry(rank=92, tap_y=1000, raw_text="92"),
    ]
    specs, skipped, wait_reason = build_next_rank_spec(
        entries,
        next_target_rank=90,
        end_rank=100,
        processed_ranks=set(),
        tap_x=700,
    )
    assert specs == []
    assert skipped == []
    assert wait_reason == "ranking_missing_expected_rank"


def test_build_next_rank_spec_rejects_jump_to_larger_rank() -> None:
    entries = [
        RankingEntry(rank=18, tap_y=900, raw_text="18"),
        RankingEntry(rank=9, tap_y=1000, raw_text="9"),
    ]
    specs, skipped, wait_reason = build_next_rank_spec(
        entries,
        next_target_rank=17,
        end_rank=100,
        processed_ranks=set(),
        tap_x=700,
    )
    assert specs == []
    assert skipped == []
    assert wait_reason == "ranking_missing_expected_rank"


def test_build_next_rank_spec_ignores_partial_rank_ocr() -> None:
    entries = [
        RankingEntry(rank=18, tap_y=900, raw_text="18"),
        RankingEntry(rank=9, tap_y=1000, raw_text="9"),
    ]
    specs, skipped, wait_reason = build_next_rank_spec(
        entries,
        next_target_rank=19,
        end_rank=100,
        processed_ranks=set(),
        tap_x=700,
    )
    assert specs == []
    assert skipped == []
    assert wait_reason == "ranking_partial_rank_ocr"
    assert is_partial_rank_ocr(9, 19) is True


def test_compute_next_target_rank_skips_manual_and_completed() -> None:
    assert compute_next_target_rank(
        1,
        20,
        completed_ranks={1, 2, 3, 4, 5, 6},
        skipped_ranks={8},
        manual_skip_ranks={7, 9},
    ) == 10


def test_capture_state_resume_and_preload_match_ids() -> None:
    from scripts.capture_daily_screenshots import CaptureState, DailyCaptureBot, CaptureConfig

    state = CaptureState.new(
        run_id="20260705-120000",
        target_date="07-05",
        start_rank=1,
        end_rank=20,
    )
    for rank in range(1, 17):
        state.set_rank_status(rank, "completed")
        record = state.get_rank_record(rank)
        record["match_ids"] = [f"07-05 12:{rank:02d}|32:09"]
    state.set_rank_status(17, "pending")

    config = CaptureConfig(
        output_dir=ROOT / "screenshots.test",
        start_rank=1,
        end_rank=20,
        resume=True,
    )
    bot = DailyCaptureBot(config)
    bot.capture_state = state
    bot.run_id = state.run_id
    bot.preload_from_state()

    assert bot._next_expected_rank == 17
    assert 16 in bot._processed_player_ranks
    assert 17 not in bot._processed_player_ranks
    assert "07-05 12:16|32:09" in bot._processed_match_ids


def test_capture_state_resume_skips_to_next_failed_rank() -> None:
    from scripts.capture_daily_screenshots import CaptureState, DailyCaptureBot, CaptureConfig

    state = CaptureState.new(
        run_id="20260706-180000",
        target_date="07-05",
        start_rank=62,
        end_rank=100,
    )
    for rank in range(62, 65):
        state.set_rank_status(rank, "completed" if rank == 62 else "skipped")
    for rank in range(65, 90):
        state.set_rank_status(rank, "skipped")
    state.set_rank_status(90, "failed")

    config = CaptureConfig(
        output_dir=ROOT / "screenshots.test",
        start_rank=62,
        end_rank=100,
        resume=True,
    )
    bot = DailyCaptureBot(config)
    bot.capture_state = state
    bot.run_id = state.run_id
    bot.preload_from_state()

    assert bot._next_expected_rank == 90
    assert 64 in bot._processed_player_ranks
    assert 90 not in bot._processed_player_ranks


def test_capture_state_resume_retries_first_of_multiple_failed_ranks() -> None:
    from scripts.capture_daily_screenshots import CaptureState, DailyCaptureBot, CaptureConfig

    state = CaptureState.new(
        run_id="20260710-010000",
        target_date="07-09",
        start_rank=1,
        end_rank=40,
    )
    for rank in range(1, 21):
        state.set_rank_status(rank, "completed")
    for rank in range(21, 24):
        state.set_rank_status(rank, "failed")
    for rank in range(27, 36):
        state.set_rank_status(rank, "failed")

    config = CaptureConfig(
        output_dir=ROOT / "screenshots.test",
        start_rank=1,
        end_rank=40,
        resume=True,
    )
    bot = DailyCaptureBot(config)
    bot.capture_state = state
    bot.run_id = state.run_id
    bot.preload_from_state()

    assert bot._next_expected_rank == 21
    assert 20 in bot._processed_player_ranks
    for failed_rank in list(range(21, 24)) + list(range(27, 36)):
        assert failed_rank not in bot._processed_player_ranks


def test_process_player_unstable_profile_with_entry_ready_continues() -> None:
    import shutil

    import numpy as np

    from scripts.capture_daily_screenshots import CaptureConfig, DailyCaptureBot
    from src.adb_capture import (
        PartyReviewWaitResult,
        ProfilePartyReviewEntryWaitResult,
        SCREEN_PROFILE,
        WaitResult,
    )

    output_dir = ROOT / "screenshots.test" / "profile_unstable_continue"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    config = CaptureConfig(output_dir=output_dir)
    bot = DailyCaptureBot(config)
    bot.run_id = "test-run"
    bot.run_dir = output_dir / "runs" / bot.run_id
    bot._next_expected_rank = 23
    img = np.zeros((10, 10, 3), dtype=np.uint8)

    profile_wait = WaitResult(
        screen=SCREEN_PROFILE,
        img=img,
        elapsed_ms=15000,
        polls=2,
        stable=False,
    )
    entry_wait = ProfilePartyReviewEntryWaitResult(
        ready=True,
        img=img,
        elapsed_ms=8000,
        polls=2,
        stable=True,
        on_profile=True,
    )
    party_wait = PartyReviewWaitResult(
        state="private",
        img=img,
        elapsed_ms=9000,
        polls=3,
        stable=True,
        stable_hits_used=2,
    )

    bot.adb.tap = lambda *args, **kwargs: None  # type: ignore[method-assign]
    bot.adb.capture_bgr = lambda: img  # type: ignore[method-assign]
    bot.screen.wait_for_screen = lambda *args, **kwargs: profile_wait  # type: ignore[method-assign]
    bot._confirm_unstable_profile_entry = lambda rank: entry_wait  # type: ignore[method-assign]
    open_party_review_called = {"value": False}

    def open_party_review(rank: int):
        open_party_review_called["value"] = True
        return entry_wait, party_wait

    bot.open_party_review = open_party_review  # type: ignore[method-assign]
    bot.back_to_ranking = lambda: None  # type: ignore[method-assign]

    bot.process_player(23, 1, 700, 1000)

    assert open_party_review_called["value"] is True
    assert bot.capture_state.get_rank_status(23) == "skipped"
    assert any(
        event["event"] == "profile_unstable_but_entry_ready"
        for event in bot.events
    )
    assert not any(event["event"] == "player_error" for event in bot.events)
    shutil.rmtree(output_dir, ignore_errors=True)


def test_process_player_unstable_profile_without_entry_records_profile_entry_timeout() -> None:
    import shutil

    import numpy as np

    from scripts.capture_daily_screenshots import CaptureConfig, DailyCaptureBot
    from src.adb_capture import (
        ProfilePartyReviewEntryWaitResult,
        SCREEN_PROFILE,
        WaitResult,
    )

    output_dir = ROOT / "screenshots.test" / "profile_entry_timeout"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    config = CaptureConfig(output_dir=output_dir)
    bot = DailyCaptureBot(config)
    bot.run_id = "test-run"
    bot.run_dir = output_dir / "runs" / bot.run_id
    bot._next_expected_rank = 27
    img = np.zeros((10, 10, 3), dtype=np.uint8)

    profile_wait = WaitResult(
        screen=SCREEN_PROFILE,
        img=img,
        elapsed_ms=15000,
        polls=2,
        stable=False,
    )
    entry_wait = ProfilePartyReviewEntryWaitResult(
        ready=False,
        img=img,
        elapsed_ms=8000,
        polls=3,
        stable=False,
        on_profile=True,
    )

    bot.adb.tap = lambda *args, **kwargs: None  # type: ignore[method-assign]
    bot.adb.capture_bgr = lambda: img  # type: ignore[method-assign]
    bot.screen.wait_for_screen = lambda *args, **kwargs: profile_wait  # type: ignore[method-assign]
    bot._confirm_unstable_profile_entry = lambda rank: entry_wait  # type: ignore[method-assign]
    bot.recover_to_ranking = lambda: None  # type: ignore[method-assign]
    open_party_review_called = {"value": False}

    def fake_open_party_review(rank: int):
        open_party_review_called["value"] = True
        raise AssertionError("should not open party review")

    bot.open_party_review = fake_open_party_review  # type: ignore[method-assign]

    bot.process_player(27, 1, 700, 1000)

    assert open_party_review_called["value"] is False
    record = bot.capture_state.get_rank_record(27)
    assert record["status"] == "failed"
    assert str(record["failure_screenshot"]).endswith("rank_27_profile_entry_timeout.png")
    assert any(
        event["event"] == "player_error" and event["reason"] == "profile_entry_timeout"
        for event in bot.events
    )
    shutil.rmtree(output_dir, ignore_errors=True)


def test_process_player_unexpected_screen_still_records_failure() -> None:
    import shutil

    import numpy as np

    from scripts.capture_daily_screenshots import CaptureConfig, DailyCaptureBot
    from src.adb_capture import SCREEN_UNKNOWN, WaitResult

    output_dir = ROOT / "screenshots.test" / "unexpected_screen_failure"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    config = CaptureConfig(output_dir=output_dir)
    bot = DailyCaptureBot(config)
    bot.run_id = "test-run"
    bot.run_dir = output_dir / "runs" / bot.run_id
    bot._next_expected_rank = 30
    img = np.zeros((10, 10, 3), dtype=np.uint8)

    profile_wait = WaitResult(
        screen=SCREEN_UNKNOWN,
        img=img,
        elapsed_ms=12000,
        polls=2,
        stable=True,
    )

    bot.adb.tap = lambda *args, **kwargs: None  # type: ignore[method-assign]
    bot.adb.capture_bgr = lambda: img  # type: ignore[method-assign]
    bot.screen.wait_for_screen = lambda *args, **kwargs: profile_wait  # type: ignore[method-assign]
    bot.recover_to_ranking = lambda: None  # type: ignore[method-assign]
    confirm_called = {"value": False}

    def fake_confirm(rank: int):
        confirm_called["value"] = True
        raise AssertionError("should not confirm unstable profile")

    bot._confirm_unstable_profile_entry = fake_confirm  # type: ignore[method-assign]

    bot.process_player(30, 1, 700, 1000)

    assert confirm_called["value"] is False
    record = bot.capture_state.get_rank_record(30)
    assert record["status"] == "failed"
    assert str(record["failure_screenshot"]).endswith("rank_30_unexpected_screen.png")
    assert any(
        event["event"] == "player_error" and event["reason"] == "unexpected_screen"
        for event in bot.events
    )
    shutil.rmtree(output_dir, ignore_errors=True)


def test_output_isolation_run_dirs() -> None:
    from scripts.capture_daily_screenshots import CaptureConfig, DailyCaptureBot

    config = CaptureConfig(output_dir=ROOT / "screenshots.test")
    bot = DailyCaptureBot(config)
    bot.run_id = "run_a"
    bot.run_dir = config.output_dir / "runs" / bot.run_id
    assert bot.debug_matches_dir == config.output_dir / "runs" / "run_a" / "debug_matches"
    assert bot.debug_players_dir == config.output_dir / "runs" / "run_a" / "debug_players"
    assert bot.failures_dir == config.output_dir / "failures"
    assert bot.failures_dir != config.output_dir


def test_save_failure_screenshot_uses_isolated_failures_dir() -> None:
    import shutil

    import numpy as np

    from scripts.capture_daily_screenshots import CaptureConfig, DailyCaptureBot

    output_dir = ROOT / "screenshots.test" / "failure_screenshot_test"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    config = CaptureConfig(output_dir=output_dir)
    bot = DailyCaptureBot(config)
    img = np.zeros((10, 10, 3), dtype=np.uint8)

    path = bot.save_failure_screenshot(17, "unexpected_screen", img=img)

    assert path is not None
    assert path.parent == output_dir / "failures"
    assert path.name == "rank_17_unexpected_screen.png"
    assert path.exists()
    assert path.parent != output_dir
    shutil.rmtree(output_dir, ignore_errors=True)


def test_record_rank_failure_writes_state_and_event() -> None:
    import shutil

    import numpy as np

    from scripts.capture_daily_screenshots import CaptureConfig, DailyCaptureBot

    output_dir = ROOT / "screenshots.test" / "record_rank_failure_test"
    if output_dir.exists():
        shutil.rmtree(output_dir)
    config = CaptureConfig(output_dir=output_dir)
    bot = DailyCaptureBot(config)
    bot.run_id = "20260707-test"
    img = np.zeros((10, 10, 3), dtype=np.uint8)

    bot.record_rank_failure(42, "party_review_timeout", img=img)

    record = bot.capture_state.get_rank_record(42)
    assert record["status"] == "failed"
    assert record["failure_screenshot"] is not None
    assert record["failure_screenshot"].endswith("rank_42_party_review_timeout.png")
    assert any(event["event"] == "rank_failure_screenshot" for event in bot.events)
    shutil.rmtree(output_dir, ignore_errors=True)


def test_sanitize_failure_reason() -> None:
    from scripts.capture_daily_screenshots import DailyCaptureBot

    assert DailyCaptureBot.sanitize_failure_reason("Party Review Timeout") == "party_review_timeout"
    assert DailyCaptureBot.sanitize_failure_reason("unexpected/screen") == "unexpected_screen"


def test_entry_extract_mode_constant() -> None:
    from scripts.capture_daily_screenshots import ENTRY_EXTRACT_MODE

    assert ENTRY_EXTRACT_MODE == "ppq_ocr_required_fuzzy_no_ui_filter"


def test_load_manual_skip_ranks() -> None:
    from scripts.capture_daily_screenshots import load_manual_skip_ranks

    skip_path = ROOT / "screenshots.test" / "capture_skip_players_test.json"
    skip_path.parent.mkdir(parents=True, exist_ok=True)
    if skip_path.exists():
        skip_path.unlink()
    assert load_manual_skip_ranks(skip_path) == set()
    skip_path.write_text("[9, 11, 13, 15]", encoding="utf-8")
    try:
        assert load_manual_skip_ranks(skip_path) == {9, 11, 13, 15}
    finally:
        skip_path.unlink(missing_ok=True)


def test_mumu_filename_format() -> None:
    from datetime import datetime

    name = make_mumu_filename(datetime(2026, 7, 4, 23, 51, 48, 537000))
    assert name == "MuMu-20260704-235148-537.png"


def test_parse_wm_size_output() -> None:
    assert parse_wm_size_output("Physical size: 1600x2160") == (1600, 2160)
    assert parse_wm_size_output("Override size: 2160x1600") == (2160, 1600)
    assert parse_wm_size_output("error: closed") is None


def test_check_device_falls_back_when_mumu_tcp_closed() -> None:
    client = AdbClient()
    probe_results = {
        "127.0.0.1:16384": (False, "error: closed"),
        "emulator-5554": (True, "Physical size: 1600x2160"),
    }
    client.list_devices = lambda: ["127.0.0.1:16384", "emulator-5554"]  # type: ignore[method-assign]
    client.probe_device = lambda serial: probe_results[serial]  # type: ignore[method-assign]

    selected = client.check_device()

    assert selected == "emulator-5554"
    assert client.device_serial == "emulator-5554"


def test_check_device_fails_when_preferred_serial_unhealthy() -> None:
    client = AdbClient()
    client.list_devices = lambda: ["127.0.0.1:16384", "emulator-5554"]  # type: ignore[method-assign]
    client.probe_device = lambda serial: (False, "error: closed")  # type: ignore[method-assign]

    try:
        client.check_device(prefer="127.0.0.1:16384")
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        message = str(exc)
        assert "127.0.0.1:16384" in message
        assert "error: closed" in message
        assert "--serial" in message


def test_check_device_reports_all_failures() -> None:
    client = AdbClient()
    client.list_devices = lambda: ["127.0.0.1:16384", "emulator-5554"]  # type: ignore[method-assign]
    client.probe_device = lambda serial: (False, "error: closed")  # type: ignore[method-assign]

    try:
        client.check_device()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        message = str(exc)
        assert "127.0.0.1:16384: error: closed" in message
        assert "emulator-5554: error: closed" in message
        assert "No healthy adb device found" in message


def test_has_profile_party_review_entry_on_failure_screenshot() -> None:
    import cv2

    from src.adb_capture import OcrHelper, SCREEN_PROFILE, ScreenDetector

    img_path = ROOT / "screenshots.0707" / "failures" / "rank_23_party_review_timeout.png"
    if not img_path.exists():
        return
    img = cv2.imread(str(img_path))
    assert img is not None
    detector = ScreenDetector(OcrHelper())
    assert detector.detect(img) == SCREEN_PROFILE
    assert detector.has_profile_party_review_entry(img) is True


def test_wait_for_profile_party_review_entry_waits_for_stable_hits() -> None:
    import numpy as np

    from src.adb_capture import AdbClient, OcrHelper, SCREEN_PROFILE, ScreenDetector

    img = np.zeros((1600, 2160, 3), dtype=np.uint8)
    adb = AdbClient()
    adb.capture_bgr = lambda: img  # type: ignore[method-assign]

    detector = ScreenDetector(OcrHelper())
    poll_count = {"value": 0}

    def has_entry(_img: np.ndarray) -> bool:
        poll_count["value"] += 1
        return poll_count["value"] >= 3

    detector.detect = lambda _img, verbose=False: SCREEN_PROFILE  # type: ignore[method-assign]
    detector.has_profile_party_review_entry = has_entry  # type: ignore[method-assign]

    result = detector.wait_for_profile_party_review_entry(
        adb,
        timeout=5.0,
        poll=0.01,
        stable_hits=2,
    )
    assert result.stable is True
    assert result.ready is True
    assert result.polls >= 4


def test_open_party_review_retries_tap_when_still_on_profile() -> None:
    import numpy as np

    from scripts.capture_daily_screenshots import CaptureConfig, DailyCaptureBot
    from src.adb_capture import (
        PartyReviewWaitResult,
        ProfilePartyReviewEntryWaitResult,
        SCREEN_PROFILE,
        TAP_PROFILE_PARTY_REVIEW,
    )

    config = CaptureConfig(output_dir=ROOT / "screenshots.test")
    bot = DailyCaptureBot(config)
    img = np.zeros((10, 10, 3), dtype=np.uint8)
    taps: list[tuple[int, int]] = []

    bot.adb.tap = lambda x, y, delay=0: taps.append((int(x), int(y)))  # type: ignore[method-assign]
    entry_wait = ProfilePartyReviewEntryWaitResult(
        ready=True,
        img=img,
        elapsed_ms=100,
        polls=2,
        stable=True,
        on_profile=True,
    )
    bot.screen.wait_for_profile_party_review_entry = lambda *args, **kwargs: entry_wait  # type: ignore[method-assign]
    party_waits = [
        PartyReviewWaitResult(
            state="timeout",
            img=img,
            elapsed_ms=12000,
            polls=20,
            stable=False,
        ),
        PartyReviewWaitResult(
            state="public",
            img=img,
            elapsed_ms=800,
            polls=2,
            stable=True,
        ),
    ]
    bot.screen.wait_for_party_review = lambda *args, **kwargs: party_waits.pop(0)  # type: ignore[method-assign]
    bot.screen.detect = lambda _img: SCREEN_PROFILE  # type: ignore[method-assign]

    entry_result, party_result = bot.open_party_review(rank=23)

    assert len(taps) == 2
    assert taps == [TAP_PROFILE_PARTY_REVIEW, TAP_PROFILE_PARTY_REVIEW]
    assert entry_result.stable is True
    assert party_result.state == "public"
    assert party_result.stable is True
    assert any(event["event"] == "party_review_tap_retry" for event in bot.events)


def test_get_screen_size_error_includes_serial() -> None:
    import subprocess

    client = AdbClient(device_serial="127.0.0.1:16384")

    class FakeResult:
        returncode = 1
        stdout = b""
        stderr = b"error: closed"

    client.adb_shell = lambda *args, **kwargs: FakeResult()  # type: ignore[method-assign]

    try:
        client.get_screen_size()
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        message = str(exc)
        assert "wm size failed on 127.0.0.1:16384" in message
        assert "error: closed" in message
        assert "--serial" in message


if __name__ == "__main__":
    test_normalize_match_datetime_variants()
    test_normalize_match_duration_variants()
    test_make_global_match_id()
    test_parse_visible_ranks()
    test_extract_match_entries_pairs_duo_peak_and_time()
    test_extract_match_entries_skips_duo_peak_without_ppq()
    test_extract_match_entries_mixed_ppq_and_other_modes()
    test_extract_match_entries_merged_ocr_line()
    test_extract_match_entries_fuzzy_ocr_typo()
    test_party_page_date_scan_without_duo_peak()
    test_cross_year_capture_date_resolution()
    test_same_year_backfill_date_resolution()
    test_should_exit_before_target_after_today_done()
    test_dedup_key_stable_for_y_jitter()
    test_extract_ranking_entries_with_screen_coordinates()
    test_build_scroll_player_specs_skips_processed_ranks()
    test_build_next_rank_spec_marks_duplicate_visible_rank()
    test_build_scroll_player_specs_uses_ocr_tap_y()
    test_parse_ranking_entry_rank_two_digit()
    test_parse_ranking_entry_rank_differs_from_visible_ranks_split()
    test_extract_ranking_entries_parses_two_digit_ranks()
    test_build_scroll_player_specs_for_rank_12_and_13()
    test_swipe_ranking_one_player_constant()
    test_build_next_rank_spec_rejects_jump_to_larger_rank()
    test_build_next_rank_spec_ignores_partial_rank_ocr()
    test_compute_next_target_rank_skips_manual_and_completed()
    test_capture_state_resume_and_preload_match_ids()
    test_capture_state_resume_skips_to_next_failed_rank()
    test_capture_state_resume_retries_first_of_multiple_failed_ranks()
    test_process_player_unstable_profile_with_entry_ready_continues()
    test_process_player_unstable_profile_without_entry_records_profile_entry_timeout()
    test_process_player_unexpected_screen_still_records_failure()
    test_output_isolation_run_dirs()
    test_save_failure_screenshot_uses_isolated_failures_dir()
    test_record_rank_failure_writes_state_and_event()
    test_sanitize_failure_reason()
    test_entry_extract_mode_constant()
    test_load_manual_skip_ranks()
    test_filter_new_match_entries_skips_duplicate_start_time()
    test_should_exit_before_target_respects_start_times()
    test_resolution_constants()
    test_mumu_filename_format()
    test_parse_wm_size_output()
    test_check_device_falls_back_when_mumu_tcp_closed()
    test_check_device_fails_when_preferred_serial_unhealthy()
    test_check_device_reports_all_failures()
    test_has_profile_party_review_entry_on_failure_screenshot()
    test_wait_for_profile_party_review_entry_waits_for_stable_hits()
    test_open_party_review_retries_tap_when_still_on_profile()
    test_get_screen_size_error_includes_serial()
    print("all tests passed")
