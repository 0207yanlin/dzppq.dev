# -*- coding: utf-8 -*-
"""Resolve unresolved card sidecars from OCR evidence or manual review.

Examples:
    python scripts/review_card_sidecars.py --screenshot-dir screenshots.0823 --auto --write
    python scripts/review_card_sidecars.py --screenshot-dir screenshots.0823
    python scripts/review_card_sidecars.py --screenshot-dir screenshots.0823 --write
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.card_capture import resolve_detail_candidate  # noqa: E402
from src.card_details import load_card_details  # noqa: E402
from src.card_sidecar import load_card_sidecar, save_card_sidecar  # noqa: E402


def _review_slots(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        slot
        for slot in data["slots"]
        if slot.get("presence") == "occupied"
        and slot.get("review_artifacts")
        and not (slot.get("detail_ocr") or {}).get("review")
    ]


def _last_observation(slot: dict[str, Any]) -> dict[str, Any] | None:
    detail = slot.get("detail_ocr") or {}
    observations = (detail.get("stability") or {}).get("observations") or []
    if not observations:
        return None
    return observations[-1]


def _auto_label(
    slot: dict[str, Any], card_details: Any
) -> tuple[str | None, dict[str, Any]]:
    detail = slot.get("detail_ocr") or {}
    observation = _last_observation(slot)
    if observation is None:
        return None, {"reason": "no-ocr-observation"}
    candidates = tuple(detail.get("configured_candidates") or ())
    if not candidates:
        return None, {"reason": "no-configured-candidates"}
    return resolve_detail_candidate(
        str(observation.get("name_text") or ""),
        str(observation.get("detail_text") or ""),
        candidates,
        card_details,
    )


def _apply_label(
    slot: dict[str, Any],
    label: str,
    *,
    method: str,
    rule: str,
    original_reason: str | None,
) -> None:
    detail = slot.setdefault("detail_ocr", {})
    detail["review"] = {
        "method": method,
        "rule": rule,
        "original_reason": original_reason,
        "resolved_label": label,
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }
    detail["reason"] = "confirmed-manual-review" if method == "manual" else "confirmed-ocr-review"
    slot["label"] = label
    slot["source"] = "manual_review" if method == "manual" else "detail_ocr"
    slot["is_ground_truth"] = True


def _recompute_summary(data: dict[str, Any]) -> None:
    counts: Counter[str] = Counter()
    for slot in data["slots"]:
        if slot["presence"] == "empty":
            counts["empty"] += 1
        elif slot["presence"] == "uncertain":
            counts["uncertain"] += 1
        elif slot.get("label") == "unknown":
            counts["unresolved"] += 1
        elif slot.get("is_ground_truth"):
            counts["detail"] += 1
        else:
            counts["template"] += 1

    summary = data.setdefault("summary", {})
    for name in ("template", "detail", "empty", "uncertain", "unresolved"):
        summary[name] = counts[name]
    summary["total_slots"] = len(data["slots"])

    artifacts = data.setdefault("review_artifacts", {})
    artifacts["slots"] = {
        f"p{slot['player']}_s{slot['slot_index'] + 1}": slot["review_artifacts"]
        for slot in data["slots"]
        if slot.get("label") == "unknown" and slot.get("review_artifacts")
    }


def _print_slot(stem: str, slot: dict[str, Any], suggestion: str | None) -> None:
    detail = slot.get("detail_ocr") or {}
    observation = _last_observation(slot) or {}
    position = f"p{slot['player']}_s{slot['slot_index'] + 1}"
    print(f"\n[{stem} / {position}]")
    print(f"  review images: {slot.get('review_artifacts')}")
    print(f"  OCR name: {observation.get('name_text', '')}")
    print(f"  OCR detail: {observation.get('detail_text', '')}")
    print(f"  candidates: {', '.join(detail.get('configured_candidates') or ())}")
    if suggestion:
        print(f"  suggested: {suggestion}")


def review(*, screenshot_dir: Path, auto: bool, write: bool) -> Counter[str]:
    sidecars = sorted(screenshot_dir.glob("*.cards.json"))
    if not sidecars:
        raise SystemExit(f"No sidecars found in {screenshot_dir}")

    card_details = load_card_details()
    counts: Counter[str] = Counter()
    changed_files = 0
    stop = False

    for sidecar_path in sidecars:
        if stop:
            break
        png_path = sidecar_path.with_name(
            sidecar_path.name.removesuffix(".cards.json") + ".png"
        )
        data = load_card_sidecar(png_path)
        changed = False
        for slot in _review_slots(data):
            stem = png_path.stem
            label, debug = _auto_label(slot, card_details)
            _print_slot(stem, slot, label)

            if auto and label:
                _apply_label(
                    slot,
                    label,
                    method="ocr",
                    rule="last_stable_observation_or_timeout",
                    original_reason=(slot.get("detail_ocr") or {}).get("reason"),
                )
                counts["auto_resolved"] += 1
                changed = True
                continue

            if auto:
                counts["auto_unresolved"] += 1
                continue

            while True:
                answer = input("输入卡牌完整名称；Enter采用建议；s跳过；q退出：").strip()
                if answer.lower() == "q":
                    stop = True
                    break
                if answer.lower() == "s":
                    counts["skipped"] += 1
                    break
                answer = answer or (label or "")
                if not answer:
                    print(f"无法自动确定（{debug.get('reason', 'unknown')}），请输入名称。")
                    continue
                _apply_label(
                    slot,
                    answer,
                    method="manual",
                    rule="user_entered_label",
                    original_reason=(slot.get("detail_ocr") or {}).get("reason"),
                )
                counts["manual_resolved"] += 1
                changed = True
                break

            if stop:
                break

        if changed:
            _recompute_summary(data)
            changed_files += 1
            if write:
                save_card_sidecar(png_path, data)

    counts["changed_files"] = changed_files
    counts["validated_sidecars"] = len(sidecars)
    if not write and (counts["auto_resolved"] or counts["manual_resolved"]):
        print("DRY-RUN: results were not written; rerun with --write.")
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=ROOT / "screenshots.0823",
        help="Directory containing PNGs and .cards.json sidecars",
    )
    parser.add_argument(
        "--auto",
        action="store_true",
        help="Apply only unambiguous labels from the last OCR observation",
    )
    parser.add_argument(
        "--write",
        action="store_true",
        help="Atomically write changes; default is audit-only",
    )
    args = parser.parse_args()
    counts = review(
        screenshot_dir=args.screenshot_dir.resolve(),
        auto=args.auto,
        write=args.write,
    )
    mode = "WRITE" if args.write else "DRY-RUN"
    print(f"{mode}: " + ", ".join(f"{key}={value}" for key, value in sorted(counts.items())))


if __name__ == "__main__":
    main()
