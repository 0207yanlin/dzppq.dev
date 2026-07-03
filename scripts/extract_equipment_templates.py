# -*- coding: utf-8 -*-
"""Extract individual equipment item ROIs from match screenshots."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.detect_equipment import (  # noqa: E402
    DEFAULT_CLASSIFIER_PATH,
    labels_from_predictions,
    load_classifier,
    load_model,
    predict_image_with_classifier,
)
from src.layout import (  # noqa: E402
    HERO_X_OFFSET,
    HERO_Y_OFFSET,
    NUM_HEROES,
    NUM_PLAYERS,
    SCREENSHOT_DIR,
    crop_roi,
    roi_valid,
)

OUTPUT_DIR = ROOT / "assets" / "templates" / "equipment"

# Absolute x coordinates from the notebook, before adding HERO_X_OFFSET[slot].
ITEM_BOXES_BY_COUNT = {
    1: [(606, 345, 629, 369)],
    2: [(593, 345, 616, 369), (618, 345, 641, 369)],
    3: [(581, 345, 604, 369), (605, 345, 628, 369), (630, 345, 654, 369)],
}


def collect_screenshots(screenshot_dir: Path) -> list[Path]:
    return sorted(path for path in screenshot_dir.glob("*.png") if "_debug" not in path.parts)


def resolve_screenshot(path_or_name: str | Path, screenshot_dir: Path) -> Path:
    path = Path(path_or_name)
    if path.exists():
        return path
    candidate = screenshot_dir / path.name
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"screenshot not found: {path_or_name}")


def equipment_item_boxes(player: int, slot: int, count: int) -> list[tuple[int, int, int, int]]:
    """Return item-level ROI boxes for a predicted equipment count."""
    boxes = []
    for x1, y1, x2, y2 in ITEM_BOXES_BY_COUNT.get(count, []):
        boxes.append(
            (
                x1 + HERO_X_OFFSET[slot],
                y1 + HERO_Y_OFFSET[player],
                x2 + HERO_X_OFFSET[slot],
                y2 + HERO_Y_OFFSET[player],
            )
        )
    return boxes


def safe_stem(path: Path) -> str:
    return path.stem.replace(" ", "_")


def extract_from_screenshot(
    img_path: Path,
    classifier,
    model,
    output_dir: Path,
    pad_mode: str,
    overwrite: bool,
) -> Counter:
    img = cv2.imread(str(img_path))
    if img is None:
        raise RuntimeError(f"failed to read screenshot: {img_path}")

    predictions = predict_image_with_classifier(
        img,
        classifier=classifier,
        model=model,
        pad_mode=pad_mode,
    )
    label_rows = labels_from_predictions(predictions)

    counts: Counter = Counter()
    for player in range(NUM_PLAYERS):
        for slot in range(NUM_HEROES):
            label = label_rows[player][slot]
            if label not in {"1", "2", "3"}:
                continue
            item_count = int(label)
            for item_index, box in enumerate(
                equipment_item_boxes(player, slot, item_count),
                start=1,
            ):
                roi = crop_roi(img, box)
                if not roi_valid(roi, box):
                    counts["invalid_roi"] += 1
                    continue
                filename = (
                    f"{safe_stem(img_path)}_p{player + 1}_h{slot + 1}"
                    f"_c{item_count}_item{item_index}.png"
                )
                save_path = output_dir / filename
                if save_path.exists() and not overwrite:
                    counts["skipped_existing"] += 1
                    continue
                ok = cv2.imwrite(str(save_path), roi)
                counts["saved" if ok else "failed_write"] += 1
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshot", nargs="?", help="Screenshot path or filename")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=SCREENSHOT_DIR,
        help="Directory containing PNG screenshots",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=OUTPUT_DIR,
        help="Directory where individual equipment ROI images are saved",
    )
    parser.add_argument(
        "--classifier",
        type=Path,
        default=DEFAULT_CLASSIFIER_PATH,
        help="Saved equipment classifier path",
    )
    parser.add_argument(
        "--pad-mode",
        choices=("black", "mean"),
        default="black",
        help="Padding mode used by the equipment classifier",
    )
    parser.add_argument("--device", default=None, help="Torch device, e.g. cpu or cuda")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite existing extracted ROI images",
    )
    args = parser.parse_args()

    screenshot_dir = args.screenshot_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    classifier = load_classifier(args.classifier)
    model = load_model(args.device)
    paths = (
        [resolve_screenshot(args.screenshot, screenshot_dir)]
        if args.screenshot
        else collect_screenshots(screenshot_dir)
    )
    if not paths:
        print(f"No PNG files in {screenshot_dir}")
        return

    total: Counter = Counter()
    for img_path in paths:
        counts = extract_from_screenshot(
            img_path=img_path,
            classifier=classifier,
            model=model,
            output_dir=output_dir,
            pad_mode=args.pad_mode,
            overwrite=args.overwrite,
        )
        total.update(counts)
        print(f"{img_path.name}: {dict(counts)}")

    print(f"Output: {output_dir}")
    print(f"Total: {dict(total)}")


if __name__ == "__main__":
    main()
