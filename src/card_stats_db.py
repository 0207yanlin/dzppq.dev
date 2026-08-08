# -*- coding: utf-8 -*-
"""Build card recommendation stats directly from match SQLite databases."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from src.card_rules import resolve_card_labels, split_card_prefix
from src.match_db import ensure_match_schema, parse_match_batch
from src.runtime_paths import project_root

StatItem = tuple[str, int, float]
DEFAULT_RECENCY_HALF_LIFE_DAYS = 2.0
MIN_RECENCY_WEIGHT = 0.0
DEFAULT_LOOKBACK_DAYS = 10
ADJUSTED_RANK_PRIOR = 8
CARD_PREFIX_TYPES = ("彩", "黄", "蓝", "白", "其他")
MIN_CARD_STATS_DATE = date(2026, 7, 27)


@dataclass
class RankStats:
    appearances: int = 0
    weighted_appearances: float = 0.0
    rank_sum: float = 0.0
    wins: float = 0.0
    top4: float = 0.0
    top2: float = 0.0

    def add(self, rank: int, weight: float = 1.0, *, top2_threshold: int = 2) -> None:
        self.appearances += 1
        self.weighted_appearances += weight
        self.rank_sum += rank * weight
        if rank == 1:
            self.wins += weight
        if rank <= 4:
            self.top4 += weight
        if rank <= top2_threshold:
            self.top2 += weight

    def to_dict(
        self,
        *,
        baseline_rank: float | None = None,
        prior: int = ADJUSTED_RANK_PRIOR,
        top2: bool = False,
    ) -> dict[str, Any]:
        n = max(self.weighted_appearances, 1e-9)
        row: dict[str, Any] = {
            "appearances": self.appearances,
            "weighted_appearances": round(self.weighted_appearances, 2),
            "avg_rank": round(self.rank_sum / n, 2),
            "win_rate": round(self.wins * 100.0 / n, 1),
            "top4_rate": round(self.top4 * 100.0 / n, 1),
        }
        if top2:
            row["team_top2_rate"] = round(self.top2 * 100.0 / n, 1)
        if baseline_rank is not None and prior > 0:
            adjusted = (self.rank_sum + baseline_rank * prior) / (self.weighted_appearances + prior)
            row["adjusted_avg_rank"] = round(adjusted, 2)
        return row


@dataclass
class PlayerCardRecord:
    player_id: int
    match_id: int
    rank: int
    partner_player: int | None
    cards: list[str] = field(default_factory=list)
    match_batch: str | None = None
    sample_weight: float = 1.0
    team_rank: int | None = None


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(project_root()).as_posix()
    except ValueError:
        return path.as_posix()


def _batch_ordinal(batch: str | None) -> int:
    if batch and len(batch) == 4 and batch.isdigit():
        return int(batch[:2]) * 100 + int(batch[2:])
    return 0


def _batch_date(batch: str | None, reference: date | None = None) -> date | None:
    if not batch or len(batch) != 4 or not batch.isdigit():
        return None
    current = reference or date.today()
    try:
        candidate = date(current.year, int(batch[:2]), int(batch[2:]))
    except ValueError:
        return None
    if candidate > current + timedelta(days=1):
        candidate = candidate.replace(year=candidate.year - 1)
    return candidate


def _is_supported_batch(batch: str | None) -> bool:
    inferred = _batch_date(batch)
    return inferred is not None and inferred >= MIN_CARD_STATS_DATE


def _select_analysis_batches(
    batches: set[str],
    *,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    reference_date: date | None = None,
) -> tuple[set[str], dict[str, Any]]:
    """Select the same natural-day window used by the HTML analyzer."""
    if lookback_days < 1:
        raise ValueError("lookback_days must be >= 1")

    dated_batches = [
        (batch, inferred)
        for batch in batches
        if (inferred := _batch_date(batch, reference_date)) is not None
        and inferred >= MIN_CARD_STATS_DATE
    ]
    if not dated_batches:
        return set(), {
            "lookback_days": lookback_days,
            "latest_batch": None,
            "start_batch": None,
            "start_date": None,
            "end_date": None,
            "batch_range": [],
        }

    latest_batch, latest_date = max(dated_batches, key=lambda item: item[1])
    start_date = latest_date - timedelta(days=lookback_days - 1)
    selected = {
        batch
        for batch, batch_date in dated_batches
        if start_date <= batch_date <= latest_date
    }
    ordered = sorted(
        selected,
        key=lambda batch: _batch_date(batch, reference_date) or date.min,
    )
    return selected, {
        "lookback_days": lookback_days,
        "latest_batch": latest_batch,
        "start_batch": start_date.strftime("%m%d"),
        "start_date": start_date.isoformat(),
        "end_date": latest_date.isoformat(),
        "batch_range": [ordered[0], ordered[-1]] if ordered else [],
    }


def _card_prefix_type(card_name: str) -> str:
    prefix, _ = split_card_prefix(card_name)
    if prefix:
        return prefix
    return "其他"


def find_bot_player_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(
        """
        SELECT p7.id AS p7_id, p8.id AS p8_id
        FROM players p7
        JOIN players p8 ON p8.match_id = p7.match_id AND p8.rank = 8
        WHERE p7.rank = 7
          AND (
            p7.partner_player = 8
            OR p8.partner_player = 7
            OR EXISTS (
              SELECT 1
              FROM pairs pair
              WHERE pair.match_id = p7.match_id
                AND (
                  (pair.player_a = 7 AND pair.player_b = 8)
                  OR (pair.player_a = 8 AND pair.player_b = 7)
                )
            )
          )
        """
    ).fetchall()
    bot_ids: set[int] = set()
    for row in rows:
        bot_ids.add(int(row[0]))
        bot_ids.add(int(row[1]))
    return bot_ids


def _compute_sample_weights(
    records: list[PlayerCardRecord],
    *,
    half_life_days: float = DEFAULT_RECENCY_HALF_LIFE_DAYS,
    min_weight: float = MIN_RECENCY_WEIGHT,
    reference_date: date | None = None,
) -> None:
    dated_records = [
        (record, _batch_date(record.match_batch, reference_date))
        for record in records
    ]
    latest_date = max(
        (batch_date for _, batch_date in dated_records if batch_date is not None),
        default=None,
    )
    if latest_date is None:
        for record in records:
            record.sample_weight = 1.0
        return
    decay = 0.6931471805599453 / max(half_life_days, 1e-6)
    for record, batch_date in dated_records:
        if batch_date is None:
            record.sample_weight = min_weight
            continue
        days_ago = max((latest_date - batch_date).days, 0)
        record.sample_weight = max(min_weight, 2.718281828459045 ** (-decay * days_ago))


def _assign_team_ranks(records: list[PlayerCardRecord]) -> None:
    by_match_rank = {(record.match_id, record.rank): record for record in records}
    records_by_match: dict[int, list[PlayerCardRecord]] = defaultdict(list)
    for record in records:
        records_by_match[record.match_id].append(record)

    for match_id, match_records in records_by_match.items():
        seen: set[int] = set()
        teams: list[list[PlayerCardRecord]] = []
        for record in sorted(match_records, key=lambda item: item.rank):
            if record.player_id in seen:
                continue
            members = [record]
            seen.add(record.player_id)
            if record.partner_player is not None:
                partner = by_match_rank.get((match_id, int(record.partner_player)))
                if partner is not None and partner.player_id not in seen:
                    members.append(partner)
                    seen.add(partner.player_id)
            teams.append(members)

        teams.sort(key=lambda members: min(member.rank for member in members))
        for team_rank, members in enumerate(teams, start=1):
            for member in members:
                member.team_rank = team_rank


def _team_rank_value(record: PlayerCardRecord) -> int:
    return record.team_rank if record.team_rank is not None else record.rank


def _load_player_card_records(
    conn: sqlite3.Connection,
    bot_ids: set[int],
) -> tuple[list[PlayerCardRecord], dict[str, Any]]:
    ensure_match_schema(conn)
    all_match_meta = {
        int(row["id"]): {
            "path": row["path"],
            "match_date": row["match_date"],
        }
        for row in conn.execute("SELECT id, path, match_date FROM matches").fetchall()
    }
    supported_meta = {
        match_id: meta
        for match_id, meta in all_match_meta.items()
        if _is_supported_batch(meta["match_date"] or parse_match_batch(meta["path"]))
    }
    selected_batches, analysis_window = _select_analysis_batches(
        {
            meta["match_date"] or parse_match_batch(meta["path"])
            for meta in supported_meta.values()
            if meta["match_date"] or parse_match_batch(meta["path"])
        }
    )
    match_meta = {
        match_id: meta
        for match_id, meta in supported_meta.items()
        if (meta["match_date"] or parse_match_batch(meta["path"])) in selected_batches
    }
    analysis_window["source_matches"] = len(supported_meta)
    player_rows = [
        row
        for row in conn.execute("SELECT * FROM players ORDER BY match_id, rank").fetchall()
        if int(row["match_id"]) in match_meta
    ]
    kept_player_ids = {int(row["id"]) for row in player_rows if int(row["id"]) not in bot_ids}
    match_id_by_player = {
        int(row["id"]): int(row["match_id"])
        for row in player_rows
    }

    heroes_by_player: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if kept_player_ids:
        hero_rows = conn.execute(
            """
            SELECT h.id, h.player_id, h.hero_name, h.stars, he.equipment_name
            FROM heroes h
            LEFT JOIN hero_equipments he ON he.hero_id = h.id
            WHERE h.player_id IN ({})
            ORDER BY h.player_id, h.slot_index, he.item_index
            """.format(",".join("?" for _ in kept_player_ids)),
            tuple(kept_player_ids),
        ).fetchall()
        heroes_by_id: dict[int, dict[str, Any]] = {}
        for row in hero_rows:
            hero_id = int(row["id"])
            if hero_id not in heroes_by_id:
                hero = {
                    "hero_name": str(row["hero_name"]),
                    "stars": int(row["stars"] or 0),
                    "equipments": [],
                }
                heroes_by_id[hero_id] = hero
                heroes_by_player[int(row["player_id"])].append(hero)
            equipment_name = row["equipment_name"]
            if equipment_name and equipment_name != "unknown":
                heroes_by_id[hero_id]["equipments"].append(str(equipment_name))

    cards_by_player: dict[int, list[str]] = defaultdict(list)
    if kept_player_ids:
        card_rows = conn.execute(
            """
            SELECT player_id, card_name, slot_index, card_source
            FROM cards
            WHERE player_id IN ({})
            ORDER BY player_id, slot_index
            """.format(",".join("?" for _ in kept_player_ids)),
            tuple(kept_player_ids),
        ).fetchall()
        resolve_items: list[dict[str, Any]] = []
        resolve_player_ids: list[int] = []
        for row in card_rows:
            card_name = str(row["card_name"])
            if card_name == "unknown":
                continue
            player_id = int(row["player_id"])
            slot_index = int(row["slot_index"])
            meta = match_meta.get(match_id_by_player[player_id], {})
            resolve_items.append(
                {
                    "label": card_name,
                    "slot_index": slot_index,
                    "heroes": heroes_by_player.get(player_id, []),
                    "match_path": meta.get("path"),
                    "match_batch": meta.get("match_date")
                    or parse_match_batch(meta.get("path")),
                    "source": row["card_source"],
                }
            )
            resolve_player_ids.append(player_id)
        for player_id, resolved_name in zip(
            resolve_player_ids,
            resolve_card_labels(resolve_items),
            strict=True,
        ):
            cards_by_player[player_id].append(resolved_name)

    records: list[PlayerCardRecord] = []
    for player in player_rows:
        player_id = int(player["id"])
        if player_id in bot_ids:
            continue
        match_id = int(player["match_id"])
        meta = match_meta.get(match_id, {})
        match_batch = meta.get("match_date") or parse_match_batch(meta.get("path"))
        records.append(
            PlayerCardRecord(
                player_id=player_id,
                match_id=match_id,
                rank=int(player["rank"]),
                partner_player=player["partner_player"],
                cards=cards_by_player.get(player_id, []),
                match_batch=match_batch,
            )
        )
    _compute_sample_weights(
        records,
        reference_date=date.fromisoformat(analysis_window["end_date"])
        if analysis_window["end_date"]
        else None,
    )
    _assign_team_ranks(records)
    analysis_window["source_players"] = len(
        {
            int(row["id"])
            for row in conn.execute("SELECT id, match_id FROM players").fetchall()
            if int(row["match_id"]) in supported_meta and int(row["id"]) not in bot_ids
        }
    )
    analysis_window["included_players"] = len(records)
    analysis_window["excluded_players"] = (
        analysis_window["source_players"] - analysis_window["included_players"]
    )
    return records, analysis_window


def _aggregate_stats(
    items: list[StatItem],
    *,
    baseline: float,
    top2: bool = False,
) -> dict[str, dict[str, Any]]:
    stats: dict[str, RankStats] = defaultdict(RankStats)
    for key, rank, weight in items:
        if key:
            stats[key].add(rank, weight, top2_threshold=2 if top2 else 4)
    return {
        key: stat.to_dict(baseline_rank=baseline, top2=top2)
        for key, stat in stats.items()
        if stat.appearances > 0
    }


def _group_rows_by_prefix(rows: dict[str, dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {prefix: [] for prefix in CARD_PREFIX_TYPES if prefix != "其他"}
    for key in sorted(rows):
        prefix = _card_prefix_type(key)
        if prefix not in grouped:
            continue
        grouped[prefix].append({"key": key, **rows[key]})
    for prefix in grouped:
        grouped[prefix].sort(
            key=lambda row: (
                row.get("adjusted_avg_rank", row.get("avg_rank", 999.0)),
                row.get("avg_rank", 999.0),
                -row.get("top4_rate", 0.0),
                -row["appearances"],
            )
        )
    return grouped


def _add_avg_appearances_per_match(
    rows: dict[str, dict[str, Any]] | list[dict[str, Any]],
    total_matches: int,
) -> None:
    if total_matches <= 0:
        return
    iterable = rows.values() if isinstance(rows, dict) else rows
    for row in iterable:
        appearances = int(row.get("appearances", 0) or 0)
        row["avg_appearances_per_match"] = round(appearances / total_matches, 3)


def _db_quality(
    conn: sqlite3.Connection,
    bot_ids: set[int],
    match_ids: set[int],
) -> dict[str, int]:
    placeholders = ",".join("?" for _ in match_ids) or "NULL"
    params = tuple(sorted(match_ids))
    return {
        "matches": len(match_ids),
        "players": int(
            conn.execute(
                f"SELECT COUNT(*) FROM players WHERE match_id IN ({placeholders})",
                params,
            ).fetchone()[0]
        ),
        "cards": int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM cards c
                JOIN players p ON p.id = c.player_id
                WHERE p.match_id IN ({placeholders})
                """,
                params,
            ).fetchone()[0]
        ),
        "bot_player_records_excluded": sum(
            1
            for player_id in bot_ids
            if conn.execute(
                f"""
                SELECT 1 FROM players
                WHERE id = ? AND match_id IN ({placeholders})
                """,
                (player_id, *params),
            ).fetchone()
        ),
    }


