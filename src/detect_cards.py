# -*- coding: utf-8 -*-
"""Card icon detection via shape + color combined matching."""

from __future__ import annotations

from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from src.layout import (
    CARD_TEMPLATE_DIR,
    NUM_CARDS,
    NUM_PLAYERS,
    crop_roi,
    card_roi,
    roi_valid,
)

SHAPE_WEIGHT = 0.55
COLOR_WEIGHT = 0.20
CHROMA_WEIGHT = 0.25
SHAPE_CLUSTER_THRESH = 0.8

DETECTION_PARAMS = {
    "threshold": 0.75,
    "min_gap": 0.08,
    "padding": 8,
    "margin_ratio": 0.1,
}


def prepare_card_icon(
    roi: np.ndarray, bg_color: tuple[int, int, int] = (255, 255, 255), k: int = 2
) -> tuple[np.ndarray, np.ndarray]:
    h, w = roi.shape[:2]
    pixels = roi.reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(
        pixels, k, None, criteria, 10, cv2.KMEANS_PP_CENTERS
    )
    labels_2d = labels.reshape(h, w)
    border = np.zeros((h, w), dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    bg_label = Counter(labels_2d[border].tolist()).most_common(1)[0][0]

    icon = np.empty_like(roi)
    icon[:] = bg_color
    fg_mask = np.zeros((h, w), dtype=bool)
    for c in range(k):
        if c == bg_label:
            continue
        icon[labels_2d == c] = centers[c].astype(np.uint8)
        fg_mask |= labels_2d == c
    return icon, fg_mask


def crop_center(img: np.ndarray, margin_ratio: float = 0.1) -> np.ndarray:
    h, w = img.shape[:2]
    mh, mw = int(h * margin_ratio), int(w * margin_ratio)
    return img[mh : h - mh, mw : w - mw]


def _template_match_score(search: np.ndarray, template: np.ndarray) -> float:
    th, tw = template.shape[:2]
    if search.shape[0] < th or search.shape[1] < tw:
        return 0.0
    res = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    return float(res.max())


def shape_score(
    roi_icon: np.ndarray,
    tmpl_icon: np.ndarray,
    padding: int = 8,
    margin_ratio: float = 0.1,
    *,
    tmpl_shape_gray: np.ndarray | None = None,
) -> float:
    roi_gray = crop_center(cv2.cvtColor(roi_icon, cv2.COLOR_BGR2GRAY), margin_ratio)
    if tmpl_shape_gray is None:
        tmpl_gray = crop_center(cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2GRAY), margin_ratio)
    else:
        tmpl_gray = tmpl_shape_gray
    search = cv2.copyMakeBorder(
        roi_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE
    )
    return _template_match_score(search, tmpl_gray)


def chroma_score(
    roi_icon: np.ndarray,
    tmpl_icon: np.ndarray,
    padding: int = 8,
    margin_ratio: float = 0.1,
    *,
    tmpl_chroma: np.ndarray | None = None,
) -> float:
    if tmpl_chroma is None:
        tmpl_lab = cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2LAB)
        tmpl_ch = (
            crop_center(tmpl_lab[:, :, 1], margin_ratio).astype(np.float32)
            + crop_center(tmpl_lab[:, :, 2], margin_ratio).astype(np.float32)
        ) / 2
    else:
        tmpl_ch = tmpl_chroma
    roi_lab = cv2.cvtColor(roi_icon, cv2.COLOR_BGR2LAB)
    roi_ch = (
        crop_center(roi_lab[:, :, 1], margin_ratio).astype(np.float32)
        + crop_center(roi_lab[:, :, 2], margin_ratio).astype(np.float32)
    ) / 2
    search = cv2.copyMakeBorder(
        roi_ch, padding, padding, padding, padding, cv2.BORDER_REPLICATE
    )
    return _template_match_score(search, tmpl_ch)


def color_score(
    roi_icon: np.ndarray,
    tmpl_icon: np.ndarray,
    roi_fg: np.ndarray,
    tmpl_fg: np.ndarray,
) -> float:
    roi_lab = cv2.cvtColor(roi_icon, cv2.COLOR_BGR2LAB)
    tmpl_lab = cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2LAB)
    roi_hsv = cv2.cvtColor(roi_icon, cv2.COLOR_BGR2HSV)
    tmpl_hsv = cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2HSV)

    lab_dist = np.linalg.norm(
        roi_lab[roi_fg][:, 1:3].mean(axis=0) - tmpl_lab[tmpl_fg][:, 1:3].mean(axis=0)
    )
    lab_sim = max(0.0, 1.0 - lab_dist / 180.0)

    sat_dist = abs(
        roi_hsv[roi_fg][:, 1].mean() - tmpl_hsv[tmpl_fg][:, 1].mean()
    )
    sat_sim = max(0.0, 1.0 - sat_dist / 255.0)
    return lab_sim * 0.6 + sat_sim * 0.4


