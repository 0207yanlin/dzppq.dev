# -*- coding: utf-8 -*-
"""Detect concrete equipment names from hero equipment strips."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np

from src.layout import (
    EQUIPMENT_TEMPLATE_DIR,
    NUM_HEROES,
    NUM_PLAYERS,
    crop_roi,
    equipment_item_roi,
    roi_valid,
)

RAW_TEMPLATE_PREFIX = "微信图片"
UNKNOWN_LABEL = "unknown"


@dataclass(frozen=True)
class EquipmentTemplate:
    label: str
    image: np.ndarray
    signature_cache: dict[tuple[int, int], tuple[np.ndarray, ...]] = field(
        default_factory=dict,
        compare=False,
        repr=False,
    )


@dataclass
class EquipmentBatchIndex:
    """Pre-stacked template signatures grouped by ROI size."""

    labels: list[str]
    by_size: dict[tuple[int, int], dict[str, np.ndarray]]


@dataclass(frozen=True)
class EquipmentMatch:
    label: str
    score: float
    shift: tuple[int, int]
    top: list[tuple[str, float]]


def imread_unicode(path: Path) -> np.ndarray | None:
    """Read images whose paths may contain non-ASCII characters."""
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def load_equipment_templates(
    template_dir: Path | None = None,
    include_raw: bool = False,
) -> list[EquipmentTemplate]:
    """Load named equipment templates from disk."""
    directory = template_dir or EQUIPMENT_TEMPLATE_DIR
    templates: list[EquipmentTemplate] = []
    for path in sorted(directory.glob("*.jpg"), key=lambda p: p.name):
        if not include_raw and path.stem.startswith(RAW_TEMPLATE_PREFIX):
            continue
        image = imread_unicode(path)
        if image is None:
            continue
        templates.append(EquipmentTemplate(label=path.stem.strip(), image=image))
    return templates


def _template_signature(template: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, ...]:
    width, height = size
    resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
    return (
        cv2.cvtColor(resized, cv2.COLOR_BGR2LAB),
        cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(resized, cv2.COLOR_BGR2HSV),
    )


def _cached_template_signature(
    template: EquipmentTemplate,
    size: tuple[int, int],
) -> tuple[np.ndarray, ...]:
    cached = template.signature_cache.get(size)
    if cached is None:
        cached = _template_signature(template.image, size)
        template.signature_cache[size] = cached
    return cached


def _roi_signature(roi: np.ndarray) -> tuple[np.ndarray, ...]:
    return (
        cv2.cvtColor(roi, cv2.COLOR_BGR2LAB),
        cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(roi, cv2.COLOR_BGR2HSV),
    )


def _tm_ccoeff_normed_equal_size(roi: np.ndarray, template: np.ndarray) -> float:
    """Replicate cv2.matchTemplate(..., TM_CCOEFF_NORMED)[0, 0] for equal shapes."""
    roi_f = roi.astype(np.float64)
    tmpl_f = template.astype(np.float64)
    roi_centered = roi_f - roi_f.mean()
    tmpl_centered = tmpl_f - tmpl_f.mean()
    roi_norm = np.sqrt((roi_centered * roi_centered).sum())
    tmpl_norm = np.sqrt((tmpl_centered * tmpl_centered).sum())
    if roi_norm == 0 or tmpl_norm == 0:
        return 0.0
    return float((roi_centered * tmpl_centered).sum() / (roi_norm * tmpl_norm))


def _tm_ccoeff_normed_batch(roi: np.ndarray, templates: np.ndarray) -> np.ndarray:
    """Batch TM_CCOEFF_NORMED for one ROI against M equal-size templates."""
    roi_f = roi.astype(np.float64)
    tmpl_f = templates.astype(np.float64)
    roi_centered = roi_f - roi_f.mean()
    roi_norm = np.sqrt((roi_centered * roi_centered).sum())
    tmpl_mean = tmpl_f.mean(axis=(1, 2), keepdims=True)
    tmpl_centered = tmpl_f - tmpl_mean
    tmpl_norm = np.sqrt((tmpl_centered * tmpl_centered).sum(axis=(1, 2)))
    dots = (tmpl_centered * roi_centered).sum(axis=(1, 2))
    denom = tmpl_norm * roi_norm
    with np.errstate(divide="ignore", invalid="ignore"):
        scores = dots / denom
    scores[(tmpl_norm == 0) | (roi_norm == 0)] = 0.0
    return scores.astype(np.float64)


def _lab_dist_score_batch(roi_lab: np.ndarray, tmpl_labs: np.ndarray) -> np.ndarray:
    lab_dist = np.abs(
        roi_lab.astype(np.float32)[None, ...] - tmpl_labs.astype(np.float32)
    ).mean(axis=(1, 2, 3))
    return 1.0 - np.minimum(lab_dist / 85.0, 1.0)


def _equipment_similarity_from_signatures(
    roi_sig: tuple[np.ndarray, ...],
    tmpl_sig: tuple[np.ndarray, ...],
) -> float:
    roi_lab, roi_gray, roi_hsv = roi_sig
    tmpl_lab, tmpl_gray, tmpl_hsv = tmpl_sig

    lab_dist = np.mean(
        np.abs(roi_lab.astype(np.float32) - tmpl_lab.astype(np.float32))
    )
    dist_score = 1.0 - min(float(lab_dist) / 85.0, 1.0)
    gray_score = _tm_ccoeff_normed_equal_size(roi_gray, tmpl_gray)
    sat_score = _tm_ccoeff_normed_equal_size(roi_hsv[:, :, 1], tmpl_hsv[:, :, 1])
    val_score = _tm_ccoeff_normed_equal_size(roi_hsv[:, :, 2], tmpl_hsv[:, :, 2])
    return float(
        0.35 * dist_score
        + 0.30 * gray_score
        + 0.20 * sat_score
        + 0.15 * val_score
    )


def _equipment_similarity_batch(
    roi_sig: tuple[np.ndarray, ...],
    batch_entry: dict[str, np.ndarray],
) -> np.ndarray:
    roi_lab, roi_gray, roi_hsv = roi_sig
    dist_score = _lab_dist_score_batch(roi_lab, batch_entry["lab"])
    gray_score = _tm_ccoeff_normed_batch(roi_gray, batch_entry["gray"])
    sat_score = _tm_ccoeff_normed_batch(roi_hsv[:, :, 1], batch_entry["sat"])
    val_score = _tm_ccoeff_normed_batch(roi_hsv[:, :, 2], batch_entry["val"])
    return (
        0.35 * dist_score
        + 0.30 * gray_score
        + 0.20 * sat_score
        + 0.15 * val_score
    )


def equipment_similarity(roi: np.ndarray, template: np.ndarray) -> float:
    """Score one ROI against one template using color and luminance structure."""
    height, width = roi.shape[:2]
    return _equipment_similarity_from_signatures(
        _roi_signature(roi),
        _template_signature(template, (width, height)),
    )


def build_equipment_batch_index(
    templates: Sequence[EquipmentTemplate],
) -> EquipmentBatchIndex:
    """Pre-stack template signatures for each ROI size."""
    labels = [template.label for template in templates]
    stacked: dict[tuple[int, int], dict[str, np.ndarray]] = {}
    for size in _common_roi_sizes():
        parts = {"lab": [], "gray": [], "sat": [], "val": []}
        for template in templates:
            lab, gray, hsv = _cached_template_signature(template, size)
            parts["lab"].append(lab)
            parts["gray"].append(gray)
            parts["sat"].append(hsv[:, :, 1])
            parts["val"].append(hsv[:, :, 2])
        stacked[size] = {
            key: np.stack(values, axis=0) for key, values in parts.items()
        }
    return EquipmentBatchIndex(labels=labels, by_size=stacked)


def _common_roi_sizes() -> set[tuple[int, int]]:
    sizes: set[tuple[int, int]] = set()
    for player in range(NUM_PLAYERS):
        for slot in range(NUM_HEROES):
            for count in (1, 2, 3):
                for item in range(count):
                    box = equipment_item_roi(player, slot, count, item)
                    x1, y1, x2, y2 = box
                    sizes.add((x2 - x1, y2 - y1))
    return sizes


def _scores_from_batch(
    roi_sig: tuple[np.ndarray, ...],
    batch_index: EquipmentBatchIndex,
    size: tuple[int, int],
) -> list[tuple[str, float]]:
    batch_entry = batch_index.by_size.get(size)
    if batch_entry is None:
        return []
    scores = _equipment_similarity_batch(roi_sig, batch_entry)
    order = np.argsort(-scores)
    return [(batch_index.labels[idx], float(scores[idx])) for idx in order]


def _scores_from_legacy(
    roi_sig: tuple[np.ndarray, ...],
    templates: Sequence[EquipmentTemplate],
    size: tuple[int, int],
) -> list[tuple[str, float]]:
    scores = [
        (
            template.label,
            _equipment_similarity_from_signatures(
                roi_sig,
                _cached_template_signature(template, size),
            ),
        )
        for template in templates
    ]
    scores.sort(key=lambda item_score: item_score[1], reverse=True)
    return scores


def match_equipment_item(
    img: np.ndarray,
    player: int,
    slot: int,
    equipment_count: int,
    item: int,
    templates: Sequence[EquipmentTemplate],
    search_radius: int = 2,
    top_k: int = 3,
    batch_index: EquipmentBatchIndex | None = None,
    use_legacy: bool = False,
) -> EquipmentMatch:
    """Match one concrete equipment item in a hero equipment strip."""
    if not templates:
        return EquipmentMatch(UNKNOWN_LABEL, 0.0, (0, 0), [])

    box = equipment_item_roi(player, slot, equipment_count, item)
    x1, y1, x2, y2 = box
    best: tuple[float, int, int, int, list[tuple[str, float]]] | None = None
    for dy in range(-search_radius, search_radius + 1):
        for dx in range(-search_radius, search_radius + 1):
            shifted_box = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
            roi = crop_roi(img, shifted_box)
            if not roi_valid(roi, shifted_box):
                continue
            height, width = roi.shape[:2]
            roi_sig = _roi_signature(roi)
            size = (width, height)
            if use_legacy or batch_index is None or size not in batch_index.by_size:
                scores = _scores_from_legacy(roi_sig, templates, size)
            else:
                scores = _scores_from_batch(roi_sig, batch_index, size)
            if not scores:
                continue
            score = scores[0][1]
            if best is None or score > best[0]:
                best = (score, dx, dy, len(scores), scores[:top_k])

    if best is None:
        return EquipmentMatch(UNKNOWN_LABEL, 0.0, (0, 0), [])
    score, dx, dy, _count, top = best
    return EquipmentMatch(top[0][0], score, (dx, dy), top)


def detect_equipment_items(
    img: np.ndarray,
    equipment_counts: Sequence[Sequence[str | int]],
    templates: Sequence[EquipmentTemplate],
    search_radius: int = 2,
    top_k: int = 3,
    batch_index: EquipmentBatchIndex | None = None,
    use_legacy: bool = False,
) -> list[list[list[dict]]]:
    """Detect concrete equipment names for each player and hero slot."""
    if batch_index is None and not use_legacy and templates:
        batch_index = build_equipment_batch_index(templates)

    results: list[list[list[dict]]] = [[[] for _ in range(NUM_HEROES)] for _ in range(NUM_PLAYERS)]
    for player, row in enumerate(equipment_counts):
        if player >= NUM_PLAYERS:
            break
        for slot, value in enumerate(row):
            if slot >= NUM_HEROES or value == "-":
                continue
            count = int(value)
            if count <= 0:
                continue
            slot_results = []
            for item in range(count):
                match = match_equipment_item(
                    img,
                    player,
                    slot,
                    count,
                    item,
                    templates,
                    search_radius=search_radius,
                    top_k=top_k,
                    batch_index=batch_index,
                    use_legacy=use_legacy,
                )
                slot_results.append(
                    {
                        "item_index": item,
                        "label": match.label,
                        "score": match.score,
                        "shift": match.shift,
                        "top": match.top,
                    }
                )
            results[player][slot] = slot_results
    return results
