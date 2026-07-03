# -*- coding: utf-8 -*-
"""SQLite storage for full match records exported from match_ground_truth.json."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FULL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS matches (
    id                INTEGER PRIMARY KEY,
    screenshot_name   TEXT NOT NULL UNIQUE,
    path              TEXT NOT NULL,
    captured_at       TEXT,
    labeled_at        TEXT,
    verified          INTEGER NOT NULL DEFAULT 0,
    highlight_player  INTEGER,
    processed_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pairs (
    id          INTEGER PRIMARY KEY,
    match_id    INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    player_a    INTEGER NOT NULL,
    player_b    INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id              INTEGER PRIMARY KEY,
    match_id        INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    rank            INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 8),
    row_index       INTEGER NOT NULL CHECK(row_index BETWEEN 0 AND 7),
    partner_player  INTEGER,
    UNIQUE(match_id, rank)
);

CREATE TABLE IF NOT EXISTS heroes (
    id               INTEGER PRIMARY KEY,
    player_id        INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    slot_index       INTEGER NOT NULL,
    hero_name        TEXT NOT NULL,
    tier             INTEGER,
    stars            INTEGER NOT NULL,
    equipment_count  TEXT,
    hero_score       REAL
);

CREATE TABLE IF NOT EXISTS hero_equipments (
    id              INTEGER PRIMARY KEY,
    hero_id         INTEGER NOT NULL REFERENCES heroes(id) ON DELETE CASCADE,
    item_index      INTEGER NOT NULL,
    equipment_name  TEXT NOT NULL,
    equipment_score REAL
);

CREATE TABLE IF NOT EXISTS cards (
    id          INTEGER PRIMARY KEY,
    player_id   INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    slot_index  INTEGER NOT NULL CHECK(slot_index BETWEEN 0 AND 2),
    card_name   TEXT NOT NULL,
    card_score  REAL
);

CREATE INDEX IF NOT EXISTS idx_pairs_match_id ON pairs(match_id);
CREATE INDEX IF NOT EXISTS idx_players_match_id ON players(match_id);
CREATE INDEX IF NOT EXISTS idx_heroes_player_id ON heroes(player_id);
CREATE INDEX IF NOT EXISTS idx_hero_equipments_hero_id ON hero_equipments(hero_id);
CREATE INDEX IF NOT EXISTS idx_cards_player_id ON cards(player_id);
"""


def init_match_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(FULL_SCHEMA_SQL)
    conn.commit()
    return conn


def screenshot_name_exists(conn: sqlite3.Connection, screenshot_name: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM matches WHERE screenshot_name = ?",
        (screenshot_name,),
    ).fetchone()
    return row is not None


def delete_match_by_name(conn: sqlite3.Connection, screenshot_name: str) -> None:
    conn.execute("DELETE FROM matches WHERE screenshot_name = ?", (screenshot_name,))
    conn.commit()


def insert_match_entry(conn: sqlite3.Connection, screenshot_name: str, entry: dict[str, Any]) -> int:
    processed_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        """
        INSERT INTO matches
            (screenshot_name, path, captured_at, labeled_at, verified,
             highlight_player, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            screenshot_name,
            entry.get("path", screenshot_name),
            entry.get("captured_at"),
            entry.get("labeled_at"),
            1 if entry.get("verified") else 0,
            entry.get("highlight_player"),
            processed_at,
        ),
    )
    match_id = cur.lastrowid

    for pair in entry.get("pairs", []):
        if len(pair) != 2:
            continue
        conn.execute(
            "INSERT INTO pairs (match_id, player_a, player_b) VALUES (?, ?, ?)",
            (match_id, int(pair[0]), int(pair[1])),
        )

    for player in entry.get("players", []):
        cur = conn.execute(
            """
            INSERT INTO players (match_id, rank, row_index, partner_player)
            VALUES (?, ?, ?, ?)
            """,
            (
                match_id,
                player["rank"],
                player["row_index"],
                player.get("partner_player"),
            ),
        )
        player_id = cur.lastrowid

        for hero in player.get("heroes", []):
            scores = hero.get("scores") or {}
            hero_score = scores.get("hero")
            eq_scores = scores.get("equipments") or []
            cur = conn.execute(
                """
                INSERT INTO heroes
                    (player_id, slot_index, hero_name, tier, stars,
                     equipment_count, hero_score)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    hero["slot_index"],
                    hero["hero_name"],
                    hero.get("tier"),
                    hero.get("stars", 0),
                    hero.get("equipment_count"),
                    hero_score,
                ),
            )
            hero_id = cur.lastrowid
            for item_index, equipment_name in enumerate(hero.get("equipments") or []):
                equipment_score = (
                    eq_scores[item_index]
                    if item_index < len(eq_scores)
                    else None
                )
                conn.execute(
                    """
                    INSERT INTO hero_equipments
                        (hero_id, item_index, equipment_name, equipment_score)
                    VALUES (?, ?, ?, ?)
                    """,
                    (hero_id, item_index, equipment_name, equipment_score),
                )

        for card in player.get("cards", []):
            conn.execute(
                """
                INSERT INTO cards (player_id, slot_index, card_name, card_score)
                VALUES (?, ?, ?, ?)
                """,
                (
                    player_id,
                    card["slot_index"],
                    card["card_name"],
                    card.get("score"),
                ),
            )

    conn.commit()
    return match_id


def import_ground_truth(
    conn: sqlite3.Connection,
    gt_data: dict[str, Any],
    *,
    path_prefix: str | None = "screenshots.0701/",
    force: bool = False,
) -> dict[str, int]:
    stats = {"inserted": 0, "skipped": 0, "replaced": 0}
    for screenshot_name, entry in gt_data.get("screenshots", {}).items():
        rel_path = entry.get("path", "")
        if path_prefix and not rel_path.replace("\\", "/").startswith(path_prefix):
            continue
        if screenshot_name_exists(conn, screenshot_name):
            if not force:
                stats["skipped"] += 1
                continue
            delete_match_by_name(conn, screenshot_name)
            stats["replaced"] += 1
        insert_match_entry(conn, screenshot_name, entry)
        stats["inserted"] += 1
    return stats


def db_summary(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        "matches": conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
        "players": conn.execute("SELECT COUNT(*) FROM players").fetchone()[0],
        "heroes": conn.execute("SELECT COUNT(*) FROM heroes").fetchone()[0],
        "hero_equipments": conn.execute(
            "SELECT COUNT(*) FROM hero_equipments"
        ).fetchone()[0],
        "cards": conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0],
        "unknown_heroes": conn.execute(
            "SELECT COUNT(*) FROM heroes WHERE hero_name = 'unknown'"
        ).fetchone()[0],
        "unknown_cards": conn.execute(
            "SELECT COUNT(*) FROM cards WHERE card_name = 'unknown'"
        ).fetchone()[0],
    }


def export_summary_json(db_path: Path, output_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    summary = db_summary(conn)
    conn.close()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
