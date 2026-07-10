# -*- coding: utf-8 -*-
"""ADB screenshot capture helpers promoted from test_adb.ipynb."""

from __future__ import annotations

import logging
import queue
import re
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

DEFAULT_ADB_BIN = r"C:\Program Files\Netease\MuMu\nx_main\adb.exe"
# adb shell wm size (device logical size in portrait orientation)
EXPECTED_WM_SIZE = (1600, 2160)
# Landscape screenshot / tap coordinate space used by test_adb.ipynb
EXPECTED_SCREENSHOT_SIZE = (2160, 1600)

# Navigation tap coordinates (MuMu landscape 2160x1600)
TAP_MAIN_TO_TRANSIT = (2080, 450)
TAP_TRANSIT_TO_RANKING = (1820, 700)
TAP_RANKING_STEP1 = (200, 800)
TAP_RANKING_STEP2 = (750, 350)
TOP3_PLAYER_TAPS = [(700, 500), (700, 640), (700, 800)]
SCROLL_PLAYER_TAPS = [(700, 933), (700, 1080)]
TAP_PROFILE_PARTY_REVIEW = (200, 400)
TAP_FILTER_PPQ = (2050, 500)
TAP_MATCH_ENTRY_X = 1840
TAP_SWITCH_SOLO_RANK = (200, 1500)
TAP_BACK_FROM_MATCH = (1900, 1500)
TAP_BACK_TO_RANKING = (200, 100)

# One-player swipe validated in test_adb.ipynb; legacy multi-player value kept for reference.
# SWIPE_RANKING_LEGACY = (1000, 1000, 1000, 778.35, 500)
SWIPE_RANKING_ONE_PLAYER = (1000, 1000, 1000, 870, 500)
SWIPE_RANKING = SWIPE_RANKING_ONE_PLAYER
SWIPE_PARTY_REVIEW = (1000, 1000, 1000, 775, 500)

# Screen detection ROIs
MAIN_SCREEN_FRIEND_TAB_BOX = (1250, 1395, 1310, 1430)
RANKING_TRANSIT_BOX = (1700, 700, 1900, 770)
RANKING_SCREEN_BOX = (0, 0, 500, 200)
MATCH_SCREEN_RANK_BOX = (100, 1400, 260, 1600)
MATCH_DURATION_BOX = (1270, 100, 1390, 200)
PARTY_REVIEW_PERMISSION_BOX = (600, 1000, 1800, 1200)
PARTY_REVIEW_LIST_BOX = (400, 500, 900, 1100)
# Left profile sidebar; validated on rank_23_party_review_timeout.png
PROFILE_LEFT_MENU_BOX = (0, 90, 300, 760)
RANKING_VISIBLE_RANKS_BOX = (500, 850, 600, 1100)

SCREEN_MAIN = "游戏主界面"
SCREEN_RANKING_TRANSIT = "排行榜中转界面"
SCREEN_RANKING = "排行榜界面"
SCREEN_PROFILE = "个人信息页面"
SCREEN_MATCH_TEAM_RANK = "对局截图界面-队伍排名版"
SCREEN_MATCH_SOLO_RANK = "对局截图界面-单人排名版"
SCREEN_UNKNOWN = "未知界面"

PARTY_REVIEW_PRIVATE_TEXT = "这名蛋仔设置了查阅权限"
PROFILE_PARTY_REVIEW_ENTRY_TEXT = "派对回顾"
PPQ_GAME_TEXT = "蛋仔碰碰棋"
DUO_PEAK_TEXT = "双人巅峰"
# Max vertical gap between OCR lines still treated as one party-review list entry.
MAX_MATCH_BLOCK_Y_GAP = 80

_OCR_MARKER_SEPARATORS = re.compile(r"[\s\-—–_|·・一]+")
# Common OCR confusions seen in party-review mode labels.
_OCR_MARKER_SUBSTITUTIONS = (
    ("供", "棋"),
    ("拱", "棋"),
    ("火", "人"),
    ("额", "巅"),
    ("颠", "巅"),
    ("蜂", "峰"),
)

_MATCH_DATETIME_RE = re.compile(
    r"(\d{2}-\d{2})\s*(\d{2}:\d{2})|(\d{2}-\d{2})(\d{2}:\d{2})"
)
_MATCH_DURATION_RE = re.compile(r"(\d{1,2})\s*[:：]\s*(\d{1,2})")


def crop_roi(img: np.ndarray, box: tuple[int, int, int, int]) -> np.ndarray:
    x1, y1, x2, y2 = box
    h, w = img.shape[:2]
    if not (0 <= x1 < x2 <= w and 0 <= y1 < y2 <= h):
        raise ValueError(f"ROI out of bounds: box={box}, image=({w}, {h})")
    return img[y1:y2, x1:x2].copy()


def ocr_quad_center(quad: Any) -> tuple[float, float]:
    xs = [p[0] for p in quad]
    ys = [p[1] for p in quad]
    return sum(xs) / len(xs), sum(ys) / len(ys)


def ocr_to_screen_xy(
    roi_box: tuple[int, int, int, int],
    quad: Any,
) -> tuple[int, int]:
    x1, y1, _, _ = roi_box
    cx, cy = ocr_quad_center(quad)
    return int(x1 + cx), int(y1 + cy)


def normalize_match_datetime(text: str) -> str | None:
    """Normalize OCR date/time to ``MM-DD HH:MM``."""
    compact = re.sub(r"\s+", "", text.strip())
    m = _MATCH_DATETIME_RE.search(compact)
    if not m:
        m = _MATCH_DATETIME_RE.search(text.strip())
    if not m:
        return None
    if m.group(1):
        return f"{m.group(1)} {m.group(2)}"
    return f"{m.group(3)} {m.group(4)}"


def match_date_part(normalized: str) -> str:
    return normalized.split()[0]


def _parse_mm_dd(value: str) -> tuple[int, int] | None:
    try:
        month_str, day_str = value.split("-", maxsplit=1)
        month, day = int(month_str), int(day_str)
    except (ValueError, AttributeError):
        return None
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    return month, day


