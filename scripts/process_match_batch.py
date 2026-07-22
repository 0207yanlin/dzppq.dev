# -*- coding: utf-8 -*-
"""Run label -> DB import -> meta analysis for one screenshot batch."""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.match_ground_truth import DEFAULT_GT_PATH  # noqa: E402

DEFAULT_DB_PATH = ROOT / "data" / "match_latest.db"
DEFAULT_WORKERS = 4
META_SCRIPT = (
    ROOT / ".cursor" / "skills" / "dzppq-meta-analysis" / "scripts" / "analyze_latest_meta.py"
)
BATCH_RE = re.compile(r"^\d{4}$")


def default_batch_mmdd(today: date | None = None) -> str:
    current = today or date.today()
    return f"{current.month:02d}{current.day:02d}"


def normalize_batch(batch: str) -> str:
    value = batch.strip()
    if not BATCH_RE.fullmatch(value):
        raise ValueError(f"batch must be MMDD digits, got {batch!r}")
    return value


def screenshot_dir_for_batch(batch: str, *, root: Path = ROOT) -> Path:
    return (root / f"screenshots.{batch}").resolve()


def path_prefix_for_batch(batch: str) -> str:
    return f"screenshots.{batch}/"


def collect_screenshots(screenshot_dir: Path) -> list[Path]:
    return sorted(
        path for path in screenshot_dir.glob("*.png") if "_debug" not in path.parts
    )


def run_step(cmd: list[str], *, dry_run: bool = False) -> None:
    print(f">> {' '.join(cmd)}")
    if dry_run:
        return
    subprocess.run(cmd, check=True, cwd=str(ROOT))


def build_label_cmd(
    *,
    screenshot_dir: Path,
    gt_path: Path,
    workers: int,
) -> list[str]:
    return [
        sys.executable,
        str(ROOT / "scripts" / "label_match_ground_truth.py"),
        "--screenshot-dir",
        str(screenshot_dir),
        "--gt",
        str(gt_path),
        "--workers",
        str(workers),
        "label",
        "--all",
    ]


def build_import_cmd(
    *,
    screenshot_dir: Path,
    path_prefix: str,
    gt_path: Path,
    db_path: Path,
) -> list[str]:
    return [
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


def build_meta_cmd(*, db_path: Path) -> list[str]:
    return [
        sys.executable,
        str(META_SCRIPT),
        "--db",
        str(db_path),
    ]


def process_batch(
    *,
    batch: str,
    workers: int = DEFAULT_WORKERS,
    gt_path: Path = DEFAULT_GT_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    dry_run: bool = False,
    runner=run_step,
) -> None:
    batch = normalize_batch(batch)
    screenshot_dir = screenshot_dir_for_batch(batch)
    path_prefix = path_prefix_for_batch(batch)
    gt_path = gt_path.resolve()
    db_path = db_path.resolve()

    if not screenshot_dir.is_dir():
        raise SystemExit(f"Screenshot directory not found: {screenshot_dir}")
    pngs = collect_screenshots(screenshot_dir)
    if not pngs:
        raise SystemExit(f"No PNG files in {screenshot_dir}")
    if not META_SCRIPT.is_file():
        raise SystemExit(f"Meta analysis script not found: {META_SCRIPT}")

    print(
        f"Processing batch {batch}: {len(pngs)} screenshot(s), "
        f"workers={workers}, gt={gt_path.name}, db={db_path.name}"
    )

    print("\n[1/3] Label unverified screenshots")
    runner(
        build_label_cmd(
            screenshot_dir=screenshot_dir,
            gt_path=gt_path,
            workers=workers,
        ),
        dry_run=dry_run,
    )

    print("\n[2/3] Import batch into match database")
    runner(
        build_import_cmd(
            screenshot_dir=screenshot_dir,
            path_prefix=path_prefix,
            gt_path=gt_path,
            db_path=db_path,
        ),
        dry_run=dry_run,
    )

    print("\n[3/3] Generate environment analysis report")
    runner(build_meta_cmd(db_path=db_path), dry_run=dry_run)

    print(f"\nDone. batch={batch} screenshots={len(pngs)}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--batch",
        default=None,
        help="Screenshot batch MMDD (default: today's month/day)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=DEFAULT_WORKERS,
        help=f"Parallel workers for label prediction prefetch (default: {DEFAULT_WORKERS})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned commands without executing them",
    )
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.workers < 1:
        raise SystemExit("--workers must be >= 1")
    batch = args.batch if args.batch is not None else default_batch_mmdd()
    try:
        process_batch(batch=batch, workers=args.workers, dry_run=args.dry_run)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
