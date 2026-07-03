# -*- coding: utf-8 -*-
"""SQLite storage for extracted match data."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS matches (
    id            INTEGER PRIMARY KEY,
    screenshot    TEXT NOT NULL UNIQUE,
    captured_at   TEXT,
    processed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS players (
    id            INTEGER PRIMARY KEY,
    match_id      INTEGER NOT NULL REFERENCES matches(id) ON DELETE CASCADE,
    rank          INTEGER NOT NULL CHECK(rank BETWEEN 1 AND 8),
    row_index     INTEGER NOT NULL CHECK(row_index BETWEEN 0 AND 7),
    UNIQUE(match_id, rank)
);

CREATE TABLE IF NOT EXISTS heroes (
    id            INTEGER PRIMARY KEY,
    player_id     INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    slot_index    INTEGER NOT NULL,
    hero_name     TEXT NOT NULL,
    tier          INTEGER,
    stars         INTEGER NOT NULL CHECK(stars BETWEEN 0 AND 3),
    match_score   REAL
);

CREATE TABLE IF NOT EXISTS cards (
    id            INTEGER PRIMARY KEY,
    player_id     INTEGER NOT NULL REFERENCES players(id) ON DELETE CASCADE,
    slot_index    INTEGER NOT NULL CHECK(slot_index BETWEEN 0 AND 2),
    card_name     TEXT NOT NULL,
    match_score   REAL
);

CREATE INDEX IF NOT EXISTS idx_players_match_id ON players(match_id);
CREATE INDEX IF NOT EXISTS idx_heroes_player_id ON heroes(player_id);
CREATE INDEX IF NOT EXISTS idx_cards_player_id ON cards(player_id);
"""


def init_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


def screenshot_exists(conn: sqlite3.Connection, screenshot: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM matches WHERE screenshot = ?", (screenshot,)
    ).fetchone()
    return row is not None


def delete_match_by_screenshot(conn: sqlite3.Connection, screenshot: str) -> None:
    conn.execute("DELETE FROM matches WHERE screenshot = ?", (screenshot,))
    conn.commit()


def insert_match(
    conn: sqlite3.Connection,
    screenshot: str,
    captured_at: str | None,
    players: list[dict[str, Any]],
) -> int:
    """Insert one match and all related rows. Returns match id."""
    processed_at = datetime.now(timezone.utc).isoformat()
    cur = conn.execute(
        "INSERT INTO matches (screenshot, captured_at, processed_at) VALUES (?, ?, ?)",
        (screenshot, captured_at, processed_at),
    )
    match_id = cur.lastrowid

    for player in players:
        cur = conn.execute(
            "INSERT INTO players (match_id, rank, row_index) VALUES (?, ?, ?)",
            (match_id, player["rank"], player["row_index"]),
        )
        player_id = cur.lastrowid

        for hero in player["heroes"]:
            conn.execute(
                """
                INSERT INTO heroes
                    (player_id, slot_index, hero_name, tier, stars, match_score)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    player_id,
                    hero["slot_index"],
                    hero["hero_name"],
                    hero["tier"],
                    hero["stars"],
                    hero.get("match_score"),
                ),
            )

        for card in player["cards"]:
            conn.execute(
                """
                INSERT INTO cards (player_id, slot_index, card_name, match_score)
                VALUES (?, ?, ?, ?)
                """,
                (
                    player_id,
                    card["slot_index"],
                    card["card_name"],
                    card.get("match_score"),
                ),
            )

    conn.commit()
    return match_id