def _capture_reference_datetime(
    target_date: str,
    *,
    year: int | None = None,
    reference: datetime | None = None,
) -> datetime | None:
    if reference is not None:
        return reference
    if year is not None:
        parts = _parse_mm_dd(target_date)
        if parts is None:
            return None
        month, day = parts
        try:
            return datetime(year, month, day)
        except ValueError:
            return None
    return datetime.now()


def parse_capture_target_date(
    target_date: str,
    *,
    year: int | None = None,
    reference: datetime | None = None,
) -> date | None:
    """Resolve target ``MM-DD`` to a calendar date for capture comparisons."""
    ref = _capture_reference_datetime(target_date, year=year, reference=reference)
    if ref is None:
        return None
    ref_year = year or ref.year
    parts = _parse_mm_dd(target_date)
    if parts is None:
        return None
    month, day = parts
    try:
        target_in_ref_year = date(ref_year, month, day)
    except ValueError:
        return None
    if target_in_ref_year > ref.date():
        try:
            return date(ref_year - 1, month, day)
        except ValueError:
            return None
    return target_in_ref_year


def parse_capture_entry_date(
    entry_date: str,
    target_date: str,
    *,
    year: int | None = None,
    reference: datetime | None = None,
) -> date | None:
    """Resolve entry ``MM-DD`` to the calendar date closest to the target anchor."""
    target = parse_capture_target_date(
        target_date,
        year=year,
        reference=reference,
    )
    if target is None:
        return None
    parts = _parse_mm_dd(entry_date)
    if parts is None:
        return None
    month, day = parts
    anchor_year = target.year
    candidates: list[date] = []
    for candidate_year in (anchor_year - 1, anchor_year, anchor_year + 1):
        try:
            candidates.append(date(candidate_year, month, day))
        except ValueError:
            continue
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: abs((candidate - target).days))


def y_bucket(y: float, step: int = 10) -> int:
    return int(round(y / step) * step)


def make_match_dedup_key(
    rank: int,
    player_index: int,
    normalized_datetime: str,
    tap_y: float,
) -> str:
    return f"{rank}|{player_index}|{normalized_datetime}|{y_bucket(tap_y)}"


def normalize_match_duration(text: str) -> str | None:
    """Normalize OCR duration text to ``MM:SS``."""
    normalized = text.strip().replace("：", ":")
    m = _MATCH_DURATION_RE.search(normalized)
    if not m:
        return None
    minutes, seconds = int(m.group(1)), int(m.group(2))
    if seconds >= 60:
        return None
    return f"{minutes:02d}:{seconds:02d}"


def make_global_match_id(start_time: str, duration: str) -> str:
    return f"{start_time}|{duration}"


def extract_visible_match_dates(details: list[dict[str, Any]]) -> list[str]:
    dates: list[str] = []
    for item in details:
        normalized = normalize_match_datetime(str(item.get("text", "")).strip())
        if normalized:
            dates.append(normalized)
    return dates


def has_target_date_on_page(
    details: list[dict[str, Any]],
    target_date: str,
) -> bool:
    return any(match_date_part(dt) == target_date for dt in extract_visible_match_dates(details))


def all_dates_before_target(
    details: list[dict[str, Any]],
    target_date: str,
    *,
    year: int | None = None,
    reference: datetime | None = None,
) -> bool:
    dates = extract_visible_match_dates(details)
    if not dates:
        return False
    target = parse_capture_target_date(
        target_date,
        year=year,
        reference=reference,
    )
    if target is None:
        return False
    for dt in dates:
        entry = parse_capture_entry_date(
            match_date_part(dt),
            target_date,
            year=year,
            reference=reference,
        )
        if entry is None or entry >= target:
            return False
    return True


def is_before_target_date(
    entry_date: str,
    target_date: str,
    *,
    year: int | None = None,
    reference: datetime | None = None,
) -> bool:
    entry = parse_capture_entry_date(
        entry_date,
        target_date,
        year=year,
        reference=reference,
    )
    target = parse_capture_target_date(
        target_date,
        year=year,
        reference=reference,
    )
    if entry is None or target is None:
        return False
    return entry < target


@dataclass(frozen=True)
class PageDateSummary:
    visible_dates: list[str]
    today_dates: list[str]
    before_target_dates: list[str]


def page_date_summary(
    details: list[dict[str, Any]],
    target_date: str,
    *,
    year: int | None = None,
    reference: datetime | None = None,
) -> PageDateSummary:
    visible_dates = extract_visible_match_dates(details)
    today_dates = [
        dt for dt in visible_dates if match_date_part(dt) == target_date
    ]
    before_target_dates = [
        dt
        for dt in visible_dates
        if match_date_part(dt) != target_date
        and is_before_target_date(
            match_date_part(dt),
            target_date,
            year=year,
            reference=reference,
        )
    ]
    return PageDateSummary(
        visible_dates=visible_dates,
        today_dates=today_dates,
        before_target_dates=before_target_dates,
    )


def page_has_before_target_date(
    details: list[dict[str, Any]],
    target_date: str,
    *,
    year: int | None = None,
    reference: datetime | None = None,
) -> bool:
    return bool(
        page_date_summary(
            details,
            target_date,
            year=year,
            reference=reference,
        ).before_target_dates
    )


def parse_visible_ranks(ocr_text: str) -> list[int]:
    ranks: list[int] = []
    for token in re.findall(r"\d+", ocr_text):
        if len(token) == 2 and token.isdigit():
            left, right = int(token[0]), int(token[1])
            if 1 <= left <= 9 and 1 <= right <= 9 and left != right:
                ranks.extend([left, right])
                continue
        value = int(token)
        if 1 <= value <= 200:
            ranks.append(value)
    return ranks


