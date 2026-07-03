# -*- coding: utf-8 -*-
"""Verify match database integrity against match_ground_truth.json."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.match_db import db_summary  # noqa: E402
from src.match_ground_truth import DEFAULT_GT_PATH, load_match_ground_truth  # noqa: E402


def verify(args: argparse.Namespace) -> dict:
    gt = load_match_ground_truth(args.gt)
    gt_names = [
        name
        for name, entry in gt.get("screenshots", {}).items()
        if entry.get("path", "").replace("\\", "/").startswith(args.path_prefix)
    ]
    png_names = sorted(
        p.name for p in args.screenshot_dir.glob("*.png") if "_debug" not in p.parts
    )

    conn = sqlite3.connect(str(args.db))
    summary = db_summary(conn)
    db_names = {
        row[0]
        for row in conn.execute("SELECT screenshot_name FROM matches").fetchall()
    }
    conn.close()

    report = {
        "gt_count": len(gt_names),
        "png_count": len(png_names),
        "db_matches": summary["matches"],
        "summary": summary,
        "gt_not_png": sorted(set(gt_names) - set(png_names)),
        "png_not_gt": sorted(set(png_names) - set(gt_names)),
        "db_not_png": sorted(db_names - set(png_names)),
    }
    report["players_ok"] = summary["players"] == summary["matches"] * 8
    report["cards_ok"] = summary["cards"] == summary["matches"] * 24
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gt", type=Path, default=DEFAULT_GT_PATH)
    parser.add_argument("--db", type=Path, default=ROOT / "data" / "matches_0701.db")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=ROOT / "screenshots.0701",
    )
    parser.add_argument(
        "--path-prefix",
        default="screenshots.0701/",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "matches_0701_report.json",
    )
    parser.set_defaults(func=verify)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
