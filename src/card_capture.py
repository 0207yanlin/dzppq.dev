# -*- coding: utf-8 -*-
"""Runtime card capture and workbook-authoritative detail confirmation."""

from __future__ import annotations

import re
import time
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.card_details import CardDetails
from src.detect_cards import (
    CardDetectionResult,
    detect_cards_detailed,
    should_confirm_card_detail,
)
from src.layout import (
    CARD_DETAIL_NAME_ROI,
    CARD_DETAIL_TEXT_ROI,
    NUM_CARDS,
    NUM_PLAYERS,
    card_roi,
    card_tap_point,
    crop_roi,
)

UNKNOWN = "unknown"
NAME_FUZZY_THRESHOLD = 0.88
NAME_FUZZY_LEAD = 0.08
DETAIL_FUZZY_THRESHOLD = 0.72
DETAIL_FUZZY_LEAD = 0.08
DETAIL_SETTLE_SECONDS = 0.35
DETAIL_POLL_INTERVAL_SECONDS = 0.20
DETAIL_STABILITY_TIMEOUT_SECONDS = 8.0
DETAIL_STABLE_HITS = 2
DETAIL_MAX_POLLS = 12


@dataclass
class CardCaptureResult:
    slots: list[dict[str, Any]]
    summary: dict[str, Any]
    review_artifacts: dict[str, Any]


def normalize_detail_ocr(text: str) -> str:
    """Remove whitespace and punctuation without applying card aliases."""
    return "".join(
        char
        for char in str(text)
        if not char.isspace()
        and not unicodedata.category(char).startswith(("P", "Z"))
    )


def card_body(card_name: str) -> str:
    return card_name.split("·", 1)[1] if "·" in card_name else card_name


def _normalize_name_for_candidate(observed: str, candidate: str) -> str:
    """Apply narrowly scoped OCR repairs only when a candidate supports them."""
    normalized = normalize_detail_ocr(observed)
    candidate_normalized = normalize_detail_ocr(card_body(candidate))
    if normalized == "开揽" and candidate_normalized == "开攒":
        return "开攒"
    if candidate_normalized.endswith("pro"):
        repaired = re.sub(r"(?:r[oO]|pr0)$", "pro", normalized)
        if repaired == candidate_normalized:
            return repaired
    return normalized


def _name_matches(
    observed: str,
    candidates: Sequence[str],
) -> tuple[list[str], str, list[dict[str, Any]], dict[str, str]]:
    normalized_by_candidate = {
        candidate: _normalize_name_for_candidate(observed, candidate)
        for candidate in candidates
    }
    exact = [
        candidate
        for candidate in candidates
        if normalize_detail_ocr(card_body(candidate)) == normalized_by_candidate[candidate]
    ]
    if exact:
        return exact, "exact", [], normalized_by_candidate

    scored = [
        {
            "candidate": candidate,
            "score": SequenceMatcher(
                None,
                normalized_by_candidate[candidate],
                normalize_detail_ocr(card_body(candidate)),
            ).ratio(),
        }
        for candidate in candidates
        if normalize_detail_ocr(card_body(candidate))
    ]
    scored.sort(key=lambda item: float(item["score"]), reverse=True)
    if not scored or float(scored[0]["score"]) < NAME_FUZZY_THRESHOLD:
        return [], "fuzzy", scored, normalized_by_candidate
    second = float(scored[1]["score"]) if len(scored) > 1 else 0.0
    if float(scored[0]["score"]) - second < NAME_FUZZY_LEAD:
        return [], "fuzzy", scored, normalized_by_candidate
    return [str(scored[0]["candidate"])], "fuzzy", scored, normalized_by_candidate


def _conservative_matches(
    observed: str,
    choices: Sequence[tuple[str, str]],
    *,
    threshold: float,
    lead: float,
) -> tuple[list[str], list[dict[str, Any]]]:
    normalized = normalize_detail_ocr(observed)
    scored = [
        {
            "candidate": candidate,
            "score": SequenceMatcher(
                None, normalized, normalize_detail_ocr(reference)
            ).ratio(),
        }
        for candidate, reference in choices
        if normalize_detail_ocr(reference)
    ]
    scored.sort(key=lambda item: float(item["score"]), reverse=True)
    if not scored or float(scored[0]["score"]) < threshold:
        return [], scored
    second = float(scored[1]["score"]) if len(scored) > 1 else 0.0
    if float(scored[0]["score"]) - second < lead:
        return [], scored
    return [str(scored[0]["candidate"])], scored