def parse_ranking_entry_rank(text: str) -> int | None:
    """Parse one ranking number from a single OCR detail text."""
    cleaned = text.strip()
    if not cleaned:
        return None
    tokens = re.findall(r"\d+", cleaned)
    if not tokens:
        return None
    token = max(tokens, key=len)
    value = int(token)
    if 1 <= value <= 200:
        return value
    return None


@dataclass(frozen=True)
class RankingEntry:
    rank: int
    tap_y: int
    raw_text: str


def extract_ranking_entries(
    details: list[dict[str, Any]],
    roi_box: tuple[int, int, int, int],
) -> list[RankingEntry]:
    """Extract visible ranking rows with screen Y coordinates from OCR details."""
    entries: list[RankingEntry] = []
    for item in details:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        rank = parse_ranking_entry_rank(text)
        if rank is None:
            continue
        _, tap_y = ocr_to_screen_xy(roi_box, item["box"])
        tap_y_int = int(tap_y)
        entries.append(
            RankingEntry(rank=rank, tap_y=tap_y_int, raw_text=text)
        )
    entries.sort(key=lambda entry: (entry.tap_y, entry.rank))
    return entries


def is_partial_rank_ocr(visible_rank: int, next_target_rank: int) -> bool:
    """True when a single-digit OCR likely belongs to a partially visible two-digit rank."""
    if visible_rank >= next_target_rank or visible_rank >= 10:
        return False
    return next_target_rank >= 10 and next_target_rank % 10 == visible_rank


def compute_next_target_rank(
    start_rank: int,
    end_rank: int,
    *,
    completed_ranks: set[int],
    skipped_ranks: set[int],
    manual_skip_ranks: set[int],
) -> int | None:
    """Return the next rank that still needs processing, or None if done."""
    rank = max(start_rank, 1)
    while rank <= end_rank:
        if rank in completed_ranks or rank in skipped_ranks:
            rank += 1
            continue
        if rank in manual_skip_ranks:
            rank += 1
            continue
        return rank
    return None


def build_next_rank_spec(
    entries: list[RankingEntry],
    *,
    next_target_rank: int,
    end_rank: int,
    processed_ranks: set[int],
    tap_x: int,
) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, str]], str | None]:
    """Build tap spec for exactly ``next_target_rank`` or a wait reason to keep scrolling."""
    if next_target_rank > end_rank or next_target_rank <= 3:
        return [], [], None

    specs: list[tuple[int, int, int, int]] = []
    skipped: list[tuple[int, str]] = []
    min_rank = 4
    visible_in_range = [
        entry.rank
        for entry in entries
        if min_rank <= entry.rank <= end_rank
    ]

    for entry in entries:
        if entry.rank == next_target_rank and entry.rank in processed_ranks:
            skipped.append((entry.rank, "duplicate_rank_visible_after_swipe"))
            return [], skipped, None

    for entry in entries:
        if entry.rank == next_target_rank:
            specs.append((entry.rank, 1, tap_x, entry.tap_y))
            return specs, skipped, None

    for rank in visible_in_range:
        if is_partial_rank_ocr(rank, next_target_rank):
            return [], skipped, "ranking_partial_rank_ocr"

    if visible_in_range and all(rank > next_target_rank for rank in visible_in_range):
        return [], skipped, "ranking_missing_expected_rank"

    if visible_in_range and all(rank < next_target_rank for rank in visible_in_range):
        return [], skipped, "ranking_target_ahead"

    if not visible_in_range or next_target_rank not in visible_in_range:
        return [], skipped, "ranking_missing_expected_rank"

    return [], skipped, None


def build_scroll_player_specs_from_entries(
    entries: list[RankingEntry],
    *,
    next_expected_rank: int,
    end_rank: int,
    processed_ranks: set[int],
    tap_x: int,
) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, str]]]:
    """Build scroll-player tap specs for the next expected rank only."""
    specs, skipped, _wait_reason = build_next_rank_spec(
        entries,
        next_target_rank=next_expected_rank,
        end_rank=end_rank,
        processed_ranks=processed_ranks,
        tap_x=tap_x,
    )
    return specs, skipped


def make_mumu_filename(when: datetime | None = None) -> str:
    dt = when or datetime.now()
    ms = int(dt.microsecond / 1000)
    return dt.strftime(f"MuMu-%Y%m%d-%H%M%S-{ms:03d}.png")


@dataclass(frozen=True)
class MatchEntry:
    normalized_datetime: str
    tap_y: int
    duo_peak_y: int
    time_y: int
    dedup_key: str


def _normalize_ocr_marker(text: str) -> str:
    """Normalize OCR mode labels for fuzzy marker matching."""
    normalized = _OCR_MARKER_SEPARATORS.sub("", text.strip())
    for src, dst in _OCR_MARKER_SUBSTITUTIONS:
        normalized = normalized.replace(src, dst)
    return normalized


def _has_ppq_marker(text: str) -> bool:
    if PPQ_GAME_TEXT in text:
        return True
    return PPQ_GAME_TEXT in _normalize_ocr_marker(text)


def _has_duo_peak_marker(text: str) -> bool:
    if DUO_PEAK_TEXT in text:
        return True
    normalized = _normalize_ocr_marker(text)
    if DUO_PEAK_TEXT in normalized:
        return True
    return "双人巅" in normalized


def _match_block_has_ppq(
    items: list[tuple[int, int, str, Any]],
    duo_idx: int,
) -> bool:
    """Return True if a PPQ marker appears in the same list entry as duo peak."""
    duo_y = items[duo_idx][0]
    duo_text = items[duo_idx][2]
    if _has_ppq_marker(duo_text):
        return True

    k = duo_idx - 1
    while k >= 0:
        prev_y, _, prev_text, _ = items[k]
        if _has_duo_peak_marker(prev_text):
            break
        if normalize_match_datetime(prev_text):
            break
        if duo_y - prev_y > MAX_MATCH_BLOCK_Y_GAP:
            break
        if _has_ppq_marker(prev_text):
            return True
        k -= 1

    k = duo_idx + 1
    while k < len(items):
        next_y, _, next_text, _ = items[k]
        if _has_duo_peak_marker(next_text):
            break
        if normalize_match_datetime(next_text):
            break
        if next_y - duo_y > MAX_MATCH_BLOCK_Y_GAP:
            break
        if _has_ppq_marker(next_text):
            return True
        k += 1

    return False


