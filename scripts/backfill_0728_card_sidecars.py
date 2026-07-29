# -*- coding: utf-8 -*-
"""Audit and backfill the manually reviewed 0728 card sidecars."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.card_sidecar import load_card_sidecar, save_card_sidecar  # noqa: E402

SCREENSHOT_DIR = ROOT / "screenshots.0728"
BACKFILL_BATCH = "0728-manual-review"


@dataclass(frozen=True)
class Resolution:
    label: str
    rule: str
    expected_name_text: str
    expected_raw_stem: str


TIMED_GIFT = Resolution(
    label="蓝·定时礼物",
    rule="timed_gift_visual_rescue",
    expected_name_text="定时礼物",
    expected_raw_stem="蓝·半步满级+满级玩家",
)
RESOLUTIONS = {
    ("MuMu-20260729-010739-386", 8, 0): TIMED_GIFT,
    ("MuMu-20260729-013013-872", 8, 0): TIMED_GIFT,
    ("MuMu-20260729-013127-694", 8, 0): TIMED_GIFT,
    ("MuMu-20260729-021933-289", 8, 0): TIMED_GIFT,
    ("MuMu-20260729-022123-905", 8, 0): TIMED_GIFT,
    ("MuMu-20260729-023225-235", 8, 0): TIMED_GIFT,
    ("MuMu-20260729-090943-344", 5, 0): TIMED_GIFT,
    ("MuMu-20260729-091310-371", 2, 0): TIMED_GIFT,
    ("MuMu-20260729-091950-330", 5, 0): TIMED_GIFT,
    ("MuMu-20260729-100754-451", 2, 0): TIMED_GIFT,
    ("MuMu-20260729-102407-174", 2, 0): TIMED_GIFT,
    ("MuMu-20260729-103104-238", 2, 0): TIMED_GIFT,
    ("MuMu-20260729-092124-497", 7, 0): Resolution(
        label="蓝·最强支援",
        rule="forced_logout_manual_review",
        expected_name_text="吸吸宝pro",
        expected_raw_stem="蓝·重质拍档支援",
    ),
    ("MuMu-20260729-092124-497", 8, 0): Resolution(
        label="蓝·半步满级",
        rule="forced_logout_manual_review",
        expected_name_text="吸吸宝pro",
        expected_raw_stem="蓝·半步满级+满级玩家",
    ),
}
EXPECTED_COUNTS = {
    "timed_gift_visual_rescue": 12,
    "forced_logout_manual_review": 2,
}


def _slot_key(stem: str, slot: dict[str, Any]) -> tuple[str, int, int]:
    return stem, int(slot["player"]), int(slot["slot_index"])


def _recompute_summary(data: dict[str, Any]) -> None:
    counts: Counter[str] = Counter()
    for slot in data["slots"]:
        presence = slot["presence"]
        label = slot.get("label")
        if presence == "empty":
            counts["empty"] += 1
        elif presence == "uncertain":
            counts["uncertain"] += 1
        elif label == "unknown":
            counts["unresolved"] += 1
        elif slot.get("source") == "detail_ocr":
            counts["detail"] += 1
        else:
            counts["template"] += 1

    summary = data.setdefault("summary", {})
    for key in ("template", "detail", "empty", "uncertain", "unresolved"):
        summary[key] = counts[key]
    summary["total_slots"] = len(data["slots"])

    pending = {
        f"p{slot['player']}_s{slot['slot_index'] + 1}": slot["review_artifacts"]
        for slot in data["slots"]
        if slot.get("label") == "unknown" and slot.get("review_artifacts")
    }
    data.setdefault("review_artifacts", {})["slots"] = pending


def _validate_unresolved_review_slots(
    stem: str,
    slots: list[dict[str, Any]],
) -> None:
    unexpected = []
    for slot in slots:
        if (
            slot.get("label") == "unknown"
            and slot.get("review_artifacts")
            and _slot_key(stem, slot) not in RESOLUTIONS
        ):
            unexpected.append(f"p{slot['player']}_s{slot['slot_index'] + 1}")
    if unexpected:
        raise SystemExit(f"Unresolved review slots lack resolutions: {stem} {unexpected}")


def backfill(*, write: bool) -> Counter[str]:
    sidecars = sorted(SCREENSHOT_DIR.glob("*.cards.json"))
    if not sidecars:
        raise SystemExit(f"No sidecars found in {SCREENSHOT_DIR}")

    counts: Counter[str] = Counter()
    seen: set[tuple[str, int, int]] = set()
    changed_files = 0

    for sidecar_path in sidecars:
        png_path = sidecar_path.with_name(
            sidecar_path.name.removesuffix(".cards.json") + ".png"
        )
        data = load_card_sidecar(png_path)
        stem = png_path.stem
        changed = False
        _validate_unresolved_review_slots(stem, data["slots"])

        for slot in data["slots"]:
            key = _slot_key(stem, slot)
            resolution = RESOLUTIONS.get(key)
            if resolution is None:
                continue
            seen.add(key)

            detail = slot.get("detail_ocr") or {}
            backfill_meta = detail.get("backfill") or {}
            if backfill_meta.get("batch") == BACKFILL_BATCH:
                if slot.get("label") != resolution.label:
                    raise SystemExit(f"Backfilled label changed unexpectedly: {key}")
                counts[resolution.rule] += 1
                continue

            if slot.get("presence") != "occupied":
                raise SystemExit(f"Resolution target is not occupied: {key}")
            if slot.get("label") != "unknown" or slot.get("is_ground_truth"):
                raise SystemExit(f"Resolution target is not unresolved: {key}")
            if not slot.get("review_artifacts"):
                raise SystemExit(f"Resolution target has no review artifacts: {key}")
            if detail.get("reason") != "name-mismatch-or-stale":
                raise SystemExit(f"Unexpected detail reason for {key}: {detail.get('reason')}")
            if detail.get("name_text") != resolution.expected_name_text:
                raise SystemExit(f"Unexpected OCR name for {key}: {detail.get('name_text')}")
            raw_stem = (slot.get("template_debug") or {}).get("top1_raw_asset_stem")
            if raw_stem != resolution.expected_raw_stem:
                raise SystemExit(f"Unexpected raw template for {key}: {raw_stem}")

            detail["backfill"] = {
                "batch": BACKFILL_BATCH,
                "rule": resolution.rule,
                "original_reason": detail.get("reason"),
                "original_name_text": detail.get("name_text"),
                "resolved_label": resolution.label,
            }
            detail["reason"] = "confirmed-manual-review"
            slot["detail_ocr"] = detail
            slot["label"] = resolution.label
            slot["source"] = "detail_ocr"
            slot["is_ground_truth"] = True
            counts[resolution.rule] += 1
            changed = True

        if changed:
            changed_files += 1
            _recompute_summary(data)
            if write:
                save_card_sidecar(png_path, data)

    if seen != set(RESOLUTIONS):
        missing = sorted(set(RESOLUTIONS) - seen)
        raise SystemExit(f"Resolution targets not found: {missing}")
    actual = {key: counts[key] for key in EXPECTED_COUNTS}
    if actual != EXPECTED_COUNTS:
        raise SystemExit(f"Backfill audit mismatch: expected={EXPECTED_COUNTS}, actual={actual}")

    for sidecar_path in sidecars:
        png_path = sidecar_path.with_name(
            sidecar_path.name.removesuffix(".cards.json") + ".png"
        )
        load_card_sidecar(png_path)

    counts["changed_files"] = changed_files
    counts["validated_sidecars"] = len(sidecars)
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Atomically save verified changes (default is audit-only dry run)",
    )
    args = parser.parse_args()
    counts = backfill(write=args.write)
    mode = "WRITE" if args.write else "DRY-RUN"
    print(f"{mode}: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


if __name__ == "__main__":
    main()
