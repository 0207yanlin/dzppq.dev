# -*- coding: utf-8 -*-
"""Audit and backfill the manually reviewed 0730 card sidecars."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.card_capture import resolve_detail_candidate  # noqa: E402
from src.card_details import load_card_details  # noqa: E402
from src.card_sidecar import load_card_sidecar, save_card_sidecar  # noqa: E402

SCREENSHOT_DIR = ROOT / "screenshots.0730"
BACKFILL_BATCH = "0730-manual-review"
MANUAL_RESOLUTIONS = {
    ("MuMu-20260731-114614-915", 2, 2): "彩·射手礼包",
}
EXPECTED_TIMEOUTS = {
    ("MuMu-20260731-002723-096", 3, 2),
    ("MuMu-20260731-002723-096", 6, 0),
    ("MuMu-20260731-002723-096", 8, 0),
    ("MuMu-20260731-002855-962", 7, 0),
    ("MuMu-20260731-002855-962", 8, 0),
    ("MuMu-20260731-003010-658", 2, 0),
    ("MuMu-20260731-003010-658", 8, 0),
    ("MuMu-20260731-003744-459", 2, 0),
    ("MuMu-20260731-004005-129", 3, 0),
    ("MuMu-20260731-004710-679", 8, 1),
    ("MuMu-20260731-004937-491", 6, 0),
    ("MuMu-20260731-010116-299", 3, 1),
}
EXPECTED_SKIPS = {
    ("MuMu-20260731-000551-166", 8, 2),
    ("MuMu-20260731-001225-343", 8, 2),
    ("MuMu-20260731-001618-991", 8, 2),
    ("MuMu-20260731-003229-588", 8, 2),
    ("MuMu-20260731-004445-146", 8, 2),
    ("MuMu-20260731-101456-442", 8, 2),
    ("MuMu-20260731-102633-422", 8, 2),
}


def _key(stem: str, slot: dict[str, Any]) -> tuple[str, int, int]:
    return stem, int(slot["player"]), int(slot["slot_index"])


def _recompute_summary(data: dict[str, Any]) -> None:
    counts: Counter[str] = Counter()
    for slot in data["slots"]:
        if slot["presence"] == "empty":
            counts["empty"] += 1
        elif slot["presence"] == "uncertain":
            counts["uncertain"] += 1
        elif slot.get("label") == "unknown":
            counts["unresolved"] += 1
        elif slot.get("source") == "detail_ocr":
            counts["detail"] += 1
        else:
            counts["template"] += 1

    summary = data.setdefault("summary", {})
    for name in ("template", "detail", "empty", "uncertain", "unresolved"):
        summary[name] = counts[name]
    summary["total_slots"] = len(data["slots"])
    data.setdefault("review_artifacts", {})["slots"] = {
        f"p{slot['player']}_s{slot['slot_index'] + 1}": slot["review_artifacts"]
        for slot in data["slots"]
        if slot.get("label") == "unknown" and slot.get("review_artifacts")
    }


def _apply_resolution(
    *,
    slot: dict[str, Any],
    label: str,
    rule: str,
    original_reason: str | None,
) -> None:
    detail = slot.setdefault("detail_ocr", {})
    detail["backfill"] = {
        "batch": BACKFILL_BATCH,
        "rule": rule,
        "original_reason": original_reason,
        "resolved_label": label,
    }
    detail["reason"] = "confirmed-manual-review"
    slot["label"] = label
    slot["source"] = "detail_ocr"
    slot["is_ground_truth"] = True


def backfill(*, write: bool) -> Counter[str]:
    sidecars = sorted(SCREENSHOT_DIR.glob("*.cards.json"))
    if not sidecars:
        raise SystemExit(f"No sidecars found in {SCREENSHOT_DIR}")

    card_details = load_card_details()
    counts: Counter[str] = Counter()
    seen_targets: set[tuple[str, int, int]] = set()
    changed_files = 0

    for sidecar_path in sidecars:
        png_path = sidecar_path.with_name(
            sidecar_path.name.removesuffix(".cards.json") + ".png"
        )
        data = load_card_sidecar(png_path)
        stem = png_path.stem
        changed = False

        for slot in data["slots"]:
            key = _key(stem, slot)
            is_manual = key in MANUAL_RESOLUTIONS
            is_timeout = key in EXPECTED_TIMEOUTS
            is_skip = key in EXPECTED_SKIPS
            if not (is_manual or is_timeout or is_skip):
                continue
            seen_targets.add(key)

            detail = slot.get("detail_ocr") or {}
            previous = detail.get("backfill") or {}
            if previous.get("batch") == BACKFILL_BATCH:
                if is_manual and slot.get("label") != MANUAL_RESOLUTIONS[key]:
                    raise SystemExit(f"Backfilled label changed unexpectedly: {key}")
                if is_timeout and slot.get("label") == "unknown":
                    raise SystemExit(f"Backfilled timeout label is unknown: {key}")
                counts["already_backfilled"] += 1
                continue

            if (
                not is_skip
                and (
                    slot.get("presence") != "occupied"
                    or slot.get("label") != "unknown"
                )
            ):
                raise SystemExit(f"Target is not unresolved and occupied: {key}")
            if not slot.get("review_artifacts"):
                raise SystemExit(f"Target has no review artifacts: {key}")

            if is_skip:
                if detail or slot.get("source") != "template":
                    raise SystemExit(f"Skip target unexpectedly has OCR data: {key}")
                counts["skipped_uncertain"] += 1
                continue

            if is_manual:
                label = MANUAL_RESOLUTIONS[key]
                rule = "confirmed_manual_label"
                original_reason = detail.get("reason")
                if original_reason != "name-mismatch-or-stale":
                    raise SystemExit(f"Unexpected manual-review reason: {key}")
            else:
                if detail.get("reason") != "detail-stability-timeout":
                    raise SystemExit(f"Unexpected timeout reason: {key}")
                observations = (detail.get("stability") or {}).get("observations") or []
                if not observations:
                    raise SystemExit(f"Timeout target has no OCR observation: {key}")
                observation = observations[-1]
                label, debug = resolve_detail_candidate(
                    str(observation.get("name_text") or ""),
                    str(observation.get("detail_text") or ""),
                    tuple(detail.get("configured_candidates") or ()),
                    card_details,
                )
                if not label:
                    raise SystemExit(
                        f"Could not resolve OCR timeout: {key}; reason={debug.get('reason')}"
                    )
                rule = "ocr_timeout_last_observation"
                original_reason = detail.get("reason")

            _apply_resolution(
                slot=slot,
                label=label,
                rule=rule,
                original_reason=original_reason,
            )
            counts[rule] += 1
            changed = True

        if changed:
            changed_files += 1
            _recompute_summary(data)
            if write:
                save_card_sidecar(png_path, data)

    expected = set(MANUAL_RESOLUTIONS) | EXPECTED_TIMEOUTS | EXPECTED_SKIPS
    if seen_targets != expected:
        raise SystemExit(
            f"Target audit mismatch: missing={sorted(expected - seen_targets)}, "
            f"unexpected={sorted(seen_targets - expected)}"
        )
    if counts["confirmed_manual_label"] + counts["already_backfilled"] == 0:
        raise SystemExit("Manual resolution was not applied or verified")
    if counts["ocr_timeout_last_observation"] + counts["already_backfilled"] < len(
        EXPECTED_TIMEOUTS
    ):
        raise SystemExit("Not all OCR timeout targets were applied or verified")
    if counts["skipped_uncertain"] + counts["already_backfilled"] < len(EXPECTED_SKIPS):
        raise SystemExit("Not all uncertain targets were verified as skipped")

    for sidecar_path in sidecars:
        load_card_sidecar(
            sidecar_path.with_name(
                sidecar_path.name.removesuffix(".cards.json") + ".png"
            )
        )

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
