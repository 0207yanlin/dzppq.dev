# -*- coding: utf-8 -*-
"""Predict concrete equipment names for screenshots."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.detect_equipment_items import (  # noqa: E402
    detect_equipment_items,
    load_equipment_templates,
)
from src.layout import SCREENSHOT_DIR  # noqa: E402

DEFAULT_COUNTS_PATH = ROOT / "data" / "equipment_ground_truth.json"


def resolve_screenshot(path_or_name: str | Path, screenshot_dir: Path) -> Path:
    path = Path(path_or_name)
    if path.exists():
        return path
    candidate = screenshot_dir / path.name
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"screenshot not found: {path_or_name}")


def load_counts(path: Path, screenshot_name: str) -> list[list[str]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return data["labels"][screenshot_name]
    except KeyError as exc:
        raise KeyError(f"no equipment-count labels for {screenshot_name}") from exc


def format_predictions(predictions: list[list[list[dict]]]) -> str:
    lines = []
    for player, row in enumerate(predictions, start=1):
        parts = []
        for slot, items in enumerate(row, start=1):
            if not items:
                continue
            labels = ", ".join(
                f"{item['label']}({item['score']:.3f})" for item in items
            )
            parts.append(f"H{slot}: {labels}")
        lines.append(f"玩家{player}: " + (" | ".join(parts) if parts else "无装备"))
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("screenshot", help="Screenshot path or filename")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=SCREENSHOT_DIR,
        help="Directory containing screenshots",
    )
    parser.add_argument(
        "--counts",
        type=Path,
        default=DEFAULT_COUNTS_PATH,
        help="Equipment-count labels JSON",
    )
    parser.add_argument(
        "--search-radius",
        type=int,
        default=2,
        help="Pixel radius for local ROI search",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    img_path = resolve_screenshot(args.screenshot, args.screenshot_dir)
    img = cv2.imread(str(img_path))
    if img is None:
        raise RuntimeError(f"failed to read screenshot: {img_path}")
    counts = load_counts(args.counts, img_path.name)
    templates = load_equipment_templates()
    predictions = detect_equipment_items(
        img,
        counts,
        templates,
        search_radius=args.search_radius,
    )
    print(img_path.name)
    print(format_predictions(predictions))


if __name__ == "__main__":
    main()
