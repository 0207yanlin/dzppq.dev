# -*- coding: utf-8 -*-
"""Versioned per-screenshot card sidecars.

Each ``foo.png`` is paired with ``foo.cards.json``.  Version 1 stores exactly
one record for every card ROI (8 players x 3 slots), including empty and
uncertain slots, while its detector-compatible adapter omits empty slots.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from src.layout import NUM_CARDS, NUM_PLAYERS

SCHEMA_VERSION = 1
PRESENCE_VALUES = frozenset({"empty", "occupied", "uncertain"})
_TOP_LEVEL_KEYS = frozenset(
    {
        "schema_version",
        "image",
        "slots",
        "capture_metadata",
        "summary",
        "review_artifacts",
    }
)
_SLOT_KEYS = frozenset(
    {
        "player",
        "row_index",
        "slot_index",
        "presence",
        "label",
        "score",
        "source",
        "is_ground_truth",
        "template_debug",
        "detail_ocr",
        "review_artifacts",
    }
)


class CardSidecarError(ValueError):
    """Raised when a card sidecar violates the versioned contract."""


def card_sidecar_path(png_path: str | Path) -> Path:
    """Return the same-stem ``.cards.json`` path for a PNG."""
    path = Path(png_path)
    if path.suffix.lower() != ".png":
        raise CardSidecarError(f"card sidecar identity requires a PNG: {path}")
    return path.with_name(f"{path.stem}.cards.json")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise CardSidecarError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> None:
    raise CardSidecarError(f"invalid JSON numeric constant: {value}")


def _require_object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CardSidecarError(f"{field} must be an object")
    return value


def _validate_optional_object(container: dict, field: str) -> None:
    if field in container and container[field] is not None:
        _require_object(container[field], field)


def validate_card_sidecar(
    data: dict[str, Any],
    png_path: str | Path | None = None,
) -> None:
    """Strictly validate schema v1 and optional PNG identity."""
    if not isinstance(data, dict):
        raise CardSidecarError("card sidecar must be an object")
    extra = set(data) - _TOP_LEVEL_KEYS
    if extra:
        raise CardSidecarError(f"unsupported top-level fields: {sorted(extra)}")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CardSidecarError(f"schema_version must be {SCHEMA_VERSION}")

    image = _require_object(data.get("image"), "image")
    if set(image) != {"filename", "stem"}:
        raise CardSidecarError("image must contain exactly filename and stem")
    filename = image.get("filename")
    stem = image.get("stem")
    if not isinstance(filename, str) or Path(filename).name != filename:
        raise CardSidecarError("image.filename must be a basename")
    if Path(filename).suffix.lower() != ".png":
        raise CardSidecarError("image.filename must identify a PNG")
    if not isinstance(stem, str) or not stem or stem != Path(filename).stem:
        raise CardSidecarError("image.stem must match image.filename")
    if png_path is not None:
        png = Path(png_path)
        if png.suffix.lower() != ".png" or png.name != filename or png.stem != stem:
            raise CardSidecarError(
                f"sidecar image identity {filename!r} does not match {png.name!r}"
            )

    for field in ("capture_metadata", "summary", "review_artifacts"):
        _validate_optional_object(data, field)

    slots = data.get("slots")
    expected_count = NUM_PLAYERS * NUM_CARDS
    if not isinstance(slots, list) or len(slots) != expected_count:
        raise CardSidecarError(f"slots must contain exactly {expected_count} records")

    seen: set[tuple[int, int]] = set()
    for index, raw_slot in enumerate(slots):
        slot = _require_object(raw_slot, f"slots[{index}]")
        extra = set(slot) - _SLOT_KEYS
        if extra:
            raise CardSidecarError(
                f"slots[{index}] has unsupported fields: {sorted(extra)}"
            )
        required = {
            "player",
            "row_index",
            "slot_index",
            "presence",
            "label",
            "score",
            "source",
            "is_ground_truth",
        }
        missing = required - set(slot)
        if missing:
            raise CardSidecarError(
                f"slots[{index}] missing required fields: {sorted(missing)}"
            )

        player = slot["player"]
        row_index = slot["row_index"]
        slot_index = slot["slot_index"]
        if type(player) is not int or not 1 <= player <= NUM_PLAYERS:
            raise CardSidecarError(f"slots[{index}].player is out of range")
        if type(row_index) is not int or not 0 <= row_index < NUM_PLAYERS:
            raise CardSidecarError(f"slots[{index}].row_index is out of range")
        if player != row_index + 1:
            raise CardSidecarError(f"slots[{index}] player/row_index disagree")
        if type(slot_index) is not int or not 0 <= slot_index < NUM_CARDS:
            raise CardSidecarError(f"slots[{index}].slot_index is out of range")
        coordinate = (row_index, slot_index)
        if coordinate in seen:
            raise CardSidecarError(f"duplicate card slot {coordinate}")
        seen.add(coordinate)

        presence = slot["presence"]
        if presence not in PRESENCE_VALUES:
            raise CardSidecarError(f"slots[{index}].presence is invalid")
        label = slot["label"]
        if label is not None and (not isinstance(label, str) or not label.strip()):
            raise CardSidecarError(f"slots[{index}].label must be null or non-empty")
        score = slot["score"]
        if score is not None and (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or not 0.0 <= float(score) <= 1.0
        ):
            raise CardSidecarError(f"slots[{index}].score must be null or 0..1")
        source = slot["source"]
        if source is not None and (
            not isinstance(source, str) or not source.strip()
        ):
            raise CardSidecarError(f"slots[{index}].source must be null or non-empty")
        if type(slot["is_ground_truth"]) is not bool:
            raise CardSidecarError(f"slots[{index}].is_ground_truth must be boolean")
        if presence == "empty" and (label is not None or score is not None):
            raise CardSidecarError(f"slots[{index}] empty slot must have null label/score")
        if presence == "occupied" and label is None:
            raise CardSidecarError(f"slots[{index}] occupied slot requires a label")
        if slot["is_ground_truth"] and label is None:
            raise CardSidecarError(
                f"slots[{index}] ground-truth slot requires a label"
            )
        for field in ("template_debug", "detail_ocr", "review_artifacts"):
            if field in slot and slot[field] is not None:
                _require_object(slot[field], f"slots[{index}].{field}")

    expected = {
        (row_index, slot_index)
        for row_index in range(NUM_PLAYERS)
        for slot_index in range(NUM_CARDS)
    }
    if seen != expected:
        raise CardSidecarError("slots must uniquely cover the complete 8x3 grid")
    try:
        json.dumps(data, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise CardSidecarError(f"sidecar contains non-JSON data: {exc}") from exc


def create_card_sidecar(
    png_path: str | Path,
    slots: Iterable[dict[str, Any]],
    *,
    capture_metadata: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
    review_artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build and validate a schema-v1 sidecar document."""
    png = Path(png_path)
    data: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "image": {"filename": png.name, "stem": png.stem},
        "slots": list(slots),
    }
    if capture_metadata is not None:
        data["capture_metadata"] = capture_metadata
    if summary is not None:
        data["summary"] = summary
    if review_artifacts is not None:
        data["review_artifacts"] = review_artifacts
    validate_card_sidecar(data, png)
    return data


