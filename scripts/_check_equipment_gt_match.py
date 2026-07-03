# -*- coding: utf-8 -*-
"""Compare equipment-count predictions against equipment_ground_truth.json."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.detect_equipment import labels_from_predictions
from src.match_ground_truth import PredictionContext

GT_PATH = ROOT / "data" / "equipment_ground_truth.json"
DEFAULT_SCREENSHOT_DIR = ROOT / "screenshots"
TARGET_DIR = ROOT / "screenshots.0701"


def compare_dir(screenshot_dir: Path, labels_gt: dict, ctx: PredictionContext) -> None:
    names = sorted(labels_gt)
    total = correct = 0
    missing_files: list[str] = []
    wrong_screenshots: list[tuple[str, list, str]] = []

    for name in names:
        img_path = screenshot_dir / name
        if not img_path.exists():
            missing_files.append(name)
            continue
        img = cv2.imread(str(img_path))
        if img is None:
            missing_files.append(name)
            continue
        pred_rows = labels_from_predictions(ctx.predict_equipment_counts(img, name))
        gt_rows = labels_gt[name]
        shot_total = shot_correct = 0
        diffs: list[tuple[int, int, str, str]] = []
        for player, (gt_row, pred_row) in enumerate(zip(gt_rows, pred_rows)):
            for slot, (gt_label, pred_label) in enumerate(zip(gt_row, pred_row)):
                shot_total += 1
                total += 1
                if gt_label == pred_label:
                    shot_correct += 1
                    correct += 1
                else:
                    diffs.append((player + 1, slot + 1, gt_label, pred_label))
        if diffs:
            wrong_screenshots.append((name, diffs, f"{shot_correct}/{shot_total}"))

    print(f"\n=== {screenshot_dir.name} ===")
    print(f"GT entries checked: {len(names)}")
    print(f"Missing files: {len(missing_files)}")
    if total:
        print(f"Slot accuracy: {correct}/{total} = {correct / total:.4f}")
    print(f"Screenshots with errors: {len(wrong_screenshots)}")
    for name, diffs, acc in wrong_screenshots[:15]:
        print(f"  {name} ({acc})")
        for diff in diffs[:6]:
            print(f"    player={diff[0]} slot={diff[1]}: gt={diff[2]} pred={diff[3]}")
        if len(diffs) > 6:
            print(f"    ... +{len(diffs) - 6} more")


def main() -> None:
    gt = json.loads(GT_PATH.read_text(encoding="utf-8"))
    labels_gt = gt["labels"]
    ctx = PredictionContext(method="classifier")
    ctx.initialize(DEFAULT_SCREENSHOT_DIR)

    compare_dir(DEFAULT_SCREENSHOT_DIR, labels_gt, ctx)

    target_names = sorted(p.name for p in TARGET_DIR.glob("*.png"))
    in_gt = [n for n in target_names if n in labels_gt]
    missing = [n for n in target_names if n not in labels_gt]
    print(f"\n=== screenshots.0701 coverage ===")
    print(f"Total screenshots: {len(target_names)}")
    print(f"In equipment GT: {len(in_gt)}")
    print(f"Missing from equipment GT: {len(missing)}")
    if in_gt:
        subset = {name: labels_gt[name] for name in in_gt}
        compare_dir(TARGET_DIR, subset, ctx)


if __name__ == "__main__":
    main()
