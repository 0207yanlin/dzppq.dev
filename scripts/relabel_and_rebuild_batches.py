# -*- coding: utf-8 -*-
"""Re-predict GT labels and rebuild DB rows for a range of screenshot batches."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_BATCHES = ("0702", "0703", "0704", "0705", "0706", "0707")


def _batch_range(start: str, end: str) -> list[str]:
    start_i = int(start)
    end_i = int(end)
    if end_i < start_i:
        raise ValueError(f"end batch {end!r} is before start batch {start!r}")
    return [f"{value:04d}"[-4:] for value in range(start_i, end_i + 1)]


def _count_pngs(screenshot_dir: Path) -> int:
    return sum(
        1
        for path in screenshot_dir.glob("*.png")
        if "_debug" not in path.parts
    )


def _run(cmd: list[str], *, dry_run: bool, show_cmd: bool = False) -> None:
    if show_cmd:
        print(f">> {' '.join(cmd)}")
    if dry_run:
        return
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def command_relabel(args: argparse.Namespace) -> None:
    batches = list(args.batches) if args.batches else _batch_range(args.from_batch, args.to_batch)
    gt_path = args.gt.resolve()
    db_path = args.db.resolve()

    batch_dirs: list[tuple[str, Path, int]] = []
    total_pngs = 0
    for batch in batches:
        screenshot_dir = (ROOT / f"screenshots.{batch}").resolve()
        if not screenshot_dir.is_dir():
            raise SystemExit(f"Screenshot directory not found: {screenshot_dir}")
        png_count = _count_pngs(screenshot_dir)
        if png_count == 0:
            raise SystemExit(f"No PNG files in {screenshot_dir}")
        batch_dirs.append((batch, screenshot_dir, png_count))
        total_pngs += png_count

    steps = []
    if not args.db_only:
        steps.append("predict")
    if not args.predict_only:
        steps.append("import")
    step_text = " + ".join(steps) if steps else "noop"

    print(
        f"Relabel {len(batch_dirs)} batch(es), {total_pngs} screenshot(s), "
        f"workers={args.workers}, steps={step_text}"
    )
    if not args.quiet:
        print("Per-batch predict progress will show as: Predicting [n/total] pct% filename")

    completed_pngs = 0
    for batch_index, (batch, screenshot_dir, png_count) in enumerate(batch_dirs, start=1):
        path_prefix = f"screenshots.{batch}/"
        batch_header = (
            f"\n[Batch {batch_index}/{len(batch_dirs)}] {batch} "
            f"({png_count} png, overall {completed_pngs}/{total_pngs} done)"
        )
        print(batch_header)

        if not args.db_only:
            print(f"  -> predicting {png_count} screenshot(s)...")
            predict_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "label_match_ground_truth.py"),
                "--screenshot-dir",
                str(screenshot_dir),
                "--gt",
                str(gt_path),
                "--workers",
                str(args.workers),
                "predict",
                "--write",
            ]
            if args.force:
                predict_cmd.append("--force")
            if args.quiet:
                predict_cmd.append("--quiet")
            _run(predict_cmd, dry_run=args.dry_run, show_cmd=args.verbose)
            if not args.dry_run:
                completed_pngs += png_count
                print(f"  -> predict done ({completed_pngs}/{total_pngs})")

        if not args.predict_only:
            print(f"  -> importing GT into {db_path.name}...")
            build_cmd = [
                sys.executable,
                str(ROOT / "scripts" / "build_match_database.py"),
                "--screenshot-dir",
                str(screenshot_dir),
                "--path-prefix",
                path_prefix,
                "--gt",
                str(gt_path),
                "--db",
                str(db_path),
                "--force",
                "--allow-partial",
            ]
            if args.quiet:
                build_cmd.append("--quiet")
            _run(build_cmd, dry_run=args.dry_run, show_cmd=args.verbose)

    print(f"\nDone. processed {completed_pngs}/{total_pngs} screenshot(s) across {len(batch_dirs)} batch(es).")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--from-batch",
        default=DEFAULT_BATCHES[0],
        help="First batch MMDD (default: 0702)",
    )
    parser.add_argument(
        "--to-batch",
        default=DEFAULT_BATCHES[-1],
        help="Last batch MMDD (default: 0707)",
    )
    parser.add_argument(
        "--batches",
        nargs="+",
        help="Explicit batch list, e.g. 0702 0705 0707",
    )
    parser.add_argument("--workers", type=int, default=4, help="Parallel workers (default: 4)")
    parser.add_argument(
        "--force",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Force re-predict and replace DB rows (default: true)",
    )
    parser.add_argument(
        "--gt",
        type=Path,
        default=ROOT / "data" / "match_ground_truth.json",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=ROOT / "data" / "match_latest.db",
    )
    parser.add_argument(
        "--predict-only",
        action="store_true",
        help="Only update GT predictions, skip DB import",
    )
    parser.add_argument(
        "--db-only",
        action="store_true",
        help="Only import existing GT into DB, skip prediction",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print commands without executing",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Hide per-screenshot predict progress from label_match_ground_truth",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print underlying subprocess commands",
    )
    parser.set_defaults(func=command_relabel)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.predict_only and args.db_only:
        raise SystemExit("Use only one of --predict-only / --db-only")
    args.func(args)


if __name__ == "__main__":
    main()