def combined_score(
    roi_icon: np.ndarray,
    tmpl_icon: np.ndarray,
    roi_fg: np.ndarray,
    tmpl_fg: np.ndarray,
    padding: int = 8,
    margin_ratio: float = 0.1,
    *,
    tmpl_shape_gray: np.ndarray | None = None,
    tmpl_chroma: np.ndarray | None = None,
) -> float:
    sh = shape_score(
        roi_icon,
        tmpl_icon,
        padding,
        margin_ratio,
        tmpl_shape_gray=tmpl_shape_gray,
    )
    col = color_score(roi_icon, tmpl_icon, roi_fg, tmpl_fg)
    chroma = chroma_score(
        roi_icon,
        tmpl_icon,
        padding,
        margin_ratio,
        tmpl_chroma=tmpl_chroma,
    )
    return SHAPE_WEIGHT * sh + COLOR_WEIGHT * col + CHROMA_WEIGHT * chroma


def adaptive_min_gap(best_score: float, min_gap: float = 0.08) -> float:
    if best_score > 0.9:
        return 0.0
    return min_gap


def _enrich_card_template_sig(icon: np.ndarray, fg: np.ndarray) -> dict:
    margin_ratio = DETECTION_PARAMS["margin_ratio"]
    lab = cv2.cvtColor(icon, cv2.COLOR_BGR2LAB)
    return {
        "icon": icon,
        "fg": fg,
        "shape_gray": crop_center(cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY), margin_ratio),
        "chroma": (
            crop_center(lab[:, :, 1], margin_ratio).astype(np.float32)
            + crop_center(lab[:, :, 2], margin_ratio).astype(np.float32)
        ) / 2,
    }


def load_template_sigs(template_dir: Path | None = None) -> dict[str, dict]:
    directory = template_dir or CARD_TEMPLATE_DIR
    sigs = {}
    for path in directory.glob("*.jpg"):
        if path.name.startswith("player"):
            continue
        buf = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        timg = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if timg is not None:
            icon, fg = prepare_card_icon(timg)
            sigs[path.name] = _enrich_card_template_sig(icon, fg)
    return sigs


def match_card_roi(
    roi: np.ndarray,
    template_sigs: dict[str, dict],
    threshold: float = 0.75,
    min_gap: float = 0.08,
    padding: int = 8,
    margin_ratio: float = 0.1,
) -> tuple[str, float]:
    roi_icon, roi_fg = prepare_card_icon(roi)
    details = []
    for name, sig in template_sigs.items():
        sh = shape_score(
            roi_icon,
            sig["icon"],
            padding,
            margin_ratio,
            tmpl_shape_gray=sig.get("shape_gray"),
        )
        col = color_score(roi_icon, sig["icon"], roi_fg, sig["fg"])
        combined = combined_score(
            roi_icon,
            sig["icon"],
            roi_fg,
            sig["fg"],
            padding,
            margin_ratio,
            tmpl_shape_gray=sig.get("shape_gray"),
            tmpl_chroma=sig.get("chroma"),
        )
        details.append(
            {"name": name, "combined": combined, "shape": sh, "color": col}
        )
    if not details:
        return "unknown", 0.0

    details.sort(key=lambda d: d["combined"], reverse=True)
    high_shape = [d for d in details if d["shape"] >= SHAPE_CLUSTER_THRESH]
    if len(high_shape) >= 2:
        color_ranked = sorted(high_shape, key=lambda d: d["color"], reverse=True)
        winner = color_ranked[0]
        second_color = color_ranked[1]["color"]
        if winner["color"] >= 0.85 and winner["color"] - second_color >= 0.03:
            return winner["name"].replace(".jpg", ""), winner["combined"]

    winner = details[0]
    second_score = details[1]["combined"] if len(details) > 1 else 0.0
    gap_threshold = adaptive_min_gap(winner["combined"], min_gap)
    if winner["combined"] >= threshold and (
        winner["combined"] - second_score
    ) >= gap_threshold:
        return winner["name"].replace(".jpg", ""), winner["combined"]
    return "unknown", winner["combined"]


def detect_cards(
    img: np.ndarray,
    template_sigs: dict[str, dict] | None = None,
) -> list[dict]:
    """Detect cards for all players.

    Returns list of {player, row_index, cards: [{slot_index, label, score}]}.
    """
    if template_sigs is None:
        template_sigs = load_template_sigs()

    params = DETECTION_PARAMS
    results = []
    for j in range(NUM_PLAYERS):
        cards = []
        for i in range(NUM_CARDS):
            box = card_roi(j, i)
            roi = crop_roi(img, box)
            if not roi_valid(roi, box):
                cards.append({"slot_index": i, "label": "unknown", "score": 0.0})
                continue
            label, score = match_card_roi(
                roi,
                template_sigs,
                params["threshold"],
                params["min_gap"],
                params["padding"],
                params["margin_ratio"],
            )
            cards.append({"slot_index": i, "label": label, "score": score})
        results.append({"player": j + 1, "row_index": j, "cards": cards})
    return results
