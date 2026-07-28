# -*- coding: utf-8 -*-
"""Independent contract tests for card sidecars and GT integration."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.label_match_ground_truth import (  # noqa: E402
    build_parser,
    load_prediction_context,
)
from src.card_sidecar import (  # noqa: E402
    CardSidecarError,
    card_sidecar_path,
    card_sidecar_to_cards_by_player,
    create_card_sidecar,
    fingerprint_card_sidecar,
    load_card_sidecar,
    save_card_sidecar,
)
from src.match_ground_truth import (  # noqa: E402
    PredictionContext,
    merge_prediction,
    prediction_cache_valid,
)


def _slots() -> list[dict]:
    slots = []
    for row_index in range(8):
        for slot_index in range(3):
            slots.append(
                {
                    "player": row_index + 1,
                    "row_index": row_index,
                    "slot_index": slot_index,
                    "presence": "empty",
                    "label": None,
                    "score": None,
                    "source": None,
                    "is_ground_truth": False,
                }
            )
    return slots


def _sidecar(png_path: Path) -> dict:
    slots = _slots()
    slots[0].update(
        {
            "presence": "occupied",
            "label": "蓝·开攒",
            "score": 0.97,
            "source": "template",
            "template_debug": {"top1": "蓝·开攒"},
        }
    )
    slots[1].update(
        {
            "presence": "uncertain",
            "label": None,
            "score": 0.51,
            "source": "template",
            "detail_ocr": {"text": ""},
        }
    )
    slots[2].update(
        {
            "presence": "occupied",
            "label": "人工确认原名",
            "score": 1.0,
            "source": "detail_ocr",
            "is_ground_truth": True,
            "review_artifacts": {"crop": "review/p1-s3.png"},
        }
    )
    return create_card_sidecar(
        png_path,
        slots,
        capture_metadata={"batch": "0727"},
        summary={"occupied": 2, "uncertain": 1},
        review_artifacts={"directory": "review"},
    )


def _merge_inputs(cards_by_player: list[dict]) -> dict:
    return {
        "pair_info": {
            "pairs": [[1, 2], [3, 4], [5, 6], [7, 8]],
            "partner_by_player": {1: 2, 2: 1, 3: 4, 4: 3, 5: 6, 6: 5, 7: 8, 8: 7},
            "highlight_player": None,
        },
        "lineups": [{"heroes": []} for _ in range(8)],
        "stars_by_player": [[] for _ in range(8)],
        "equipment_counts": [["-"] * 9 for _ in range(8)],
        "equipment_item_preds": [[[] for _ in range(9)] for _ in range(8)],
        "hero_templates": {},
        "equipment_templates": [],
        "cards_by_player": cards_by_player,
    }


def test_sidecar_atomic_round_trip_path_and_fingerprint(tmp_path: Path) -> None:
    png = tmp_path / "match.001.png"
    png.write_bytes(b"png")
    data = _sidecar(png)

    destination = save_card_sidecar(png, data)

    assert destination == tmp_path / "match.001.cards.json"
    assert card_sidecar_path(png) == destination
    assert load_card_sidecar(png) == data
    assert fingerprint_card_sidecar(load_card_sidecar(png)) == fingerprint_card_sidecar(
        data
    )
    assert not list(tmp_path.glob("*.tmp"))


def test_sidecar_strictly_rejects_bad_grid_and_png_identity(tmp_path: Path) -> None:
    png = tmp_path / "match.png"
    png.write_bytes(b"png")
    data = _sidecar(png)

    duplicate = deepcopy(data)
    duplicate["slots"][1]["slot_index"] = 0
    with pytest.raises(CardSidecarError, match="duplicate card slot"):
        save_card_sidecar(png, duplicate)

    wrong_identity = deepcopy(data)
    wrong_identity["image"] = {"filename": "other.png", "stem": "other"}
    with pytest.raises(CardSidecarError, match="does not match"):
        save_card_sidecar(png, wrong_identity)

    extra = deepcopy(data)
    extra["slots"][0]["unversioned_debug"] = {}
    with pytest.raises(CardSidecarError, match="unsupported fields"):
        save_card_sidecar(png, extra)


def test_load_rejects_duplicate_json_keys(tmp_path: Path) -> None:
    png = tmp_path / "match.png"
    png.write_bytes(b"png")
    card_sidecar_path(png).write_text(
        '{"schema_version":1,"schema_version":1}', encoding="utf-8"
    )

    with pytest.raises(CardSidecarError, match="duplicate JSON key"):
        load_card_sidecar(png)


def test_adapter_skips_empty_and_keeps_uncertain_auditable(tmp_path: Path) -> None:
    data = _sidecar(tmp_path / "match.png")

    rows = card_sidecar_to_cards_by_player(data)

    assert len(rows) == 8
    assert len(rows[0]["cards"]) == 3
    assert rows[0]["cards"][1] == {
        "slot_index": 1,
        "label": "unknown",
        "score": 0.51,
        "presence": "uncertain",
        "is_ground_truth": False,
        "from_sidecar": True,
        "source": "template",
    }
    assert rows[1]["cards"] == []


def test_merge_uses_gt_verbatim_preserves_0727_sidecar_and_resolves_legacy(
    monkeypatch,
) -> None:
    cards = [
        {
            "player": row + 1,
            "row_index": row,
            "cards": [],
        }
        for row in range(8)
    ]
    cards[0]["cards"] = [
        {
            "slot_index": 0,
            "label": "必须保持原样",
            "score": 1.0,
            "source": "detail_ocr",
            "is_ground_truth": True,
            "from_sidecar": True,
        },
        {
            "slot_index": 1,
            "label": "蓝·开攒",
            "score": 0.9,
            "source": "template",
            "is_ground_truth": False,
            "from_sidecar": True,
        },
        {"slot_index": 2, "label": "legacy", "score": 0.8},
    ]
    monkeypatch.setattr(
        "src.match_ground_truth.resolve_card_label",
        lambda *_args, **_kwargs: "硬编码改写",
    )

    prediction = merge_prediction(
        np.zeros((1, 1, 3), dtype=np.uint8),
        match_path="screenshots.0727/example.png",
        **_merge_inputs(cards),
    )
    output = prediction["players"][0]["cards"]

    assert output == [
        {
            "slot_index": 0,
            "card_name": "必须保持原样",
            "score": 1.0,
            "source": "detail_ocr",
        },
        {
            "slot_index": 1,
            "card_name": "蓝·开攒",
            "score": 0.9,
            "source": "template",
        },
        {"slot_index": 2, "card_name": "硬编码改写", "score": 0.8},
    ]
    assert all(
        set(card) <= {"slot_index", "card_name", "score", "source"}
        for card in output
    )


def test_prediction_context_prefers_valid_sidecar_over_detect_cards(
    tmp_path: Path,
    monkeypatch,
) -> None:
    png = tmp_path / "match.png"
    png.write_bytes(b"png")
    save_card_sidecar(png, _sidecar(png))
    ctx = PredictionContext()
    ctx.hero_templates = {}
    ctx.equipment_templates = []
    ctx.predict_equipment_counts = lambda *_args: [
        [{"slot_index": slot, "label": "-", "score": 1.0} for slot in range(9)]
        for _ in range(8)
    ]
    monkeypatch.setattr(
        "src.match_ground_truth.detect_equipment_items",
        lambda *_args, **_kwargs: [[[] for _ in range(9)] for _ in range(8)],
    )
    monkeypatch.setattr(
        "src.match_ground_truth.detect_pairs",
        lambda _img: _merge_inputs([])["pair_info"],
    )
    monkeypatch.setattr(
        "src.match_ground_truth.detect_lineups",
        lambda *_args, **_kwargs: [{"heroes": []} for _ in range(8)],
    )
    monkeypatch.setattr(
        "src.match_ground_truth.detect_stars",
        lambda _img: [[] for _ in range(8)],
    )
    monkeypatch.setattr(
        "src.match_ground_truth.detect_cards",
        lambda *_args, **_kwargs: pytest.fail("offline detect_cards must be skipped"),
    )

    prediction = ctx.predict_screenshot(
        png, np.zeros((1, 1, 3), dtype=np.uint8)
    )

    assert [card["card_name"] for card in prediction["players"][0]["cards"]] == [
        "蓝·开攒大亨",
        "unknown",
        "人工确认原名",
    ]
    assert ctx.card_sigs is None


def test_sidecar_fingerprint_invalidates_only_unverified_cache(tmp_path: Path) -> None:
    png = tmp_path / "match.png"
    png.write_bytes(b"png")
    data = _sidecar(png)
    old_fingerprint = fingerprint_card_sidecar(data)
    entry = {
        "verified": False,
        "template_metadata": {"cards": {"count": 1}},
        "card_sidecar_fingerprint": old_fingerprint,
    }
    assert prediction_cache_valid(
        entry, {"cards": {"count": 1}}, old_fingerprint
    )

    changed = deepcopy(data)
    changed["slots"][0]["label"] = "人工修正"
    new_fingerprint = fingerprint_card_sidecar(changed)

    assert new_fingerprint != old_fingerprint
    assert not prediction_cache_valid(
        entry, {"cards": {"count": 1}}, new_fingerprint
    )
    entry["verified"] = True
    assert prediction_cache_valid(entry, {"changed": True}, new_fingerprint)


@pytest.mark.parametrize("command", ["predict", "label"])
def test_cli_subcommands_can_ignore_card_sidecar(command: str) -> None:
    args = build_parser().parse_args(
        [command, "match.png", "--ignore-card-sidecar"]
    )

    assert args.ignore_card_sidecar is True


def test_predict_ignore_card_sidecar_reaches_prediction_context(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured: dict = {}

    class FakeContext:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

        def initialize(self, screenshot_dir: Path) -> None:
            captured["screenshot_dir"] = screenshot_dir

    monkeypatch.setattr(
        "scripts.label_match_ground_truth.PredictionContext",
        FakeContext,
    )
    args = build_parser().parse_args(
        [
            "--screenshot-dir",
            str(tmp_path),
            "predict",
            "match.png",
            "--ignore-card-sidecar",
        ]
    )

    context = load_prediction_context(args)

    assert isinstance(context, FakeContext)
    assert captured["ignore_card_sidecar"] is True
    assert captured["screenshot_dir"] == tmp_path.resolve()
