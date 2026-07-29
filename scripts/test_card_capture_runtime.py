# -*- coding: utf-8 -*-
"""Independent runtime tests for ADB card capture integration."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.capture_daily_screenshots import CaptureConfig, DailyCaptureBot  # noqa: E402
from src.adb_capture import MatchEntry, SCREEN_MATCH_SOLO_RANK  # noqa: E402
from src.card_capture import (  # noqa: E402
    CardCaptureResult,
    card_body,
    capture_cards_runtime,
    resolve_detail_candidate,
)
from src.card_details import (  # noqa: E402
    CardDetails,
    CardDetailsValidationError,
    load_card_details,
)
from src.card_sidecar import load_card_sidecar  # noqa: E402
from src.detect_cards import CardDetectionResult  # noqa: E402


def _details() -> CardDetails:
    candidates = ("蓝·同名卡", "黄·同名卡")
    return CardDetails(
        by_color={
            "白": {},
            "蓝": {"蓝·同名卡": "造成蓝色伤害"},
            "黄": {"黄·同名卡": "获得黄色护盾"},
            "彩": {},
        },
        same_template_candidates={"组合模板": candidates},
        asset_candidates={"组合模板": candidates},
    )


def _match(
    presence: str,
    *,
    stem: str = "组合模板",
    label: str = "unknown",
    score: float = 0.9,
) -> CardDetectionResult:
    return CardDetectionResult(
        label=label,
        score=score,
        presence=presence,  # type: ignore[arg-type]
        debug={"top1_raw_asset_stem": stem, "reject_reason": "accepted"},
    )


def _detector_for(matches: dict[tuple[int, int], CardDetectionResult]):
    def detector(_img: np.ndarray, _sigs: dict) -> list[dict]:
        return [
            {
                "player": row + 1,
                "row_index": row,
                "cards": [
                    {
                        "slot_index": slot,
                        "match": matches.get((row, slot), _match("empty", score=0.0)),
                    }
                    for slot in range(3)
                ],
            }
            for row in range(8)
        ]

    return detector


class FakeAdb:
    def __init__(self, panels: list[np.ndarray]) -> None:
        self.panels = panels
        self.taps: list[tuple[int, int]] = []

    def tap(self, x: int, y: int) -> None:
        self.taps.append((x, y))

    def capture_bgr(self) -> np.ndarray:
        if len(self.panels) > 1:
            return self.panels.pop(0)
        return self.panels[0]


class FakeOcr:
    def __init__(self, texts: list[tuple[str, str]]) -> None:
        self.texts = texts
        self.index = 0

    def ocr_text(self, _img: np.ndarray, _box) -> str:
        pair = self.texts[min(self.index // 2, len(self.texts) - 1)]
        value = pair[self.index % 2]
        self.index += 1
        return value


def _fast_detail_poll() -> dict:
    return {
        "detail_settle_seconds": 0.0,
        "detail_poll_interval_seconds": 0.0,
        "detail_timeout_seconds": 0.1,
        "sleep": lambda _seconds: None,
    }


def test_empty_third_slot_and_uncertain_never_reuse_hover_detail(
    tmp_path: Path,
) -> None:
    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    panel = np.ones_like(overview)
    matches = {
        (0, 0): _match("occupied"),
        (0, 1): _match("uncertain", score=0.5),
        (0, 2): _match("empty", score=0.0),
    }
    adb = FakeAdb([panel])
    result = capture_cards_runtime(
        overview,
        adb=adb,
        ocr=FakeOcr([("同名卡", "造成蓝色伤害")]),
        card_details=_details(),
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for(matches),
        **_fast_detail_poll(),
    )

    assert len(adb.taps) == 1
    assert result.slots[0]["label"] == "蓝·同名卡"
    assert result.slots[0]["is_ground_truth"] is True
    assert result.slots[1]["presence"] == "uncertain"
    assert result.slots[1]["label"] == "unknown"
    assert result.slots[1]["is_ground_truth"] is False
    assert Path(result.slots[1]["review_artifacts"]["icon"]).exists()
    assert result.slots[2]["presence"] == "empty"
    assert result.slots[2]["label"] is None
    assert result.slots[2]["score"] is None


def test_consecutive_identical_cards_can_both_confirm(tmp_path: Path) -> None:
    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    matches = {(0, 0): _match("occupied"), (0, 1): _match("occupied")}
    adb = FakeAdb([np.ones_like(overview), np.ones_like(overview)])
    result = capture_cards_runtime(
        overview,
        adb=adb,
        ocr=FakeOcr(
            [
                ("同名卡", "造成蓝色伤害"),
                ("同名卡", "造成蓝色伤害"),
            ]
        ),
        card_details=_details(),
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for(matches),
        **_fast_detail_poll(),
    )

    assert len(adb.taps) == 2
    assert [slot["label"] for slot in result.slots[:2]] == [
        "蓝·同名卡",
        "蓝·同名卡",
    ]
    assert all(slot["is_ground_truth"] for slot in result.slots[:2])


def test_wall_clock_timeout_does_not_force_extra_slow_poll(
    tmp_path: Path,
) -> None:
    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    times = iter((0.0, 3.0, 3.0))
    result = capture_cards_runtime(
        overview,
        adb=FakeAdb([np.ones_like(overview), np.ones_like(overview)]),
        ocr=FakeOcr(
            [
                ("同名卡", "造成蓝色伤害"),
                ("同名卡", "造成蓝色伤害"),
            ]
        ),
        card_details=_details(),
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for({(0, 0): _match("occupied")}),
        detail_settle_seconds=0.0,
        detail_poll_interval_seconds=0.0,
        detail_timeout_seconds=2.5,
        sleep=lambda _seconds: None,
        monotonic=lambda: next(times),
    )

    slot = result.slots[0]
    assert slot["label"] == "unknown"
    assert slot["detail_ocr"]["stability"]["polls"] == 1
    assert slot["detail_ocr"]["stability"]["minimum_polls_completed"] is False
    assert slot["detail_ocr"]["stability"]["elapsed_ms"] == 3000.0
    assert slot["detail_ocr"]["stability"]["stop_reason"] == "wall-clock-timeout"


def test_name_mismatch_is_unresolved_and_saves_review_artifacts(
    tmp_path: Path,
) -> None:
    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    result = capture_cards_runtime(
        overview,
        adb=FakeAdb([np.ones_like(overview)]),
        ocr=FakeOcr([("上一张别的卡", "旧详情")]),
        card_details=_details(),
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for({(0, 0): _match("occupied")}),
        **_fast_detail_poll(),
    )
    slot = result.slots[0]

    assert slot["label"] == "unknown"
    assert slot["is_ground_truth"] is False
    assert "mismatch" in slot["detail_ocr"]["reason"]
    assert slot["detail_ocr"]["attempt_count"] == 2
    assert len(slot["detail_ocr"]["attempts"]) == 2
    assert {"icon", "name", "detail", "full"} <= set(slot["review_artifacts"])
    assert all(Path(path).exists() for path in slot["review_artifacts"].values())


def test_stale_panel_retries_tap_once_then_confirms(tmp_path: Path) -> None:
    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    adb = FakeAdb([np.ones_like(overview)])
    result = capture_cards_runtime(
        overview,
        adb=adb,
        ocr=FakeOcr(
            [
                ("上一张别的卡", "旧详情"),
                ("上一张别的卡", "旧详情"),
                ("同名卡", "造成蓝色伤害"),
                ("同名卡", "造成蓝色伤害"),
            ]
        ),
        card_details=_details(),
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for({(0, 0): _match("occupied")}),
        **_fast_detail_poll(),
    )

    slot = result.slots[0]
    assert len(adb.taps) == 2
    assert slot["label"] == "蓝·同名卡"
    assert slot["detail_ocr"]["attempt_count"] == 2
    assert slot["detail_ocr"]["attempts"][0]["reason"] == "name-mismatch-or-stale"
    assert slot["detail_ocr"]["attempts"][1]["reason"] == "confirmed-detail"


def test_ambiguous_in_group_name_does_not_trigger_stale_retry(
    tmp_path: Path,
) -> None:
    candidates = ("蓝·非常非常相似卡牌甲", "蓝·非常非常相似卡牌乙")
    details = CardDetails(
        by_color={
            "白": {},
            "蓝": {candidate: "" for candidate in candidates},
            "黄": {},
            "彩": {},
        },
        same_template_candidates={"组合模板": candidates},
        asset_candidates={"组合模板": candidates},
    )
    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    adb = FakeAdb([np.ones_like(overview)])
    result = capture_cards_runtime(
        overview,
        adb=adb,
        ocr=FakeOcr([("非常非常相似卡牌丙", "")]),
        card_details=details,
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for({(0, 0): _match("occupied")}),
        **_fast_detail_poll(),
    )

    assert len(adb.taps) == 1
    assert result.slots[0]["detail_ocr"]["reason"] == "name-ambiguous"
    assert result.slots[0]["detail_ocr"]["attempt_count"] == 1


def test_old_detail_first_then_updated_panel_is_the_only_gt(
    tmp_path: Path,
) -> None:
    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    result = capture_cards_runtime(
        overview,
        adb=FakeAdb(
            [np.ones_like(overview), np.ones_like(overview), np.ones_like(overview)]
        ),
        ocr=FakeOcr(
            [
                ("同名卡", "获得黄色护盾"),
                ("同名卡", "造成蓝色伤害"),
                ("同名卡", "造成蓝色伤害"),
            ]
        ),
        card_details=_details(),
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for({(0, 0): _match("occupied")}),
        **_fast_detail_poll(),
    )
    slot = result.slots[0]

    assert slot["label"] == "蓝·同名卡"
    assert slot["is_ground_truth"] is True
    observations = slot["detail_ocr"]["stability"]["observations"]
    assert observations[0]["detail_text"] == "获得黄色护盾"
    assert observations[-1]["detail_text"] == "造成蓝色伤害"
    assert slot["detail_ocr"]["stability"]["polls"] == 3


def test_transition_gets_immediate_followup_sample(tmp_path: Path) -> None:
    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    sleeps: list[float] = []
    result = capture_cards_runtime(
        overview,
        adb=FakeAdb([np.ones_like(overview)]),
        ocr=FakeOcr(
            [
                ("同名卡", "获得黄色护盾"),
                ("同名卡", "造成蓝色伤害"),
                ("同名卡", "造成蓝色伤害"),
            ]
        ),
        card_details=_details(),
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for({(0, 0): _match("occupied")}),
        detail_settle_seconds=0.0,
        detail_poll_interval_seconds=0.2,
        detail_timeout_seconds=2.5,
        sleep=sleeps.append,
        monotonic=lambda: 0.0,
    )

    assert result.slots[0]["label"] == "蓝·同名卡"
    assert result.slots[0]["detail_ocr"]["stability"]["polls"] == 3
    assert sleeps == [0.2]


def test_detail_stability_timeout_is_unresolved(tmp_path: Path) -> None:
    class AlternatingOcr:
        def __init__(self) -> None:
            self.calls = 0

        def ocr_text(self, _img: np.ndarray, _box) -> str:
            poll = self.calls // 2
            self.calls += 1
            if self.calls % 2:
                return "同名卡"
            return "造成蓝色伤害" if poll % 2 == 0 else "获得黄色护盾"

    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    result = capture_cards_runtime(
        overview,
        adb=FakeAdb([np.ones_like(overview)]),
        ocr=AlternatingOcr(),
        card_details=_details(),
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for({(0, 0): _match("occupied")}),
        detail_settle_seconds=0.0,
        detail_poll_interval_seconds=0.0,
        detail_timeout_seconds=0.002,
        sleep=lambda _seconds: None,
    )
    slot = result.slots[0]

    assert slot["label"] == "unknown"
    assert slot["is_ground_truth"] is False
    assert slot["detail_ocr"]["reason"] == "detail-stability-timeout"
    assert slot["detail_ocr"]["stability"]["stable"] is False


def test_sidecar_score_is_clamped_and_raw_score_is_auditable(
    tmp_path: Path,
) -> None:
    overview = np.zeros((1600, 2160, 3), dtype=np.uint8)
    matches = {
        (0, 0): _match("occupied", stem="普通模板", label="蓝·普通", score=-0.02),
        (0, 1): _match("occupied", stem="普通模板", label="黄·普通", score=1.03),
    }
    result = capture_cards_runtime(
        overview,
        adb=FakeAdb([np.ones_like(overview)]),
        ocr=FakeOcr([("", "")]),
        card_details=_details(),
        template_sigs={},
        review_dir=tmp_path / "review",
        detector=_detector_for(matches),
        **_fast_detail_poll(),
    )

    assert result.slots[0]["score"] == 0.0
    assert result.slots[0]["template_debug"]["raw_score"] == -0.02
    assert result.slots[1]["score"] == 1.0
    assert result.slots[1]["template_debug"]["raw_score"] == 1.03


def test_cross_color_same_name_requires_unique_detail() -> None:
    resolved, debug = resolve_detail_candidate(
        "同名卡",
        "获得黄色护盾",
        ("蓝·同名卡", "黄·同名卡"),
        _details(),
    )
    assert resolved == "黄·同名卡"
    assert debug["reason"] == "confirmed-detail"

    unresolved, empty_debug = resolve_detail_candidate(
        "同名卡",
        "",
        ("蓝·同名卡", "黄·同名卡"),
        _details(),
    )
    assert unresolved is None
    assert empty_debug["reason"] == "detail-ocr-empty"


def test_empty_name_uniquely_resolves_from_workbook_detail() -> None:
    resolved, debug = resolve_detail_candidate(
        "",
        "获得黄色护盾",
        ("蓝·同名卡", "黄·同名卡"),
        _details(),
    )

    assert resolved == "黄·同名卡"
    assert debug["reason"] == "confirmed-detail"
    assert debug["name_match"] == "empty"


@pytest.mark.parametrize(
    "observed,candidate",
    [
        ("开揽", "蓝·开攒"),
        ("装备共鸣法rO", "蓝·装备共鸣法pro"),
        ("装备共鸣法ro", "蓝·装备共鸣法pro"),
        ("装备共鸣法pr0", "蓝·装备共鸣法pro"),
    ],
)
def test_candidate_local_name_repairs(observed: str, candidate: str) -> None:
    details = CardDetails(
        by_color={"白": {}, "蓝": {candidate: ""}, "黄": {}, "彩": {}},
        same_template_candidates={"模板": (candidate, "蓝·其他卡")},
        asset_candidates={"模板": (candidate, "蓝·其他卡")},
    )
    resolved, debug = resolve_detail_candidate(
        observed,
        "",
        (candidate, "蓝·其他卡"),
        details,
    )

    assert resolved == candidate
    assert debug["reason"] == "confirmed-name"
    assert debug["name_text"] == observed
    assert debug["name_normalized"] == observed
    assert debug["name_normalized_for_match"] == card_body(candidate)
    assert (
        debug["name_normalized_for_match_by_candidate"][candidate]
        == card_body(candidate)
    )


def test_name_repairs_are_not_global_aliases() -> None:
    resolved, debug = resolve_detail_candidate(
        "开揽",
        "",
        ("蓝·开蓝", "蓝·其他卡"),
        _details(),
    )

    assert resolved is None
    assert debug["name_normalized_for_match"] == "开揽"
    assert debug["name_normalized_for_match_by_candidate"]["蓝·开蓝"] == "开揽"


@pytest.mark.parametrize(
    "stem,name_text,expected",
    [
        ("黄·吸吸宝pro快速成型", "快速成型", "黄·快速成型"),
        ("黄·摇盒高手", "摇盒高手", "黄·摇盒高手"),
        ("蓝·重质拍档支援", "最佳拍档", "蓝·最佳拍档"),
        ("蓝·重质拍档支援", "重质也重量pro", "蓝·重质也重量pro"),
        ("蓝·重质拍档支援", "最强支援", "蓝·最强支援"),
        ("蓝·重质拍档支援", "重质也重量pro", "蓝·重质也重量pro"),
        ("黄·吸吸宝pro快速成型", "吸吸宝pro", "黄·吸吸宝pro"),
        ("蓝·一起刷刷刷+天降揪揪pro", "天降揪揪pro", "蓝·天降揪揪pro"),
        ("黄·巨神兵", "迅迅迅捷双剑", "黄·迅迅迅捷双剑"),
        ("黄·终极反击", "终极反击", "黄·终极反击"),
        ("黄·终极反击", "学术反击", "黄·学术反击"),
    ],
)
def test_rank6_0727_detail_observations_resolve(
    stem: str,
    name_text: str,
    expected: str,
) -> None:
    details = load_card_details()
    resolved, debug = resolve_detail_candidate(
        name_text,
        "",
        details.same_template_candidates[stem],
        details,
    )
    assert resolved == expected
    assert debug["reason"] == "confirmed-name"


def _empty_slots() -> list[dict]:
    return [
        {
            "player": row + 1,
            "row_index": row,
            "slot_index": slot,
            "presence": "empty",
            "label": None,
            "score": None,
            "source": None,
            "is_ground_truth": False,
        }
        for row in range(8)
        for slot in range(3)
    ]


def test_detail_failure_still_saves_overview_sidecar_and_state(
    tmp_path: Path,
) -> None:
    output = tmp_path / "shots"
    bot = DailyCaptureBot(CaptureConfig(output_dir=output))
    bot.run_id = "test-run"
    bot.run_dir = output / "runs" / bot.run_id
    bot.card_details = _details()
    bot.card_template_sigs = {}

    overview = np.full((1600, 2160, 3), 17, dtype=np.uint8)
    detail = np.full((1600, 2160, 3), 231, dtype=np.uint8)
    ok, overview_encoded = cv2.imencode(".png", overview)
    assert ok
    ok, detail_encoded = cv2.imencode(".png", detail)
    assert ok
    overview_bytes = overview_encoded.tobytes()
    detail_bytes = detail_encoded.tobytes()
    bot.adb._last_png = overview_bytes
    bot.adb._last_bgr = overview
    bot.adb.tap = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    bot.adb.capture_bgr = lambda: overview.copy()  # type: ignore[method-assign]
    bot.screen.wait_until = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: SCREEN_MATCH_SOLO_RANK
    )
    bot.ocr.ocr_text = lambda *_args, **_kwargs: "10:00"  # type: ignore[method-assign]
    bot.return_from_match = lambda *_args, **_kwargs: None  # type: ignore[method-assign]

    def failed_cards(*_args, review_dir: Path, **_kwargs) -> CardCaptureResult:
        bot.adb._last_png = detail_bytes  # simulate detail screenshots polluting cache
        review_dir.mkdir(parents=True, exist_ok=True)
        artifact = review_dir / "p1_s1_icon.png"
        artifact.write_bytes(b"review")
        slots = _empty_slots()
        slots[0] = {
            "player": 1,
            "row_index": 0,
            "slot_index": 0,
            "presence": "occupied",
            "label": "unknown",
            "score": 0.91,
            "source": "template",
            "is_ground_truth": False,
            "detail_ocr": {"reason": "name-mismatch-or-stale"},
            "review_artifacts": {"icon": str(artifact)},
        }
        return CardCaptureResult(
            slots=slots,
            summary={
                "template": 0,
                "detail": 0,
                "empty": 23,
                "uncertain": 0,
                "unresolved": 1,
                "total_slots": 24,
                "elapsed_ms": 1.0,
            },
            review_artifacts={
                "directory": str(review_dir),
                "slots": {"p1_s1": {"icon": str(artifact)}},
            },
        )

    entry = MatchEntry(
        normalized_datetime="07-27 12:00",
        tap_y=600,
        duo_peak_y=580,
        time_y=620,
        dedup_key="rank|entry",
    )
    with (
        patch(
            "scripts.capture_daily_screenshots.capture_cards_runtime",
            side_effect=failed_cards,
        ),
        patch(
            "scripts.capture_daily_screenshots.make_mumu_filename",
            return_value="overview.png",
        ),
    ):
        status = bot.capture_match_screenshot(1, entry)

    assert status == "saved"
    png = output / "overview.png"
    assert png.read_bytes() == overview_bytes
    sidecar = load_card_sidecar(png)
    assert len(sidecar["slots"]) == 24
    assert sidecar["slots"][0]["label"] == "unknown"
    assert sidecar["slots"][0]["review_artifacts"]
    record = bot.capture_state.get_rank_record(1)
    assert record["saved_paths"] == [str(png)]
    assert record["sidecar_paths"] == [str(output / "overview.cards.json")]
    state_payload = json.loads(bot.state_path.read_text(encoding="utf-8"))
    assert state_payload["ranks"]["1"]["sidecar_paths"] == record["sidecar_paths"]


def test_sidecar_write_failure_leaves_no_new_png_or_state(
    tmp_path: Path,
) -> None:
    output = tmp_path / "shots"
    bot = DailyCaptureBot(CaptureConfig(output_dir=output))
    bot.run_id = "test-run"
    bot.run_dir = output / "runs" / bot.run_id
    bot.card_details = _details()
    bot.card_template_sigs = {}
    overview = np.full((1600, 2160, 3), 17, dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", overview)
    assert ok
    bot.adb._last_png = encoded.tobytes()
    bot.adb._last_bgr = overview
    bot.adb.tap = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    bot.adb.capture_bgr = lambda: overview.copy()  # type: ignore[method-assign]
    bot.screen.wait_until = (  # type: ignore[method-assign]
        lambda *_args, **_kwargs: SCREEN_MATCH_SOLO_RANK
    )
    bot.ocr.ocr_text = lambda *_args, **_kwargs: "10:00"  # type: ignore[method-assign]
    bot.return_from_match = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    card_result = CardCaptureResult(
        slots=_empty_slots(),
        summary={
            "template": 0,
            "detail": 0,
            "empty": 24,
            "uncertain": 0,
            "unresolved": 0,
            "total_slots": 24,
            "elapsed_ms": 1.0,
        },
        review_artifacts={},
    )
    entry = MatchEntry(
        normalized_datetime="07-27 12:00",
        tap_y=600,
        duo_peak_y=580,
        time_y=620,
        dedup_key="rank|entry",
    )

    with (
        patch(
            "scripts.capture_daily_screenshots.capture_cards_runtime",
            return_value=card_result,
        ),
        patch(
            "scripts.capture_daily_screenshots.make_mumu_filename",
            return_value="pair-failure.png",
        ),
        patch(
            "scripts.capture_daily_screenshots.save_card_sidecar",
            side_effect=OSError("sidecar write failed"),
        ),
    ):
        status = bot.capture_match_screenshot(1, entry)

    assert status == "skipped"
    assert not (output / "pair-failure.png").exists()
    assert not (output / "pair-failure.cards.json").exists()
    assert "1" not in bot.capture_state.ranks
    assert not bot.state_path.exists()
    assert not list(output.glob(".pair-failure.capture-*"))


def test_workbook_validation_happens_before_any_adb_operation(
    tmp_path: Path,
) -> None:
    bot = DailyCaptureBot(
        CaptureConfig(
            output_dir=tmp_path / "shots",
            card_details_workbook=tmp_path / "bad.xlsx",
            auto_connect=True,
        )
    )
    adb_called: list[str] = []
    bot.adb.connect = lambda *_args, **_kwargs: adb_called.append("connect")  # type: ignore[method-assign]
    bot.adb.check_device = lambda *_args, **_kwargs: adb_called.append("check")  # type: ignore[method-assign]
    bot.adb.tap = lambda *_args, **_kwargs: adb_called.append("tap")  # type: ignore[method-assign]

    with patch(
        "scripts.capture_daily_screenshots.load_card_details",
        side_effect=CardDetailsValidationError("invalid workbook"),
    ):
        with pytest.raises(CardDetailsValidationError, match="invalid workbook"):
            bot.run()

    assert adb_called == []
