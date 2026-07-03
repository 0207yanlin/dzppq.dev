# -*- coding: utf-8 -*-
"""Generate and interactively review new hero/card template candidates."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.layout import ROOT as PROJECT_ROOT, card_roi, crop_roi, hero_roi  # noqa: E402
from src.match_ground_truth import (  # noqa: E402
    DEFAULT_GT_PATH,
    load_match_ground_truth,
    save_match_ground_truth,
)
from src.parse import parse_hero_label  # noqa: E402
from src.template_capture import (  # noqa: E402
    card_template_path,
    hero_template_path,
    save_card_template,
    save_hero_template,
    template_exists,
)

DEFAULT_CANDIDATES_DIR = PROJECT_ROOT / "data" / "template_candidates"
DEFAULT_CANDIDATES_JSON = DEFAULT_CANDIDATES_DIR / "candidates.json"

HERO_SCORE_THRESHOLD = 0.75
CARD_SCORE_THRESHOLD = 0.75
SIGNATURE_SIZE = (32, 32)
CLUSTER_DISTANCE = 12.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _candidate_key(kind: str, screenshot: str, player: int, slot: int) -> str:
    return f"{kind}:{screenshot}:{player}:{slot}"


def _image_signature(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(gray, SIGNATURE_SIZE, interpolation=cv2.INTER_AREA)
    return resized.astype(np.float32)


def _signature_distance(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.mean(np.abs(a - b)))


def _resolve_screenshot_path(entry: dict) -> Path:
    rel_path = entry.get("path", "")
    path = PROJECT_ROOT / rel_path.replace("/", "\\")
    if path.exists():
        return path
    name = Path(rel_path).name
    candidate = PROJECT_ROOT / "screenshots.0701" / name
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"screenshot not found: {rel_path}")


def _crop_candidate(
    img: np.ndarray,
    kind: str,
    player: int,
    slot: int,
) -> np.ndarray:
    box = hero_roi(player, slot) if kind == "hero" else card_roi(player, slot)
    return crop_roi(img, box)


def _load_registry(data: dict) -> dict[str, dict]:
    registry: dict[str, dict] = {}
    for item in data.get("candidates", []):
        key = _candidate_key(item["kind"], item["screenshot"], item["player"], item["slot"])
        registry[key] = item
    return registry


def _collect_raw_candidates(
    gt_data: dict,
    *,
    path_prefix: str,
    hero_threshold: float,
    card_threshold: float,
) -> list[dict]:
    raw: list[dict] = []
    for screenshot_name, entry in gt_data.get("screenshots", {}).items():
        rel_path = entry.get("path", "").replace("\\", "/")
        if path_prefix and not rel_path.startswith(path_prefix):
            continue
        try:
            img_path = _resolve_screenshot_path(entry)
            img = cv2.imread(str(img_path))
            if img is None:
                continue
        except FileNotFoundError:
            continue

        for player in entry.get("players", []):
            row_index = player["row_index"]
            for hero in player.get("heroes", []):
                hero_name = hero.get("hero_name", "")
                scores = hero.get("scores") or {}
                hero_score = scores.get("hero")
                reason = None
                if hero_name == "unknown":
                    reason = "unknown"
                elif hero_score is not None and float(hero_score) < hero_threshold:
                    reason = "low_score"
                if reason is None:
                    continue
                crop = _crop_candidate(img, "hero", row_index, hero["slot_index"])
                raw.append(
                    {
                        "kind": "hero",
                        "reason": reason,
                        "screenshot": screenshot_name,
                        "path": rel_path,
                        "player": row_index,
                        "slot": hero["slot_index"],
                        "predicted_label": hero_name,
                        "score": hero_score,
                        "crop": crop,
                    }
                )

            for card in player.get("cards", []):
                card_name = card.get("card_name", "")
                card_score = card.get("score")
                reason = None
                if card_name == "unknown":
                    reason = "unknown"
                elif card_score is not None and float(card_score) < card_threshold:
                    reason = "low_score"
                if reason is None:
                    continue
                crop = _crop_candidate(img, "card", row_index, card["slot_index"])
                raw.append(
                    {
                        "kind": "card",
                        "reason": reason,
                        "screenshot": screenshot_name,
                        "path": rel_path,
                        "player": row_index,
                        "slot": card["slot_index"],
                        "predicted_label": card_name,
                        "score": card_score,
                        "crop": crop,
                    }
                )
    return raw


def _cluster_candidates(raw: list[dict]) -> list[dict]:
    clusters: list[dict] = []
    for item in raw:
        signature = _image_signature(item["crop"])
        matched = None
        for cluster in clusters:
            if cluster["kind"] != item["kind"]:
                continue
            if _signature_distance(signature, cluster["signature"]) <= CLUSTER_DISTANCE:
                matched = cluster
                break
        if matched is None:
            clusters.append(
                {
                    "kind": item["kind"],
                    "signature": signature,
                    "representative": item,
                    "members": [item],
                }
            )
        else:
            matched["members"].append(item)
    return clusters


def generate_candidates(
    gt_data: dict,
    *,
    output_dir: Path,
    output_json: Path,
    path_prefix: str,
    hero_threshold: float,
    card_threshold: float,
    reset_rejected: bool = False,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    existing = {}
    if output_json.exists():
        existing = _load_registry(json.loads(output_json.read_text(encoding="utf-8")))

    raw = _collect_raw_candidates(
        gt_data,
        path_prefix=path_prefix,
        hero_threshold=hero_threshold,
        card_threshold=card_threshold,
    )
    clusters = _cluster_candidates(raw)

    candidates: list[dict] = []
    for idx, cluster in enumerate(clusters, start=1):
        rep = cluster["representative"]
        candidate_id = f"c{idx:04d}"
        crop_name = f"{candidate_id}_{cluster['kind']}.jpg"
        crop_path = output_dir / crop_name
        cv2.imencode(".jpg", rep["crop"])[1].tofile(str(crop_path))

        key = _candidate_key(rep["kind"], rep["screenshot"], rep["player"], rep["slot"])
        prior = existing.get(key, {})
        status = prior.get("status", "pending")
        if status == "accepted":
            pass
        elif reset_rejected and status in {"rejected", "skipped"}:
            status = "pending"
        elif status in {"rejected", "skipped"}:
            pass

        candidates.append(
            {
                "id": prior.get("id", candidate_id),
                "kind": rep["kind"],
                "reason": rep["reason"],
                "screenshot": rep["screenshot"],
                "path": rep["path"],
                "player": rep["player"],
                "slot": rep["slot"],
                "predicted_label": rep["predicted_label"],
                "score": rep["score"],
                "crop_path": str(crop_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "cluster_size": len(cluster["members"]),
                "cluster_examples": [
                    {
                        "screenshot": member["screenshot"],
                        "player": member["player"],
                        "slot": member["slot"],
                        "predicted_label": member["predicted_label"],
                        "score": member["score"],
                    }
                    for member in cluster["members"][:5]
                ],
                "cluster_members": [
                    {
                        "screenshot": member["screenshot"],
                        "player": member["player"],
                        "slot": member["slot"],
                        "predicted_label": member["predicted_label"],
                    }
                    for member in cluster["members"]
                ],
                "status": status,
                "template_name": prior.get("template_name"),
                "saved_template_path": prior.get("saved_template_path"),
                "reviewed_at": prior.get("reviewed_at"),
            }
        )

    payload = {
        "version": 1,
        "generated_at": _utc_now(),
        "path_prefix": path_prefix,
        "candidate_count": len(candidates),
        "pending_count": sum(1 for c in candidates if c["status"] == "pending"),
        "candidates": candidates,
    }
    output_json.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return payload


def _format_candidate(item: dict) -> str:
    lines = [
        f"[{item['id']}] {item['kind']} candidate ({item['reason']})",
        f"  screenshot: {item['screenshot']}",
        f"  player/slot: P{item['player'] + 1} slot {item['slot'] + 1}",
        f"  predicted: {item['predicted_label']} score={item.get('score')}",
        f"  crop: {item['crop_path']}",
        f"  cluster_size: {item.get('cluster_size', 1)}",
    ]
    members = item.get("cluster_members") or item.get("cluster_examples") or []
    if len(members) > 1:
        lines.append("  similar slots:")
        for ex in members:
            if (
                ex["screenshot"] == item["screenshot"]
                and ex["player"] == item["player"]
                and ex["slot"] == item["slot"]
            ):
                continue
            lines.append(
                f"    - {ex['screenshot']} P{ex['player'] + 1} slot {ex['slot'] + 1}"
            )
    return "\n".join(lines)


def _update_gt_card(
    gt_data: dict,
    screenshot: str,
    player: int,
    slot: int,
    card_name: str,
) -> bool:
    entry = gt_data.get("screenshots", {}).get(screenshot)
    if entry is None:
        return False
    for player_row in entry.get("players", []):
        if player_row["row_index"] != player:
            continue
        for card in player_row.get("cards", []):
            if card["slot_index"] == slot:
                card["card_name"] = card_name
                card.pop("score", None)
                return True
    return False


def _update_gt_hero(
    gt_data: dict,
    screenshot: str,
    player: int,
    slot: int,
    hero_label: str,
) -> bool:
    entry = gt_data.get("screenshots", {}).get(screenshot)
    if entry is None:
        return False
    tier, hero_name = parse_hero_label(hero_label)
    for player_row in entry.get("players", []):
        if player_row["row_index"] != player:
            continue
        for hero in player_row.get("heroes", []):
            if hero["slot_index"] == slot:
                hero["hero_name"] = hero_name
                if tier is not None:
                    hero["tier"] = tier
                hero.pop("scores", None)
                return True
    return False


def _apply_gt_label(
    gt_data: dict,
    kind: str,
    screenshot: str,
    player: int,
    slot: int,
    label: str,
    *,
    only_if_predicted: str | None = None,
) -> bool:
    if kind == "card":
        entry = gt_data.get("screenshots", {}).get(screenshot)
        if entry is None:
            return False
        for player_row in entry.get("players", []):
            if player_row["row_index"] != player:
                continue
            for card in player_row.get("cards", []):
                if card["slot_index"] != slot:
                    continue
                if only_if_predicted is not None and card.get("card_name") != only_if_predicted:
                    return False
                return _update_gt_card(gt_data, screenshot, player, slot, label)
    else:
        entry = gt_data.get("screenshots", {}).get(screenshot)
        if entry is None:
            return False
        for player_row in entry.get("players", []):
            if player_row["row_index"] != player:
                continue
            for hero in player_row.get("heroes", []):
                if hero["slot_index"] != slot:
                    continue
                if only_if_predicted is not None and hero.get("hero_name") != only_if_predicted:
                    return False
                return _update_gt_hero(gt_data, screenshot, player, slot, label)
    return False


def _apply_gt_label_to_members(
    gt_data: dict,
    item: dict,
    label: str,
    *,
    apply_cluster: bool,
) -> int:
    members = [{"screenshot": item["screenshot"], "player": item["player"], "slot": item["slot"]}]
    if apply_cluster:
        members = item.get("cluster_members") or item.get("cluster_examples") or members

    updated = 0
    seen: set[tuple[str, int, int]] = set()
    default_predicted = item.get("predicted_label")
    for member in members:
        key = (member["screenshot"], member["player"], member["slot"])
        if key in seen:
            continue
        seen.add(key)
        only_if = member.get("predicted_label", default_predicted)
        if _apply_gt_label(
            gt_data,
            item["kind"],
            member["screenshot"],
            member["player"],
            member["slot"],
            label,
            only_if_predicted=only_if,
        ):
            updated += 1
    return updated


def _should_review_item(
    item: dict,
    *,
    include_rejected: bool,
    review_id: str | None,
) -> bool:
    if review_id and item.get("id") != review_id:
        return False
    if review_id:
        return True
    status = item.get("status")
    if status == "pending":
        return True
    if include_rejected and status == "rejected":
        return True
    return False


def review_candidates(
    candidates_json: Path,
    *,
    gt_path: Path,
    auto_yes: bool = False,
    include_rejected: bool = False,
    review_id: str | None = None,
) -> dict[str, int]:
    data = json.loads(candidates_json.read_text(encoding="utf-8"))
    stats = {"accepted": 0, "gt_mapped": 0, "rejected": 0, "skipped": 0, "quit": 0}
    gt_data = load_match_ground_truth(gt_path)
    gt_dirty = False

    for item in data.get("candidates", []):
        if not _should_review_item(
            item,
            include_rejected=include_rejected,
            review_id=review_id,
        ):
            continue
        print("\n" + _format_candidate(item))
        if auto_yes:
            answer = "n"
        else:
            answer = input(
                "Accept as new template? [y/N/m=map existing/s=skip/q=quit]: "
            ).strip().lower()
        if answer in {"q", "quit"}:
            stats["quit"] += 1
            break
        if answer in {"s", "skip"}:
            item["status"] = "skipped"
            item["reviewed_at"] = _utc_now()
            stats["skipped"] += 1
            continue

        if answer in {"m", "map", "existing"}:
            label = input("Existing template/card/hero name: ").strip()
            if not label:
                print("Empty label; marking skipped.")
                item["status"] = "skipped"
                item["reviewed_at"] = _utc_now()
                stats["skipped"] += 1
                continue
            apply_cluster = True
            if (item.get("cluster_size") or 1) > 1:
                cluster_answer = input(
                    f"Apply '{label}' to all {item['cluster_size']} similar slots? [Y/n]: "
                ).strip().lower()
                apply_cluster = cluster_answer not in {"n", "no"}
            updated = _apply_gt_label_to_members(
                gt_data,
                item,
                label,
                apply_cluster=apply_cluster,
            )
            if updated == 0:
                print("No GT rows updated.")
                item["status"] = "skipped"
                stats["skipped"] += 1
                continue
            gt_dirty = True
            item["status"] = "gt_mapped"
            item["template_name"] = label
            item["gt_updates"] = updated
            item["reviewed_at"] = _utc_now()
            stats["gt_mapped"] += 1
            print(f"Updated {updated} GT slot(s) to '{label}'.")
            continue

        if answer not in {"y", "yes"}:
            gt_label = input(
                "Correct GT label for existing template? [name or Enter=skip]: "
            ).strip()
            if gt_label:
                apply_cluster = False
                if (item.get("cluster_size") or 1) > 1:
                    cluster_answer = input(
                        f"Apply '{gt_label}' to all {item['cluster_size']} similar slots? [Y/n]: "
                    ).strip().lower()
                    apply_cluster = cluster_answer not in {"n", "no"}
                updated = _apply_gt_label_to_members(
                    gt_data,
                    item,
                    gt_label,
                    apply_cluster=apply_cluster,
                )
                if updated > 0:
                    gt_dirty = True
                    item["status"] = "gt_mapped"
                    item["template_name"] = gt_label
                    item["gt_updates"] = updated
                    item["reviewed_at"] = _utc_now()
                    stats["gt_mapped"] += 1
                    print(f"Updated {updated} GT slot(s) to '{gt_label}'.")
                    continue
            item["status"] = "rejected"
            item["reviewed_at"] = _utc_now()
            stats["rejected"] += 1
            continue

        default_name = item.get("template_name") or ""
        template_name = input(
            f"Template name [{default_name}]: "
        ).strip() or default_name
        if not template_name:
            print("Empty template name; marking skipped.")
            item["status"] = "skipped"
            item["reviewed_at"] = _utc_now()
            stats["skipped"] += 1
            continue

        template_path = (
            hero_template_path(template_name)
            if item["kind"] == "hero"
            else card_template_path(template_name)
        )
        overwrite = False
        if template_exists(template_path):
            overwrite_answer = input(
                f"Template exists at {template_path.name}. Overwrite? [y/N]: "
            ).strip().lower()
            overwrite = overwrite_answer in {"y", "yes"}

        gt_entry = gt_data.get("screenshots", {}).get(item["screenshot"])
        if gt_entry is None:
            print("Could not find GT entry; marking skipped.")
            item["status"] = "skipped"
            stats["skipped"] += 1
            continue

        img_path = _resolve_screenshot_path(gt_entry)
        img = cv2.imread(str(img_path))
        if img is None:
            print("Could not read screenshot; marking skipped.")
            item["status"] = "skipped"
            stats["skipped"] += 1
            continue

        if item["kind"] == "hero":
            saved = save_hero_template(
                img,
                item["player"],
                item["slot"],
                template_name,
                overwrite=overwrite,
            )
        else:
            saved = save_card_template(
                img,
                item["player"],
                item["slot"],
                template_name,
                overwrite=overwrite,
            )

        if saved is None:
            print("Template not saved.")
            item["status"] = "skipped"
            stats["skipped"] += 1
            continue

        item["status"] = "accepted"
        item["template_name"] = template_name
        item["saved_template_path"] = str(saved.relative_to(PROJECT_ROOT)).replace("\\", "/")
        item["reviewed_at"] = _utc_now()
        stats["accepted"] += 1
        print(f"Saved template: {saved}")

    if gt_dirty:
        save_match_ground_truth(gt_data, gt_path)
        print(f"\nSaved GT corrections to {gt_path}")

    data["pending_count"] = sum(
        1 for candidate in data.get("candidates", []) if candidate.get("status") == "pending"
    )
    data["review_finished_at"] = _utc_now()
    candidates_json.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return stats


def command_generate(args: argparse.Namespace) -> None:
    gt_data = load_match_ground_truth(args.gt)
    payload = generate_candidates(
        gt_data,
        output_dir=args.output_dir.resolve(),
        output_json=args.output_json.resolve(),
        path_prefix=args.path_prefix,
        hero_threshold=args.hero_threshold,
        card_threshold=args.card_threshold,
        reset_rejected=getattr(args, "reset_rejected", False),
    )
    print(f"Wrote {payload['candidate_count']} candidates to {args.output_json}")
    print(f"Pending review: {payload['pending_count']}")


def command_review(args: argparse.Namespace) -> None:
    if not args.output_json.exists():
        raise SystemExit(f"Candidates file not found: {args.output_json}")
    stats = review_candidates(
        args.output_json,
        gt_path=args.gt,
        auto_yes=args.auto_no,
        include_rejected=args.include_rejected,
        review_id=args.review_id,
    )
    print("\nReview summary:")
    for key, value in stats.items():
        print(f"  {key}: {value}")
    if stats["accepted"] > 0:
        print(
            "\nNew templates saved. Re-run prediction/database build to apply them:\n"
            "  python scripts/build_match_database.py --predict --force"
        )
    if stats.get("gt_mapped", 0) > 0:
        print(
            "\nGT labels corrected. Re-export database to apply:\n"
            "  python scripts/build_match_database.py --force"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gt", type=Path, default=DEFAULT_GT_PATH)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_CANDIDATES_DIR,
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_CANDIDATES_JSON,
    )
    parser.add_argument(
        "--path-prefix",
        default="screenshots.0701/",
    )
    parser.add_argument("--hero-threshold", type=float, default=HERO_SCORE_THRESHOLD)
    parser.add_argument("--card-threshold", type=float, default=CARD_SCORE_THRESHOLD)

    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate", help="Scan GT and write candidate crops")
    generate.add_argument(
        "--path-prefix",
        default="screenshots.0701/",
        help="Only scan GT screenshots whose path starts with this prefix",
    )
    generate.add_argument(
        "--hero-threshold",
        type=float,
        default=HERO_SCORE_THRESHOLD,
        help="Include hero slots with match score below this value",
    )
    generate.add_argument(
        "--card-threshold",
        type=float,
        default=CARD_SCORE_THRESHOLD,
        help="Include card slots with match score below this value",
    )
    generate.add_argument(
        "--reset-rejected",
        action="store_true",
        help="Turn rejected/skipped candidates back into pending",
    )
    generate.set_defaults(func=command_generate)

    review = subparsers.add_parser(
        "review",
        help="Interactively confirm candidates and save templates",
    )
    review.add_argument(
        "--auto-no",
        action="store_true",
        help="Do not prompt; mark all pending as rejected (for non-interactive runs)",
    )
    review.add_argument(
        "--include-rejected",
        action="store_true",
        help="Also show previously rejected candidates",
    )
    review.add_argument(
        "--id",
        dest="review_id",
        metavar="CANDIDATE_ID",
        help="Review a specific candidate (e.g. c0001), even if rejected",
    )
    review.set_defaults(func=command_review)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