def extract_match_entries(
    details: list[dict[str, Any]],
    roi_box: tuple[int, int, int, int],
    *,
    rank: int,
    player_index: int,
    require_ppq: bool = True,
) -> list[MatchEntry]:
    """Pair party-review rows with datetime rows in the match list."""
    items: list[tuple[int, int, str, Any]] = []
    for item in details:
        text = str(item.get("text", "")).strip()
        if not text:
            continue
        x, y = ocr_to_screen_xy(roi_box, item["box"])
        items.append((y, x, text, item))

    items.sort(key=lambda row: (row[0], row[1]))
    entries: list[MatchEntry] = []
    i = 0
    while i < len(items):
        _, _, text, _ = items[i]
        if not _has_duo_peak_marker(text):
            i += 1
            continue
        if require_ppq and not _match_block_has_ppq(items, i):
            i += 1
            continue
        duo_y = items[i][0]
        time_y: int | None = None
        normalized: str | None = None
        j = i + 1
        while j < len(items):
            candidate = normalize_match_datetime(items[j][2])
            if candidate:
                time_y = items[j][0]
                normalized = candidate
                break
            if _has_duo_peak_marker(items[j][2]):
                break
            j += 1
        if normalized is not None and time_y is not None:
            tap_y = int(round((duo_y + time_y) / 2))
            key = make_match_dedup_key(rank, player_index, normalized, tap_y)
            entries.append(
                MatchEntry(
                    normalized_datetime=normalized,
                    tap_y=tap_y,
                    duo_peak_y=duo_y,
                    time_y=time_y,
                    dedup_key=key,
                )
            )
            i = j + 1
        else:
            i += 1
    return entries


def filter_new_match_entries(
    entries: list[MatchEntry],
    processed_entry_keys: set[str],
    seen_dedup_keys: set[str],
    seen_start_times: set[str],
) -> list[MatchEntry]:
    """Filter match entries not yet handled by dedup key or start time."""
    return [
        entry
        for entry in entries
        if entry.dedup_key not in processed_entry_keys
        and entry.dedup_key not in seen_dedup_keys
        and entry.normalized_datetime not in seen_start_times
    ]


def should_exit_before_target_after_today_done(
    date_summary: PageDateSummary,
    entries: list[MatchEntry],
    target_date: str,
    seen_this_player: set[str],
    processed_entry_keys: set[str],
    seen_start_times: set[str] | None = None,
) -> bool:
    """Exit when page mixes older dates with fully handled today duo-peak entries."""
    if not date_summary.before_target_dates:
        return False
    page_today_entries = [
        entry
        for entry in entries
        if match_date_part(entry.normalized_datetime) == target_date
    ]
    if not page_today_entries:
        return False
    remaining = [
        entry
        for entry in page_today_entries
        if entry.dedup_key not in seen_this_player
        and entry.dedup_key not in processed_entry_keys
        and (
            seen_start_times is None
            or entry.normalized_datetime not in seen_start_times
        )
    ]
    return not remaining


