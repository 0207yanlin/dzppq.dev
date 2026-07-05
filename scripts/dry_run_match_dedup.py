# -*- coding: utf-8 -*-
"""Read-only predict + whole-match similarity dry run (does not write GT or DB)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from itertools import combinations
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.match_db import (  # noqa: E402
    DEFAULT_SIMILARITY_THRESHOLD,
    build_match_fingerprint,
    cluster_similar_entries,
    compare_match_fingerprints,
    is_similar_match,
)
from src.match_ground_truth import (  # noqa: E402
    PredictionContext,
    build_screenshot_entry,
)


def collect_screenshots(screenshot_dir: Path) -> list[Path]:
    return sorted(
        path for path in screenshot_dir.glob("MuMu-*.png") if "_debug" not in path.parts
    )


def _predict_worker(args_tuple: tuple[str, str, str, str, int]) -> tuple[str, dict, float]:
    screenshot_path_str, screenshot_dir_str, method, device, search_radius = args_tuple
    screenshot_path = Path(screenshot_path_str)
    screenshot_dir = Path(screenshot_dir_str)
    ctx = PredictionContext(
        method=method,
        device=device or None,
        search_radius=search_radius,
        verbose=False,
    )
    ctx.initialize(screenshot_dir)
    started = time.perf_counter()
    prediction = ctx.predict_screenshot(screenshot_path)
    elapsed = time.perf_counter() - started
    entry = build_screenshot_entry(
        screenshot_path,
        prediction,
        verified=False,
        template_metadata=ctx.template_metadata,
    )
    return screenshot_path.name, entry, elapsed


def predict_entries(
    paths: list[Path],
    screenshot_dir: Path,
    *,
    workers: int = 1,
    method: str = "classifier",
    device: str | None = None,
    search_radius: int = 2,
) -> list[tuple[str, dict[str, object], float]]:
    if workers <= 1:
        ctx = PredictionContext(
            method=method,
            device=device,
            search_radius=search_radius,
            verbose=False,
        )
        ctx.initialize(screenshot_dir)
        results: list[tuple[str, dict[str, object], float]] = []
        for path in paths:
            started = time.perf_counter()
            prediction = ctx.predict_screenshot(path)
            elapsed = time.perf_counter() - started
            entry = build_screenshot_entry(
                path,
                prediction,
                verified=False,
                template_metadata=ctx.template_metadata,
            )
            results.append((path.name, entry, elapsed))
        return results

    task_args = [
        (str(path), str(screenshot_dir), method, device or "", search_radius)
        for path in paths
    ]
    results = []
    with ProcessPoolExecutor(max_workers=workers) as executor:
        futures = [executor.submit(_predict_worker, args) for args in task_args]
        for future in as_completed(futures):
            results.append(future.result())
    results.sort(key=lambda item: item[0])
    return results


def build_report(
    items: list[tuple[str, dict[str, object]]],
    *,
    threshold: float,
    min_hero_rank: float,
) -> dict[str, object]:
    clusters = cluster_similar_entries(
        items,
        threshold=threshold,
        min_hero_rank=min_hero_rank,
    )
    edges: list[dict[str, object]] = []
    records = [(name, build_match_fingerprint(entry)) for name, entry in items]
    for (name_a, fp_a), (name_b, fp_b) in combinations(records, 2):
        similar, metrics = is_similar_match(
            fp_a,
            fp_b,
            threshold=threshold,
            min_hero_rank=min_hero_rank,
        )
        if metrics["score"] >= max(threshold - 0.1, 0.78):
            edges.append(
                {
                    "a": name_a,
                    "b": name_b,
                    "duplicate": similar,
                    **{key: round(value, 4) for key, value in metrics.items()},
                }
            )
    edges.sort(key=lambda row: row["score"], reverse=True)

    unique_matches = len(items) - sum(len(group) - 1 for group in clusters)
    return {
        "screenshot_count": len(items),
        "cluster_count": len(clusters),
        "estimated_unique_matches": unique_matches,
        "threshold": threshold,
        "min_hero_rank": min_hero_rank,
        "clusters": [
            {
                "keep": group[0],
                "members": group,
                "size": len(group),
            }
            for group in clusters
        ],
        "top_edges": edges[:100],
    }


def command_dry_run(args: argparse.Namespace) -> None:
    screenshot_dir = args.screenshot_dir.resolve()
    paths = collect_screenshots(screenshot_dir)
    if not paths:
        raise SystemExit(f"No MuMu PNG files in {screenshot_dir}")

    started = time.perf_counter()
    predicted = predict_entries(
        paths,
        screenshot_dir,
        workers=args.workers,
        method=args.method,
        device=args.device,
        search_radius=args.search_radius,
    )
    elapsed = time.perf_counter() - started
    items = [(name, entry) for name, entry, _ in predicted]
    report = build_report(
        items,
        threshold=args.similarity_threshold,
        min_hero_rank=args.min_hero_rank,
    )
    report["predict_seconds"] = round(elapsed, 2)
    report["workers"] = args.workers
    report["per_screenshot_seconds"] = round(
        sum(item[2] for item in predicted) / max(len(predicted), 1),
        2,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote dry-run report to {args.output}")


def command_benchmark(args: argparse.Namespace) -> None:
    screenshot_dir = args.screenshot_dir.resolve()
    paths = collect_screenshots(screenshot_dir)
    if not paths:
        raise SystemExit(f"No MuMu PNG files in {screenshot_dir}")
    if args.limit:
        paths = paths[: args.limit]

    results: list[dict[str, object]] = []
    baseline_names: list[str] | None = None
    for workers in args.worker_grid:
        started = time.perf_counter()
        predicted = predict_entries(
            paths,
            screenshot_dir,
            workers=workers,
            method=args.method,
            device=args.device,
            search_radius=args.search_radius,
        )
        elapsed = time.perf_counter() - started
        names = [name for name, _, _ in predicted]
        if baseline_names is None:
            baseline_names = names
        elif names != baseline_names:
            raise SystemExit("Worker run produced a different screenshot ordering set.")

        results.append(
            {
                "workers": workers,
                "screenshots": len(predicted),
                "wall_seconds": round(elapsed, 2),
                "avg_predict_seconds": round(
                    sum(item[2] for item in predicted) / max(len(predicted), 1),
                    2,
                ),
            }
        )

    report = {
        "screenshot_dir": str(screenshot_dir),
        "screenshot_count": len(paths),
        "runs": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote benchmark report to {args.output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--screenshot-dir",
        type=Path,
        default=ROOT / "screenshots.0705",
    )
    common.add_argument(
        "--method",
        choices=("classifier", "1nn"),
        default="classifier",
    )
    common.add_argument("--device", default=None)
    common.add_argument("--search-radius", type=int, default=2)
    common.add_argument(
        "--similarity-threshold",
        type=float,
        default=DEFAULT_SIMILARITY_THRESHOLD,
    )
    common.add_argument(
        "--min-hero-rank",
        type=float,
        default=0.82,
    )
    common.add_argument("--workers", type=int, default=1)

    subparsers = parser.add_subparsers(dest="command", required=True)

    dry_run = subparsers.add_parser("dry-run", parents=[common], help="Predict and cluster duplicate matches")
    dry_run.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "dry_run_match_dedup_0705.json",
    )
    dry_run.set_defaults(func=command_dry_run)

    benchmark = subparsers.add_parser(
        "benchmark",
        parents=[common],
        help="Compare read-only predict throughput across worker counts",
    )
    benchmark.add_argument(
        "--worker-grid",
        type=int,
        nargs="+",
        default=[1, 2, 4],
    )
    benchmark.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Optional cap on screenshot count for faster benchmark",
    )
    benchmark.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "predict_parallel_benchmark_0705.json",
    )
    benchmark.set_defaults(func=command_benchmark)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
