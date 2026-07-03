# -*- coding: utf-8 -*-
"""Save hero/card template ROIs with Unicode-safe file writes."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from src.detect_cards import prepare_card_icon
from src.layout import (
    CARD_TEMPLATE_DIR,
    HERO_TEMPLATE_DIR,
    card_roi,
    crop_roi,
    hero_roi,
)

UNKNOWN = "unknown"


def save_jpg(path: Path, image: np.ndarray, quality: int = 95) -> None:
    """Write JPEG via imencode to support non-ASCII paths on Windows."""
    path.parent.mkdir(parents=True, exist_ok=True)
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError(f"failed to encode image: {path}")
    path.write_bytes(buf.tobytes())


def hero_template_path(hero_label: str, template_dir: Path | None = None) -> Path:
    directory = template_dir or HERO_TEMPLATE_DIR
    return directory / f"{hero_label}.jpg"


def card_template_path(card_name: str, template_dir: Path | None = None) -> Path:
    directory = template_dir or CARD_TEMPLATE_DIR
    return directory / f"{card_name}.jpg"


def template_exists(path: Path) -> bool:
    return path.exists()


def save_hero_template(
    img: np.ndarray,
    player: int,
    slot: int,
    hero_label: str,
    template_dir: Path | None = None,
    overwrite: bool = False,
) -> Path | None:
    """Save a hero portrait ROI as a template. Returns path if saved."""
    if not hero_label or hero_label == UNKNOWN:
        return None
    path = hero_template_path(hero_label, template_dir)
    if path.exists() and not overwrite:
        return None
    roi = crop_roi(img, hero_roi(player, slot))
    save_jpg(path, roi)
    return path


def save_card_template(
    img: np.ndarray,
    player: int,
    slot: int,
    card_name: str,
    template_dir: Path | None = None,
    overwrite: bool = False,
) -> Path | None:
    """Save a card icon ROI (background removed) as a template."""
    if not card_name or card_name == UNKNOWN:
        return None
    path = card_template_path(card_name, template_dir)
    if path.exists() and not overwrite:
        return None
    roi = crop_roi(img, card_roi(player, slot))
    icon, _ = prepare_card_icon(roi, bg_color=(255, 255, 255))
    save_jpg(path, icon)
    return path


def capture_missing_templates(
    img: np.ndarray,
    hero_updates: list[tuple[int, int, str, str]],
    card_updates: list[tuple[int, int, str, str]],
    *,
    ask: bool = True,
) -> list[Path]:
    """Save templates for corrected hero/card names.

    Each update is (player, slot, old_name, new_name).
    """
    saved: list[Path] = []
    for player, slot, old_name, new_name in hero_updates:
        if new_name == old_name or new_name == UNKNOWN:
            continue
        path = hero_template_path(new_name)
        if path.exists():
            continue
        if ask:
            answer = input(
                f"Save hero template '{new_name}' from player {player + 1} slot {slot + 1}? [y/N] "
            ).strip().lower()
            if answer not in {"y", "yes"}:
                continue
        result = save_hero_template(img, player, slot, new_name)
        if result is not None:
            saved.append(result)
            print(f"Saved hero template: {result.name}")

    for player, slot, old_name, new_name in card_updates:
        if new_name == old_name or new_name == UNKNOWN:
            continue
        path = card_template_path(new_name)
        if path.exists():
            continue
        if ask:
            answer = input(
                f"Save card template '{new_name}' from player {player + 1} slot {slot + 1}? [y/N] "
            ).strip().lower()
            if answer not in {"y", "yes"}:
                continue
        result = save_card_template(img, player, slot, new_name)
        if result is not None:
            saved.append(result)
            print(f"Saved card template: {result.name}")
    return saved
