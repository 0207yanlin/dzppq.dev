# -*- coding: utf-8 -*-
"""Generate and interactively review new hero/card template candidates."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.layout import ROOT as PROJECT_ROOT, card_roi, crop_roi, hero_roi  # noqa: E402
from src.detect_cards import (  # noqa: E402
    DETECTION_PARAMS as CARD_DETECTION_PARAMS,
    diagnose_card_match,
    load_template_sigs,
)
from src.detect_heroes import (  # noqa: E402
    DETECTION_PARAMS as HERO_DETECTION_PARAMS,
    build_hero_template_cache,
    crop_center,
    load_templates,
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
DEFAULT_GT_PATH = PROJECT_ROOT / "data" / "match_ground_truth.json"

HERO_SCORE_THRESHOLD = 0.75
CARD_SCORE_THRESHOLD = 0.75
SIGNATURE_SIZE = (32, 32)
CLUSTER_DISTANCE = 12.0
REVIEW_MEMBER_LIMIT = 10


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_match_ground_truth(path: Path | None = None) -> dict:
    gt_path = path or DEFAULT_GT_PATH
    if not gt_path.exists():
        return {
            "version": 1,
            "description": "Full match ground truth (pairs, heroes, equipment, cards)",
            "screenshots": {},
        }
    return json.loads(gt_path.read_text(encoding="utf-8"))


def save_match_ground_truth(data: dict, path: Path | None = None) -> None:
    gt_path = path or DEFAULT_GT_PATH
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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


def _normalize_template_label(name: str | None) -> str | None:
    if not name:
        return None
    return name.replace(".jpg", "")


class _DiagnosticContext:
    def __init__(self) -> None:
        self.card_sigs: dict | None = None
        self._hero_templates: dict | None = None
        self._hero_gray_cache: dict[str, np.ndarray] | None = None

    def card_templates(self) -> dict:
        if self.card_sigs is None:
            self.card_sigs = load_template_sigs()
        return self.card_sigs

    def hero_templates(self) -> tuple[dict, dict[str, np.ndarray]]:
        if self._hero_templates is None:
            templates = load_templates()
            self._hero_templates = templates
            self._hero_gray_cache = build_hero_template_cache(
                templates,
                HERO_DETECTION_PARAMS["margin_ratio"],
            )
        assert self._hero_gray_cache is not None
        return self._hero_templates, self._hero_gray_cache


def _empty_match_debug(
    *,
    threshold: float,
    min_gap: float,
    reject_reason: str = "no_templates",
) -> dict:
    return {
        "top1_label": None,
        "top1_score": 0.0,
        "top2_label": None,
        "top2_score": 0.0,
        "gap": 0.0,
        "threshold": threshold,
        "min_gap": min_gap,
        "gap_threshold": min_gap,
        "reject_reason": reject_reason,
    }


def _diagnose_card_match(crop: np.ndarray, template_sigs: dict) -> dict:
    return diagnose_card_match(crop, template_sigs)


def _diagnose_hero_match(
    crop: np.ndarray,
    templates: dict,
    template_gray_cache: dict[str, np.ndarray],
) -> dict:
    params = HERO_DETECTION_PARAMS
    threshold = float(params["threshold"])
    min_gap = float(params["min_gap"])
    padding = int(params["padding"])
    margin_ratio = float(params["margin_ratio"])

    roi_gray = crop_center(cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY), margin_ratio)
    search = cv2.copyMakeBorder(
        roi_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE
    )
    scores: list[tuple[float, str]] = []
    for name in templates:
        temp_gray = template_gray_cache[name]
        th, tw = temp_gray.shape
        if search.shape[0] < th or search.shape[1] < tw:
            continue
        res = cv2.matchTemplate(search, temp_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        scores.append((float(max_val), name))
    scores.sort(reverse=True)

    if not scores:
        return _empty_match_debug(threshold=threshold, min_gap=min_gap)

    top1_score, top1_name = scores[0]
    top2_score, top2_name = scores[1] if len(scores) > 1 else (0.0, None)
    gap = top1_score - top2_score

    if top1_score < threshold:
        reject_reason = "below_threshold"
    elif gap < min_gap:
        reject_reason = "below_min_gap"
    else:
        reject_reason = "accepted"

    return {
        "top1_label": _normalize_template_label(top1_name),
        "top1_score": top1_score,
        "top2_label": _normalize_template_label(top2_name),
        "top2_score": top2_score,
        "gap": gap,
        "threshold": threshold,
        "min_gap": min_gap,
        "gap_threshold": min_gap,
        "reject_reason": reject_reason,
    }


def _annotate_match_debug(raw: list[dict], diag: _DiagnosticContext) -> None:
    card_sigs = diag.card_templates()
    hero_templates, hero_gray_cache = diag.hero_templates()
    for item in raw:
        crop = item.get("crop")
        if crop is None:
            continue
        if item["kind"] == "card":
            debug = _diagnose_card_match(crop, card_sigs)
        else:
            debug = _diagnose_hero_match(crop, hero_templates, hero_gray_cache)
        if item.get("reason") == "low_score" and item.get("predicted_label") not in {
            None,
            "",
            "unknown",
        }:
            debug["reject_reason"] = "accepted_low_score"
        item["match_debug"] = debug


def _serialize_member(member: dict) -> dict:
    payload = {
        "screenshot": member["screenshot"],
        "player": member["player"],
        "slot": member["slot"],
        "path": member["path"],
        "predicted_label": member["predicted_label"],
        "score": member["score"],
        "reason": member["reason"],
    }
    if member.get("match_debug") is not None:
        payload["match_debug"] = member["match_debug"]
    return payload


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
    _annotate_match_debug(raw, _DiagnosticContext())
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
                "match_debug": rep.get("match_debug"),
                "crop_path": str(crop_path.relative_to(PROJECT_ROOT)).replace("\\", "/"),
                "cluster_size": len(cluster["members"]),
                "cluster_examples": [
                    _serialize_member(member)
                    for member in cluster["members"][:5]
                ],
                "cluster_members": [
                    _serialize_member(member)
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


def _format_score(score: float | None) -> str:
    if score is None:
        return "?"
    return f"{float(score):.3f}"


def _format_match_debug(debug: dict | None) -> list[str]:
    if not debug:
        return []
    top1 = debug.get("top1_label") or "?"
    top2 = debug.get("top2_label") or "?"
    lines = [
        "  match_debug:",
        (
            f"    top1: {top1} score={_format_score(debug.get('top1_score'))} "
            f"top2: {top2} score={_format_score(debug.get('top2_score'))} "
            f"gap={_format_score(debug.get('gap'))} "
            f"reject={debug.get('reject_reason', '?')}"
        ),
    ]
    extras: list[str] = []
    if debug.get("top1_shape") is not None:
        extras.append(f"top1_shape={_format_score(debug.get('top1_shape'))}")
    if debug.get("top1_color") is not None:
        extras.append(f"top1_color={_format_score(debug.get('top1_color'))}")
    if debug.get("top2_shape") is not None:
        extras.append(f"top2_shape={_format_score(debug.get('top2_shape'))}")
    if debug.get("top2_color") is not None:
        extras.append(f"top2_color={_format_score(debug.get('top2_color'))}")
    if debug.get("match_path"):
        extras.append(f"path={debug['match_path']}")
    if extras:
        lines.append(f"    {' '.join(extras)}")
    return lines


def _member_match_debug_suffix(member: dict) -> str:
    debug = member.get("match_debug")
    if not debug:
        return ""
    top1 = debug.get("top1_label") or "?"
    top2 = debug.get("top2_label") or "?"
    return (
        f" | top1={top1}({_format_score(debug.get('top1_score'))}) "
        f"top2={top2}({_format_score(debug.get('top2_score'))}) "
        f"gap={_format_score(debug.get('gap'))} "
        f"reject={debug.get('reject_reason', '?')}"
    )


def _enrich_cluster_members(item: dict) -> list[dict]:
    """Normalize cluster members; backfill score/reason from cluster_examples when missing."""
    members = list(item.get("cluster_members") or [])
    if not members:
        members = list(item.get("cluster_examples") or [])
    if not members:
        members = [
            {
                "screenshot": item["screenshot"],
                "player": item["player"],
                "slot": item["slot"],
                "predicted_label": item.get("predicted_label"),
                "score": item.get("score"),
                "reason": item.get("reason"),
                "path": item.get("path"),
                "match_debug": item.get("match_debug"),
            }
        ]

    example_lookup: dict[tuple[str, int, int], dict] = {}
    for example in item.get("cluster_examples") or []:
        key = (example["screenshot"], example["player"], example["slot"])
        example_lookup[key] = example

    default_reason = item.get("reason", "")
    enriched: list[dict] = []
    for member in members:
        key = (member["screenshot"], member["player"], member["slot"])
        example = example_lookup.get(key, {})
        enriched.append(
            {
                "screenshot": member["screenshot"],
                "player": member["player"],
                "slot": member["slot"],
                "path": member.get("path") or example.get("path") or item.get("path"),
                "predicted_label": member.get("predicted_label") or "unknown",
                "score": (
                    member["score"]
                    if member.get("score") is not None
                    else example.get("score")
                ),
                "reason": member.get("reason") or example.get("reason") or default_reason,
                "match_debug": (
                    member.get("match_debug")
                    or example.get("match_debug")
                    or item.get("match_debug")
                ),
            }
        )
    return enriched


def _prediction_distribution(members: list[dict]) -> list[tuple[str, int]]:
    counts = Counter(member.get("predicted_label") or "unknown" for member in members)
    return counts.most_common()


def _format_candidate(item: dict, *, member_limit: int = REVIEW_MEMBER_LIMIT) -> str:
    lines = [
        f"[{item['id']}] {item['kind']} candidate ({item['reason']})",
        f"  screenshot: {item['screenshot']}",
        f"  player/slot: P{item['player'] + 1} slot {item['slot'] + 1}",
        f"  predicted: {item['predicted_label']} score={_format_score(item.get('score'))}",
        f"  crop: {item['crop_path']}",
        f"  cluster_size: {item.get('cluster_size', 1)}",
    ]

    members = _enrich_cluster_members(item)
    distribution = _prediction_distribution(members)
    if distribution:
        dist_text = ", ".join(f"{label} x{count}" for label, count in distribution)
        lines.append(f"  prediction_dist: {dist_text}")

    lines.extend(_format_match_debug(item.get("match_debug")))

    lines.append("  cluster_samples:")
    for member in members[:member_limit]:
        lines.append(
            f"    - {member['screenshot']} P{member['player'] + 1} slot {member['slot'] + 1} "
            f"-> {member['predicted_label']} score={_format_score(member.get('score'))} "
            f"reason={member.get('reason', '')}"
            f"{_member_match_debug_suffix(member)}"
        )
    remaining = len(members) - min(len(members), member_limit)
    if remaining > 0:
        lines.append(f"    ... and {remaining} more")

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
    member_limit: int = REVIEW_MEMBER_LIMIT,
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
        print("\n" + _format_candidate(item, member_limit=member_limit))
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
        member_limit=args.member_limit,
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
    review.add_argument(
        "--member-limit",
        type=int,
        default=REVIEW_MEMBER_LIMIT,
        help="Max cluster sample lines to print per candidate (default: 10)",
    )
    review.set_defaults(func=command_review)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