def resolve_detail_candidate(
    name_text: str,
    detail_text: str,
    candidates: Sequence[str],
    card_details: CardDetails,
) -> tuple[str | None, dict[str, Any]]:
    """Resolve OCR only inside one workbook-configured template group."""
    name_normalized = normalize_detail_ocr(name_text)
    debug: dict[str, Any] = {
        "name_text": name_text,
        "name_normalized": name_normalized,
        "detail_text": detail_text,
        "configured_candidates": list(candidates),
    }
    if not name_normalized:
        debug["name_normalized_for_match"] = ""
        debug["name_normalized_for_match_by_candidate"] = {}
        debug["name_match"] = "empty"
        detail_candidates = list(candidates)
    else:
        (
            name_matches,
            name_match_kind,
            name_scores,
            normalized_by_candidate,
        ) = _name_matches(
            name_text,
            candidates,
        )
        debug["name_normalized_for_match_by_candidate"] = normalized_by_candidate
        debug["name_match"] = name_match_kind
        if name_scores:
            debug["name_scores"] = name_scores

        if not name_matches:
            debug["name_normalized_for_match"] = name_normalized
            top_score = float(name_scores[0]["score"]) if name_scores else 0.0
            debug["reason"] = (
                "name-ambiguous"
                if top_score >= NAME_FUZZY_THRESHOLD
                else "name-mismatch-or-stale"
            )
            return None, debug
        debug["name_normalized_for_match"] = normalized_by_candidate[name_matches[0]]
        if len(name_matches) == 1:
            debug["reason"] = "confirmed-name"
            return name_matches[0], debug
        detail_candidates = name_matches

    detail_normalized = normalize_detail_ocr(detail_text)
    if not detail_normalized:
        debug["reason"] = (
            "name-and-detail-ocr-empty" if not name_normalized else "detail-ocr-empty"
        )
        return None, debug
    detail_choices: list[tuple[str, str]] = []
    for candidate in detail_candidates:
        color = candidate.split("·", 1)[0] if "·" in candidate else ""
        reference = card_details.by_color.get(color, {}).get(candidate, "")
        if reference:
            detail_choices.append((candidate, reference))
    detail_matches, detail_scores = _conservative_matches(
        detail_text,
        detail_choices,
        threshold=DETAIL_FUZZY_THRESHOLD,
        lead=DETAIL_FUZZY_LEAD,
    )
    debug["detail_scores"] = detail_scores
    if len(detail_matches) != 1:
        debug["reason"] = "detail-mismatch-or-nonunique"
        return None, debug
    debug["reason"] = "confirmed-detail"
    return detail_matches[0], debug


def _save_crop(path: Path, image: np.ndarray) -> str | None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, encoded = cv2.imencode(".png", image)
    if not ok:
        return None
    path.write_bytes(encoded.tobytes())
    return str(path)


def _artifact_crops(
    root: Path,
    prefix: str,
    *,
    icon: np.ndarray,
    detail_img: np.ndarray | None = None,
    include_full: bool = False,
) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    icon_path = _save_crop(root / f"{prefix}_icon.png", icon)
    if icon_path:
        artifacts["icon"] = icon_path
    if detail_img is not None:
        name_path = _save_crop(
            root / f"{prefix}_name.png",
            crop_roi(detail_img, CARD_DETAIL_NAME_ROI),
        )
        detail_path = _save_crop(
            root / f"{prefix}_detail.png",
            crop_roi(detail_img, CARD_DETAIL_TEXT_ROI),
        )
        if name_path:
            artifacts["name"] = name_path
        if detail_path:
            artifacts["detail"] = detail_path
        if include_full:
            full_path = _save_crop(root / f"{prefix}_full.png", detail_img)
            if full_path:
                artifacts["full"] = full_path
    return artifacts


def _base_slot(
    row_index: int,
    slot_index: int,
    match: CardDetectionResult,
) -> dict[str, Any]:
    raw_score = float(match.score)
    if match.presence == "empty":
        label: str | None = None
        score: float | None = None
        source: str | None = None
    else:
        label = match.label if match.presence == "occupied" else UNKNOWN
        score = min(1.0, max(0.0, raw_score))
        source = "template"
    template_debug = dict(match.debug)
    template_debug["raw_score"] = raw_score
    return {
        "player": row_index + 1,
        "row_index": row_index,
        "slot_index": slot_index,
        "presence": match.presence,
        "label": label,
        "score": score,
        "source": source,
        "is_ground_truth": False,
        "template_debug": template_debug,
    }


