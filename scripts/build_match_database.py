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

from src.match_db import (  # noqa: E402
    DEFAULT_SIMILARITY_THRESHOLD,
    db_summary,
    import_ground_truth,
    init_match_db,
)
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
    workers: int = 1,
) -> None:
    cmd = [
        sys.executable,
        str(ROOT / "scripts" / "label_match_ground_truth.py"),
        "--screenshot-dir",
        str(screenshot_dir),
        "--gt",
        str(gt_path),
        "--workers",
        str(workers),
    ]
    if quiet:
        cmd.append("--quiet")
    if force:
        cmd.append("--force")
    cmd.extend(["predict", "--write"])
    print("Running batch prediction for all screenshots...")
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def command_build(args: argparse.Namespace) -> None:
    gt_path = args.gt.resolve()
    db_path = args.db.resolve()
    path_prefix = args.path_prefix or ""

    png_count: int | None = None
    if args.predict:
        screenshot_dir = args.screenshot_dir.resolve()
        png_count = len(collect_screenshots(screenshot_dir))
        if png_count == 0:
            raise SystemExit(f"No PNG files in {screenshot_dir}")
        run_batch_predict(
            screenshot_dir,
            gt_path,
            quiet=args.quiet,
            force=args.force,
            workers=args.workers,
        )

    gt_data = load_match_ground_truth(gt_path)
    gt_in_dir = sum(
        1
        for entry in gt_data.get("screenshots", {}).values()
        if not path_prefix
        or entry.get("path", "").replace("\\", "/").startswith(path_prefix)
    )
    if gt_in_dir == 0:
        raise SystemExit(f"No GT entries matched path_prefix={path_prefix!r}")

    if path_prefix and not args.predict:
        screenshot_dir = args.screenshot_dir.resolve()
        png_count = len(collect_screenshots(screenshot_dir))
        if png_count == 0:
            raise SystemExit(f"No PNG files in {screenshot_dir}")
        if gt_in_dir < png_count:
            print(
                f"Warning: GT has {gt_in_dir} entries for prefix {path_prefix!r}, "
                f"but directory has {png_count} PNG files."
            )
    elif not path_prefix:
        print(f"Importing {gt_in_dir} GT entries across all screenshot batches")

    conn = init_match_db(db_path)
    stats = import_ground_truth(
        conn,
        gt_data,
        path_prefix=path_prefix,
        force=args.force,
        dedupe_similar=not args.no_dedupe_similar,
        similarity_threshold=args.similarity_threshold,
        min_hero_rank=args.min_hero_rank,
        min_pairs=args.min_pairs,
    )
    summary = db_summary(conn)
    conn.close()

    print(f"Database: {db_path}")
    print(
        "Import: "
        f"inserted={stats['inserted']} "
        f"skipped={stats['skipped']} "
        f"replaced={stats['replaced']} "
        f"skipped_similar={stats.get('skipped_similar', 0)}"
    )
    similar_skips = stats.get("similar_skips") or []
    for item in similar_skips[:20]:
        print(
            "Similar skip: "
            f"{item['screenshot_name']} -> {item['duplicate_of']} "
            f"(score={item['score']}, hero={item['hero_rank']})"
        )
    if len(similar_skips) > 20:
        print(f"... and {len(similar_skips) - 20} more similar skips")
    print("Summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")

    expected_players = summary["matches"] * 8
    expected_cards = summary["matches"] * 24
    if summary["players"] != expected_players:
        print(f"Warning: expected {expected_players} players, got {summary['players']}")
    if summary["cards"] != expected_cards:
        print(f"Warning: expected {expected_cards} cards, got {summary['cards']}")
    expected_matches = gt_in_dir - stats.get("skipped_similar", 0)
    if summary["matches"] != expected_matches and not args.allow_partial:
        print(
            f"Warning: expected about {expected_matches} unique matches after dedupe "
            f"(gt_in_dir={gt_in_dir}, skipped_similar={stats.get('skipped_similar', 0)}), "
            f"got {summary['matches']} "
            "(use --allow-partial to silence)"
        )
    elif (
        png_count is not None
        and summary["matches"] != png_count
        and not args.allow_partial
        and args.no_dedupe_similar
        and path_prefix
    ):
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
        default=ROOT / "data" / "match_latest.db",
    )
    parser.add_argument(
        "--path-prefix",
        default="",
        help="Only import GT entries whose path starts with this prefix; empty imports all batches",
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
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel workers when --predict is used (default: 1)",
    )
    parser.add_argument("--quiet", action="store_true", default=True)
    parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Do not warn when match count differs from PNG count",
    )
    parser.add_argument(
        "--no-dedupe-similar",
        action="store_true",
        help="Disable whole-match similarity deduplication during import",
    )
    parser.add_argument(
        "--similarity-threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
        help="Similarity score threshold for duplicate match detection",
    )
    parser.add_argument(
        "--min-hero-rank",
        type=float,
        default=0.82,
        help="Minimum per-rank hero similarity required for duplicate detection",
    )
    parser.add_argument(
        "--min-pairs",
        type=float,
        default=0.99,
        help="Minimum pairs similarity required for duplicate detection",
    )
    parser.set_defaults(func=command_build)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
