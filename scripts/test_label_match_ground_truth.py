# -*- coding: utf-8 -*-
"""Unit tests for label_match_ground_truth helpers."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.label_match_ground_truth import (  # noqa: E402
    PredictionResult,
    apply_prediction_results,
    run_parallel_predictions,
)


def _fake_predict(_ctx, img_path: Path, **_kwargs) -> PredictionResult:
    return PredictionResult(
        img_path=img_path,
        entry={"path": img_path.name, "players": []},
        summary=f"summary:{img_path.name}",
    )


def test_run_parallel_predictions_preserves_order(monkeypatch) -> None:
    monkeypatch.setattr(
        "scripts.label_match_ground_truth.predict_screenshot_entry",
        _fake_predict,
    )
    paths = [Path(f"img_{index}.png") for index in range(5)]

    serial = run_parallel_predictions(paths, None, workers=1)
    parallel = run_parallel_predictions(paths, None, workers=4)

    assert [result.img_path.name for result in serial] == [path.name for path in paths]
    assert [result.img_path.name for result in parallel] == [path.name for path in paths]
    assert len(serial) == len(parallel) == 5


def test_run_parallel_predictions_raises_on_worker_error(monkeypatch) -> None:
    def fake_predict(_ctx, img_path: Path, **_kwargs) -> PredictionResult:
        if img_path.name == "bad.png":
            return PredictionResult(img_path=img_path, error="boom")
        return PredictionResult(
            img_path=img_path,
            entry={"path": img_path.name},
            summary="ok",
        )

    monkeypatch.setattr(
        "scripts.label_match_ground_truth.predict_screenshot_entry",
        fake_predict,
    )
    paths = [Path("good.png"), Path("bad.png"), Path("also_good.png")]

    with pytest.raises(RuntimeError, match="bad.png: boom"):
        run_parallel_predictions(paths, None, workers=4)


def test_apply_prediction_results_writes_new_entries_only() -> None:
    gt_data = {"screenshots": {}}
    results = [
        PredictionResult(
            Path("a.png"),
            entry={"path": "a.png", "players": []},
            summary="summary:a.png",
        ),
        PredictionResult(
            Path("b.png"),
            entry={"path": "b.png", "players": []},
            summary="summary:b.png",
            reused_cached=True,
        ),
    ]

    apply_prediction_results(gt_data, results)

    assert "a.png" in gt_data["screenshots"]
    assert gt_data["screenshots"]["a.png"]["path"] == "a.png"
    assert "b.png" not in gt_data["screenshots"]