def _wait_for_stable_detail(
    *,
    adb: Any,
    ocr: Any,
    settle_seconds: float,
    poll_interval_seconds: float,
    timeout_seconds: float,
    stable_hits: int,
    max_polls: int,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float],
) -> tuple[np.ndarray | None, str, str, dict[str, Any]]:
    """Poll until normalized name and detail OCR are unchanged twice."""
    settle = max(0.0, float(settle_seconds))
    interval = max(0.0, float(poll_interval_seconds))
    timeout = max(0.0, float(timeout_seconds))
    required_hits = max(2, int(stable_hits))
    poll_limit = max(required_hits, int(max_polls))
    if settle:
        sleep(settle)

    started = monotonic()
    deadline = started + timeout
    previous: tuple[str, str] | None = None
    consecutive = 0
    observations: list[dict[str, Any]] = []
    last_img: np.ndarray | None = None
    last_name = ""
    last_detail = ""

    while True:
        if observations and (
            len(observations) >= poll_limit or monotonic() >= deadline
        ):
            elapsed = monotonic() - started
            return (
                last_img,
                last_name,
                last_detail,
                {
                    "stable": False,
                    "stable_hits": consecutive,
                    "required_stable_hits": required_hits,
                    "polls": len(observations),
                    "minimum_polls_completed": len(observations) >= required_hits,
                    "elapsed_ms": round(elapsed * 1000, 1),
                    "observations": observations,
                    "reason": "detail-stability-timeout",
                    "stop_reason": (
                        "max-polls"
                        if len(observations) >= poll_limit
                        else "wall-clock-timeout"
                    ),
                },
            )
        last_img = adb.capture_bgr()
        last_name = ocr.ocr_text(last_img, CARD_DETAIL_NAME_ROI)
        last_detail = ocr.ocr_text(last_img, CARD_DETAIL_TEXT_ROI)
        normalized = (
            normalize_detail_ocr(last_name),
            normalize_detail_ocr(last_detail),
        )
        observations.append(
            {
                "name_text": last_name,
                "detail_text": last_detail,
                "name_normalized": normalized[0],
                "detail_normalized": normalized[1],
            }
        )
        changed = previous is not None and normalized != previous
        if normalized == previous:
            consecutive += 1
        else:
            previous = normalized
            consecutive = 1
        minimum_polls_completed = len(observations) >= required_hits
        if consecutive >= required_hits:
            return (
                last_img,
                last_name,
                last_detail,
                {
                    "stable": True,
                    "stable_hits": consecutive,
                    "required_stable_hits": required_hits,
                    "polls": len(observations),
                    "minimum_polls_completed": minimum_polls_completed,
                    "elapsed_ms": round((monotonic() - started) * 1000, 1),
                    "observations": observations,
                },
            )
        # A transition gets an immediate follow-up sample, preserving more of
        # the fixed wall-clock budget for the new panel to repeat.
        if interval and not changed:
            sleep(interval)


