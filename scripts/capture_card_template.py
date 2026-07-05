# -*- coding: utf-8 -*-
"""Crop a card icon from a match screenshot and save it as a template."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.layout import NUM_CARDS, NUM_PLAYERS, SCREENSHOT_DIR  # noqa: E402
from src.template_capture import (  # noqa: E402
    card_template_path,
    imread_image,
    save_card_template,
    template_exists,
)


def resolve_screenshot(path_or_name: str | Path, screenshot_dir: Path) -> Path:
    path = Path(path_or_name)
    if path.exists():
        return path.resolve()
    candidate = screenshot_dir / path.name
    if candidate.exists():
        return candidate.resolve()
    raise FileNotFoundError(f"screenshot not found: {path_or_name}")


def parse_row(value: str) -> int:
    row = int(value)
    if not 1 <= row <= NUM_PLAYERS:
        raise argparse.ArgumentTypeError(f"row must be between 1 and {NUM_PLAYERS}")
    return row - 1


def parse_col(value: str) -> int:
    col = int(value)
    if not 1 <= col <= NUM_CARDS:
        raise argparse.ArgumentTypeError(f"col must be between 1 and {NUM_CARDS}")
    return col - 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Crop a card icon from a match screenshot and save it to "
            "assets/templates/cards/. Does not modify ground truth."
        ),
    )
    parser.add_argument(
        "screenshot",
        help=(
            "Screenshot path: absolute path, or filename resolved under --screenshot-dir"
        ),
    )
    parser.add_argument(
        "--row",
        type=parse_row,
        required=True,
        metavar="N",
        help=f"Player row, 1-{NUM_PLAYERS} (top to bottom)",
    )
    parser.add_argument(
        "--col",
        type=parse_col,
        required=True,
        metavar="N",
        help=f"Card column, 1-{NUM_CARDS} (left to right)",
    )
    parser.add_argument(
        "--name",
        required=True,
        help="Template filename stem, e.g. 蓝·新卡名",
    )
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=SCREENSHOT_DIR,
        help="Directory used to resolve screenshot filenames",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Overwrite an existing template file",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    screenshot_dir = args.screenshot_dir.resolve()
    img_path = resolve_screenshot(args.screenshot, screenshot_dir)

    template_path = card_template_path(args.name)
    if template_exists(template_path) and not args.overwrite:
        raise SystemExit(
            f"Template already exists: {template_path.name}. Use --overwrite to replace it."
        )

    img = imread_image(img_path)
    if img is None:
        raise SystemExit(f"failed to read screenshot: {img_path}")

    saved = save_card_template(
        img,
        args.row,
        args.col,
        args.name,
        overwrite=args.overwrite,
    )
    if saved is None:
        raise SystemExit("template was not saved")

    print(f"Screenshot: {img_path}")
    print(f"Row/col: {args.row + 1}/{args.col + 1}")
    print(f"Saved: {saved}")


if __name__ == "__main__":
    main()
