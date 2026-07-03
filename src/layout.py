# -*- coding: utf-8 -*-
"""Screenshot ROI layout constants and helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

NUM_PLAYERS = 8
NUM_HEROES = 9
NUM_CARDS = 3

HERO_X_OFFSET = [0, 74, 149, 223, 297, 371, 445, 519, 594]
HERO_Y_OFFSET = [0, 93, 185, 280, 372, 465, 559, 652]
STAR_Y_OFFSET = [0, 93, 186, 279, 373, 466, 560, 653]
CARD_X_OFFSET = [0, 77, 154]
CARD_Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]

HERO_X_BASE = 582
HERO_Y_BASE = 306
HERO_W = 70
HERO_H = 39

STAR_Y_BASE = 287
STAR_H = 17

CARD_X_BASE = 1340
CARD_Y_BASE = 305
CARD_W = 45
CARD_H = 45

EQUIPMENT_X_BASE = 581
EQUIPMENT_Y_BASE = 345
EQUIPMENT_W = 72
EQUIPMENT_H = 24
EQUIPMENT_ITEM_H = 24
EQUIPMENT_ITEM_ROIS = {
    1: ((606, 629),),
    2: ((593, 616), (618, 641)),
    3: ((581, 604), (605, 628), (630, 654)),
}

HERO_TEMPLATE_DIR = ROOT / "assets" / "templates" / "heroes"
CARD_TEMPLATE_DIR = ROOT / "assets" / "templates" / "cards"
EQUIPMENT_TEMPLATE_DIR = ROOT / "assets" / "templates" / "equipments"
SCREENSHOT_DIR = ROOT / "screenshots"


def hero_roi(player: int, slot: int) -> tuple[int, int, int, int]:
    """Return (x1, y1, x2, y2) for a hero portrait ROI."""
    x1 = HERO_X_BASE + HERO_X_OFFSET[slot]
    y1 = HERO_Y_BASE + HERO_Y_OFFSET[player]
    return x1, y1, x1 + HERO_W, y1 + HERO_H


def star_roi(player: int, slot: int) -> tuple[int, int, int, int]:
    """Return (x1, y1, x2, y2) for a star badge ROI."""
    x1 = HERO_X_BASE + HERO_X_OFFSET[slot]
    y1 = STAR_Y_BASE + STAR_Y_OFFSET[player]
    return x1, y1, x1 + HERO_W, y1 + STAR_H


def card_roi(player: int, slot: int) -> tuple[int, int, int, int]:
    """Return (x1, y1, x2, y2) for a card icon ROI."""
    x1 = CARD_X_BASE + CARD_X_OFFSET[slot]
    y1 = CARD_Y_BASE + CARD_Y_OFFSET[player]
    return x1, y1, x1 + CARD_W, y1 + CARD_H


def equipment_roi(player: int, slot: int) -> tuple[int, int, int, int]:
    """Return (x1, y1, x2, y2) for a hero equipment strip ROI."""
    x1 = EQUIPMENT_X_BASE + HERO_X_OFFSET[slot]
    y1 = EQUIPMENT_Y_BASE + HERO_Y_OFFSET[player]
    return x1, y1, x1 + EQUIPMENT_W, y1 + EQUIPMENT_H


def equipment_item_roi(
    player: int,
    slot: int,
    equipment_count: int,
    item: int,
) -> tuple[int, int, int, int]:
    """Return (x1, y1, x2, y2) for one equipment icon within a hero strip."""
    if equipment_count not in EQUIPMENT_ITEM_ROIS:
        raise ValueError(f"unsupported equipment count: {equipment_count}")
    ranges = EQUIPMENT_ITEM_ROIS[equipment_count]
    if item < 0 or item >= len(ranges):
        raise IndexError(f"equipment item {item} out of range for count {equipment_count}")
    rel_x1, rel_x2 = ranges[item]
    x1 = rel_x1 + HERO_X_OFFSET[slot]
    x2 = rel_x2 + HERO_X_OFFSET[slot]
    y1 = EQUIPMENT_Y_BASE + HERO_Y_OFFSET[player]
    return x1, y1, x2, y1 + EQUIPMENT_ITEM_H


def crop_roi(img: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x1, y1, x2, y2 = box
    return img[y1:y2, x1:x2]


def roi_valid(roi: np.ndarray, box: tuple[int, int, int, int]) -> bool:
    x1, y1, x2, y2 = box
    return roi.shape[0] == (y2 - y1) and roi.shape[1] == (x2 - x1)
