# -*- coding: utf-8 -*-
"""Daily automated capture of ranked player match screenshots via ADB."""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adb_capture import (  # noqa: E402
    DEFAULT_ADB_BIN,
    MATCH_DURATION_BOX,
    PARTY_REVIEW_LIST_BOX,
    RANKING_VISIBLE_RANKS_BOX,
    SCREEN_MAIN,
    SCREEN_MATCH_SOLO_RANK,
    SCREEN_MATCH_TEAM_RANK,
    SCREEN_PROFILE,
    SCREEN_RANKING,
    SCREEN_RANKING_TRANSIT,
    SCREEN_UNKNOWN,
    SCROLL_PLAYER_TAPS,
    SWIPE_PARTY_REVIEW,
    SWIPE_RANKING_ONE_PLAYER,
    TAP_BACK_FROM_MATCH,
    TAP_BACK_TO_RANKING,
    TAP_MAIN_TO_TRANSIT,
    TAP_MATCH_ENTRY_X,
    TAP_PROFILE_PARTY_REVIEW,
    TAP_RANKING_STEP1,
    TAP_RANKING_STEP2,
    TAP_SWITCH_SOLO_RANK,
    TAP_TRANSIT_TO_RANKING,
    TOP3_PLAYER_TAPS,
    AdbClient,
    MatchEntry,
    OcrHelper,
    PartyReviewWaitResult,
    ProfilePartyReviewEntryWaitResult,
    RankingEntry,
    ScreenDetector,
    all_dates_before_target,
    build_next_rank_spec,
    compute_next_target_rank,
    extract_match_entries,
    extract_ranking_entries,
    extract_visible_match_dates,
    filter_new_match_entries,
    has_target_date_on_page,
    is_before_target_date,
    make_global_match_id,
    make_mumu_filename,
    match_date_part,
    normalize_match_duration,
    page_date_summary,
    should_exit_before_target_after_today_done,
)

logger = logging.getLogger(__name__)

ENTRY_EXTRACT_MODE = "ppq_ocr_required_fuzzy_no_ui_filter"


@dataclass
class CaptureConfig:
    adb_bin: str = DEFAULT_ADB_BIN
    device_serial: str | None = None
    connect_host: str = "127.0.0.1"
    connect_port: int = 16384
    auto_connect: bool = False
    target_date: str = ""  # MM-DD, default today
    output_dir: Path = field(default_factory=lambda: ROOT / "screenshots.0705")
    start_rank: int = 1
    end_rank: int = 100
    dry_run: bool = False
    verbose: bool = False
    tap_delay: float = 0.5
    swipe_delay: float = 0.5
    max_party_swipes: int = 30
    max_ranking_stall_rounds: int = 3
    log_path: Path | None = None
    profile_wait_timeout: float = 12.0
    party_review_wait_timeout: float = 12.0
    profile_party_review_entry_wait_timeout: float = 8.0
    profile_party_review_entry_stable_hits: int = 2
    screen_poll_interval: float = 0.6
    screen_stable_hits: int = 2
    max_no_new_today_swipes: int = 3
    max_no_date_swipes: int = 2
    debug_save_top_players: int = 0
    debug_save_top_matches: int = 0
    party_review_public_stable_hits: int = 1
    party_review_private_stable_hits: int = 2
    skip_players_path: Path | None = None
    resume: bool = False
    reset_state: bool = False
    state_path: Path | None = None

    def resolved_target_date(self) -> str:
        if self.target_date:
            return self.target_date
        return datetime.now().strftime("%m-%d")


@dataclass
class CaptureStats:
    players_attempted: int = 0
    players_skipped_private_profile: int = 0
    players_skipped_private_party: int = 0
    players_completed: int = 0
    players_skipped_duplicate_rank: int = 0
    players_skipped_manual: int = 0
    matches_saved: int = 0
    matches_skipped_duplicate: int = 0
    matches_skipped_duplicate_start_time: int = 0
    matches_skipped_global_duplicate: int = 0
    matches_skipped_old_date: int = 0
    current_rank: int | None = None
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class CaptureState:
    run_id: str
    target_date: str
    start_rank: int
    end_rank: int
    current_rank: int | None = None
    ranks: dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict) -> CaptureState:
        return cls(
            run_id=str(payload["run_id"]),
            target_date=str(payload["target_date"]),
            start_rank=int(payload["start_rank"]),
            end_rank=int(payload["end_rank"]),
            current_rank=payload.get("current_rank"),
            ranks=dict(payload.get("ranks", {})),
        )

    @classmethod
    def new(
        cls,
        *,
        run_id: str,
        target_date: str,
        start_rank: int,
        end_rank: int,
    ) -> CaptureState:
        return cls(
            run_id=run_id,
            target_date=target_date,
            start_rank=start_rank,
            end_rank=end_rank,
        )

    def rank_key(self, rank: int) -> str:
        return str(rank)

    def get_rank_record(self, rank: int) -> dict:
        return self.ranks.setdefault(
            self.rank_key(rank),
            {
                "status": "pending",
                "skip_reason": None,
                "match_ids": [],
                "saved_paths": [],
                "debug_paths": [],
                "failure_screenshot": None,
            },
        )

    def get_rank_status(self, rank: int) -> str:
        return str(self.get_rank_record(rank).get("status", "pending"))

    def set_rank_status(self, rank: int, status: str, **extra: object) -> None:
        record = self.get_rank_record(rank)
        record["status"] = status
        for key, value in extra.items():
            record[key] = value
        self.current_rank = rank if status == "running" else self.current_rank

    def completed_ranks(self) -> set[int]:
        return {
            int(rank_key)
            for rank_key, record in self.ranks.items()
            if record.get("status") == "completed"
        }

    def skipped_ranks(self) -> set[int]:
        return {
            int(rank_key)
            for rank_key, record in self.ranks.items()
            if record.get("status") == "skipped"
        }

    def preload_match_ids(self) -> set[str]:
        match_ids: set[str] = set()
        for record in self.ranks.values():
            for match_id in record.get("match_ids", []):
                match_ids.add(str(match_id))
        return match_ids


def make_run_id(when: datetime | None = None) -> str:
    dt = when or datetime.now()
    return dt.strftime("%Y%m%d-%H%M%S")


