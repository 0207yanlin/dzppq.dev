# -*- coding: utf-8 -*-
"""Baseline snapshot, equipment equivalence check, and speedup report."""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402

from src.detect_equipment import labels_from_predictions  # noqa: E402
from src.detect_equipment_items import detect_equipment_items  # noqa: E402
from src.match_ground_truth import (  # noqa: E402
    DEFAULT_GT_PATH,
    DEFAULT_SCREENSHOT_DIR,
    PredictionContext,
    load_match_ground_truth,
)

VERIFIED_SCREENSHOTS = [
    "MuMu-20260701-234311-208.png",
    "MuMu-20260701-234317-401.png",
    "MuMu-20260701-234321-963.png",
    "MuMu-20260701-234330-238.png",
    "MuMu-20260701-234334-722.png",
    "MuMu-20260701-234338-481.png",
    "MuMu-20260701-234342-767.png",
]

SCORE_TOLERANCE = 1e-6


def _extract_equipment_items(entry: dict) -> list[dict]:
    items: list[dict] = []
    for player in entry.get("players", []):
        for hero in player.get("heroes", []):
            eq_count = hero.get("equipment_count", "-")
            if eq_count == "-":
                continue
            for idx, label in enumerate(hero.get("equipments") or []):
                items.append(
                    {
                        "player": player["row_index"],
                        "slot": hero["slot_index"],
                        "item_index": idx,
                        "label": label,
                    }
                )
    return items


def _extract_equipment_preds(preds: list[list[list[dict]]]) -> list[dict]:
    items: list[dict] = []
    for player_idx, row in enumerate(preds):
        for slot_idx, slot_items in enumerate(row):
            for item in slot_items:
                items.append(
                    {
                        "player": player_idx,
                        "slot": slot_idx,
                        "item_index": item["item_index"],
                        "label": item["label"],
                        "score": float(item["score"]),
                        "shift": tuple(item["shift"]),
                        "top": [(name, float(score)) for name, score in item["top"]],
                    }
                )
    return items


def compare_equipment_preds(
    legacy_items: list[dict],
    batch_items: list[dict],
) -> list[str]:
    errors: list[str] = []
    legacy_map = {
        (item["player"], item["slot"], item["item_index"]): item
        for item in legacy_items
    }
    batch_map = {
        (item["player"], item["slot"], item["item_index"]): item
        for item in batch_items
    }
    if legacy_map.keys() != batch_map.keys():
        errors.append(
            f"slot mismatch: legacy={len(legacy_map)} batch={len(batch_map)}"
        )
    for key in sorted(legacy_map.keys()):
        legacy = legacy_map[key]
        batch = batch_map.get(key)
        if batch is None:
            errors.append(f"missing batch item for {key}")
            continue
        if legacy["label"] != batch["label"]:
            errors.append(
                f"{key}: label legacy={legacy['label']} batch={batch['label']}"
            )
        if legacy["shift"] != batch["shift"]:
            errors.append(
                f"{key}: shift legacy={legacy['shift']} batch={batch['shift']}"
            )
        if abs(legacy["score"] - batch["score"]) > SCORE_TOLERANCE:
            errors.append(
                f"{key}: score legacy={legacy['score']:.12f} "
                f"batch={batch['score']:.12f}"
            )
        legacy_top = legacy["top"]
        batch_top = batch["top"]
        if len(legacy_top) != len(batch_top):
            errors.append(f"{key}: top length mismatch")
            continue
        for idx, ((l_name, l_score), (b_name, b_score)) in enumerate(
            zip(legacy_top, batch_top)
        ):
            if l_name != b_name:
                errors.append(
                    f"{key}: top[{idx}] label legacy={l_name} batch={b_name}"
                )
            if abs(l_score - b_score) > SCORE_TOLERANCE:
                errors.append(
                    f"{key}: top[{idx}] score legacy={l_score:.12f} "
                    f"batch={b_score:.12f}"
                )
    return errors