def parse_wm_size_output(text: str) -> tuple[int, int] | None:
    """Parse ``Physical size: WxH`` or similar wm size output."""
    match = re.search(r"(\d+)\s*x\s*(\d+)", text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


ADB_RECOVERY_HINT = (
    "Try --connect to reconnect MuMu TCP, or --serial <device> to pick another instance."
)


class AdbClient:
    def __init__(
        self,
        adb_bin: str = DEFAULT_ADB_BIN,
        device_serial: str | None = None,
        *,
        tap_delay: float = 0.5,
        swipe_delay: float = 0.5,
        verbose_commands: bool = False,
    ) -> None:
        self.adb_bin = adb_bin
        self.device_serial = device_serial
        self.tap_delay = tap_delay
        self.swipe_delay = swipe_delay
        self.verbose_commands = verbose_commands
        self.width = 0
        self.height = 0
        self._last_png: bytes | None = None
        self._last_bgr: np.ndarray | None = None

    def adb(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        cmd = [self.adb_bin]
        if self.device_serial and args and args[0] not in {
            "connect",
            "devices",
            "kill-server",
            "start-server",
            "disconnect",
        }:
            cmd.extend(["-s", self.device_serial])
        cmd.extend(args)
        if self.verbose_commands:
            logger.debug("adb %s", " ".join(cmd[1:]))
        return subprocess.run(cmd, capture_output=True, timeout=timeout)

    def adb_shell(self, *args: str, timeout: float = 30) -> subprocess.CompletedProcess[str]:
        return self.adb("shell", *args, timeout=timeout)

    def _adb_for_serial(
        self,
        serial: str,
        *args: str,
        timeout: float = 30,
    ) -> subprocess.CompletedProcess[bytes]:
        cmd = [self.adb_bin, "-s", serial, *args]
        if self.verbose_commands:
            logger.debug("adb %s", " ".join(cmd[1:]))
        return subprocess.run(cmd, capture_output=True, timeout=timeout)

    def _adb_shell_for_serial(
        self,
        serial: str,
        *args: str,
        timeout: float = 10,
    ) -> subprocess.CompletedProcess[bytes]:
        return self._adb_for_serial(serial, "shell", *args, timeout=timeout)

    def list_devices(self) -> list[str]:
        result = self.adb("devices")
        lines = result.stdout.decode().strip().splitlines()[1:]
        return [
            ln.split()[0]
            for ln in lines
            if ln.strip() and "device" in ln.split()[1:]
        ]

    def probe_device(self, serial: str) -> tuple[bool, str]:
        """Return whether ``serial`` can execute ``wm size`` and parse dimensions."""
        result = self._adb_shell_for_serial(serial, "wm", "size")
        text = (result.stdout or result.stderr or b"").decode().strip()
        if result.returncode != 0:
            return False, text or f"returncode {result.returncode}"
        if parse_wm_size_output(text) is None:
            return False, f"Cannot parse screen size: {text!r}"
        return True, text

    def _ordered_device_candidates(
        self,
        devices: list[str],
        prefer: str | None,
    ) -> list[str]:
        if prefer:
            return [prefer] if prefer in devices else []
        ordered: list[str] = []
        for serial in devices:
            if serial.startswith("127.0.0.1:") and serial not in ordered:
                ordered.append(serial)
        if self.device_serial and self.device_serial in devices:
            if self.device_serial not in ordered:
                ordered.append(self.device_serial)
        for serial in devices:
            if serial not in ordered:
                ordered.append(serial)
        return ordered

    def connect(self, host: str = "127.0.0.1", port: int = 16384) -> str:
        target = f"{host}:{port}"
        result = self.adb("connect", target)
        message = (result.stdout or b"").decode().strip() or (result.stderr or b"").decode().strip()
        logger.info("adb connect: %s", message)
        self.device_serial = target
        return target

    def check_device(self, prefer: str | None = None) -> str:
        devices = self.list_devices()
        if not devices:
            raise RuntimeError("No adb device detected.")

        if prefer and prefer not in devices:
            raise RuntimeError(
                f"Preferred device {prefer!r} not in adb devices list: {devices}. "
                f"{ADB_RECOVERY_HINT}"
            )

        candidates = (
            [devices[0]]
            if len(devices) == 1 and not prefer
            else self._ordered_device_candidates(devices, prefer)
        )
        failures: list[tuple[str, str]] = []
        for serial in candidates:
            ok, reason = self.probe_device(serial)
            if ok:
                if failures:
                    logger.warning(
                        "Device %s unavailable (%s), using %s",
                        failures[0][0],
                        failures[0][1],
                        serial,
                    )
                elif len(devices) > 1 and not prefer:
                    others = [device for device in devices if device != serial]
                    logger.warning(
                        "Multiple devices detected, using %s (others: %s)",
                        serial,
                        ", ".join(others),
                    )
                self.device_serial = serial
                return self.device_serial
            failures.append((serial, reason))

        if prefer:
            raise RuntimeError(
                f"Preferred device {prefer} is not healthy: {failures[0][1]}. "
                f"{ADB_RECOVERY_HINT}"
            )

        details = "; ".join(f"{serial}: {reason}" for serial, reason in failures)
        raise RuntimeError(
            f"No healthy adb device found. Tried: {details}. {ADB_RECOVERY_HINT}"
        )

    def get_screen_size(self) -> tuple[int, int]:
        if not self.device_serial:
            self.check_device()
        serial = self.device_serial
        assert serial is not None
        result = self.adb_shell("wm", "size")
        text = (result.stdout or result.stderr or b"").decode().strip()
        if result.returncode != 0:
            raise RuntimeError(
                f"wm size failed on {serial}: {text}. {ADB_RECOVERY_HINT}"
            )
        parsed = parse_wm_size_output(text)
        if parsed is None:
            raise RuntimeError(
                f"Cannot parse screen size on {serial}: {text!r}. {ADB_RECOVERY_HINT}"
            )
        self.width, self.height = parsed
        return self.width, self.height

    def validate_wm_size(self) -> tuple[int, int]:
        """Validate device ``wm size`` matches expected portrait logical size."""
        serial = self.device_serial or "<unknown>"
        try:
            wm_w, wm_h = self.get_screen_size()
        except RuntimeError:
            raise
        except Exception as exc:
            raise RuntimeError(
                f"Resolution check failed on {serial}: {exc}. {ADB_RECOVERY_HINT}"
            ) from exc

        expected_wm_w, expected_wm_h = EXPECTED_WM_SIZE
        if (wm_w, wm_h) != (expected_wm_w, expected_wm_h):
            raise RuntimeError(
                f"Unexpected wm size on {serial}: {wm_w}x{wm_h}; "
                f"expected {expected_wm_w}x{expected_wm_h} for hardcoded coordinates. "
                f"{ADB_RECOVERY_HINT}"
            )
        return wm_w, wm_h

    def _validate_screenshot_dimensions(self, img: np.ndarray) -> tuple[int, int]:
        serial = self.device_serial or "<unknown>"
        shot_h, shot_w = img.shape[:2]
        expected_shot_w, expected_shot_h = EXPECTED_SCREENSHOT_SIZE
        if (shot_w, shot_h) != (expected_shot_w, expected_shot_h):
            wm_w, wm_h = self.width, self.height
            raise RuntimeError(
                f"Unexpected screenshot size on {serial}: {shot_w}x{shot_h}; "
                f"expected {expected_shot_w}x{expected_shot_h} for hardcoded coordinates. "
                f"(wm size was {wm_w}x{wm_h}) {ADB_RECOVERY_HINT}"
            )
        return shot_w, shot_h

    def capture_bgr_validated(self) -> np.ndarray:
        """Capture one screenshot after wm-size check and validate dimensions."""
        serial = self.device_serial or "<unknown>"
        wm_w, wm_h = self.validate_wm_size()
        try:
            img = self.capture_bgr()
        except Exception as exc:
            raise RuntimeError(
                f"Screenshot capture failed on {serial}: {exc}. {ADB_RECOVERY_HINT}"
            ) from exc
        shot_w, shot_h = self._validate_screenshot_dimensions(self._last_bgr)  # type: ignore[arg-type]
        logger.info(
            "Resolution OK: device=%s wm=%sx%s screenshot=%sx%s",
            serial,
            wm_w,
            wm_h,
            shot_w,
            shot_h,
        )
        return img

    def validate_resolution(self) -> tuple[tuple[int, int], tuple[int, int]]:
        """Validate wm size and landscape screenshot dimensions.

        Returns ``((wm_w, wm_h), (shot_w, shot_h))`` on success.
        """
        wm_w, wm_h = self.validate_wm_size()
        try:
            img = self.capture_bgr()
        except Exception as exc:
            serial = self.device_serial or "<unknown>"
            raise RuntimeError(
                f"Screenshot capture failed on {serial}: {exc}. {ADB_RECOVERY_HINT}"
            ) from exc
        shot_w, shot_h = self._validate_screenshot_dimensions(self._last_bgr)  # type: ignore[arg-type]
        serial = self.device_serial or "<unknown>"
        logger.info(
            "Resolution OK: device=%s wm=%sx%s screenshot=%sx%s",
            serial,
            wm_w,
            wm_h,
            shot_w,
            shot_h,
        )
        return (wm_w, wm_h), (shot_w, shot_h)

    def tap(self, x: int | float, y: int | float, delay: float | None = None) -> None:
        ix, iy = int(round(x)), int(round(y))
        self.adb_shell("input", "tap", str(ix), str(iy))
        logger.debug("tap (%s, %s)", ix, iy)
        time.sleep(delay if delay is not None else self.tap_delay)

    def swipe(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        duration_ms: int = 500,
        delay: float | None = None,
    ) -> None:
        args = [str(int(round(v))) for v in (x1, y1, x2, y2)] + [str(duration_ms)]
        self.adb_shell("input", "swipe", *args)
        logger.debug("swipe (%s,%s)->(%s,%s) %sms", x1, y1, x2, y2, duration_ms)
        time.sleep(delay if delay is not None else self.swipe_delay)

    def capture_png(self) -> bytes:
        result = self.adb("exec-out", "screencap", "-p", timeout=15)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.decode())
        png_bytes = result.stdout
        if not png_bytes.startswith(b"\x89PNG"):
            raise RuntimeError("Invalid screenshot data from device")
        self._last_png = png_bytes
        img = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("Failed to decode screenshot")
        self._last_bgr = img
        return png_bytes

    def capture_bgr(self) -> np.ndarray:
        self.capture_png()
        assert self._last_bgr is not None
        return self._last_bgr.copy()

    def has_cached_png(self) -> bool:
        return bool(self._last_png)

    def save_cached_png(self, path: Path) -> Path:
        """Write the most recent screenshot without triggering a new screencap."""
        if not self._last_png:
            return self.save_png(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self._last_png)
        return path

    def save_png(self, path: Path, png_bytes: bytes | None = None) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = png_bytes if png_bytes is not None else self.capture_png()
        path.write_bytes(data)
        return path


@dataclass(frozen=True)
class WarmupResult:
    backend: str
    elapsed_ms: float
    success: bool
    error: str | None = None


@dataclass
class _WorkerTask:
    fn: Callable[[], Any]
    done: threading.Event = field(default_factory=threading.Event)
    result: Any = None
    error: BaseException | None = None


class OcrHelper:
    def __init__(self, *, use_cls: bool = True) -> None:
        self._local = threading.local()
        self._backend: str | None = None
        self._use_cls = use_cls
        self._init_lock = threading.Lock()
        self._worker_lock = threading.Lock()
        self._task_queue: queue.Queue[_WorkerTask | None] | None = None
        self._worker_thread: threading.Thread | None = None
        self._warmup_lock = threading.Lock()
        self._warmup_done = threading.Event()
        self._warmup_result: WarmupResult | None = None
        self._warmup_queued = False

    def _create_engine(self) -> Any:
        rapidocr_error: Exception | None = None
        try:
            from rapidocr_onnxruntime import RapidOCR

            engine = RapidOCR(use_cls=self._use_cls)
            self._backend = "rapidocr"
            logger.info("OCR backend: rapidocr (use_cls=%s)", self._use_cls)
            return engine
        except Exception as exc:
            rapidocr_error = exc
            logger.warning("rapidocr unavailable: %s", exc)

        easyocr_error: Exception | None = None
        try:
            import easyocr

            engine = easyocr.Reader(["ch_sim", "en"], gpu=False, verbose=False)
            self._backend = "easyocr"
            logger.info("OCR backend: easyocr")
            return engine
        except Exception as exc:
            easyocr_error = exc
            logger.warning("easyocr unavailable: %s", exc)

        parts: list[str] = []
        if rapidocr_error is not None:
            parts.append(f"rapidocr: {rapidocr_error}")
        if easyocr_error is not None:
            parts.append(f"easyocr: {easyocr_error}")
        message = "OCR engine unavailable. " + "; ".join(parts)
        raise ImportError(message) from (easyocr_error or rapidocr_error)

    def _get_engine(self) -> Any:
        engine = getattr(self._local, "engine", None)
        if engine is not None:
            return engine
        with self._init_lock:
            engine = getattr(self._local, "engine", None)
            if engine is None:
                engine = self._create_engine()
                self._local.engine = engine
        return engine

    def run_ocr(
        self,
        patch: np.ndarray,
        *,
        use_cls: bool | None = None,
    ) -> list[dict[str, Any]]:
        engine = self._get_engine()
        if self._backend == "easyocr":
            raw = engine.readtext(patch)
            return [{"text": t, "score": float(s), "box": b} for b, t, s in raw]
        ocr_kwargs: dict[str, Any] = {}
        if use_cls is not None:
            ocr_kwargs["use_cls"] = use_cls
        result, _ = engine(patch, **ocr_kwargs)
        if not result:
            return []
        return [{"text": t, "score": float(s), "box": b} for b, t, s in result]

    def ocr_text(
        self,
        img: np.ndarray,
        box: tuple[int, int, int, int] | None = None,
        *,
        use_cls: bool | None = None,
    ) -> str:
        patch = crop_roi(img, box) if box is not None else img
        return "".join(item["text"] for item in self.run_ocr(patch, use_cls=use_cls))

    def ocr_details(
        self,
        img: np.ndarray,
        box: tuple[int, int, int, int] | None = None,
        *,
        use_cls: bool | None = None,
    ) -> list[dict[str, Any]]:
        patch = crop_roi(img, box) if box is not None else img
        return self.run_ocr(patch, use_cls=use_cls)

    def _run_warmup_once(self) -> WarmupResult:
        started = time.perf_counter()
        try:
            dummy = np.zeros((32, 128, 3), dtype=np.uint8)
            self.ocr_text(dummy)
            backend = self._backend or "unknown"
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info("OCR warmup completed (backend=%s, %.0f ms)", backend, elapsed_ms)
            return WarmupResult(backend=backend, elapsed_ms=elapsed_ms, success=True)
        except Exception as exc:
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.warning("OCR warmup failed after %.0f ms: %s", elapsed_ms, exc)
            return WarmupResult(
                backend=self._backend or "unknown",
                elapsed_ms=elapsed_ms,
                success=False,
                error=str(exc),
            )

    def _ensure_worker(self) -> None:
        with self._worker_lock:
            if self._worker_thread is not None and self._worker_thread.is_alive():
                return
            self._task_queue = queue.Queue()
            self._worker_thread = threading.Thread(
                target=self._worker_loop,
                name="ocr-worker",
                daemon=True,
            )
            self._worker_thread.start()

    def _worker_loop(self) -> None:
        assert self._task_queue is not None
        while True:
            task = self._task_queue.get()
            if task is None:
                self._task_queue.task_done()
                break
            try:
                task.result = task.fn()
            except BaseException as exc:
                task.error = exc
            finally:
                task.done.set()
                self._task_queue.task_done()

    def _submit(self, fn: Callable[[], Any], *, timeout: float | None = 120.0) -> Any:
        self._ensure_worker()
        assert self._task_queue is not None
        task = _WorkerTask(fn=fn)
        self._task_queue.put(task)
        if not task.done.wait(timeout):
            raise TimeoutError("OCR task did not finish in time")
        if task.error is not None:
            raise task.error
        return task.result

    def _submit_async(self, fn: Callable[[], Any]) -> None:
        self._ensure_worker()
        assert self._task_queue is not None
        self._task_queue.put(_WorkerTask(fn=fn))

    def _queue_warmup_if_needed(self) -> None:
        with self._warmup_lock:
            if self._warmup_result is not None or self._warmup_queued:
                return
            self._warmup_queued = True

            def _do_warmup() -> WarmupResult:
                result = self._run_warmup_once()
                self._warmup_result = result
                self._warmup_done.set()
                return result

            self._submit_async(_do_warmup)

    def run_on_ocr_thread(
        self,
        fn: Callable[[], Any],
        *,
        timeout: float | None = 120.0,
    ) -> Any:
        """Run *fn* on the OCR worker thread so it reuses the warmed-up engine."""
        return self._submit(fn, timeout=timeout)

    def warmup(self, *, blocking: bool = False, timeout: float | None = 120.0) -> WarmupResult | None:
        """Preload OCR models on the OCR worker thread. Returns result when blocking=True."""
        with self._warmup_lock:
            if self._warmup_result is not None:
                return self._warmup_result
        self._queue_warmup_if_needed()
        if not blocking:
            return None
        finished = self._warmup_done.wait(timeout)
        result = self._warmup_result
        if not finished or result is None:
            raise TimeoutError("OCR warmup did not finish in time")
        if not result.success:
            raise RuntimeError(result.error or "OCR warmup failed")
        return result

    def start_warmup_async(self) -> None:
        """Start OCR warmup in the background if not already started."""
        self.warmup(blocking=False)

    def ensure_ready(self, timeout: float | None = 120.0) -> WarmupResult:
        """Wait for OCR warmup and return the result."""
        result = self.warmup(blocking=True, timeout=timeout)
        assert result is not None
        return result

    @property
    def warmup_finished(self) -> bool:
        return self._warmup_done.is_set()

    @property
    def warmup_result(self) -> WarmupResult | None:
        return self._warmup_result


@dataclass
class WaitResult:
    screen: str
    img: np.ndarray | None
    elapsed_ms: int
    polls: int
    stable: bool


@dataclass
class PartyReviewWaitResult:
    state: str  # private | public | timeout
    img: np.ndarray | None
    elapsed_ms: int
    polls: int
    stable: bool
    stable_hits_used: int = 0


@dataclass
class ProfilePartyReviewEntryWaitResult:
    ready: bool
    img: np.ndarray | None
    elapsed_ms: int
    polls: int
    stable: bool
    on_profile: bool


class ScreenDetector:
    def __init__(self, ocr: OcrHelper) -> None:
        self.ocr = ocr

    def detect(self, img: np.ndarray, *, verbose: bool = False) -> str:
        match_rank_text = self.ocr.ocr_text(img, MATCH_SCREEN_RANK_BOX)
        if "单人排名" in match_rank_text:
            name = SCREEN_MATCH_TEAM_RANK
        elif "双人排名" in match_rank_text:
            name = SCREEN_MATCH_SOLO_RANK
        else:
            title_text = self.ocr.ocr_text(img, RANKING_SCREEN_BOX)
            if "排行榜" in title_text:
                name = SCREEN_RANKING
            elif "个人信息" in title_text:
                name = SCREEN_PROFILE
            elif "排行榜" in self.ocr.ocr_text(img, RANKING_TRANSIT_BOX):
                name = SCREEN_RANKING_TRANSIT
            elif "好友" in self.ocr.ocr_text(img, MAIN_SCREEN_FRIEND_TAB_BOX):
                name = SCREEN_MAIN
            else:
                name = SCREEN_UNKNOWN
        if verbose:
            logger.info("detect_screen -> %s", name)
        return name

    def has_party_review_content(self, img: np.ndarray) -> bool:
        for item in self.ocr.ocr_details(img, PARTY_REVIEW_LIST_BOX):
            text = str(item.get("text", "")).strip()
            if DUO_PEAK_TEXT in text:
                return True
            if normalize_match_datetime(text):
                return True
        return False

    def has_profile_party_review_entry(self, img: np.ndarray) -> bool:
        text = self.ocr.ocr_text(img, PROFILE_LEFT_MENU_BOX)
        if PROFILE_PARTY_REVIEW_ENTRY_TEXT in text:
            return True
        # OCR may drop the last character on narrow crops.
        return "派对回" in text

    def is_profile_party_review_entry_ready(self, img: np.ndarray) -> bool:
        return (
            self.detect(img) == SCREEN_PROFILE
            and self.has_profile_party_review_entry(img)
        )

    def wait_for_profile_party_review_entry(
        self,
        adb: AdbClient,
        *,
        timeout: float = 8.0,
        poll: float = 0.6,
        stable_hits: int = 2,
        verbose: bool = False,
    ) -> ProfilePartyReviewEntryWaitResult:
        start = time.time()
        deadline = start + timeout
        last_img: np.ndarray | None = None
        polls = 0
        consecutive = 0
        last_on_profile = False

        while time.time() < deadline:
            polls += 1
            last_img = adb.capture_bgr()
            screen = self.detect(last_img)
            last_on_profile = screen == SCREEN_PROFILE
            ready = last_on_profile and self.has_profile_party_review_entry(last_img)
            if verbose:
                logger.debug(
                    "wait_for_profile_party_review_entry poll=%s ready=%s on_profile=%s",
                    polls,
                    ready,
                    last_on_profile,
                )
            if ready:
                consecutive += 1
                if consecutive >= stable_hits:
                    elapsed_ms = int((time.time() - start) * 1000)
                    return ProfilePartyReviewEntryWaitResult(
                        ready=True,
                        img=last_img,
                        elapsed_ms=elapsed_ms,
                        polls=polls,
                        stable=True,
                        on_profile=True,
                    )
            else:
                consecutive = 0
            time.sleep(poll)

        elapsed_ms = int((time.time() - start) * 1000)
        return ProfilePartyReviewEntryWaitResult(
            ready=False,
            img=last_img,
            elapsed_ms=elapsed_ms,
            polls=polls,
            stable=False,
            on_profile=last_on_profile,
        )

    def classify_party_review_state(self, img: np.ndarray) -> str | None:
        """Return ``private``, ``public``, or ``None`` if still loading."""
        if self.is_party_review_private(img):
            return "private"
        if self.has_party_review_content(img):
            return "public"
        if self.detect(img) != SCREEN_PROFILE:
            return "public"
        return None

    def wait_for_screen(
        self,
        adb: AdbClient,
        expected: set[str],
        *,
        timeout: float = 8.0,
        poll: float = 0.6,
        stable_hits: int = 2,
        verbose: bool = False,
    ) -> WaitResult:
        start = time.time()
        deadline = start + timeout
        last = SCREEN_UNKNOWN
        last_img: np.ndarray | None = None
        polls = 0
        consecutive = 0
        stable_screen = SCREEN_UNKNOWN

        while time.time() < deadline:
            polls += 1
            last_img = adb.capture_bgr()
            last = self.detect(last_img)
            if verbose:
                logger.debug("wait_for_screen poll=%s screen=%s", polls, last)
            if last in expected:
                if last == stable_screen:
                    consecutive += 1
                else:
                    stable_screen = last
                    consecutive = 1
                if consecutive >= stable_hits:
                    elapsed_ms = int((time.time() - start) * 1000)
                    return WaitResult(
                        screen=last,
                        img=last_img,
                        elapsed_ms=elapsed_ms,
                        polls=polls,
                        stable=True,
                    )
            else:
                stable_screen = SCREEN_UNKNOWN
                consecutive = 0
            time.sleep(poll)

        elapsed_ms = int((time.time() - start) * 1000)
        return WaitResult(
            screen=last,
            img=last_img,
            elapsed_ms=elapsed_ms,
            polls=polls,
            stable=False,
        )

    def wait_for_party_review(
        self,
        adb: AdbClient,
        *,
        timeout: float = 12.0,
        poll: float = 0.6,
        stable_hits: int = 2,
        public_stable_hits: int = 1,
        private_stable_hits: int = 2,
        verbose: bool = False,
    ) -> PartyReviewWaitResult:
        start = time.time()
        deadline = start + timeout
        last_img: np.ndarray | None = None
        polls = 0
        consecutive = 0
        stable_state = ""

        while time.time() < deadline:
            polls += 1
            last_img = adb.capture_bgr()
            state = self.classify_party_review_state(last_img)
            if verbose:
                logger.debug("wait_for_party_review poll=%s state=%s", polls, state)
            if state in {"private", "public"}:
                required_hits = (
                    private_stable_hits if state == "private" else public_stable_hits
                )
                if state == stable_state:
                    consecutive += 1
                else:
                    stable_state = state
                    consecutive = 1
                if consecutive >= required_hits:
                    elapsed_ms = int((time.time() - start) * 1000)
                    return PartyReviewWaitResult(
                        state=state,
                        img=last_img,
                        elapsed_ms=elapsed_ms,
                        polls=polls,
                        stable=True,
                        stable_hits_used=required_hits,
                    )
            else:
                stable_state = ""
                consecutive = 0
            time.sleep(poll)

        elapsed_ms = int((time.time() - start) * 1000)
        return PartyReviewWaitResult(
            state="timeout",
            img=last_img,
            elapsed_ms=elapsed_ms,
            polls=polls,
            stable=False,
            stable_hits_used=0,
        )

    def wait_until(
        self,
        adb: AdbClient,
        expected: set[str],
        *,
        timeout: float = 8.0,
        poll: float = 0.6,
    ) -> str:
        result = self.wait_for_screen(
            adb,
            expected,
            timeout=timeout,
            poll=poll,
            stable_hits=1,
            verbose=False,
        )
        return result.screen

    def is_party_review_private(self, img: np.ndarray) -> bool:
        text = self.ocr.ocr_text(img, PARTY_REVIEW_PERMISSION_BOX)
        return PARTY_REVIEW_PRIVATE_TEXT in text or "查阅权限" in text
