# -*- coding: utf-8 -*-
"""Evaluate hero template matching: heroes vs heroes.new A/B and parameter sweep."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.detect_heroes import (  # noqa: E402
    DETECTION_PARAMS,
    build_hero_template_cache,
    crop_center,
    detect_lineups,
    load_templates,
)
from src.layout import HERO_TEMPLATE_DIR, ROOT as SRC_ROOT, crop_roi, hero_roi, roi_valid  # noqa: E402
from src.match_ground_truth import DEFAULT_GT_PATH, load_match_ground_truth  # noqa: E402
from src.parse import parse_hero_label  # noqa: E402

HEROES_NEW_DIR = SRC_ROOT / "assets" / "templates" / "heroes.new"
DEFAULT_REPORT_DIR = ROOT / "data" / "hero_template_eval"
SKIP_HEROES = {"魔鬼蛋"}
HERO_ALIASES = {
    "双面教师林野·前排": "双面教师林野",
    "双面教师林野·后排": "双面教师林野",
}


@dataclass(frozen=True)
class MatchConfig:
    name: str
    template_dir: Path
    threshold: float = 0.75
    min_gap: float = 0.08
    padding: int = 8
    margin_ratio: float = 0.1
    strategy: str = "gray_template"
    search_radius: int = 0


@dataclass
class SlotSample:
    screenshot: str
    date_group: str
    row_index: int
    slot_index: int
    expected_name: str
    expected_tier: int | None
    img: np.ndarray
    box: tuple[int, int, int, int]


@dataclass
class SlotResult:
    sample: SlotSample
    config_name: str
    predicted_label: str | None
    predicted_name: str | None
    predicted_tier: int | None
    score: float
    second_score: float
    gap: float
    accepted: bool
    reject_reason: str
    top_k: list[tuple[str, float]] = field(default_factory=list)
    shift: tuple[int, int] = (0, 0)


def normalize_hero_name(name: str) -> str:
    return HERO_ALIASES.get(name, name)


def hero_names_match(expected: str, predicted: str | None) -> bool:
    if predicted is None:
        return False
    return normalize_hero_name(expected) == normalize_hero_name(predicted)


def screenshot_date_group(path: str) -> str:
    if "screenshots.0701/" in path or path.startswith("screenshots.0701"):
        return "0701"
    if "screenshots.0702/" in path or path.startswith("screenshots.0702"):
        return "0702"
    return "other"


def imread_unicode(path: Path) -> np.ndarray | None:
    return cv2.imdecode(np.fromfile(str(path), dtype=np.uint8), cv2.IMREAD_COLOR)


def _tm_ccoeff_normed_equal_size(roi: np.ndarray, template: np.ndarray) -> float:
    roi_f = roi.astype(np.float64)
    tmpl_f = template.astype(np.float64)
    roi_centered = roi_f - roi_f.mean()
    tmpl_centered = tmpl_f - tmpl_f.mean()
    roi_norm = np.sqrt((roi_centered * roi_centered).sum())
    tmpl_norm = np.sqrt((tmpl_centered * tmpl_centered).sum())
    if roi_norm == 0 or tmpl_norm == 0:
        return 0.0
    return float((roi_centered * tmpl_centered).sum() / (roi_norm * tmpl_norm))


def _template_signature(template: np.ndarray, size: tuple[int, int]) -> tuple[np.ndarray, ...]:
    width, height = size
    resized = cv2.resize(template, (width, height), interpolation=cv2.INTER_AREA)
    return (
        cv2.cvtColor(resized, cv2.COLOR_BGR2LAB),
        cv2.cvtColor(resized, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(resized, cv2.COLOR_BGR2HSV),
    )


def _roi_signature(roi: np.ndarray) -> tuple[np.ndarray, ...]:
    return (
        cv2.cvtColor(roi, cv2.COLOR_BGR2LAB),
        cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY),
        cv2.cvtColor(roi, cv2.COLOR_BGR2HSV),
    )


def _multichannel_similarity(
    roi_sig: tuple[np.ndarray, ...],
    tmpl_sig: tuple[np.ndarray, ...],
) -> float:
    roi_lab, roi_gray, roi_hsv = roi_sig
    tmpl_lab, tmpl_gray, tmpl_hsv = tmpl_sig
    lab_dist = np.mean(np.abs(roi_lab.astype(np.float32) - tmpl_lab.astype(np.float32)))
    dist_score = 1.0 - min(float(lab_dist) / 85.0, 1.0)
    gray_score = _tm_ccoeff_normed_equal_size(roi_gray, tmpl_gray)
    sat_score = _tm_ccoeff_normed_equal_size(roi_hsv[:, :, 1], tmpl_hsv[:, :, 1])
    val_score = _tm_ccoeff_normed_equal_size(roi_hsv[:, :, 2], tmpl_hsv[:, :, 2])
    return float(0.35 * dist_score + 0.30 * gray_score + 0.20 * sat_score + 0.15 * val_score)


def _lab_dist_score_batch(roi_lab: np.ndarray, tmpl_labs: np.ndarray) -> np.ndarray:
    lab_dist = np.abs(
        roi_lab.astype(np.float32)[None, ...] - tmpl_labs.astype(np.float32)
    ).mean(axis=(1, 2, 3))
    return 1.0 - np.minimum(lab_dist / 85.0, 1.0)


def _tm_ccoeff_normed_batch(roi: np.ndarray, templates: np.ndarray) -> np.ndarray:
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


@dataclass
class TemplateScorer:
    config: MatchConfig
    names: list[str]
    gray_templates: list[np.ndarray] | None = None
    multichannel_batch: dict[str, np.ndarray] | None = None
    sliding_templates: list[np.ndarray] | None = None

    @classmethod
    def build(cls, config: MatchConfig, templates: dict[str, np.ndarray]) -> TemplateScorer:
        names = sorted(templates.keys())
        margin = config.margin_ratio
        roi_gray_shape = crop_center(
            np.zeros((39, 70), dtype=np.uint8),
            margin,
        ).shape

        if config.strategy == "gray_template":
            gray_templates = []
            for name in names:
                temp_gray = crop_center(
                    cv2.cvtColor(templates[name], cv2.COLOR_BGR2GRAY),
                    margin,
                )
                gray_templates.append(temp_gray)
            return cls(config, names, gray_templates=gray_templates)

        if config.strategy == "gray_resize":
            gray_templates = []
            h, w = roi_gray_shape
            for name in names:
                temp_gray = crop_center(
                    cv2.cvtColor(templates[name], cv2.COLOR_BGR2GRAY),
                    margin,
                )
                gray_templates.append(
                    cv2.resize(temp_gray, (w, h), interpolation=cv2.INTER_AREA)
                )
            return cls(config, names, gray_templates=gray_templates)

        if config.strategy == "multichannel_resize":
            h, w = roi_gray_shape
            parts = {"lab": [], "gray": [], "sat": [], "val": []}
            for name in names:
                tmpl_crop = crop_center(templates[name], margin)
                lab, gray, hsv = _template_signature(tmpl_crop, (w, h))
                parts["lab"].append(lab)
                parts["gray"].append(gray)
                parts["sat"].append(hsv[:, :, 1])
                parts["val"].append(hsv[:, :, 2])
            batch = {key: np.stack(values, axis=0) for key, values in parts.items()}
            return cls(config, names, multichannel_batch=batch)

        if config.strategy == "template_sliding":
            sliding_templates = []
            for name in names:
                temp_gray = crop_center(
                    cv2.cvtColor(templates[name], cv2.COLOR_BGR2GRAY),
                    margin,
                )
                sliding_templates.append(temp_gray)
            return cls(config, names, sliding_templates=sliding_templates)

        raise ValueError(f"unknown strategy: {config.strategy}")

    def score_roi(self, roi: np.ndarray) -> list[tuple[str, float]]:
        margin = self.config.margin_ratio
        padding = self.config.padding

        if self.config.strategy == "gray_template":
            assert self.gray_templates is not None
            roi_gray = crop_center(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), margin)
            search = cv2.copyMakeBorder(
                roi_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE
            )
            scores: list[tuple[str, float]] = []
            for name, temp_gray in zip(self.names, self.gray_templates):
                th, tw = temp_gray.shape
                if search.shape[0] < th or search.shape[1] < tw:
                    scores.append((name, 0.0))
                    continue
                res = cv2.matchTemplate(search, temp_gray, cv2.TM_CCOEFF_NORMED)
                scores.append((name, float(res.max())))
            scores.sort(key=lambda item: item[1], reverse=True)
            return scores

        if self.config.strategy == "gray_resize":
            assert self.gray_templates is not None
            roi_gray = crop_center(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), margin)
            stacked = np.stack(self.gray_templates, axis=0)
            values = _tm_ccoeff_normed_batch(roi_gray, stacked)
            scores = [(name, float(score)) for name, score in zip(self.names, values)]
            scores.sort(key=lambda item: item[1], reverse=True)
            return scores

        if self.config.strategy == "multichannel_resize":
            assert self.multichannel_batch is not None
            roi_crop = crop_center(roi, margin)
            roi_sig = _roi_signature(roi_crop)
            roi_lab, roi_gray, roi_hsv = roi_sig
            batch = self.multichannel_batch
            dist_score = _lab_dist_score_batch(roi_lab, batch["lab"])
            gray_score = _tm_ccoeff_normed_batch(roi_gray, batch["gray"])
            sat_score = _tm_ccoeff_normed_batch(roi_hsv[:, :, 1], batch["sat"])
            val_score = _tm_ccoeff_normed_batch(roi_hsv[:, :, 2], batch["val"])
            values = (
                0.35 * dist_score
                + 0.30 * gray_score
                + 0.20 * sat_score
                + 0.15 * val_score
            )
            scores = [(name, float(score)) for name, score in zip(self.names, values)]
            scores.sort(key=lambda item: item[1], reverse=True)
            return scores

        if self.config.strategy == "template_sliding":
            assert self.sliding_templates is not None
            roi_gray = crop_center(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), margin)
            rh, rw = roi_gray.shape
            scores = []
            for name, temp_gray in zip(self.names, self.sliding_templates):
                th, tw = temp_gray.shape
                if th < rh or tw < rw:
                    resized = cv2.resize(temp_gray, (rw, rh), interpolation=cv2.INTER_AREA)
                    score = _tm_ccoeff_normed_equal_size(roi_gray, resized)
                else:
                    res = cv2.matchTemplate(temp_gray, roi_gray, cv2.TM_CCOEFF_NORMED)
                    score = float(res.max())
                scores.append((name, score))
            scores.sort(key=lambda item: item[1], reverse=True)
            return scores

        raise ValueError(f"unknown strategy: {self.config.strategy}")


def crop_shifted_roi(
    img: np.ndarray,
    box: tuple[int, int, int, int],
    shift: tuple[int, int],
) -> np.ndarray | None:
    x1, y1, x2, y2 = box
    dx, dy = shift
    shifted_box = (x1 + dx, y1 + dy, x2 + dx, y2 + dy)
    roi = crop_roi(img, shifted_box)
    if not roi_valid(roi, shifted_box):
        return None
    return roi


def score_roi_against_templates(
    img: np.ndarray,
    box: tuple[int, int, int, int],
    scorer: TemplateScorer,
    top_k: int = 5,
) -> tuple[list[tuple[str, float]], tuple[int, int]]:
    best_top: list[tuple[str, float]] = []
    best_shift = (0, 0)

    for dy in range(-scorer.config.search_radius, scorer.config.search_radius + 1):
        for dx in range(-scorer.config.search_radius, scorer.config.search_radius + 1):
            roi = crop_shifted_roi(img, box, (dx, dy))
            if roi is None:
                continue
            scores = scorer.score_roi(roi)
            if not scores:
                continue
            if not best_top or scores[0][1] > best_top[0][1]:
                best_top = scores[:top_k]
                best_shift = (dx, dy)

    return best_top, best_shift


def decide_match(
    top_scores: Sequence[tuple[str, float]],
    config: MatchConfig,
) -> tuple[str | None, float, float, float, bool, str]:
    if not top_scores:
        return None, 0.0, 0.0, 0.0, False, "no_scores"
    best_name, best_score = top_scores[0]
    second_score = top_scores[1][1] if len(top_scores) > 1 else 0.0
    gap = best_score - second_score
    if best_score < config.threshold:
        return None, best_score, second_score, gap, False, "below_threshold"
    if gap < config.min_gap:
        return None, best_score, second_score, gap, False, "below_min_gap"
    return best_name, best_score, second_score, gap, True, "accepted"


def label_to_name(label: str | None) -> str | None:
    if label is None:
        return None
    _, name = parse_hero_label(label.replace(".jpg", ""))
    return name


def label_to_tier(label: str | None) -> int | None:
    if label is None:
        return None
    tier, _ = parse_hero_label(label.replace(".jpg", ""))
    return tier


def collect_slot_samples(
    gt_path: Path,
    *,
    limit: int | None = None,
) -> tuple[list[SlotSample], dict[str, int]]:
    gt = load_match_ground_truth(gt_path)
    samples: list[SlotSample] = []
    stats = Counter()
    image_cache: dict[str, np.ndarray] = {}

    for screenshot_name, entry in gt["screenshots"].items():
        img_path = ROOT / entry["path"]
        if not img_path.exists():
            stats["missing_screenshots"] += 1
            continue
        if screenshot_name not in image_cache:
            img = imread_unicode(img_path)
            if img is None:
                stats["unreadable_screenshots"] += 1
                continue
            image_cache[screenshot_name] = img
        img = image_cache[screenshot_name]
        date_group = screenshot_date_group(entry["path"])

        for player in entry["players"]:
            row_index = int(player["row_index"])
            for hero in player["heroes"]:
                expected_name = hero["hero_name"]
                if expected_name in SKIP_HEROES:
                    stats["skipped_devil"] += 1
                    continue
                slot_index = int(hero["slot_index"])
                box = hero_roi(row_index, slot_index)
                roi = crop_roi(img, box)
                if not roi_valid(roi, box):
                    stats["invalid_roi"] += 1
                    continue
                samples.append(
                    SlotSample(
                        screenshot=screenshot_name,
                        date_group=date_group,
                        row_index=row_index,
                        slot_index=slot_index,
                        expected_name=expected_name,
                        expected_tier=hero.get("tier"),
                        img=img,
                        box=box,
                    )
                )
                stats["slots"] += 1
                if limit is not None and len(samples) >= limit:
                    return samples, dict(stats)
    return samples, dict(stats)


def evaluate_slot_level(
    samples: Sequence[SlotSample],
    config: MatchConfig,
    templates: dict[str, np.ndarray],
) -> list[SlotResult]:
    scorer = TemplateScorer.build(config, templates)
    results: list[SlotResult] = []
    for sample in samples:
        top_k, shift = score_roi_against_templates(
            sample.img,
            sample.box,
            scorer,
        )
        label, score, second, gap, accepted, reason = decide_match(top_k, config)
        pred_name = label_to_name(label)
        results.append(
            SlotResult(
                sample=sample,
                config_name=config.name,
                predicted_label=label.replace(".jpg", "") if label else None,
                predicted_name=pred_name,
                predicted_tier=label_to_tier(label),
                score=score,
                second_score=second,
                gap=gap,
                accepted=accepted,
                reject_reason=reason,
                top_k=[(name.replace(".jpg", ""), sc) for name, sc in top_k],
                shift=shift,
            )
        )
    return results


def evaluate_lineup_level(
    gt_path: Path,
    config: MatchConfig,
    templates: dict[str, np.ndarray],
) -> dict[str, Any]:
    gt = load_match_ground_truth(gt_path)
    cache = build_hero_template_cache(templates, config.margin_ratio)
    params = {
        "threshold": config.threshold,
        "min_gap": config.min_gap,
        "padding": config.padding,
        "margin_ratio": config.margin_ratio,
    }

    total_gt_slots = 0
    matched = 0
    missing = 0
    wrong = 0
    early_break_loss = 0
    per_date = defaultdict(lambda: Counter())

    image_cache: dict[str, np.ndarray] = {}
    for screenshot_name, entry in gt["screenshots"].items():
        img_path = ROOT / entry["path"]
        if not img_path.exists():
            continue
        if screenshot_name not in image_cache:
            img = imread_unicode(img_path)
            if img is None:
                continue
            image_cache[screenshot_name] = img
        img = image_cache[screenshot_name]
        date_group = screenshot_date_group(entry["path"])

        lineups = detect_lineups(img, templates, cache)
        for player in entry["players"]:
            row_index = int(player["row_index"])
            pred_by_slot = {
                hero["slot_index"]: hero
                for hero in lineups[row_index]["heroes"]
            }
            gt_slots = []
            for hero in player["heroes"]:
                if hero["hero_name"] in SKIP_HEROES:
                    continue
                gt_slots.append(hero)
            total_gt_slots += len(gt_slots)

            for hero in gt_slots:
                slot = int(hero["slot_index"])
                expected = hero["hero_name"]
                pred = pred_by_slot.get(slot)
                if pred is None:
                    missing += 1
                    per_date[date_group]["missing"] += 1
                    if slot > max(pred_by_slot.keys(), default=-1):
                        early_break_loss += 1
                        per_date[date_group]["early_break_loss"] += 1
                    continue
                pred_name = label_to_name(pred["label"])
                if hero_names_match(expected, pred_name):
                    matched += 1
                    per_date[date_group]["matched"] += 1
                else:
                    wrong += 1
                    per_date[date_group]["wrong"] += 1

    return {
        "config": config.name,
        "total_gt_slots": total_gt_slots,
        "matched": matched,
        "missing": missing,
        "wrong": wrong,
        "accuracy": matched / total_gt_slots if total_gt_slots else 0.0,
        "early_break_loss": early_break_loss,
        "by_date": {k: dict(v) for k, v in per_date.items()},
    }


def summarize_slot_results(results: Sequence[SlotResult]) -> dict[str, Any]:
    total = len(results)
    if total == 0:
        return {"total": 0}

    accepted = [r for r in results if r.accepted]
    correct = [r for r in accepted if hero_names_match(r.sample.expected_name, r.predicted_name)]
    wrong = [r for r in accepted if not hero_names_match(r.sample.expected_name, r.predicted_name)]
    rejected = [r for r in results if not r.accepted]

    scores = [r.score for r in results]
    accepted_scores = [r.score for r in accepted]
    correct_scores = [r.score for r in correct]

    by_date: dict[str, Counter] = defaultdict(Counter)
    reject_reasons = Counter()
    wrong_pairs = Counter()

    top1_correct = 0
    for r in results:
        by_date[r.sample.date_group]["total"] += 1
        reject_reasons[r.reject_reason] += 1
        if r.top_k:
            top_name = label_to_name(r.top_k[0][0])
            if hero_names_match(r.sample.expected_name, top_name):
                top1_correct += 1
        if r.accepted:
            if hero_names_match(r.sample.expected_name, r.predicted_name):
                by_date[r.sample.date_group]["correct"] += 1
            else:
                by_date[r.sample.date_group]["wrong"] += 1
                wrong_pairs[(r.sample.expected_name, r.predicted_name or "unknown")] += 1

    return {
        "total": total,
        "accepted": len(accepted),
        "correct": len(correct),
        "wrong": len(wrong),
        "rejected": len(rejected),
        "accuracy_all": len(correct) / total,
        "top1_accuracy": top1_correct / total,
        "accuracy_accepted": len(correct) / len(accepted) if accepted else 0.0,
        "accept_rate": len(accepted) / total,
        "reject_rate": len(rejected) / total,
        "unknown_rate": len(rejected) / total,
        "score_mean": float(np.mean(scores)) if scores else 0.0,
        "score_p10": float(np.percentile(scores, 10)) if scores else 0.0,
        "score_p50": float(np.percentile(scores, 50)) if scores else 0.0,
        "accepted_score_mean": float(np.mean(accepted_scores)) if accepted_scores else 0.0,
        "correct_score_mean": float(np.mean(correct_scores)) if correct_scores else 0.0,
        "reject_reasons": dict(reject_reasons),
        "by_date": {
            date: {
                "total": counts["total"],
                "correct": counts["correct"],
                "wrong": counts["wrong"],
                "accuracy": counts["correct"] / counts["total"] if counts["total"] else 0.0,
            }
            for date, counts in by_date.items()
        },
        "top_wrong_pairs": [
            {"expected": exp, "predicted": pred, "count": cnt}
            for (exp, pred), cnt in wrong_pairs.most_common(15)
        ],
    }


def compare_configs(
    baseline: list[SlotResult],
    candidate: list[SlotResult],
) -> dict[str, Any]:
    by_key = {
        (
            r.sample.screenshot,
            r.sample.row_index,
            r.sample.slot_index,
        ): r
        for r in baseline
    }
    candidate_fixed = []
    candidate_regressed = []
    both_wrong = Counter()

    for r in candidate:
        key = (r.sample.screenshot, r.sample.row_index, r.sample.slot_index)
        base = by_key.get(key)
        if base is None:
            continue
        base_ok = base.accepted and hero_names_match(base.sample.expected_name, base.predicted_name)
        cand_ok = r.accepted and hero_names_match(r.sample.expected_name, r.predicted_name)
        if not base_ok and cand_ok:
            candidate_fixed.append(
                {
                    "screenshot": r.sample.screenshot,
                    "slot": r.sample.slot_index,
                    "expected": r.sample.expected_name,
                    "baseline_pred": base.predicted_name,
                    "candidate_pred": r.predicted_name,
                    "candidate_score": r.score,
                }
            )
        elif base_ok and not cand_ok:
            candidate_regressed.append(
                {
                    "screenshot": r.sample.screenshot,
                    "slot": r.sample.slot_index,
                    "expected": r.sample.expected_name,
                    "baseline_pred": base.predicted_name,
                    "candidate_pred": r.predicted_name,
                    "candidate_score": r.score,
                }
            )
        elif not base_ok and not cand_ok:
            both_wrong[r.sample.expected_name] += 1

    return {
        "candidate_fixed_count": len(candidate_fixed),
        "candidate_regressed_count": len(candidate_regressed),
        "both_wrong_top_heroes": [
            {"hero": hero, "count": cnt} for hero, cnt in both_wrong.most_common(15)
        ],
        "candidate_fixed_samples": candidate_fixed[:20],
        "candidate_regressed_samples": candidate_regressed[:20],
    }


def _match_params() -> dict[str, float | int]:
    return {
        "threshold": DETECTION_PARAMS["threshold"],
        "min_gap": DETECTION_PARAMS["min_gap"],
        "padding": DETECTION_PARAMS["padding"],
        "margin_ratio": DETECTION_PARAMS["margin_ratio"],
    }


def default_configs() -> list[MatchConfig]:
    base = _match_params()
    configs = [
        MatchConfig("heroes_baseline", HERO_TEMPLATE_DIR, strategy="gray_template", **base),
        MatchConfig("heroes_new_default", HEROES_NEW_DIR, strategy="gray_template", **base),
    ]
    for threshold in (0.75, 0.70, 0.65, 0.60, 0.55, 0.50, 0.45):
        for min_gap in (0.08, 0.05, 0.03, 0.01, 0.00):
            if threshold >= 0.75 and min_gap >= 0.08:
                continue
            configs.append(
                MatchConfig(
                    f"heroes_new_multi_t{threshold:.2f}_g{min_gap:.2f}",
                    HEROES_NEW_DIR,
                    threshold=threshold,
                    min_gap=min_gap,
                    padding=base["padding"],
                    margin_ratio=base["margin_ratio"],
                    strategy="multichannel_resize",
                    search_radius=2,
                )
            )
    for strategy, threshold, min_gap in (
        ("gray_resize", 0.50, 0.01),
        ("multichannel_resize", 0.50, 0.01),
        ("template_sliding", 0.55, 0.01),
    ):
        configs.append(
            MatchConfig(
                f"heroes_new_{strategy}",
                HEROES_NEW_DIR,
                threshold=threshold,
                min_gap=min_gap,
                padding=base["padding"],
                margin_ratio=base["margin_ratio"],
                strategy=strategy,
                search_radius=2,
            )
        )
    return configs


def build_report(
    input_stats: dict[str, int],
    slot_summaries: dict[str, dict[str, Any]],
    lineup_summaries: list[dict[str, Any]],
    comparisons: dict[str, Any],
    configs: Sequence[MatchConfig],
) -> dict[str, Any]:
    heroes_new_sweep = {
        name: summary
        for name, summary in slot_summaries.items()
        if name.startswith("heroes_new_")
    }
    best_sweep = max(
        heroes_new_sweep.items(),
        key=lambda item: (
            item[1].get("correct", 0),
            -item[1].get("wrong", 0),
            item[1].get("top1_accuracy", 0),
        ),
        default=(None, {}),
    )

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_stats": input_stats,
        "template_dirs": {
            "heroes": str(HERO_TEMPLATE_DIR),
            "heroes.new": str(HEROES_NEW_DIR),
        },
        "configs_tested": [cfg.name for cfg in configs],
        "slot_summaries": slot_summaries,
        "lineup_summaries": lineup_summaries,
        "comparisons": comparisons,
        "best_heroes_new_config": {
            "name": best_sweep[0],
            "summary": best_sweep[1],
        },
        "interpretation": build_interpretation(
            slot_summaries.get("heroes_baseline", {}),
            slot_summaries.get("heroes_new_default", {}),
            lineup_summaries,
            best_sweep[1] if best_sweep[0] else {},
            best_sweep[0],
        ),
    }


def template_naming_inventory() -> dict[str, Any]:
    heroes = {p.stem for p in HERO_TEMPLATE_DIR.glob("*.jpg")}
    heroes_new = {p.stem for p in HEROES_NEW_DIR.glob("*.jpg")}
    return {
        "heroes_count": len(heroes),
        "heroes_new_count": len(heroes_new),
        "common_count": len(heroes & heroes_new),
        "only_heroes": sorted(heroes - heroes_new),
        "only_heroes_new": sorted(heroes_new - heroes),
    }


def build_interpretation(
    baseline: dict[str, Any],
    candidate_default: dict[str, Any],
    lineup_summaries: Sequence[dict[str, Any]],
    best_candidate: dict[str, Any],
    best_candidate_name: str | None = None,
) -> list[str]:
    notes: list[str] = []
    if baseline and candidate_default:
        delta = candidate_default.get("accuracy_all", 0) - baseline.get("accuracy_all", 0)
        notes.append(
            f"heroes.new default slot-level accuracy vs heroes baseline: "
            f"{candidate_default.get('accuracy_all', 0):.3f} vs {baseline.get('accuracy_all', 0):.3f} "
            f"(delta {delta:+.3f})."
        )
        for date in ("0701", "0702"):
            b = baseline.get("by_date", {}).get(date, {})
            c = candidate_default.get("by_date", {}).get(date, {})
            if b and c:
                notes.append(
                    f"{date}: heroes.new {c.get('accuracy', 0):.3f} vs heroes {b.get('accuracy', 0):.3f}."
                )

    lineup_by_name = {item["config"]: item for item in lineup_summaries}
    base_lineup = lineup_by_name.get("heroes_baseline")
    new_lineup = lineup_by_name.get("heroes_new_default")
    if base_lineup and new_lineup:
        notes.append(
            f"Lineup-level early-break losses: heroes {base_lineup.get('early_break_loss', 0)}, "
            f"heroes.new {new_lineup.get('early_break_loss', 0)}."
        )
        slot_base = baseline.get("accuracy_all", 0)
        slot_new = candidate_default.get("accuracy_all", 0)
        lineup_base = base_lineup.get("accuracy", 0)
        lineup_new = new_lineup.get("accuracy", 0)
        if slot_new > lineup_new + 0.05 or slot_base > lineup_base + 0.05:
            notes.append(
                "Slot-level accuracy is materially higher than lineup-level, indicating early-break "
                "or sequential truncation is a major error source."
            )

    if best_candidate and best_candidate_name:
        notes.append(
            "Best heroes.new sweep config by correct count: "
            f"{best_candidate_name} "
            f"accuracy_all={best_candidate.get('accuracy_all', 0):.3f}, "
            f"wrong={best_candidate.get('wrong', 0)}, "
            f"reject_rate={best_candidate.get('reject_rate', 0):.3f}."
        )
        base_acc = baseline.get("accuracy_all", 0)
        best_acc = best_candidate.get("accuracy_all", 0)
        if best_acc >= base_acc - 0.01:
            notes.append(
                "At least one heroes.new alternative strategy matches or nearly matches heroes baseline "
                "slot-level accuracy, suggesting图鉴模板可用，但需改匹配策略/参数而非直接替换目录。"
            )
        else:
            notes.append(
                "Even the best heroes.new alternative remains below heroes baseline; "
                "template preprocessing or stronger partial-match scoring is still required."
            )

    default_reject = candidate_default.get("reject_reasons", {})
    if default_reject.get("no_scores") or candidate_default.get("reject_rate", 0) >= 0.99:
        notes.append(
            "heroes.new with current gray_template cannot match because atlas templates are larger "
            "than the ROI search window and are skipped by matchTemplate; resize/sliding/multichannel "
            "strategies are required."
        )
    return notes


def print_report_summary(report: dict[str, Any]) -> None:
    print("=== Hero Template Evaluation ===")
    print("Input:", report["input_stats"])
    print()
    for name in ("heroes_baseline", "heroes_new_default"):
        summary = report["slot_summaries"].get(name)
        if not summary:
            continue
        print(f"[slot] {name}")
        print(
            f"  accuracy_all={summary['accuracy_all']:.3f} top1={summary.get('top1_accuracy', 0):.3f} "
            f"accept={summary['accept_rate']:.3f} wrong={summary['wrong']} "
            f"reject={summary['reject_rate']:.3f}"
        )
        for date, stats in summary.get("by_date", {}).items():
            print(f"  {date}: acc={stats['accuracy']:.3f} total={stats['total']}")
        print()

    best = report.get("best_heroes_new_config", {})
    if best.get("name"):
        print(f"[best heroes.new sweep] {best['name']}")
        s = best["summary"]
        print(
            f"  accuracy_all={s.get('accuracy_all', 0):.3f} wrong={s.get('wrong', 0)} "
            f"reject_rate={s.get('reject_rate', 0):.3f}"
        )
        print()

    print("[lineup-level]")
    for item in report["lineup_summaries"]:
        print(
            f"  {item['config']}: acc={item['accuracy']:.3f} "
            f"missing={item['missing']} wrong={item['wrong']} "
            f"early_break_loss={item['early_break_loss']}"
        )
    print()

    comp = report["comparisons"].get("heroes_new_default_vs_baseline", {})
    if comp:
        print(
            f"[A/B] fixed={comp.get('candidate_fixed_count', 0)} "
            f"regressed={comp.get('candidate_regressed_count', 0)}"
        )
    print()
    print("Interpretation:")
    for note in report.get("interpretation", []):
        print(f"- {note}")


def render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# Hero Template Evaluation Report",
        "",
        f"Generated at: {report['generated_at']}",
        "",
        "## Input",
        "",
        json.dumps(report["input_stats"], ensure_ascii=False, indent=2),
        "",
        "## Slot-level summaries",
        "",
    ]
    for name, summary in report["slot_summaries"].items():
        lines.extend(
            [
                f"### {name}",
                "",
                f"- accuracy_all: {summary.get('accuracy_all', 0):.3f}",
                f"- accept_rate: {summary.get('accept_rate', 0):.3f}",
                f"- wrong: {summary.get('wrong', 0)}",
                f"- reject_rate: {summary.get('reject_rate', 0):.3f}",
                "",
            ]
        )
    lines.extend(["## Interpretation", ""])
    for note in report.get("interpretation", []):
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def run_evaluation(
    gt_path: Path,
    report_dir: Path,
    *,
    limit: int | None = None,
    quick: bool = False,
) -> dict[str, Any]:
    samples, input_stats = collect_slot_samples(gt_path, limit=limit)
    input_stats = {**input_stats, **template_naming_inventory()}
    configs = default_configs()
    if quick:
        configs = [
            cfg
            for cfg in configs
            if cfg.name
            in {
                "heroes_baseline",
                "heroes_new_default",
                "heroes_new_multi_t0.50_g0.01",
                "heroes_new_multichannel_resize",
            }
        ]

    slot_summaries: dict[str, dict[str, Any]] = {}
    slot_results_by_config: dict[str, list[SlotResult]] = {}
    for index, config in enumerate(configs, start=1):
        print(f"[{index}/{len(configs)}] evaluating {config.name}...", flush=True)
        templates = load_templates(config.template_dir)
        results = evaluate_slot_level(samples, config, templates)
        slot_results_by_config[config.name] = results
        slot_summaries[config.name] = summarize_slot_results(results)
        summary = slot_summaries[config.name]
        print(
            f"  acc={summary.get('accuracy_all', 0):.3f} "
            f"wrong={summary.get('wrong', 0)} reject={summary.get('reject_rate', 0):.3f}",
            flush=True,
        )

    lineup_summaries = []
    base = _match_params()
    for config in (
        MatchConfig("heroes_baseline", HERO_TEMPLATE_DIR, strategy="gray_template", **base),
        MatchConfig("heroes_new_default", HEROES_NEW_DIR, strategy="gray_template", **base),
    ):
        templates = load_templates(config.template_dir)
        lineup_summaries.append(evaluate_lineup_level(gt_path, config, templates))

    comparisons = {
        "heroes_new_default_vs_baseline": compare_configs(
            slot_results_by_config["heroes_baseline"],
            slot_results_by_config["heroes_new_default"],
        )
    }
    heroes_new_sweep = {
        name: summary
        for name, summary in slot_summaries.items()
        if name.startswith("heroes_new_") and name != "heroes_new_default"
    }
    if heroes_new_sweep:
        best_name = max(
            heroes_new_sweep.items(),
            key=lambda item: (
                item[1].get("correct", 0),
                -item[1].get("wrong", 0),
                item[1].get("top1_accuracy", 0),
            ),
        )[0]
        if best_name in slot_results_by_config:
            comparisons[f"{best_name}_vs_baseline"] = compare_configs(
                slot_results_by_config["heroes_baseline"],
                slot_results_by_config[best_name],
            )

    report = build_report(input_stats, slot_summaries, lineup_summaries, comparisons, configs)
    report_dir.mkdir(parents=True, exist_ok=True)
    json_path = report_dir / "hero_template_eval.json"
    md_path = report_dir / "hero_template_eval.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    print_report_summary(report)
    print(f"Report written to {json_path}")
    print(f"Markdown written to {md_path}")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate hero template matching A/B.")
    parser.add_argument("--gt-path", type=Path, default=DEFAULT_GT_PATH)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--limit", type=int, default=None, help="Limit slot samples for quick runs.")
    parser.add_argument("--quick", action="store_true", help="Run a reduced config set.")
    args = parser.parse_args()
    run_evaluation(args.gt_path, args.report_dir, limit=args.limit, quick=args.quick)


if __name__ == "__main__":
    main()