def run_snapshot(
    ctx: PredictionContext,
    screenshot_dir: Path,
    names: list[str],
    *,
    use_legacy_equipment: bool,
) -> dict:
    results: dict[str, dict] = {}
    for name in names:
        img_path = screenshot_dir / name
        prediction = ctx.predict_screenshot(
            img_path,
            use_legacy_equipment=use_legacy_equipment,
            return_timings=True,
        )
        timings = prediction.pop("_timings", {})
        results[name] = {
            "timings": timings,
            "players": deepcopy(prediction["players"]),
            "pairs": prediction["pairs"],
            "highlight_player": prediction.get("highlight_player"),
        }
    return results


def run_equipment_only_compare(
    ctx: PredictionContext,
    screenshot_dir: Path,
    name: str,
) -> tuple[list[dict], list[dict], float, float]:
    import time

    img_path = screenshot_dir / name
    img = cv2.imread(str(img_path))
    assert img is not None
    counts = labels_from_predictions(ctx.predict_equipment_counts(img, name))

    started = time.perf_counter()
    legacy_preds = detect_equipment_items(
        img,
        counts,
        ctx.equipment_templates,
        search_radius=ctx.search_radius,
        use_legacy=True,
    )
    legacy_time = time.perf_counter() - started

    started = time.perf_counter()
    batch_preds = detect_equipment_items(
        img,
        counts,
        ctx.equipment_templates,
        search_radius=ctx.search_radius,
        batch_index=ctx.equipment_batch_index,
        use_legacy=False,
    )
    batch_time = time.perf_counter() - started
    return (
        _extract_equipment_preds(legacy_preds),
        _extract_equipment_preds(batch_preds),
        legacy_time,
        batch_time,
    )


def summarize_timings(results: dict[str, dict]) -> dict[str, float]:
    totals: dict[str, float] = {}
    count = len(results)
    for entry in results.values():
        for stage, value in entry["timings"].items():
            totals[stage] = totals.get(stage, 0.0) + value
    return {stage: value / count for stage, value in totals.items()}


