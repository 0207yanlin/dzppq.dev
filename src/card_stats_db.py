# -*- coding: utf-8 -*-
"""Build card recommendation stats directly from match SQLite databases."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.card_rules import resolve_card_label, split_card_prefix
from src.match_db import ensure_match_schema, parse_match_batch
from src.runtime_paths import project_root

StatItem = tuple[str, int, float]
DEFAULT_RECENCY_HALF_LIFE_DAYS = 2.0
MIN_RECENCY_WEIGHT = 0.25
ADJUSTED_RANK_PRIOR = 8
CARD_PREFIX_TYPES = ("彩", "黄", "蓝", "白", "其他")


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
) -> None:
    batches = {record.match_batch for record in records if record.match_batch}
    if not batches:
        for record in records:
            record.sample_weight = 1.0
        return
    max_ord = max(_batch_ordinal(batch) for batch in batches)
    decay = 0.6931471805599453 / max(half_life_days, 1e-6)
    for record in records:
        if not record.match_batch:
            record.sample_weight = min_weight
            continue
        days_ago = max(max_ord - _batch_ordinal(record.match_batch), 0)
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


def _load_player_card_records(conn: sqlite3.Connection, bot_ids: set[int]) -> list[PlayerCardRecord]:
    ensure_match_schema(conn)
    match_meta = {
        int(row["id"]): {
            "path": row["path"],
            "match_date": row["match_date"],
        }
        for row in conn.execute("SELECT id, path, match_date FROM matches").fetchall()
    }
    player_rows = conn.execute("SELECT * FROM players ORDER BY match_id, rank").fetchall()
    kept_player_ids = {int(row["id"]) for row in player_rows if int(row["id"]) not in bot_ids}

    heroes_by_player: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if kept_player_ids:
        hero_rows = conn.execute(
            """
            SELECT h.id, h.player_id, h.stars, he.equipment_name
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
            SELECT player_id, card_name, slot_index
            FROM cards
            WHERE player_id IN ({})
            ORDER BY player_id, slot_index
            """.format(",".join("?" for _ in kept_player_ids)),
            tuple(kept_player_ids),
        ).fetchall()
        for row in card_rows:
            card_name = str(row["card_name"])
            if card_name == "unknown":
                continue
            player_id = int(row["player_id"])
            slot_index = int(row["slot_index"])
            resolved_name = resolve_card_label(
                card_name,
                slot_index,
                heroes_by_player.get(player_id, []),
            )
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
    _compute_sample_weights(records)
    _assign_team_ranks(records)
    return records


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


def _db_quality(conn: sqlite3.Connection, bot_ids: set[int]) -> dict[str, int]:
    return {
        "matches": int(conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]),
        "players": int(conn.execute("SELECT COUNT(*) FROM players").fetchone()[0]),
        "cards": int(conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]),
        "bot_player_records_excluded": len(bot_ids),
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
        quality = _db_quality(conn, bot_ids)
        records = _load_player_card_records(conn, bot_ids)
        if not records:
            raise ValueError(f"No usable player records in DB: {path}")

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
            "overview": {"quality": quality},
            "rankings": {
                "cards": {
                    "single_cards_by_prefix": single_by_prefix,
                    "blue_cards_team_rank_by_prefix": blue_by_prefix,
                }
            },
        }
    finally:
        conn.close()