def load_manual_skip_ranks(path: Path | None) -> set[int]:
    if path is None:
        return set()
    if not path.exists():
        logger.warning("Skip players file not found: %s", path)
        return set()
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Skip players file must be a JSON list: {path}")
    return {int(rank) for rank in payload}


def default_state_path(output_dir: Path) -> Path:
    return output_dir / "capture_state.json"


class DailyCaptureBot:
    def __init__(self, config: CaptureConfig) -> None:
        self.config = config
        self.adb = AdbClient(
            adb_bin=config.adb_bin,
            device_serial=config.device_serial,
            tap_delay=config.tap_delay,
            swipe_delay=config.swipe_delay,
            verbose_commands=config.verbose,
        )
        self.ocr = OcrHelper()
        self.screen = ScreenDetector(self.ocr)
        self.stats = CaptureStats()
        self.events: list[dict] = []
        self._processed_entry_keys: set[str] = set()
        self._processed_match_ids: set[str] = set()
        self._processed_player_ranks: set[int] = set()
        self._debug_player_records: list[dict] = []
        self._debug_match_records: list[dict] = []
        self._manual_skip_ranks = load_manual_skip_ranks(config.skip_players_path)
        self._rank_fingerprints: dict[int, tuple[tuple[str, ...], frozenset[str]]] = {}
        self.run_id = ""
        self.run_dir = config.output_dir
        self.capture_state = CaptureState.new(
            run_id="",
            target_date=config.resolved_target_date(),
            start_rank=config.start_rank,
            end_rank=config.end_rank,
        )
        self._next_expected_rank = max(config.start_rank, 1)

    @property
    def state_path(self) -> Path:
        return self.config.state_path or default_state_path(self.config.output_dir)

    @property
    def debug_players_dir(self) -> Path:
        return self.run_dir / "debug_players"

    @property
    def debug_matches_dir(self) -> Path:
        return self.run_dir / "debug_matches"

    @property
    def failures_dir(self) -> Path:
        return self.config.output_dir / "failures"

    def should_debug_save_player(self, rank: int) -> bool:
        return (
            self.config.debug_save_top_players > 0
            and rank <= self.config.debug_save_top_players
        )

    def should_debug_save_match(self, rank: int) -> bool:
        return (
            self.config.debug_save_top_matches > 0
            and rank <= self.config.debug_save_top_matches
        )

    @staticmethod
    def make_debug_match_filename(
        rank: int,
        match_datetime: str,
        duration: str | None,
    ) -> str:
        parts = match_datetime.split(maxsplit=1)
        date_part = parts[0]
        time_part = parts[1].replace(":", "") if len(parts) > 1 else "0000"
        dur = (duration or "unknown").replace(":", "")
        return f"rank_{rank:02d}_{date_part}_{time_part}_{dur}.png"

    def save_debug_match_screenshot(
        self,
        rank: int,
        match_datetime: str,
        duration: str | None,
        match_id: str,
        *,
        use_cached: bool = True,
    ) -> Path | None:
        if not self.should_debug_save_match(rank):
            return None
        out_dir = self.debug_matches_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / self.make_debug_match_filename(rank, match_datetime, duration)
        if use_cached and self.adb.has_cached_png():
            self.adb.save_cached_png(path)
        else:
            self.adb.save_png(path)
        self._debug_match_records.append(
            {
                "run_id": self.run_id,
                "source_rank": rank,
                "rank": rank,
                "match_id": match_id,
                "datetime": match_datetime,
                "duration": duration,
                "path": str(path),
                "duplicate_skipped": False,
            }
        )
        return path

    def return_from_match(self, rank: int, match_datetime: str) -> None:
        self.log_event(
            "match_return_start",
            rank=rank,
            datetime=match_datetime,
        )
        self.adb.tap(*TAP_BACK_FROM_MATCH)
        time.sleep(self.config.tap_delay)
        self.log_event(
            "match_return_end",
            rank=rank,
            datetime=match_datetime,
        )

    def record_debug_match_duplicate(
        self,
        rank: int,
        match_datetime: str,
        duration: str | None,
        match_id: str,
    ) -> None:
        if not self.should_debug_save_match(rank):
            return
        self._debug_match_records.append(
            {
                "run_id": self.run_id,
                "source_rank": rank,
                "rank": rank,
                "match_id": match_id,
                "datetime": match_datetime,
                "duration": duration,
                "path": None,
                "duplicate_skipped": True,
            }
        )

    def save_debug_screenshot(self, rank: int, stage: str, img=None) -> Path | None:
        if not self.should_debug_save_player(rank):
            return None
        out_dir = self.debug_players_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"rank_{rank:02d}_{stage}.png"
        if img is not None:
            ok, buf = cv2.imencode(".png", img)
            if ok:
                path.write_bytes(buf.tobytes())
        else:
            self.adb.save_png(path)
        return path

    @staticmethod
    def sanitize_failure_reason(reason: str) -> str:
        return "".join(
            ch if ch.isalnum() or ch in {"-", "_"} else "_"
            for ch in reason.strip().lower()
        ) or "unknown"

    def save_failure_screenshot(self, rank: int, reason: str, img=None) -> Path | None:
        out_dir = self.failures_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_reason = self.sanitize_failure_reason(reason)
        path = out_dir / f"rank_{rank:02d}_{safe_reason}.png"
        try:
            if img is not None:
                ok, buf = cv2.imencode(".png", img)
                if ok:
                    path.write_bytes(buf.tobytes())
                    return path
            self.adb.save_png(path)
            return path
        except Exception as exc:
            self.log_event(
                "rank_failure_screenshot_error",
                rank=rank,
                reason=reason,
                error=str(exc),
            )
            return None

    def log_event(self, event: str, **payload) -> None:
        record = {
            "ts": datetime.now().isoformat(timespec="seconds"),
            "event": event,
            "run_id": self.run_id,
            **payload,
        }
        self.events.append(record)
        logger.info("%s %s", event, payload)

    def emit_progress(self, rank: int, *, status: str) -> None:
        self.stats.current_rank = rank
        line = (
            f"rank {rank}/{self.config.end_rank} "
            f"saved={self.stats.matches_saved} status={status}"
        )
        logger.info(line)
        self.log_event(
            "progress",
            rank=rank,
            end_rank=self.config.end_rank,
            saved=self.stats.matches_saved,
            status=status,
        )

    def save_state(self) -> None:
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(self.capture_state.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def init_capture_state(self) -> None:
        state_path = self.state_path
        if (
            self.config.resume
            and not self.config.reset_state
            and state_path.exists()
        ):
            self.capture_state = CaptureState.from_dict(
                json.loads(state_path.read_text(encoding="utf-8"))
            )
            self.run_id = self.capture_state.run_id
            self.log_event("state_resume", state_path=str(state_path))
        else:
            self.run_id = make_run_id()
            self.capture_state = CaptureState.new(
                run_id=self.run_id,
                target_date=self.config.resolved_target_date(),
                start_rank=self.config.start_rank,
                end_rank=self.config.end_rank,
            )
            self.log_event("state_new", state_path=str(state_path))
        self.run_dir = self.config.output_dir / "runs" / self.run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.preload_from_state()
        self.save_state()

    def preload_from_state(self) -> None:
        for rank_str, record in self.capture_state.ranks.items():
            rank = int(rank_str)
            status = str(record.get("status", "pending"))
            if status in {"completed", "skipped"}:
                self._processed_player_ranks.add(rank)
        self._processed_match_ids.update(self.capture_state.preload_match_ids())
        next_rank = compute_next_target_rank(
            self.config.start_rank,
            self.config.end_rank,
            completed_ranks=self.capture_state.completed_ranks(),
            skipped_ranks=self.capture_state.skipped_ranks(),
            manual_skip_ranks=self._manual_skip_ranks,
        )
        if next_rank is not None:
            self._next_expected_rank = next_rank

    def skip_rank_manual(self, rank: int) -> None:
        self.capture_state.set_rank_status(
            rank,
            "skipped",
            skip_reason="manual_skip",
        )
        self.mark_player_processed(rank)
        self.stats.players_skipped_manual += 1
        self.log_event("player_skip", rank=rank, reason="manual_skip")
        self.emit_progress(rank, status="skipped")
        self.save_state()
        self._next_expected_rank = rank + 1

    def mark_rank_completed(
        self,
        rank: int,
        *,
        skip_reason: str | None = None,
        failed: bool = False,
        **extra: object,
    ) -> None:
        if failed:
            status = "failed"
        elif skip_reason:
            status = "skipped"
        else:
            status = "completed"
        state_extra: dict[str, object] = dict(extra)
        if skip_reason:
            state_extra["skip_reason"] = skip_reason
        self.capture_state.set_rank_status(rank, status, **state_extra)
        self.capture_state.current_rank = None
        self.save_state()

    def record_rank_failure(self, rank: int, reason: str, *, img=None) -> None:
        screenshot_path = self.save_failure_screenshot(rank, reason, img=img)
        extra: dict[str, object] = {}
        if screenshot_path is not None:
            extra["failure_screenshot"] = str(screenshot_path)
            self.log_event(
                "rank_failure_screenshot",
                rank=rank,
                reason=reason,
                path=str(screenshot_path),
            )
        self.mark_rank_completed(rank, failed=True, **extra)

    def record_rank_match_saved(
        self,
        rank: int,
        match_id: str,
        *,
        saved_path: str | None = None,
        debug_path: str | None = None,
    ) -> None:
        record = self.capture_state.get_rank_record(rank)
        if match_id not in record["match_ids"]:
            record["match_ids"].append(match_id)
        if saved_path and saved_path not in record["saved_paths"]:
            record["saved_paths"].append(saved_path)
        if debug_path and debug_path not in record["debug_paths"]:
            record["debug_paths"].append(debug_path)
        self.save_state()

    def check_possible_rank_mismatch(
        self,
        rank: int,
        visible_dates: list[str],
        match_ids: set[str],
    ) -> None:
        date_fp = tuple(visible_dates)
        for other_rank, (other_dates, other_match_ids) in self._rank_fingerprints.items():
            if other_rank == rank:
                continue
            if date_fp and date_fp == other_dates:
                self.log_event(
                    "possible_rank_mismatch",
                    rank=rank,
                    other_rank=other_rank,
                    reason="same_visible_dates",
                    visible_dates=list(date_fp),
                )
            overlap = match_ids & other_match_ids
            if overlap:
                self.log_event(
                    "possible_rank_mismatch",
                    rank=rank,
                    other_rank=other_rank,
                    reason="shared_match_ids",
                    shared_match_ids=sorted(overlap),
                )
        existing_dates, existing_match_ids = self._rank_fingerprints.get(rank, ((), frozenset()))
        merged_dates = date_fp or existing_dates
        merged_match_ids = existing_match_ids | frozenset(match_ids)
        self._rank_fingerprints[rank] = (merged_dates, merged_match_ids)

    def run(self) -> CaptureStats:
        if self.config.auto_connect:
            self.adb.connect(self.config.connect_host, self.config.connect_port)
        self.adb.check_device(self.config.device_serial)
        (wm_w, wm_h), (shot_w, shot_h) = self.adb.validate_resolution()
        self.config.output_dir.mkdir(parents=True, exist_ok=True)
        self.init_capture_state()
        self.log_event(
            "start",
            target_date=self.config.resolved_target_date(),
            output=str(self.config.output_dir),
            run_id=self.run_id,
            run_dir=str(self.run_dir),
            dry_run=self.config.dry_run,
            wm_size=f"{wm_w}x{wm_h}",
            screenshot_size=f"{shot_w}x{shot_h}",
            resume=self.config.resume,
            manual_skip_ranks=sorted(self._manual_skip_ranks),
        )

        self.navigate_to_ppq_ranking()
        self.process_top3()
        self.process_scroll_players()
        self.write_debug_players_file()
        self.write_debug_matches_file()
        self.write_log_file()
        self.log_event("finish", stats=self.stats.to_dict())
        logger.info("Done: %s", self.stats.to_dict())
        return self.stats

    def write_log_file(self) -> None:
        log_path = self.config.log_path or (
            self.config.output_dir / "capture_log.json"
        )
        payload = {
            "run_id": self.run_id,
            "run_dir": str(self.run_dir),
            "config": {
                **asdict(self.config),
                "output_dir": str(self.config.output_dir),
                "log_path": str(self.config.log_path) if self.config.log_path else None,
                "skip_players_path": (
                    str(self.config.skip_players_path)
                    if self.config.skip_players_path
                    else None
                ),
                "state_path": str(self.state_path),
            },
            "stats": self.stats.to_dict(),
            "events": self.events,
        }
        log_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        latest_path = self.config.output_dir / "latest_capture_log.json"
        latest_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        run_log_path = self.run_dir / "capture_log.json"
        run_log_path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Wrote log: %s", log_path)

    def write_debug_players_file(self) -> None:
        if not self._debug_player_records:
            return
        out_path = self.debug_players_dir / "top_players_debug.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(self._debug_player_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Wrote debug players: %s", out_path)

    def write_debug_matches_file(self) -> None:
        if not self._debug_match_records:
            return
        out_path = self.debug_matches_dir / "top_matches_debug.json"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(self._debug_match_records, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Wrote debug matches: %s", out_path)

    def navigate_to_ppq_ranking(self) -> None:
        screen = self.screen.detect(self.adb.capture_bgr())
        if screen == SCREEN_RANKING:
            self.log_event("navigation_skip", reason="already_on_ranking")
            return

        if screen == SCREEN_MAIN:
            self.adb.tap(*TAP_MAIN_TO_TRANSIT)
        elif screen != SCREEN_RANKING_TRANSIT:
            self.log_event("navigation_warning", current_screen=screen)
            self.adb.tap(*TAP_MAIN_TO_TRANSIT)

        self.screen.wait_until(self.adb, {SCREEN_RANKING_TRANSIT}, timeout=6)
        self.adb.tap(*TAP_TRANSIT_TO_RANKING)
        self.screen.wait_until(self.adb, {SCREEN_RANKING}, timeout=8)
        self.adb.tap(*TAP_RANKING_STEP1)
        time.sleep(0.8)
        self.adb.tap(*TAP_RANKING_STEP2)
        self.screen.wait_until(self.adb, {SCREEN_RANKING}, timeout=8)
        self.log_event("navigation_complete")

    def process_top3(self) -> None:
        for index, (x, y) in enumerate(TOP3_PLAYER_TAPS, start=1):
            rank = index
            if rank < self.config.start_rank:
                continue
            if rank > self.config.end_rank:
                return
            if rank in self._manual_skip_ranks:
                self.skip_rank_manual(rank)
                continue
            if rank in self._processed_player_ranks:
                continue
            self.process_player(rank, index, x, y)

    def process_scroll_players(self) -> None:
        if self._next_expected_rank <= 3:
            self._next_expected_rank = max(self._next_expected_rank, 4)
        stall_rounds = 0
        last_visible_max_rank: int | None = None
        tap_x = SCROLL_PLAYER_TAPS[0][0]
        while self._next_expected_rank <= self.config.end_rank:
            if self._next_expected_rank in self._manual_skip_ranks:
                self.skip_rank_manual(self._next_expected_rank)
                continue
            if self._next_expected_rank in self._processed_player_ranks:
                self._next_expected_rank += 1
                continue

            ranking_entries = self.read_visible_ranking_entries()
            if ranking_entries:
                self.log_event(
                    "visible_ranks",
                    ranks=[entry.rank for entry in ranking_entries],
                    tap_ys=[entry.tap_y for entry in ranking_entries],
                    raw_texts=[entry.raw_text for entry in ranking_entries],
                    next_target_rank=self._next_expected_rank,
                )
            player_specs, skipped_ranks, wait_reason = self.build_scroll_player_specs(
                ranking_entries,
                tap_x=tap_x,
            )
            for rank, reason in skipped_ranks:
                self.stats.players_skipped_duplicate_rank += 1
                self.log_event("ranking_entry_skip", rank=rank, reason=reason)
            if wait_reason:
                visible_ranks = [entry.rank for entry in ranking_entries]
                visible_max_rank = max(visible_ranks) if visible_ranks else None
                self.log_event(
                    wait_reason,
                    next_target_rank=self._next_expected_rank,
                    visible_ranks=visible_ranks,
                    visible_max_rank=visible_max_rank,
                    target_rank=self._next_expected_rank,
                )
                if wait_reason == "ranking_target_ahead":
                    if visible_max_rank is not None and (
                        last_visible_max_rank is None
                        or visible_max_rank > last_visible_max_rank
                    ):
                        stall_rounds = 0
                        last_visible_max_rank = visible_max_rank
                    else:
                        stall_rounds += 1
                else:
                    stall_rounds += 1
                if stall_rounds >= self.config.max_ranking_stall_rounds:
                    self.log_event(
                        "ranking_stall_stop",
                        last_rank=self._next_expected_rank,
                        wait_reason=wait_reason,
                    )
                    break
                self.swipe_ranking()
                continue

            if player_specs:
                stall_rounds = 0
                last_visible_max_rank = None
                for rank, player_index, x, y in player_specs:
                    if rank > self.config.end_rank:
                        return
                    self.process_player(rank, player_index, x, y)
            else:
                stall_rounds += 1
                if stall_rounds >= self.config.max_ranking_stall_rounds:
                    self.log_event("ranking_stall_stop", last_rank=self._next_expected_rank)
                    break

            if self._next_expected_rank > self.config.end_rank:
                break
            self.swipe_ranking()

    def build_scroll_player_specs(
        self,
        ranking_entries: list[RankingEntry],
        *,
        tap_x: int,
    ) -> tuple[list[tuple[int, int, int, int]], list[tuple[int, str]], str | None]:
        if not ranking_entries:
            self.log_event(
                "ranking_ocr_fallback",
                next_expected_rank=self._next_expected_rank,
            )
            return [], [], "ranking_missing_expected_rank"
        return build_next_rank_spec(
            ranking_entries,
            next_target_rank=self._next_expected_rank,
            end_rank=self.config.end_rank,
            processed_ranks=self._processed_player_ranks,
            tap_x=tap_x,
        )

    def read_visible_ranking_entries(self) -> list[RankingEntry]:
        img = self.adb.capture_bgr()
        details = self.ocr.ocr_details(img, RANKING_VISIBLE_RANKS_BOX)
        entries = extract_ranking_entries(details, RANKING_VISIBLE_RANKS_BOX)
        if entries and self.config.verbose:
            logger.debug(
                "rank OCR entries -> %s",
                [(entry.rank, entry.tap_y, entry.raw_text) for entry in entries],
            )
        return entries

    def read_visible_ranks(self) -> list[int]:
        return [entry.rank for entry in self.read_visible_ranking_entries()]

    def swipe_ranking(self) -> None:
        x1, y1, x2, y2, duration = SWIPE_RANKING_ONE_PLAYER
        self.adb.swipe(x1, y1, x2, y2, duration_ms=int(duration))
        self.log_event("swipe_ranking", swipe=SWIPE_RANKING_ONE_PLAYER)

    def mark_player_processed(self, rank: int) -> None:
        self._processed_player_ranks.add(rank)

    def open_party_review(
        self,
        rank: int,
    ) -> tuple[ProfilePartyReviewEntryWaitResult, PartyReviewWaitResult]:
        party_wait: PartyReviewWaitResult | None = None
        entry_wait: ProfilePartyReviewEntryWaitResult | None = None
        max_party_review_tap_attempts = 2
        for tap_attempt in range(max_party_review_tap_attempts):
            if tap_attempt > 0:
                self.log_event(
                    "party_review_tap_retry",
                    rank=rank,
                    attempt=tap_attempt + 1,
                )
            entry_wait = self.screen.wait_for_profile_party_review_entry(
                self.adb,
                timeout=self.config.profile_party_review_entry_wait_timeout,
                poll=self.config.screen_poll_interval,
                stable_hits=self.config.profile_party_review_entry_stable_hits,
                verbose=self.config.verbose,
            )
            self.log_event(
                "profile_party_review_entry_wait",
                rank=rank,
                attempt=tap_attempt + 1,
                ready=entry_wait.ready,
                stable=entry_wait.stable,
                on_profile=entry_wait.on_profile,
                elapsed_ms=entry_wait.elapsed_ms,
                polls=entry_wait.polls,
            )
            self.adb.tap(*TAP_PROFILE_PARTY_REVIEW, delay=0)
            party_wait = self.screen.wait_for_party_review(
                self.adb,
                timeout=self.config.party_review_wait_timeout,
                poll=self.config.screen_poll_interval,
                public_stable_hits=self.config.party_review_public_stable_hits,
                private_stable_hits=self.config.party_review_private_stable_hits,
                verbose=self.config.verbose,
            )
            if party_wait.state in {"private", "public"} and party_wait.stable:
                break
            if tap_attempt >= max_party_review_tap_attempts - 1:
                break
            still_on_profile = (
                party_wait.img is not None
                and self.screen.detect(party_wait.img) == SCREEN_PROFILE
            )
            if not still_on_profile:
                break
            if party_wait.state != "timeout" and party_wait.stable:
                break

        assert entry_wait is not None
        assert party_wait is not None
        return entry_wait, party_wait

    def process_player(self, rank: int, player_index: int, x: int, y: int) -> None:
        if rank < self._next_expected_rank:
            return
        if rank in self._processed_player_ranks:
            self.stats.players_skipped_duplicate_rank += 1
            self.log_event(
                "player_skip",
                rank=rank,
                reason="duplicate_rank_visible_after_swipe",
            )
            return
        self.stats.players_attempted += 1
        self.capture_state.set_rank_status(rank, "running")
        self.save_state()
        self.log_event("player_start", rank=rank, player_index=player_index, tap=(x, y))
        self.emit_progress(rank, status="running")

        ranking_entry_path = None
        if self.should_debug_save_player(rank):
            ranking_entry_path = self.save_debug_screenshot(
                rank,
                "ranking_entry",
                self.adb.capture_bgr(),
            )
        self.adb.tap(x, y, delay=0)
        profile_wait = self.screen.wait_for_screen(
            self.adb,
            {SCREEN_PROFILE, SCREEN_RANKING},
            timeout=self.config.profile_wait_timeout,
            poll=self.config.screen_poll_interval,
            stable_hits=self.config.screen_stable_hits,
            verbose=self.config.verbose,
        )
        after_tap_path = self.save_debug_screenshot(
            rank,
            "after_player_tap",
            profile_wait.img,
        )
        self.log_event(
            "player_wait_result",
            rank=rank,
            screen=profile_wait.screen,
            elapsed_ms=profile_wait.elapsed_ms,
            polls=profile_wait.polls,
            stable=profile_wait.stable,
        )

        debug_record: dict = {
            "run_id": self.run_id,
            "rank": rank,
            "player_index": player_index,
            "tap": [x, y],
            "player_wait": {
                "screen": profile_wait.screen,
                "elapsed_ms": profile_wait.elapsed_ms,
                "polls": profile_wait.polls,
                "stable": profile_wait.stable,
            },
            "screenshots": {
                "ranking_entry": str(ranking_entry_path) if ranking_entry_path else None,
                "after_player_tap": str(after_tap_path) if after_tap_path else None,
                "party_review_wait": None,
            },
        }

        if profile_wait.screen == SCREEN_RANKING and profile_wait.stable:
            self.mark_player_processed(rank)
            self.stats.players_skipped_private_profile += 1
            self.log_event("player_skip", rank=rank, reason="private_profile")
            self.mark_rank_completed(rank, skip_reason="private_profile")
            self.emit_progress(rank, status="skipped")
            if self.should_debug_save_player(rank):
                self._debug_player_records.append(debug_record)
            self._next_expected_rank = max(self._next_expected_rank, rank + 1)
            return
        if profile_wait.screen != SCREEN_PROFILE or not profile_wait.stable:
            should_mark_processed = (
                profile_wait.stable
                and profile_wait.screen != SCREEN_UNKNOWN
            )
            if should_mark_processed:
                self.mark_player_processed(rank)
            self.stats.errors.append(
                f"rank {rank}: expected profile, got {profile_wait.screen}"
            )
            self.log_event(
                "player_error",
                rank=rank,
                reason="unexpected_screen",
                screen=profile_wait.screen,
                stable=profile_wait.stable,
                marked_processed=should_mark_processed,
            )
            if self.should_debug_save_player(rank):
                self._debug_player_records.append(debug_record)
            self.record_rank_failure(
                rank,
                "unexpected_screen",
                img=profile_wait.img,
            )
            self.emit_progress(rank, status="failed")
            self.recover_to_ranking()
            self._next_expected_rank = max(self._next_expected_rank, rank + 1)
            return

        self.mark_player_processed(rank)
        entry_wait, party_wait = self.open_party_review(rank)
        party_review_path = self.save_debug_screenshot(
            rank,
            "party_review_wait",
            party_wait.img,
        )
        debug_record["profile_party_review_entry_wait"] = {
            "ready": entry_wait.ready,
            "elapsed_ms": entry_wait.elapsed_ms,
            "polls": entry_wait.polls,
            "stable": entry_wait.stable,
            "on_profile": entry_wait.on_profile,
        }
        debug_record["party_review_wait"] = {
            "state": party_wait.state,
            "elapsed_ms": party_wait.elapsed_ms,
            "polls": party_wait.polls,
            "stable": party_wait.stable,
            "stable_hits_used": party_wait.stable_hits_used,
        }
        debug_record["screenshots"]["party_review_wait"] = (
            str(party_review_path) if party_review_path else None
        )
        self.log_event(
            "party_review_wait_result",
            rank=rank,
            state=party_wait.state,
            elapsed_ms=party_wait.elapsed_ms,
            polls=party_wait.polls,
            stable=party_wait.stable,
            stable_hits_used=party_wait.stable_hits_used,
        )

        if party_wait.state == "private" and party_wait.stable:
            self.stats.players_skipped_private_party += 1
            self.log_event("player_skip", rank=rank, reason="private_party_review")
            self.mark_rank_completed(rank, skip_reason="private_party_review")
            self.emit_progress(rank, status="skipped")
            if self.should_debug_save_player(rank):
                self._debug_player_records.append(debug_record)
            self.back_to_ranking()
            self._next_expected_rank = max(self._next_expected_rank, rank + 1)
            return
        if party_wait.state == "timeout" or not party_wait.stable:
            self.stats.errors.append(f"rank {rank}: party review load timeout")
            self.log_event("player_skip", rank=rank, reason="party_review_timeout")
            self.record_rank_failure(
                rank,
                "party_review_timeout",
                img=party_wait.img,
            )
            self.emit_progress(rank, status="failed")
            if self.should_debug_save_player(rank):
                self._debug_player_records.append(debug_record)
            self.back_to_ranking()
            self._next_expected_rank = max(self._next_expected_rank, rank + 1)
            return

        if self.should_debug_save_player(rank):
            self._debug_player_records.append(debug_record)

        self.process_player_matches(rank, player_index)
        self.back_to_ranking()
        self.stats.players_completed += 1
        self.mark_rank_completed(rank)
        self._next_expected_rank = max(self._next_expected_rank, rank + 1)
        self.log_event("player_done", rank=rank)
        self.emit_progress(rank, status="completed")

    def process_player_matches(self, rank: int, player_index: int) -> None:
        seen_this_player: set[str] = set()
        seen_start_times_this_player: set[str] = set()
        no_new_today_swipes = 0
        no_date_swipes = 0
        target_date = self.config.resolved_target_date()

        for swipe_index in range(self.config.max_party_swipes):
            img = self.adb.capture_bgr()
            details = self.ocr.ocr_details(img, PARTY_REVIEW_LIST_BOX)
            date_summary = page_date_summary(details, target_date)
            visible_dates = date_summary.visible_dates
            entries = extract_match_entries(
                details,
                PARTY_REVIEW_LIST_BOX,
                rank=rank,
                player_index=player_index,
            )
            ocr_texts = [
                str(item.get("text", "")).strip()
                for item in details
                if str(item.get("text", "")).strip()
            ]
            for entry in entries:
                if (
                    entry.normalized_datetime in seen_start_times_this_player
                    and match_date_part(entry.normalized_datetime) == target_date
                ):
                    self.stats.matches_skipped_duplicate_start_time += 1
                    self.log_event(
                        "match_skip",
                        rank=rank,
                        reason="duplicate_start_time_this_player",
                        datetime=entry.normalized_datetime,
                    )
            new_entries = filter_new_match_entries(
                entries,
                self._processed_entry_keys,
                seen_this_player,
                seen_start_times_this_player,
            )
            today_entries = [
                entry
                for entry in new_entries
                if match_date_part(entry.normalized_datetime) == target_date
            ]
            page_today_entries = [
                entry
                for entry in entries
                if match_date_part(entry.normalized_datetime) == target_date
            ]
            remaining_today_entry_count = sum(
                1
                for entry in page_today_entries
                if entry.dedup_key not in seen_this_player
                and entry.dedup_key not in self._processed_entry_keys
                and entry.normalized_datetime not in seen_start_times_this_player
            )

            self.log_event(
                "party_review_page_scan",
                rank=rank,
                visible_dates=visible_dates,
                today_dates=date_summary.today_dates,
                before_target_dates=date_summary.before_target_dates,
                today_count=len(date_summary.today_dates),
                ocr_texts=ocr_texts,
                entry_count=len(entries),
                page_today_entry_count=len(page_today_entries),
                entry_datetimes=[entry.normalized_datetime for entry in entries],
                entry_extract_mode=ENTRY_EXTRACT_MODE,
                new_entry_count=len(new_entries),
                new_today_entry_count=len(today_entries),
                processed_today_count=len(page_today_entries) - remaining_today_entry_count,
                remaining_today_entry_count=remaining_today_entry_count,
            )
            if swipe_index == 0:
                self.check_possible_rank_mismatch(rank, visible_dates, set())

            if visible_dates and all_dates_before_target(details, target_date):
                self.log_event(
                    "party_review_stop",
                    rank=rank,
                    reason="all_dates_before_target",
                )
                break

            if not visible_dates:
                no_date_swipes += 1
                if no_date_swipes >= self.config.max_no_date_swipes:
                    self.log_event(
                        "party_review_stop",
                        rank=rank,
                        reason="no_dates_after_swipes",
                    )
                    break
            else:
                no_date_swipes = 0

            processed_any_today = False
            stop_old = False
            for entry in new_entries:
                entry_date = match_date_part(entry.normalized_datetime)
                if entry_date != target_date:
                    if is_before_target_date(entry_date, target_date):
                        self.stats.matches_skipped_old_date += 1
                        self.log_event(
                            "match_skip",
                            rank=rank,
                            reason="old_date",
                            datetime=entry.normalized_datetime,
                        )
                        stop_old = True
                    continue

                seen_this_player.add(entry.dedup_key)
                seen_start_times_this_player.add(entry.normalized_datetime)
                if entry.dedup_key in self._processed_entry_keys:
                    self.stats.matches_skipped_duplicate += 1
                    continue

                saved = self.capture_match_screenshot(
                    rank,
                    entry,
                )
                self._processed_entry_keys.add(entry.dedup_key)
                if saved == "saved":
                    self.stats.matches_saved += 1
                    processed_any_today = True
                    self.emit_progress(rank, status="match_saved")
                elif saved == "duplicate":
                    processed_any_today = True
                elif saved == "skipped":
                    self.stats.matches_skipped_duplicate += 1

            if should_exit_before_target_after_today_done(
                date_summary,
                entries,
                target_date,
                seen_this_player,
                self._processed_entry_keys,
                seen_start_times_this_player,
            ):
                self.log_event(
                    "party_review_stop",
                    rank=rank,
                    reason="before_target_after_today_done",
                )
                break

            if stop_old:
                self.log_event("party_review_stop", rank=rank, reason="reached_old_date")
                break

            if has_target_date_on_page(details, target_date):
                if processed_any_today:
                    no_new_today_swipes = 0
                elif date_summary.today_dates and not page_today_entries:
                    no_new_today_swipes = 0
                elif not today_entries:
                    no_new_today_swipes += 1
                    if no_new_today_swipes >= self.config.max_no_new_today_swipes:
                        self.log_event(
                            "party_review_stop",
                            rank=rank,
                            reason="no_new_today_after_swipes",
                        )
                        break
                else:
                    no_new_today_swipes = 0
            elif not new_entries:
                no_new_today_swipes += 1
                if no_new_today_swipes >= self.config.max_no_new_today_swipes:
                    self.log_event(
                        "party_review_stop",
                        rank=rank,
                        reason="no_new_today_after_swipes",
                    )
                    break

            self.swipe_party_review()

    def swipe_party_review(self) -> None:
        x1, y1, x2, y2, duration = SWIPE_PARTY_REVIEW
        self.adb.swipe(x1, y1, x2, y2, duration_ms=int(duration))
        self.log_event("swipe_party_review")

    def capture_match_screenshot(self, rank: int, entry: MatchEntry) -> str:
        """Return ``saved``, ``duplicate``, or ``skipped``."""
        match_datetime = entry.normalized_datetime
        tap_y = entry.tap_y
        try:
            self.log_event(
                "match_open_start",
                rank=rank,
                datetime=match_datetime,
                tap_y=tap_y,
            )
            self.adb.tap(TAP_MATCH_ENTRY_X, tap_y)
            screen = self.screen.wait_until(
                self.adb,
                {SCREEN_MATCH_TEAM_RANK, SCREEN_MATCH_SOLO_RANK},
                timeout=8,
            )
            self.log_event(
                "match_screen_wait_result",
                rank=rank,
                datetime=match_datetime,
                screen=screen,
            )
            if screen not in {SCREEN_MATCH_TEAM_RANK, SCREEN_MATCH_SOLO_RANK}:
                self.stats.errors.append(
                    f"rank {rank}: failed to open match screen for {match_datetime}"
                )
                self.log_event(
                    "match_error",
                    rank=rank,
                    reason="open_failed",
                    datetime=match_datetime,
                    screen=screen,
                )
                return "skipped"

            if screen == SCREEN_MATCH_TEAM_RANK:
                self.adb.tap(*TAP_SWITCH_SOLO_RANK)
                screen = self.screen.wait_until(
                    self.adb,
                    {SCREEN_MATCH_SOLO_RANK},
                    timeout=6,
                )
                self.log_event(
                    "match_screen_wait_result",
                    rank=rank,
                    datetime=match_datetime,
                    screen=screen,
                    phase="solo_switch",
                )
            if screen != SCREEN_MATCH_SOLO_RANK:
                self.stats.errors.append(
                    f"rank {rank}: failed to switch solo rank for {match_datetime}"
                )
                self.log_event(
                    "match_error",
                    rank=rank,
                    reason="solo_switch_failed",
                    datetime=match_datetime,
                    screen=screen,
                )
                self.return_from_match(rank, match_datetime)
                return "skipped"

            self.log_event(
                "match_duration_ocr_start",
                rank=rank,
                datetime=match_datetime,
            )
            img = self.adb.capture_bgr()
            duration_raw = self.ocr.ocr_text(img, MATCH_DURATION_BOX)
            duration = normalize_match_duration(duration_raw)
            self.log_event(
                "match_duration_ocr_end",
                rank=rank,
                datetime=match_datetime,
                duration=duration,
                raw_text=duration_raw,
            )
            match_id: str | None = None
            if duration:
                match_id = make_global_match_id(match_datetime, duration)
                self.log_event(
                    "match_duration_ocr",
                    rank=rank,
                    datetime=match_datetime,
                    duration=duration,
                    raw_text=duration_raw,
                    match_id=match_id,
                )
                if match_id in self._processed_match_ids:
                    self.stats.matches_skipped_global_duplicate += 1
                    self.record_debug_match_duplicate(
                        rank, match_datetime, duration, match_id
                    )
                    self.log_event(
                        "match_skip",
                        rank=rank,
                        reason="duplicate_match_id",
                        datetime=match_datetime,
                        duration=duration,
                        match_id=match_id,
                    )
                    self.return_from_match(rank, match_datetime)
                    return "duplicate"
            else:
                fallback_id = entry.dedup_key
                self.log_event(
                    "duration_ocr_failed",
                    rank=rank,
                    datetime=match_datetime,
                    raw_text=duration_raw,
                    fallback_key=fallback_id,
                )
                if fallback_id in self._processed_match_ids:
                    self.stats.matches_skipped_global_duplicate += 1
                    self.record_debug_match_duplicate(
                        rank, match_datetime, None, fallback_id
                    )
                    self.log_event(
                        "match_skip",
                        rank=rank,
                        reason="duplicate_match_id",
                        datetime=match_datetime,
                        match_id=fallback_id,
                    )
                    self.return_from_match(rank, match_datetime)
                    return "duplicate"
                match_id = fallback_id

            self.log_event(
                "debug_match_save_start",
                rank=rank,
                datetime=match_datetime,
                match_id=match_id,
            )
            debug_path = self.save_debug_match_screenshot(
                rank, match_datetime, duration, match_id, use_cached=True
            )
            self.log_event(
                "debug_match_save_end",
                rank=rank,
                datetime=match_datetime,
                match_id=match_id,
                debug_path=str(debug_path) if debug_path else None,
                used_cached=self.adb.has_cached_png(),
            )

            if self.config.dry_run:
                self.log_event(
                    "match_dry_run",
                    rank=rank,
                    datetime=match_datetime,
                    tap_y=tap_y,
                    duration=duration,
                    match_id=match_id,
                    debug_path=str(debug_path) if debug_path else None,
                )
                self._processed_match_ids.add(match_id)
                self.record_rank_match_saved(
                    rank,
                    match_id,
                    debug_path=str(debug_path) if debug_path else None,
                )
                self.check_possible_rank_mismatch(rank, [], {match_id})
                self.return_from_match(rank, match_datetime)
                return "saved"

            filename = make_mumu_filename()
            out_path = self.config.output_dir / filename
            self.log_event(
                "match_save_start",
                rank=rank,
                datetime=match_datetime,
                match_id=match_id,
                path=str(out_path),
            )
            if self.adb.has_cached_png():
                self.adb.save_cached_png(out_path)
            else:
                self.adb.save_png(out_path)
            self._processed_match_ids.add(match_id)
            self.record_rank_match_saved(
                rank,
                match_id,
                saved_path=str(out_path),
                debug_path=str(debug_path) if debug_path else None,
            )
            self.check_possible_rank_mismatch(rank, [], {match_id})
            self.log_event(
                "match_saved",
                rank=rank,
                datetime=match_datetime,
                duration=duration,
                match_id=match_id,
                path=str(out_path),
            )
            self.return_from_match(rank, match_datetime)
            return "saved"
        except subprocess.TimeoutExpired as exc:
            self.stats.errors.append(
                f"rank {rank}: adb timeout while processing {match_datetime}: {exc}"
            )
            self.log_event(
                "match_error",
                rank=rank,
                reason="adb_timeout",
                datetime=match_datetime,
                error=str(exc),
            )
            try:
                self.return_from_match(rank, match_datetime)
            except Exception as recover_exc:
                self.log_event(
                    "match_error",
                    rank=rank,
                    reason="return_after_timeout_failed",
                    datetime=match_datetime,
                    error=str(recover_exc),
                )
            return "skipped"
        except Exception as exc:
            self.stats.errors.append(
                f"rank {rank}: unexpected error while processing {match_datetime}: {exc}"
            )
            self.log_event(
                "match_error",
                rank=rank,
                reason="unexpected",
                datetime=match_datetime,
                error=str(exc),
            )
            try:
                self.return_from_match(rank, match_datetime)
            except Exception as recover_exc:
                self.log_event(
                    "match_error",
                    rank=rank,
                    reason="return_after_error_failed",
                    datetime=match_datetime,
                    error=str(recover_exc),
                )
            return "skipped"

    def back_to_ranking(self) -> None:
        for _ in range(4):
            screen = self.screen.detect(self.adb.capture_bgr())
            if screen == SCREEN_RANKING:
                return
            self.adb.tap(*TAP_BACK_TO_RANKING)
            time.sleep(self.config.tap_delay)
        self.log_event("recover_warning", reason="back_to_ranking_retries_exhausted")

    def recover_to_ranking(self) -> None:
        self.back_to_ranking()
        screen = self.screen.detect(self.adb.capture_bgr())
        if screen != SCREEN_RANKING:
            self.stats.errors.append(f"failed to recover to ranking from {screen}")
            self.log_event("recover_failed", screen=screen)


def default_output_dir(date_str: str | None = None) -> Path:
    mmdd = (date_str or datetime.now().strftime("%m-%d")).replace("-", "")
    return ROOT / f"screenshots.{mmdd}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Capture daily ranked player match screenshots via ADB.",
    )
    parser.add_argument(
        "--date",
        default="",
        help="Target match date as MM-DD (default: today).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output directory (default: screenshots.MMDD).",
    )
    parser.add_argument("--adb-bin", default=DEFAULT_ADB_BIN)
    parser.add_argument("--serial", default=None, help="ADB device serial.")
    parser.add_argument("--connect", action="store_true", help="Run adb connect before capture.")
    parser.add_argument("--connect-host", default="127.0.0.1")
    parser.add_argument("--connect-port", type=int, default=16384)
    parser.add_argument("--start-rank", type=int, default=1)
    parser.add_argument("--end-rank", type=int, default=100)
    parser.add_argument("--dry-run", action="store_true", help="Navigate/OCR without saving PNGs.")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--log", type=Path, default=None, help="Structured JSON log path.")
    parser.add_argument(
        "--debug-save-top-players",
        type=int,
        default=0,
        help="Save debug screenshots for top N players under debug_players/.",
    )
    parser.add_argument(
        "--debug-save-top-matches",
        type=int,
        default=0,
        help=(
            "Save deduplicated today match screenshots for top N players "
            "under runs/<run_id>/debug_matches/ (works with --dry-run)."
        ),
    )
    parser.add_argument(
        "--skip-players",
        type=Path,
        default=None,
        help="JSON file listing ranks to skip manually (e.g. data/capture_skip_players.json).",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from capture_state.json, skipping completed/skipped ranks.",
    )
    parser.add_argument(
        "--state",
        type=Path,
        default=None,
        help="Custom capture_state.json path (default: <output>/capture_state.json).",
    )
    parser.add_argument(
        "--reset-state",
        action="store_true",
        help="Ignore existing capture_state.json and start a fresh run.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    target_date = args.date or datetime.now().strftime("%m-%d")
    output_dir = args.output or default_output_dir(target_date)
    config = CaptureConfig(
        adb_bin=args.adb_bin,
        device_serial=args.serial,
        connect_host=args.connect_host,
        connect_port=args.connect_port,
        auto_connect=args.connect,
        target_date=target_date,
        output_dir=output_dir,
        start_rank=args.start_rank,
        end_rank=args.end_rank,
        dry_run=args.dry_run,
        verbose=args.verbose,
        log_path=args.log,
        debug_save_top_players=args.debug_save_top_players,
        debug_save_top_matches=args.debug_save_top_matches,
        skip_players_path=args.skip_players,
        resume=args.resume,
        reset_state=args.reset_state,
        state_path=args.state,
    )

    try:
        DailyCaptureBot(config).run()
    except Exception as exc:
        logger.exception("Capture failed: %s", exc)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
