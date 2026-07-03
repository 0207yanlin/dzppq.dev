# -*- coding: utf-8 -*-
"""Detect player team pairing from lineup color blocks."""

from __future__ import annotations

import cv2
import numpy as np

from src.layout import CARD_Y_OFFSET, NUM_PLAYERS, crop_roi

COLOR_X1 = 520
COLOR_X2 = 580
COLOR_Y_BASE = 305
COLOR_H = 45


def team_color_roi(player: int) -> tuple[int, int, int, int]:
    """Return (x1, y1, x2, y2) for a player's team color block."""
    y1 = COLOR_Y_BASE + CARD_Y_OFFSET[player]
    return COLOR_X1, y1, COLOR_X2, y1 + COLOR_H


def crop_team_color_roi(img: np.ndarray, player: int) -> np.ndarray:
    return crop_roi(img, team_color_roi(player))


def extract_team_color(roi: np.ndarray, bright_v: int = 230) -> np.ndarray:
    """Extract representative color; filter highlights and use LAB median."""
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = (hsv[:, :, 1] > 35) & (hsv[:, :, 2] > 50) & (hsv[:, :, 2] < bright_v)
    if mask.sum() < 15:
        mask = (hsv[:, :, 1] > 25) & (hsv[:, :, 2] > 40)
    if mask.sum() < 10:
        mask = np.ones(roi.shape[:2], dtype=bool)
    lab = cv2.cvtColor(roi, cv2.COLOR_BGR2LAB)[mask]
    return np.median(lab, axis=0).astype(np.float32)


def color_distance(
    a: np.ndarray,
    b: np.ndarray,
    ignore_lightness: bool = True,
) -> float:
    if ignore_lightness:
        return float(np.linalg.norm(a[1:] - b[1:]))
    return float(np.linalg.norm(a - b))


def detect_highlight_player(colors: list[np.ndarray], z_threshold: float = 1.2) -> int | None:
    """The highlighted player ROI is often brighter when opened from home page."""
    lightness = np.array([c[0] for c in colors], dtype=np.float32)
    med = float(np.median(lightness))
    mad = float(np.median(np.abs(lightness - med))) or 1.0
    scores = (lightness - med) / mad
    if scores.max() < z_threshold:
        return None
    return int(np.argmax(scores))


def _pair_players_subset(
    colors: list[np.ndarray],
    players: list[int],
) -> tuple[float, list[tuple[int, int]]]:
    if not players:
        return 0.0, []
    a = players[0]
    best: tuple[float, list[tuple[int, int]]] | None = None
    for b in players[1:]:
        d = color_distance(colors[a], colors[b])
        rest_total, rest_pairs = _pair_players_subset(
            colors, [p for p in players if p not in (a, b)]
        )
        total = d + rest_total
        pairs = [(a, b)] + rest_pairs
        if best is None or total < best[0]:
            best = (total, pairs)
    assert best is not None
    return best


def pair_players_by_team_color(
    colors: list[np.ndarray],
    highlight_player: int | None = None,
) -> tuple[list[tuple[int, int]], int | None]:
    """Pair 8 players into 4 teams; returns 0-based pairs and highlight index."""
    n = len(colors)
    if highlight_player is None:
        highlight_player = detect_highlight_player(colors)

    if highlight_player is not None:
        remainder_candidates = [
            (min(highlight_player, j), max(highlight_player, j))
            for j in range(n)
            if j != highlight_player
        ]
    else:
        remainder_candidates = [(i, j) for i in range(n) for j in range(i + 1, n)]

    best: tuple[float, tuple[int, int], list[tuple[int, int]]] | None = None
    for i, j in remainder_candidates:
        remaining = [p for p in range(n) if p not in (i, j)]
        total, inner_pairs = _pair_players_subset(colors, remaining)
        candidate = (total, (i, j), inner_pairs)
        if best is None or candidate[0] < best[0]:
            best = candidate

    assert best is not None
    _, remainder, inner_pairs = best
    pairs = inner_pairs + [remainder]
    return sorted((min(a, b), max(a, b)) for a, b in pairs), highlight_player


def detect_pairs(img: np.ndarray) -> dict:
    """Detect team pairs for all players in one screenshot."""
    color_rois = [crop_team_color_roi(img, j) for j in range(NUM_PLAYERS)]
    team_colors = [extract_team_color(roi) for roi in color_rois]
    pairs_0based, highlight_idx = pair_players_by_team_color(team_colors)
    pairs = [[a + 1, b + 1] for a, b in pairs_0based]
    partner_by_player: dict[int, int] = {}
    for a, b in pairs:
        partner_by_player[a] = b
        partner_by_player[b] = a
    return {
        "pairs": pairs,
        "highlight_player": (highlight_idx + 1) if highlight_idx is not None else None,
        "partner_by_player": partner_by_player,
    }
