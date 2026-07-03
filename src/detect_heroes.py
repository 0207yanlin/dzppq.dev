# -*- coding: utf-8 -*-
"""Hero portrait detection via template matching."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from src.layout import (
    HERO_TEMPLATE_DIR,
    NUM_HEROES,
    NUM_PLAYERS,
    crop_roi,
    hero_roi,
    roi_valid,
)

DETECTION_PARAMS = {
    "threshold": 0.75,
    "min_gap": 0.08,
    "padding": 8,
    "margin_ratio": 0.1,
    "empty_slot_std_threshold": 10,
    "empty_slot_edge_threshold": 0.05,
}


def load_templates(template_dir: Path | None = None) -> dict[str, np.ndarray]:
    directory = template_dir or HERO_TEMPLATE_DIR
    templates = {}
    for path in directory.glob("*.jpg"):
        if path.name.startswith("player"):
            continue
        buf = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is not None:
            templates[path.name] = img
    return templates


def crop_center(gray: np.ndarray, margin_ratio: float = 0.1) -> np.ndarray:
    h, w = gray.shape[:2]
    mh, mw = int(h * margin_ratio), int(w * margin_ratio)
    return gray[mh : h - mh, mw : w - mw]


def is_empty_slot(
    roi: np.ndarray,
    std_threshold: float = 10,
    edge_threshold: float = 0.05,
) -> bool:
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    return gray.std() < std_threshold or edges.mean() / 255 < edge_threshold


def build_hero_template_cache(
    templates: dict[str, np.ndarray],
    margin_ratio: float = 0.1,
) -> dict[str, np.ndarray]:
    return {
        name: crop_center(cv2.cvtColor(timg, cv2.COLOR_BGR2GRAY), margin_ratio)
        for name, timg in templates.items()
    }


def match_roi_to_template(
    roi: np.ndarray,
    templates: dict[str, np.ndarray],
    threshold: float = 0.75,
    min_gap: float = 0.08,
    padding: int = 8,
    margin_ratio: float = 0.1,
    template_gray_cache: dict[str, np.ndarray] | None = None,
) -> tuple[str | None, float]:
    roi_gray = crop_center(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), margin_ratio)
    search = cv2.copyMakeBorder(
        roi_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE
    )
    scores = []
    for name, timg in templates.items():
        if template_gray_cache is not None:
            temp_gray = template_gray_cache[name]
        else:
            temp_gray = crop_center(cv2.cvtColor(timg, cv2.COLOR_BGR2GRAY), margin_ratio)
        th, tw = temp_gray.shape
        if search.shape[0] < th or search.shape[1] < tw:
            continue
        res = cv2.matchTemplate(search, temp_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        scores.append((max_val, name))
    scores.sort(reverse=True)
    if not scores:
        return None, 0.0
    best_score, best_name = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    if best_score >= threshold and (best_score - second_score) >= min_gap:
        return best_name, float(best_score)
    return None, float(best_score)


def detect_lineups(
    img: np.ndarray,
    templates: dict[str, np.ndarray] | None = None,
    template_gray_cache: dict[str, np.ndarray] | None = None,
) -> list[dict]:
    """Detect hero lineups for all players.

    Returns list of {player, heroes: [{slot_index, label, score}]}.
    """
    if templates is None:
        templates = load_templates()
    if template_gray_cache is None:
        template_gray_cache = build_hero_template_cache(
            templates,
            DETECTION_PARAMS["margin_ratio"],
        )

    params = DETECTION_PARAMS
    lineups = []
    for j in range(NUM_PLAYERS):
        heroes = []
        for i in range(NUM_HEROES):
            box = hero_roi(j, i)
            roi = crop_roi(img, box)
            if not roi_valid(roi, box):
                break
            if is_empty_slot(
                roi,
                params["empty_slot_std_threshold"],
                params["empty_slot_edge_threshold"],
            ):
                break
            tmp_name, score = match_roi_to_template(
                roi,
                templates,
                params["threshold"],
                params["min_gap"],
                params["padding"],
                params["margin_ratio"],
                template_gray_cache=template_gray_cache,
            )
            if tmp_name is not None:
                heroes.append(
                    {
                        "slot_index": i,
                        "label": tmp_name.replace(".jpg", ""),
                        "score": score,
                    }
                )
            else:
                break
        lineups.append({"player": j + 1, "row_index": j, "heroes": heroes})
    return lineups
