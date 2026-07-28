# -*- coding: utf-8 -*-
"""Audit and backfill the manually reviewed 0727 card sidecars."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.card_sidecar import load_card_sidecar, save_card_sidecar  # noqa: E402
from src.match_ground_truth import (  # noqa: E402
    DEFAULT_GT_PATH,
    load_match_ground_truth,
    save_match_ground_truth,
)

SCREENSHOT_DIR = ROOT / "screenshots.0727"
EXPECTED_COUNTS = {
    "kaizan_ocr": 25,
    "gongming_pro_ocr": 8,
    "transition_timeout": 3,
    "guard_detail": 2,
    "legacy_merged_unknown": 8,
    "full_level_uncollected": 55,
}
TRANSITION_FIXES = {
    ("MuMu-20260728-002050-376", 2, 0): "蓝·最后的波纹",
    ("MuMu-20260728-003139-834", 4, 0): "蓝·利己主义",
    ("MuMu-20260728-091346-978", 5, 0): "蓝·重质也重量pro",
}
STALE_UNKNOWN = {
    ("MuMu-20260728-005303-177", 7, 0),
    ("MuMu-20260728-095345-772", 8, 0),
}
FULL_LEVEL_LABELS = frozenset(
    {
        "蓝·半步满级+满级玩家",
        "蓝·半步满级",
        "蓝·满级玩家",
    }
)
GUARD_DETAIL = "获得1个守护头盔，己方蛋仔获得120生命值"
LEGACY_MERGED_LABELS = frozenset(
    {
        "蓝·重质拍档支援",
        "蓝·一起刷刷刷+天降揪揪pro",
        "蓝·我们全都要+一起刷刷刷",
        "蓝·我是老大+快速成长",
        "蓝·专业打手+冒险",
        "蓝·拍档支援",
        "蓝·开攒大亨",
        "蓝·福袋有钱",
        "蓝·波纹利己",
        "黄·吸吸宝pro快速成型",
        "黄·巨神兵+迅迅迅捷双剑",
        "黄·大力巫术守护",
        "黄·装备共鸣",
        "彩·法师战士射手礼包",
        "彩·装备共鸣pro",
    }
)


def _slot_key(stem: str, slot: dict[str, Any]) -> tuple[str, int, int]:
    return stem, int(slot["player"]), int(slot["slot_index"])


def _resolution_for(stem: str, slot: dict[str, Any]) -> tuple[str, str] | None:
    if not slot.get("review_artifacts") or slot.get("presence") != "occupied":
        return None
    if slot.get("label") != "unknown" or slot.get("is_ground_truth"):
        return None

    detail = slot.get("detail_ocr") or {}
    debug = slot.get("template_debug") or {}
    name_text = detail.get("name_text")
    candidates = tuple(detail.get("configured_candidates") or ())
    raw_stem = debug.get("top1_raw_asset_stem")

    if (
        raw_stem == "蓝·开攒大亨"
        and name_text == "开揽"
        and candidates == ("蓝·开攒", "蓝·大亨")
    ):
        return "蓝·开攒", "kaizan_ocr"
    if (
        raw_stem == "彩·装备共鸣pro"
        and name_text == "装备共鸣法rO"
        and "彩·装备共鸣法pro" in candidates
    ):
        return "彩·装备共鸣法pro", "gongming_pro_ocr"
    transition_label = TRANSITION_FIXES.get(_slot_key(stem, slot))
    if (
        transition_label
        and detail.get("reason") == "detail-stability-timeout"
        and transition_label in candidates
    ):
        observations = (detail.get("stability") or {}).get("observations") or []
        if any(
            str(item.get("name_text") or "") == transition_label.split("·", 1)[1]
            for item in observations
        ):
            return transition_label, "transition_timeout"
    if (
        raw_stem == "黄·大力巫术守护"
        and name_text == ""
        and detail.get("detail_text") == GUARD_DETAIL
        and "黄·守护" in candidates
    ):
        return "黄·守护", "guard_detail"
    return None


def _recompute_summary(data: dict[str, Any]) -> None:
    counts = Counter()
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
    review = data.setdefault("review_artifacts", {})
    review["slots"] = pending


def _clean_ground_truth(*, write: bool) -> int:
    data = load_match_ground_truth(DEFAULT_GT_PATH)
    removed = 0
    for screenshot_name, entry in data.get("screenshots", {}).items():
        match_path = str(entry.get("path") or screenshot_name).replace("\\", "/")
        if not match_path.startswith("screenshots.0727/"):
            continue
        for player in entry.get("players", []):
            cards = player.get("cards", [])
            kept = [
                card
                for card in cards
                if card.get("card_name") not in FULL_LEVEL_LABELS
            ]
            removed += len(cards) - len(kept)
            player["cards"] = kept
    if write and removed:
        save_match_ground_truth(data, DEFAULT_GT_PATH)
    return removed


def backfill(*, write: bool) -> Counter[str]:
    counts: Counter[str] = Counter()
    changed_files = 0
    sidecars = sorted(SCREENSHOT_DIR.glob("*.cards.json"))
    if not sidecars:
        raise SystemExit(f"No sidecars found in {SCREENSHOT_DIR}")

    for sidecar_path in sidecars:
        png_path = sidecar_path.with_name(sidecar_path.name.removesuffix(".cards.json") + ".png")
        data = load_card_sidecar(png_path)
        changed = False
        stem = png_path.stem
        for slot in data["slots"]:
            backfill_meta = (slot.get("detail_ocr") or {}).get("backfill") or {}
            if backfill_meta.get("batch") == "0727-manual-review":
                counts[str(backfill_meta["rule"])] += 1
                continue

            if slot.get("label") in FULL_LEVEL_LABELS:
                original_label = str(slot["label"])
                detail = slot.setdefault("detail_ocr", {})
                detail["backfill"] = {
                    "batch": "0727-manual-review",
                    "rule": "full_level_uncollected",
                    "original_reason": detail.get("reason"),
                    "original_label": original_label,
                    "resolved_label": "unknown",
                }
                detail["reason"] = "shared-template-detail-not-collected-before-0728"
                detail["configured_candidates"] = [
                    "蓝·半步满级",
                    "蓝·满级玩家",
                ]
                slot["label"] = "unknown"
                slot["source"] = "template"
                slot["is_ground_truth"] = False
                counts["full_level_uncollected"] += 1
                changed = True
                continue

            if (
                slot.get("label") in LEGACY_MERGED_LABELS
                and slot.get("source") != "detail_ocr"
                and not slot.get("is_ground_truth")
            ):
                original_label = str(slot["label"])
                detail = slot.setdefault("detail_ocr", {})
                detail["backfill"] = {
                    "batch": "0727-manual-review",
                    "rule": "legacy_merged_unknown",
                    "original_reason": detail.get("reason"),
                    "original_label": original_label,
                    "resolved_label": "unknown",
                }
                detail["reason"] = "legacy-merged-template-not-detail-confirmed"
                if original_label == "黄·巨神兵+迅迅迅捷双剑":
                    detail.setdefault(
                        "configured_candidates",
                        ["黄·巨神兵", "黄·迅迅迅捷双剑"],
                    )
                slot["label"] = "unknown"
                counts["legacy_merged_unknown"] += 1
                changed = True
                continue

            resolution = _resolution_for(stem, slot)
            if resolution is None:
                continue
            label, rule = resolution
            detail = slot.setdefault("detail_ocr", {})
            original_reason = detail.get("reason")
            detail["backfill"] = {
                "batch": "0727-manual-review",
                "rule": rule,
                "original_reason": original_reason,
                "resolved_label": label,
            }
            detail["reason"] = "confirmed-manual-review"
            slot["label"] = label
            slot["source"] = "detail_ocr"
            slot["is_ground_truth"] = True
            counts[rule] += 1
            changed = True

        if changed:
            changed_files += 1
            _recompute_summary(data)
            if write:
                save_card_sidecar(png_path, data)

    stale_seen = set()
    for stem, player, slot_index in STALE_UNKNOWN:
        png_path = SCREENSHOT_DIR / f"{stem}.png"
        data = load_card_sidecar(png_path)
        slot = next(
            item
            for item in data["slots"]
            if item["player"] == player and item["slot_index"] == slot_index
        )
        if slot.get("label") != "unknown" or slot.get("is_ground_truth"):
            raise SystemExit(f"Stale panel slot must remain unknown: {stem} p{player}_s{slot_index + 1}")
        stale_seen.add((stem, player, slot_index))

    actual = {key: counts[key] for key in EXPECTED_COUNTS}
    if actual != EXPECTED_COUNTS:
        raise SystemExit(f"Backfill audit count mismatch: expected={EXPECTED_COUNTS}, actual={actual}")
    if stale_seen != STALE_UNKNOWN:
        raise SystemExit("Did not verify both stale-panel unknown slots")

    for sidecar_path in sidecars:
        png_path = sidecar_path.with_name(sidecar_path.name.removesuffix(".cards.json") + ".png")
        load_card_sidecar(png_path)

    counts["changed_files"] = changed_files
    counts["validated_sidecars"] = len(sidecars)
    counts["stale_unknown"] = len(stale_seen)
    counts["gt_full_level_removed"] = _clean_ground_truth(write=write)
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