def build_card_stats_payload(db_path: Path | str) -> dict[str, Any]:
    """Build CardStatsIndex-compatible payload from a match database."""
    path = Path(db_path)
    if not path.is_file():
        raise FileNotFoundError(f"Match DB not found: {path}")

    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    try:
        bot_ids = find_bot_player_ids(conn)
        records, analysis_window = _load_player_card_records(conn, bot_ids)
        if not records:
            raise ValueError(
                f"No usable player records on or after {MIN_CARD_STATS_DATE.isoformat()} "
                f"in DB: {path}"
            )
        quality = _db_quality(
            conn,
            bot_ids,
            {record.match_id for record in records},
        )

        total_weight = sum(record.sample_weight for record in records) or 1.0
        baseline = sum(record.rank * record.sample_weight for record in records) / total_weight
        team_baseline = (
            sum(_team_rank_value(record) * record.sample_weight for record in records) / total_weight
        )
        total_matches = len({record.match_id for record in records}) or 1

        single_items: list[StatItem] = []
        blue_team_items: list[StatItem] = []
        for record in records:
            cards = sorted(set(record.cards))
            weight = record.sample_weight
            for card in cards:
                single_items.append((card, record.rank, weight))
                if _card_prefix_type(card) == "蓝":
                    blue_team_items.append((card, _team_rank_value(record), weight))

        single_rows = _aggregate_stats(single_items, baseline=baseline)
        blue_team_rows = _aggregate_stats(blue_team_items, baseline=team_baseline, top2=True)
        single_by_prefix = _group_rows_by_prefix(single_rows)
        blue_by_prefix = _group_rows_by_prefix(blue_team_rows)
        _add_avg_appearances_per_match(single_rows, total_matches)
        for rows in single_by_prefix.values():
            _add_avg_appearances_per_match(rows, total_matches)
        for rows in blue_by_prefix.values():
            _add_avg_appearances_per_match(rows, total_matches)

        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_source": _rel(path),
            "overview": {
                "quality": quality,
                "analysis_window": analysis_window,
            },
            "methodology": {
                "recency_weighting": {
                    "enabled": True,
                    "half_life_days": DEFAULT_RECENCY_HALF_LIFE_DAYS,
                    "min_weight": MIN_RECENCY_WEIGHT,
                    "latest_batch": analysis_window["latest_batch"],
                    "batch_range": analysis_window["batch_range"],
                }
            },
            "rankings": {
                "cards": {
                    "single_cards_by_prefix": single_by_prefix,
                    "blue_cards_team_rank_by_prefix": blue_by_prefix,
                }
            },
        }
    finally:
        conn.close()
