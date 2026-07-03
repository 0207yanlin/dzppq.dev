# -*- coding: utf-8 -*-
"""Build SQLite match database from match_ground_truth.json."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.match_db import db_summary, import_ground_truth, init_match_db  # noqa: E402
from src.match_ground_truth import (  # noqa: E402
    DEFAULT_GT_PATH,
    DEFAULT_SCREENSHOT_DIR,
    load_match_ground_truth,
)


def collect_screenshots(screenshot_dir: Path) -> list[Path]:
    return sorted(
        path for path in screenshot_dir.glob("*.png") if "_debug" not in path.parts
    )


def run_batch_predict(
    screenshot_dir: Path,
    gt_path: Path,
    *,
    quiet: bool = True,
    force: bool = False,
) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "label_match_ground_truth.py"),
        "--screenshot-dir",
        str(screenshot_dir),
        "--gt",
        str(gt_path),
    ]
    if quiet:
        cmd.append("--quiet")
    cmd.extend(["predict", "--write"])
    print("Running batch prediction for all screenshots...")
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def command_build(args: argparse.Namespace) -> None:
    screenshot_dir = args.screenshot_dir.resolve()
    gt_path = args.gt.resolve()
    db_path = args.db.resolve()

    png_count = len(collect_screenshots(screenshot_dir))
    if png_count == 0:
        raise SystemExit(f"No PNG files in {screenshot_dir}")

    if args.predict:
        run_batch_predict(screenshot_dir, gt_path, quiet=args.quiet, force=args.force)

    gt_data = load_match_ground_truth(gt_path)
    gt_in_dir = sum(
        1
        for entry in gt_data.get("screenshots", {}).values()
        if entry.get("path", "").replace("\\", "/").startswith(args.path_prefix)
    )
    if gt_in_dir < png_count:
        print(
            f"Warning: GT has {gt_in_dir} entries for prefix {args.path_prefix!r}, "
            f"but directory has {png_count} PNG files."
        )

    conn = init_match_db(db_path)
    stats = import_ground_truth(
        conn,
        gt_data,
        path_prefix=args.path_prefix,
        force=args.force,
    )
    summary = db_summary(conn)
    conn.close()

    print(f"Database: {db_path}")
    print(f"Import: inserted={stats['inserted']} skipped={stats['skipped']} replaced={stats['replaced']}")
    print("Summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    expected_players = summary["matches"] * 8
    expected_cards = summary["matches"] * 24
    if summary["players"] != expected_players:
        print(f"Warning: expected {expected_players} players, got {summary['players']}")
    if summary["cards"] != expected_cards:
        print(f"Warning: expected {expected_cards} cards, got {summary['cards']}")
    if summary["matches"] != png_count and not args.allow_partial:
        print(
            f"Warning: expected {png_count} matches, got {summary['matches']} "
            "(use --allow-partial to silence)"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gt", type=Path, default=DEFAULT_GT_PATH)
    parser.add_argument("--screenshot-dir", type=Path, default=DEFAULT_SCREENSHOT_DIR)
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "data" / "matches_0701.db",
    )
    parser.add_argument(
        "--path-prefix",
        default="screenshots.0701/",
        help="Only import GT entries whose path starts with this prefix",
    )
    parser.add_argument(
        "--predict",
        action="store_true",
        help="Run label_match_ground_truth predict --write before import",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Replace existing DB rows / re-predict all screenshots",
    )
    parser.add_argument("--quiet", action="store_true", default=True)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Do not warn when match count differs from PNG count",
    )
    parser.set_defaults(func=command_build)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
