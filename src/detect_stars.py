# -*- coding: utf-8 -*-
"""Hero star level detection via HSV golden-star mask."""

from __future__ import annotations

import cv2
import numpy as np

from src.layout import NUM_HEROES, NUM_PLAYERS, crop_roi, roi_valid, star_roi

STAR_HSV_LOW = (20, 100, 130)
STAR_HSV_HIGH = (38, 255, 255)
STAR_MIN_AREA = 20


def isolate_stars(roi: np.ndarray) -> np.ndarray:
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, STAR_HSV_LOW, STAR_HSV_HIGH)
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    return mask


def count_stars_from_mask(mask: np.ndarray, min_area: int = STAR_MIN_AREA) -> int:
    n_labels, _labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    return sum(
        1 for i in range(1, n_labels) if stats[i, cv2.CC_STAT_AREA] >= min_area
    )


def detect_stars_for_player(img: np.ndarray, player: int) -> list[int]:
    """Return star counts per slot; trailing zeros trimmed."""
    stars = []
    for i in range(NUM_HEROES):
        box = star_roi(player, i)
        roi = crop_roi(img, box)
        if not roi_valid(roi, box):
            break
        stars.append(count_stars_from_mask(isolate_stars(roi)))
    while stars and stars[-1] == 0:
        stars.pop()
    return stars


def detect_stars(img: np.ndarray) -> list[list[int]]:
    """Detect star counts for all players."""
    return [detect_stars_for_player(img, j) for j in range(NUM_PLAYERS)]