def command_snapshot(args: argparse.Namespace) -> None:
    ctx = PredictionContext(method=args.method, verbose=False)
    ctx.initialize(args.screenshot_dir.resolve())
    names = args.names or VERIFIED_SCREENSHOTS
    optimized = run_snapshot(ctx, args.screenshot_dir, names, use_legacy_equipment=False)
    output = {
        "screenshots": names,
        "optimized": optimized,
        "avg_timings": summarize_timings(optimized),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote snapshot to {args.output}")
    print("Average timings (optimized):")
    for stage, value in sorted(output["avg_timings"].items()):
        print(f"  {stage}: {value:.3f}s")


def command_compare(args: argparse.Namespace) -> None:
    ctx = PredictionContext(method=args.method, verbose=False)
    ctx.initialize(args.screenshot_dir.resolve())
    names = args.names or VERIFIED_SCREENSHOTS
    all_errors: list[str] = []
    equipment_legacy_total = 0.0
    equipment_batch_total = 0.0
    for name in names:
        legacy_items, batch_items, legacy_time, batch_time = run_equipment_only_compare(
            ctx,
            args.screenshot_dir,
            name,
        )
        equipment_legacy_total += legacy_time
        equipment_batch_total += batch_time
        errors = compare_equipment_preds(legacy_items, batch_items)
        if errors:
            all_errors.extend([f"{name}: {err}" for err in errors])
        else:
            print(
                f"OK {name}: legacy={legacy_time:.2f}s batch={batch_time:.2f}s "
                f"({legacy_time / max(batch_time, 1e-9):.1f}x)"
            )
    print(
        f"\nEquipment-only totals: legacy={equipment_legacy_total:.1f}s "
        f"batch={equipment_batch_total:.1f}s "
        f"({equipment_legacy_total / max(equipment_batch_total, 1e-9):.1f}x)"
    )
    if all_errors:
        print(f"\n{len(all_errors)} mismatches:")
        for err in all_errors[:50]:
            print(f"  {err}")
        raise SystemExit(1)
    print("\nAll equipment comparisons passed.")


def command_report(args: argparse.Namespace) -> None:
    ctx = PredictionContext(method=args.method, verbose=False)
    ctx.initialize(args.screenshot_dir.resolve())
    names = args.names or VERIFIED_SCREENSHOTS

    legacy_results = run_snapshot(
        ctx,
        args.screenshot_dir,
        names,
        use_legacy_equipment=True,
    )
    optimized_results = run_snapshot(
        ctx,
        args.screenshot_dir,
        names,
        use_legacy_equipment=False,
    )

    gt_data = load_match_ground_truth(args.gt)
    label_mismatches: list[str] = []
    for name in names:
        legacy_items, batch_items, _, _ = run_equipment_only_compare(
            ctx,
            args.screenshot_dir,
            name,
        )
        errors = compare_equipment_preds(legacy_items, batch_items)
        label_mismatches.extend([f"{name}: {err}" for err in errors])

        gt_entry = gt_data.get("screenshots", {}).get(name)
        if gt_entry and gt_entry.get("verified"):
            gt_items = _extract_equipment_items(gt_entry)
            opt_items = _extract_equipment_items(
                {"players": optimized_results[name]["players"]}
            )
            gt_map = {
                (item["player"], item["slot"], item["item_index"]): item["label"]
                for item in gt_items
            }
            opt_map = {
                (item["player"], item["slot"], item["item_index"]): item["label"]
                for item in opt_items
            }
            for key, gt_label in gt_map.items():
                opt_label = opt_map.get(key)
                if opt_label != gt_label:
                    label_mismatches.append(
                        f"{name} vs GT {key}: gt={gt_label} opt={opt_label}"
                    )

    legacy_avg = summarize_timings(legacy_results)
    optimized_avg = summarize_timings(optimized_results)
    report = {
        "screenshots": names,
        "legacy_avg_timings": legacy_avg,
        "optimized_avg_timings": optimized_avg,
        "speedup": {
            stage: legacy_avg.get(stage, 0.0) / max(optimized_avg.get(stage, 1e-9), 1e-9)
            for stage in sorted(set(legacy_avg) | set(optimized_avg))
        },
        "equipment_equivalence_errors": label_mismatches,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    print("=== Speedup Report ===")
    print(f"Screenshots: {len(names)}")
    print("\nAverage timings:")
    for stage in sorted(set(legacy_avg) | set(optimized_avg)):
        legacy = legacy_avg.get(stage, 0.0)
        optimized = optimized_avg.get(stage, 0.0)
        speedup = legacy / max(optimized, 1e-9)
        print(f"  {stage:18s} legacy={legacy:7.3f}s  optimized={optimized:7.3f}s  {speedup:.2f}x")
    if label_mismatches:
        print(f"\n{len(label_mismatches)} mismatches found.")
        for err in label_mismatches[:20]:
            print(f"  {err}")
        raise SystemExit(1)
    print("\nAll checks passed.")
    print(f"Report written to {args.output}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=DEFAULT_SCREENSHOT_DIR,
    )
    parser.add_argument(
        "--gt",
        type=Path,
        default=DEFAULT_GT_PATH,
    )
    parser.add_argument(
        "--method",
        choices=("classifier", "1nn"),
        default="classifier",
    )
    parser.add_argument(
        "--names",
        nargs="*",
        help="Screenshot filenames (default: 7 verified)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="Save optimized timing snapshot")
    snapshot.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "speedup_snapshot.json",
    )
    snapshot.set_defaults(func=command_snapshot)

    compare = subparsers.add_parser("compare", help="Compare legacy vs batch equipment")
    compare.set_defaults(func=command_compare)

    report = subparsers.add_parser("report", help="Full before/after speedup report")
    report.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "speedup_report.json",
    )
    report.set_defaults(func=command_report)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