def capture_cards_runtime(
    overview_bgr: np.ndarray,
    *,
    adb: Any,
    ocr: Any,
    card_details: CardDetails,
    template_sigs: dict,
    review_dir: Path,
    detector: Callable[[np.ndarray, dict], list[dict]] = detect_cards_detailed,
    detail_settle_seconds: float = DETAIL_SETTLE_SECONDS,
    detail_poll_interval_seconds: float = DETAIL_POLL_INTERVAL_SECONDS,
    detail_timeout_seconds: float = DETAIL_STABILITY_TIMEOUT_SECONDS,
    detail_stable_hits: int = DETAIL_STABLE_HITS,
    detail_max_polls: int = DETAIL_MAX_POLLS,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> CardCaptureResult:
    """Detect all 24 slots and confirm configured groups through detail OCR."""
    started = time.perf_counter()
    rows = detector(overview_bgr, template_sigs)
    detected = {
        (int(row["row_index"]), int(card["slot_index"])): card["match"]
        for row in rows
        for card in row["cards"]
    }
    counts = {
        "template": 0,
        "detail": 0,
        "empty": 0,
        "uncertain": 0,
        "unresolved": 0,
    }
    slots: list[dict[str, Any]] = []
    artifact_slots: dict[str, Any] = {}
    configured_stems = set(card_details.same_template_candidates)

    for row_index in range(NUM_PLAYERS):
        for slot_index in range(NUM_CARDS):
            coordinate = (row_index, slot_index)
            match = detected.get(coordinate)
            if match is None:
                match = CardDetectionResult(
                    label=UNKNOWN,
                    score=0.0,
                    presence="uncertain",
                    debug={"reject_reason": "invalid-or-missing-roi"},
                )
            slot = _base_slot(row_index, slot_index, match)
            prefix = f"p{row_index + 1}_s{slot_index + 1}"
            icon = crop_roi(overview_bgr, card_roi(row_index, slot_index)).copy()

            if match.presence == "empty":
                counts["empty"] += 1
                slots.append(slot)
                continue
            if match.presence == "uncertain":
                counts["uncertain"] += 1
                artifacts = _artifact_crops(review_dir, prefix, icon=icon)
                if artifacts:
                    slot["review_artifacts"] = artifacts
                    artifact_slots[prefix] = artifacts
                slots.append(slot)
                continue
            if not should_confirm_card_detail(match, configured_stems):
                counts["template"] += 1
                slots.append(slot)
                continue

            raw_stem = str(match.debug.get("top1_raw_asset_stem"))
            candidates = card_details.same_template_candidates[raw_stem]
            detail_img: np.ndarray | None = None
            detail_debug: dict[str, Any]
            attempts: list[dict[str, Any]] = []
            try:
                resolved = None
                for attempt_number in (1, 2):
                    adb.tap(*card_tap_point(row_index, slot_index))
                    (
                        detail_img,
                        name_text,
                        detail_text,
                        stability_debug,
                    ) = _wait_for_stable_detail(
                        adb=adb,
                        ocr=ocr,
                        settle_seconds=detail_settle_seconds,
                        poll_interval_seconds=detail_poll_interval_seconds,
                        timeout_seconds=detail_timeout_seconds,
                        stable_hits=detail_stable_hits,
                        max_polls=detail_max_polls,
                        sleep=sleep,
                        monotonic=monotonic,
                    )
                    if stability_debug["stable"]:
                        resolved, attempt_debug = resolve_detail_candidate(
                            name_text,
                            detail_text,
                            candidates,
                            card_details,
                        )
                        attempt_debug["stability"] = stability_debug
                    else:
                        attempt_debug = {
                            "configured_candidates": list(candidates),
                            "reason": "detail-stability-timeout",
                            "stability": stability_debug,
                        }
                    attempts.append(
                        {"attempt": attempt_number, **attempt_debug}
                    )
                    detail_debug = attempt_debug
                    clearly_stale = (
                        stability_debug["stable"]
                        and attempt_debug["reason"] == "name-mismatch-or-stale"
                    )
                    if resolved is not None or not clearly_stale:
                        break
                detail_debug = {
                    **detail_debug,
                    "attempt_count": len(attempts),
                    "attempts": attempts,
                }
            except Exception as exc:
                resolved = None
                detail_debug = {
                    "configured_candidates": list(candidates),
                    "reason": "detail-capture-error",
                    "error": str(exc),
                    "attempt_count": len(attempts),
                    "attempts": attempts,
                }

            slot["detail_ocr"] = detail_debug
            if resolved is not None:
                slot.update(
                    {
                        "label": resolved,
                        "source": "detail_ocr",
                        "is_ground_truth": True,
                    }
                )
                counts["detail"] += 1
            else:
                slot.update(
                    {
                        "label": UNKNOWN,
                        "source": "template",
                        "is_ground_truth": False,
                    }
                )
                counts["unresolved"] += 1
                artifacts = _artifact_crops(
                    review_dir,
                    prefix,
                    icon=icon,
                    detail_img=detail_img,
                    include_full=True,
                )
                if artifacts:
                    slot["review_artifacts"] = artifacts
                    artifact_slots[prefix] = artifacts
            slots.append(slot)

    summary: dict[str, Any] = {
        **counts,
        "total_slots": len(slots),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 1),
    }
    review_artifacts = (
        {"directory": str(review_dir), "slots": artifact_slots}
        if artifact_slots
        else {}
    )
    return CardCaptureResult(
        slots=slots,
        summary=summary,
        review_artifacts=review_artifacts,
    )