def load_card_sidecar(png_path: str | Path) -> dict[str, Any]:
    """Atomically-observable read with strict schema and image validation."""
    png = Path(png_path)
    path = card_sidecar_path(png)
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except CardSidecarError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise CardSidecarError(f"failed to read {path}: {exc}") from exc
    validate_card_sidecar(data, png)
    return data


def save_card_sidecar(
    png_path: str | Path,
    data: dict[str, Any],
) -> Path:
    """Validate and atomically replace a PNG's sidecar."""
    png = Path(png_path)
    validate_card_sidecar(data, png)
    destination = card_sidecar_path(png)
    destination.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        data,
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    ) + "\n"
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
    return destination


def fingerprint_card_sidecar(data: dict[str, Any]) -> str:
    """Return a stable SHA-256 content fingerprint for a valid sidecar."""
    validate_card_sidecar(data)
    canonical = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def load_card_sidecar_fingerprint(png_path: str | Path) -> str | None:
    """Return the valid sidecar fingerprint, or ``None`` when absent/invalid."""
    path = card_sidecar_path(png_path)
    if not path.exists():
        return None
    try:
        return fingerprint_card_sidecar(load_card_sidecar(png_path))
    except CardSidecarError:
        return None


def card_sidecar_to_cards_by_player(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Adapt a sidecar to ``detect_cards`` output while retaining audit fields."""
    validate_card_sidecar(data)
    rows = [
        {"player": row_index + 1, "row_index": row_index, "cards": []}
        for row_index in range(NUM_PLAYERS)
    ]
    for slot in sorted(
        data["slots"], key=lambda item: (item["row_index"], item["slot_index"])
    ):
        if slot["presence"] == "empty":
            continue
        card: dict[str, Any] = {
            "slot_index": slot["slot_index"],
            "label": slot["label"] or "unknown",
            "score": slot["score"],
            "presence": slot["presence"],
            "is_ground_truth": slot["is_ground_truth"],
            "from_sidecar": True,
        }
        if slot["source"] is not None:
            card["source"] = slot["source"]
        rows[slot["row_index"]]["cards"].append(card)
    return rows
