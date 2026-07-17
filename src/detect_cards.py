# -*- coding: utf-8 -*-
"""Card template matching with shared scoring and disambiguation."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.card_rules import CARD_LABEL_ALIASES, normalize_card_label
from src.layout import (
    CARD_TEMPLATE_DIR,
    NUM_CARDS,
    NUM_PLAYERS,
    card_roi,
    crop_roi,
    roi_valid,
)

DETECTION_PARAMS = {
    "threshold": 0.74,
    "min_gap": 0.08,
    "padding": 4,
    "margin_ratio": 0.12,
}

SHAPE_WEIGHT = 0.55
COLOR_WEIGHT = 0.20
CHROMA_WEIGHT = 0.25
SHAPE_CLUSTER_THRESH = 0.8

UNKNOWN = "unknown"

# Per-group disambiguation for visually similar cards.
VISUAL_CARD_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "labels": frozenset({"白·最后的波纹", "蓝·波纹利己", "彩·卡牌宝袋"}),
        "strategies": ("shape_family_color_rescue", "known_family_low_gap_accept"),
    },
    {
        "labels": frozenset({"白·克隆技术", "蓝·克隆技术"}),
        "strategies": ("shape_family_color_rescue",),
    },
    {
        "labels": frozenset({"黄·超级卡包", "蓝·延时礼物"}),
        "strategies": ("shape_family_color_rescue",),
        "allow_lower_shape": True,
    },
    {
        "labels": frozenset({"彩·大富翁", "蓝·带不走"}),
        "strategies": ("shape_family_color_rescue",),
    },
    {
        "labels": frozenset({"白·满级玩家", "蓝·半步满级+满级玩家"}),
        "strategies": ("shape_family_color_rescue",),
    },
    {
        "labels": frozenset({"白·马太效应", "彩·马太效应max"}),
        "strategies": ("shape_family_color_rescue",),
    },
    {
        "labels": frozenset({"白·我来助你", "黄·我来助你pro"}),
        "strategies": ("shape_family_color_rescue",),
    },
    {
        "labels": frozenset({"黄·热传导", "彩·热传导pro"}),
        "strategies": ("shape_family_color_rescue",),
        "allow_near_threshold": True,
    },
    {
        "labels": frozenset({"彩·锦上添花pro", "蓝·刷宝专家"}),
        "strategies": ("shape_family_color_rescue", "known_family_low_gap_accept"),
    },
    {
        "labels": frozenset({"蓝·攻防联合", "蓝·友谊连接"}),
        "strategies": ("known_family_low_gap_accept",),
        "low_gap_variant": "narrow",
    },
    {
        "labels": frozenset({"白·蛋仔变变变", "彩·装备变变变"}),
        "strategies": ("shape_family_color_rescue",),
    },
    {
        "labels": frozenset({"白·打手", "蓝·打手"}),
        "strategies": ("shape_family_color_rescue",),
    },
)

_LABEL_TO_GROUP: dict[str, dict[str, Any]] = {}
for _group in VISUAL_CARD_GROUPS:
    for _label in _group["labels"]:
        _LABEL_TO_GROUP[_label] = _group


def normalize_template_label(name: str) -> str:
    """Normalize a template filename / label for matching output."""
    stem = name.replace(".jpg", "").replace(".jpeg", "").replace(".png", "")
    return normalize_card_label(CARD_LABEL_ALIASES.get(stem, stem))


def crop_center(img: np.ndarray, margin_ratio: float = 0.12) -> np.ndarray:
    h, w = img.shape[:2]
    mh, mw = int(h * margin_ratio), int(w * margin_ratio)
    return img[mh : h - mh, mw : w - mw]


def prepare_card_icon(
    roi: np.ndarray,
    bg_color: tuple[int, int, int] = (255, 255, 255),
    k: int = 2,
) -> tuple[np.ndarray, np.ndarray]:
    """Remove background via k-means; return (icon, fg_mask)."""
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
    for cluster in range(k):
        if cluster == bg_label:
            continue
        icon[labels_2d == cluster] = centers[cluster].astype(np.uint8)
        fg_mask |= labels_2d == cluster
    return icon, fg_mask


def _template_match_score(search: np.ndarray, template: np.ndarray) -> float:
    th, tw = template.shape[:2]
    if search.shape[0] < th or search.shape[1] < tw:
        return 0.0
    res = cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED)
    return float(res.max())


def shape_score(
    roi_icon: np.ndarray,
    tmpl_icon: np.ndarray,
    padding: int = 4,
    margin_ratio: float = 0.12,
    *,
    tmpl_shape_gray: np.ndarray | None = None,
) -> float:
    roi_gray = crop_center(cv2.cvtColor(roi_icon, cv2.COLOR_BGR2GRAY), margin_ratio)
    if tmpl_shape_gray is None:
        tmpl_gray = crop_center(cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2GRAY), margin_ratio)
    else:
        tmpl_gray = crop_center(tmpl_shape_gray, margin_ratio)
    search = cv2.copyMakeBorder(
        roi_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE
    )
    return _template_match_score(search, tmpl_gray)


def chroma_score(
    roi_icon: np.ndarray,
    tmpl_icon: np.ndarray,
    padding: int = 4,
    margin_ratio: float = 0.12,
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
        tmpl_ch = crop_center(tmpl_chroma, margin_ratio)

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

    if not roi_fg.any() or not tmpl_fg.any():
        return 0.0

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
    padding: int = 4,
    margin_ratio: float = 0.12,
    *,
    tmpl_shape_gray: np.ndarray | None = None,
    tmpl_chroma: np.ndarray | None = None,
) -> float:
    shape = shape_score(
        roi_icon,
        tmpl_icon,
        padding,
        margin_ratio,
        tmpl_shape_gray=tmpl_shape_gray,
    )
    color = color_score(roi_icon, tmpl_icon, roi_fg, tmpl_fg)
    chroma = chroma_score(
        roi_icon,
        tmpl_icon,
        padding,
        margin_ratio,
        tmpl_chroma=tmpl_chroma,
    )
    return SHAPE_WEIGHT * shape + COLOR_WEIGHT * color + CHROMA_WEIGHT * chroma


def adaptive_min_gap(best_score: float, min_gap: float = 0.08) -> float:
    """Relax gap requirement for very high-confidence combined scores."""
    if best_score > 0.9:
        return 0.0
    return min_gap


def load_template_sigs(template_dir: Path | None = None) -> dict[str, dict]:
    directory = template_dir or CARD_TEMPLATE_DIR
    sigs: dict[str, dict] = {}
    for path in sorted(directory.glob("*.jpg")):
        if path.name.startswith("player"):
            continue
        buf = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is None:
            continue
        icon, fg = prepare_card_icon(img)
        lab = cv2.cvtColor(icon, cv2.COLOR_BGR2LAB)
        chroma = (lab[:, :, 1].astype(np.float32) + lab[:, :, 2].astype(np.float32)) / 2
        sigs[path.name] = {
            "icon": icon,
            "fg": fg,
            "shape_gray": cv2.cvtColor(icon, cv2.COLOR_BGR2GRAY),
            "chroma": chroma,
        }
    return sigs


def _detail_label(item: dict) -> str:
    return normalize_template_label(item["name"])


def _group_for_label(label: str | None) -> dict[str, Any] | None:
    if not label:
        return None
    return _LABEL_TO_GROUP.get(normalize_template_label(label))


def _group_members(details: list[dict], group: dict[str, Any]) -> list[dict]:
    labels = group["labels"]
    return [item for item in details if _detail_label(item) in labels]


def _match_debug_base(
    *,
    threshold: float,
    min_gap: float,
    reject_reason: str = "no_templates",
) -> dict[str, Any]:
    return {
        "top1_label": None,
        "top1_score": 0.0,
        "top2_label": None,
        "top2_score": 0.0,
        "gap": 0.0,
        "threshold": threshold,
        "min_gap": min_gap,
        "gap_threshold": min_gap,
        "reject_reason": reject_reason,
    }


def _winner_debug(
    winner: dict,
    second: dict | None,
    *,
    threshold: float,
    min_gap: float,
    match_path: str,
    reject_reason: str = "accepted",
    gap_threshold: float | None = None,
) -> dict[str, Any]:
    top1_score = float(winner["combined"])
    top2_score = float(second["combined"]) if second is not None else 0.0
    gap = top1_score - top2_score
    debug: dict[str, Any] = {
        "top1_label": _detail_label(winner),
        "top1_score": top1_score,
        "top2_label": _detail_label(second) if second is not None else None,
        "top2_score": top2_score,
        "gap": gap,
        "threshold": threshold,
        "min_gap": min_gap,
        "gap_threshold": gap_threshold if gap_threshold is not None else min_gap,
        "reject_reason": reject_reason,
        "top1_shape": float(winner["shape"]),
        "top1_color": float(winner["color"]),
        "top1_chroma": float(winner.get("chroma", 0.0)),
        "match_path": match_path,
    }
    if second is not None:
        debug["top2_shape"] = float(second["shape"])
        debug["top2_color"] = float(second["color"])
        debug["top2_chroma"] = float(second.get("chroma", 0.0))
    return debug


@dataclass
class CardMatchDecision:
    label: str | None
    score: float
    debug: dict[str, Any] = field(default_factory=dict)


def build_match_details(
    roi_icon: np.ndarray,
    roi_fg: np.ndarray,
    template_sigs: dict,
    *,
    padding: int,
    margin_ratio: float,
) -> list[dict]:
    details: list[dict] = []
    for name, sig in template_sigs.items():
        shape = shape_score(
            roi_icon,
            sig["icon"],
            padding,
            margin_ratio,
            tmpl_shape_gray=sig.get("shape_gray"),
        )
        color = color_score(roi_icon, sig["icon"], roi_fg, sig["fg"])
        chroma = chroma_score(
            roi_icon,
            sig["icon"],
            padding,
            margin_ratio,
            tmpl_chroma=sig.get("chroma"),
        )
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
            {
                "name": name,
                "combined": combined,
                "shape": shape,
                "color": color,
                "chroma": chroma,
            }
        )
    details.sort(key=lambda item: item["combined"], reverse=True)
    return details


def _try_shape_cluster_color(
    details: list[dict],
    *,
    threshold: float,
    min_gap: float,
) -> CardMatchDecision | None:
    high_shape = [item for item in details if item["shape"] >= SHAPE_CLUSTER_THRESH]
    if len(high_shape) < 2:
        return None
    color_ranked = sorted(high_shape, key=lambda item: item["color"], reverse=True)
    winner = color_ranked[0]
    second = color_ranked[1]
    if winner["color"] < 0.85 or winner["color"] - second["color"] < 0.03:
        return None
    debug = _winner_debug(
        winner,
        second,
        threshold=threshold,
        min_gap=min_gap,
        match_path="shape_cluster_color",
        gap_threshold=0.0,
    )
    return CardMatchDecision(_detail_label(winner), float(winner["combined"]), debug)


def _try_shape_family_color_rescue(
    details: list[dict],
    group: dict[str, Any],
    *,
    threshold: float,
    min_gap: float,
) -> CardMatchDecision | None:
    members = _group_members(details, group)
    if len(members) < 2:
        return None

    best_combined = max(item["combined"] for item in members)
    best_shape = max(item["shape"] for item in members)
    color_ranked = sorted(members, key=lambda item: item["color"], reverse=True)
    winner = color_ranked[0]
    second = color_ranked[1]

    if winner["color"] - second["color"] < 0.03:
        return None

    allow_near = bool(group.get("allow_near_threshold"))
    allow_lower_shape = bool(group.get("allow_lower_shape"))
    combined_floor = threshold - 0.03 if allow_near else threshold
    color_floor = 0.95 if allow_near else 0.85

    if winner["color"] < color_floor:
        return None
    if winner["combined"] < combined_floor:
        return None
    if best_combined - winner["combined"] > 0.10:
        return None

    if allow_near:
        group_top = max(members, key=lambda item: item["combined"])
        if _detail_label(group_top) != _detail_label(winner):
            return None

    if not allow_lower_shape and winner["shape"] < best_shape - 0.13:
        return None

    debug = _winner_debug(
        winner,
        second,
        threshold=threshold,
        min_gap=min_gap,
        match_path="shape_family_color_rescue",
        gap_threshold=0.0,
    )
    return CardMatchDecision(_detail_label(winner), float(winner["combined"]), debug)


def _try_known_family_low_gap_accept(
    details: list[dict],
    group: dict[str, Any],
    *,
    threshold: float,
    min_gap: float,
) -> CardMatchDecision | None:
    if len(details) < 2:
        return None
    winner = details[0]
    second = details[1]
    winner_label = _detail_label(winner)
    second_label = _detail_label(second)
    if winner_label not in group["labels"] or second_label not in group["labels"]:
        return None

    variant = group.get("low_gap_variant", "standard")
    if variant == "narrow":
        if winner["combined"] < 0.88:
            return None
        if winner["shape"] - second["shape"] < 0.015:
            return None
        if abs(winner["color"] - second["color"]) > 0.03:
            return None
        if winner["combined"] - second["combined"] < 0.01:
            return None
    else:
        if winner["combined"] < 0.86:
            return None
        if winner["color"] < 0.92:
            return None
        if winner["combined"] - second["combined"] < 0.04:
            return None

    debug = _winner_debug(
        winner,
        second,
        threshold=threshold,
        min_gap=min_gap,
        match_path="known_family_low_gap_accept",
        gap_threshold=0.0,
    )
    return CardMatchDecision(winner_label, float(winner["combined"]), debug)


def select_card_match(
    details: list[dict],
    *,
    threshold: float,
    min_gap: float,
) -> CardMatchDecision:
    if not details:
        debug = _match_debug_base(threshold=threshold, min_gap=min_gap)
        return CardMatchDecision(None, 0.0, debug)

    ranked = sorted(details, key=lambda item: item["combined"], reverse=True)
    cluster = _try_shape_cluster_color(ranked, threshold=threshold, min_gap=min_gap)
    if cluster is not None:
        return cluster

    for group in VISUAL_CARD_GROUPS:
        if "shape_family_color_rescue" in group.get("strategies", ()):
            rescue = _try_shape_family_color_rescue(
                ranked, group, threshold=threshold, min_gap=min_gap
            )
            if rescue is not None:
                return rescue

    for group in VISUAL_CARD_GROUPS:
        if "known_family_low_gap_accept" in group.get("strategies", ()):
            low_gap = _try_known_family_low_gap_accept(
                ranked, group, threshold=threshold, min_gap=min_gap
            )
            if low_gap is not None:
                return low_gap

    winner = ranked[0]
    second = ranked[1] if len(ranked) > 1 else None
    top1_score = float(winner["combined"])
    top2_score = float(second["combined"]) if second is not None else 0.0
    gap = top1_score - top2_score
    gap_threshold = float(adaptive_min_gap(top1_score, min_gap))

    if top1_score < threshold:
        reject_reason = "below_threshold"
        label: str | None = None
        score = top1_score
    elif gap < gap_threshold:
        reject_reason = "below_min_gap"
        label = None
        score = top1_score
    else:
        reject_reason = "accepted"
        label = _detail_label(winner)
        score = top1_score

    debug = _winner_debug(
        winner,
        second,
        threshold=threshold,
        min_gap=min_gap,
        match_path="combined",
        reject_reason=reject_reason,
        gap_threshold=gap_threshold,
    )
    return CardMatchDecision(label, score, debug)


def diagnose_card_match(crop: np.ndarray, template_sigs: dict) -> dict[str, Any]:
    """Score a crop against templates and return full match diagnostics."""
    params = DETECTION_PARAMS
    threshold = float(params["threshold"])
    min_gap = float(params["min_gap"])
    padding = int(params["padding"])
    margin_ratio = float(params["margin_ratio"])

    roi_icon, roi_fg = prepare_card_icon(crop)
    details = build_match_details(
        roi_icon,
        roi_fg,
        template_sigs,
        padding=padding,
        margin_ratio=margin_ratio,
    )
    return select_card_match(details, threshold=threshold, min_gap=min_gap).debug


def match_card_roi(
    roi_bgr: np.ndarray,
    template_sigs: dict,
    threshold: float = DETECTION_PARAMS["threshold"],
    min_gap: float = DETECTION_PARAMS["min_gap"],
    padding: int = DETECTION_PARAMS["padding"],
    margin_ratio: float = DETECTION_PARAMS["margin_ratio"],
) -> tuple[str, float]:
    roi_icon, roi_fg = prepare_card_icon(roi_bgr)
    details = build_match_details(
        roi_icon,
        roi_fg,
        template_sigs,
        padding=padding,
        margin_ratio=margin_ratio,
    )
    decision = select_card_match(details, threshold=threshold, min_gap=min_gap)
    if decision.label is None:
        return UNKNOWN, decision.score
    return decision.label, decision.score


def detect_cards(
    img: np.ndarray,
    template_sigs: dict | None = None,
) -> list[dict]:
    """Detect cards for all players."""
    if template_sigs is None:
        template_sigs = load_template_sigs()

    params = DETECTION_PARAMS
    results: list[dict] = []
    for player in range(NUM_PLAYERS):
        cards: list[dict] = []
        for slot in range(NUM_CARDS):
            box = card_roi(player, slot)
            roi = crop_roi(img, box)
            if not roi_valid(roi, box):
                continue
            label, score = match_card_roi(
                roi,
                template_sigs,
                threshold=params["threshold"],
                min_gap=params["min_gap"],
                padding=params["padding"],
                margin_ratio=params["margin_ratio"],
            )
            cards.append(
                {
                    "slot_index": slot,
                    "label": label,
                    "score": score,
                }
            )
        results.append(
            {
                "player": player + 1,
                "row_index": player,
                "cards": cards,
            }
        )
    return results
