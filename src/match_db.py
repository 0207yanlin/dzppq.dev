# -*- coding: utf-8 -*-
"""SQLite storage for full match records exported from match_ground_truth.json."""

from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

BATCH_PATH_RE = re.compile(r"screenshots\.(\d{4})[\\/]", re.IGNORECASE)

UNKNOWN_LABELS = {"", "unknown", "未知"}
DEFAULT_SIMILARITY_THRESHOLD = 0.88
DEFAULT_MIN_HERO_RANK_SIMILARITY = 0.82
DEFAULT_MIN_PAIRS_SIMILARITY = 0.99

FULL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS matches (
    id                INTEGER PRIMARY KEY,
    screenshot_name   TEXT NOT NULL UNIQUE,
    path              TEXT NOT NULL,
    captured_at       TEXT,
    match_date        TEXT,
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

CREATE INDEX IF NOT EXISTS idx_matches_match_date ON matches(match_date);
CREATE INDEX IF NOT EXISTS idx_pairs_match_id ON pairs(match_id);
CREATE INDEX IF NOT EXISTS idx_players_match_id ON players(match_id);
CREATE INDEX IF NOT EXISTS idx_heroes_player_id ON heroes(player_id);
CREATE INDEX IF NOT EXISTS idx_hero_equipments_hero_id ON hero_equipments(hero_id);
CREATE INDEX IF NOT EXISTS idx_cards_player_id ON cards(player_id);
"""


def parse_match_batch(path: str | None, captured_at: str | None = None) -> str | None:
    """Return MMDD batch key from screenshots.MMDD path prefix."""
    text = (path or "").replace("\\", "/")
    match = BATCH_PATH_RE.search(text)
    if match:
        return match.group(1)
    if captured_at and len(captured_at) >= 10:
        try:
            return datetime.fromisoformat(captured_at[:10]).strftime("%m%d")
        except ValueError:
            return None
    return None


def ensure_match_schema(conn: sqlite3.Connection) -> None:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(matches)").fetchall()
    }
    if "match_date" not in columns:
        conn.execute("ALTER TABLE matches ADD COLUMN match_date TEXT")
        conn.commit()
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_matches_match_date ON matches(match_date)"
    )
    rows = conn.execute(
        "SELECT id, path, captured_at, match_date FROM matches WHERE match_date IS NULL"
    ).fetchall()
    for match_id, path, captured_at, _ in rows:
        batch = parse_match_batch(path, captured_at)
        if batch:
            conn.execute(
                "UPDATE matches SET match_date = ? WHERE id = ?",
                (batch, match_id),
            )
    conn.commit()


def init_match_db(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(FULL_SCHEMA_SQL)
    ensure_match_schema(conn)
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


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _normalize_label(value: str | None) -> str | None:
    text = str(value or "").strip()
    if text in UNKNOWN_LABELS:
        return None
    return text


def build_match_fingerprint(entry: dict[str, Any]) -> dict[str, Any]:
    """Build a normalized whole-match fingerprint from a GT-like entry."""
    pair_set: set[tuple[int, int]] = set()
    for pair in entry.get("pairs", []) or []:
        if len(pair) != 2:
            continue
        a, b = int(pair[0]), int(pair[1])
        pair_set.add(tuple(sorted((a, b))))

    ranks: dict[int, dict[str, set[str]]] = {}
    all_heroes: set[str] = set()
    all_cards: set[str] = set()
    for player in entry.get("players", []) or []:
        rank = int(player.get("rank") or player.get("row_index", 0) + 1)
        heroes: set[str] = set()
        hero_stars: set[str] = set()
        for hero in player.get("heroes", []) or []:
            name = _normalize_label(hero.get("hero_name"))
            if not name:
                continue
            heroes.add(name)
            hero_stars.add(f"{name}:{int(hero.get('stars') or 0)}")
            all_heroes.add(f"{rank}:{name}")
        cards: set[str] = set()
        for card in player.get("cards", []) or []:
            name = _normalize_label(card.get("card_name"))
            if not name:
                continue
            cards.add(name)
            all_cards.add(f"{rank}:{name}")
        ranks[rank] = {
            "heroes": heroes,
            "hero_stars": hero_stars,
            "cards": cards,
        }

    return {
        "pairs": pair_set,
        "ranks": ranks,
        "all_heroes": all_heroes,
        "all_cards": all_cards,
    }


def compare_match_fingerprints(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, float]:
    """Compare two match fingerprints and return component scores."""
    rank_ids = sorted(set(left["ranks"]) | set(right["ranks"]))
    hero_rank = sum(
        _jaccard(
            left["ranks"].get(rank, {}).get("heroes", set()),
            right["ranks"].get(rank, {}).get("heroes", set()),
        )
        for rank in rank_ids
    ) / max(len(rank_ids), 1)
    star_rank = sum(
        _jaccard(
            left["ranks"].get(rank, {}).get("hero_stars", set()),
            right["ranks"].get(rank, {}).get("hero_stars", set()),
        )
        for rank in rank_ids
    ) / max(len(rank_ids), 1)
    card_rank = sum(
        _jaccard(
            left["ranks"].get(rank, {}).get("cards", set()),
            right["ranks"].get(rank, {}).get("cards", set()),
        )
        for rank in rank_ids
    ) / max(len(rank_ids), 1)
    pairs = _jaccard(left["pairs"], right["pairs"])
    hero_global = _jaccard(left["all_heroes"], right["all_heroes"])
    card_global = _jaccard(left["all_cards"], right["all_cards"])
    score = (
        0.48 * hero_rank
        + 0.17 * star_rank
        + 0.22 * card_rank
        + 0.08 * pairs
        + 0.05 * hero_global
    )
    return {
        "score": score,
        "hero_rank": hero_rank,
        "star_rank": star_rank,
        "card_rank": card_rank,
        "pairs": pairs,
        "hero_global": hero_global,
        "card_global": card_global,
    }


def is_similar_match(
    left: dict[str, Any],
    right: dict[str, Any],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    min_hero_rank: float = DEFAULT_MIN_HERO_RANK_SIMILARITY,
    min_pairs: float = DEFAULT_MIN_PAIRS_SIMILARITY,
) -> tuple[bool, dict[str, float]]:
    metrics = compare_match_fingerprints(left, right)
    is_similar = (
        metrics["score"] >= threshold
        and metrics["hero_rank"] >= min_hero_rank
        and metrics["pairs"] >= min_pairs
    )
    return is_similar, metrics


@dataclass
class IndexedMatch:
    screenshot_name: str
    entry: dict[str, Any]
    fingerprint: dict[str, Any]
    verified: bool


def _entry_verified(entry: dict[str, Any]) -> bool:
    return bool(entry.get("verified"))


def _reconstruct_entry_from_db(conn: sqlite3.Connection, match_id: int) -> dict[str, Any]:
    match_row = conn.execute(
        """
        SELECT screenshot_name, verified
        FROM matches
        WHERE id = ?
        """,
        (match_id,),
    ).fetchone()
    if match_row is None:
        raise ValueError(f"match not found: {match_id}")

    pairs = [
        [int(row[0]), int(row[1])]
        for row in conn.execute(
            "SELECT player_a, player_b FROM pairs WHERE match_id = ? ORDER BY id",
            (match_id,),
        ).fetchall()
    ]

    players: list[dict[str, Any]] = []
    for player_id, rank, row_index, partner_player in conn.execute(
        """
        SELECT id, rank, row_index, partner_player
        FROM players
        WHERE match_id = ?
        ORDER BY rank
        """,
        (match_id,),
    ):
        heroes = [
            {
                "slot_index": slot_index,
                "hero_name": hero_name,
                "tier": tier,
                "stars": stars,
                "equipment_count": equipment_count,
                "equipments": [
                    row[0]
                    for row in conn.execute(
                        """
                        SELECT equipment_name
                        FROM hero_equipments
                        WHERE hero_id = ?
                        ORDER BY item_index
                        """,
                        (hero_id,),
                    ).fetchall()
                ],
            }
            for hero_id, slot_index, hero_name, tier, stars, equipment_count in conn.execute(
                """
                SELECT id, slot_index, hero_name, tier, stars, equipment_count
                FROM heroes
                WHERE player_id = ?
                ORDER BY slot_index
                """,
                (player_id,),
            )
        ]
        cards = [
            {
                "slot_index": slot_index,
                "card_name": card_name,
            }
            for slot_index, card_name in conn.execute(
                """
                SELECT slot_index, card_name
                FROM cards
                WHERE player_id = ?
                ORDER BY slot_index
                """,
                (player_id,),
            )
        ]
        players.append(
            {
                "rank": rank,
                "row_index": row_index,
                "partner_player": partner_player,
                "heroes": heroes,
                "cards": cards,
            }
        )

    return {
        "pairs": pairs,
        "players": players,
        "verified": bool(match_row[1]),
    }


def load_indexed_matches(conn: sqlite3.Connection) -> list[IndexedMatch]:
    indexed: list[IndexedMatch] = []
    for match_id, screenshot_name in conn.execute(
        "SELECT id, screenshot_name FROM matches ORDER BY id"
    ):
        entry = _reconstruct_entry_from_db(conn, match_id)
        indexed.append(
            IndexedMatch(
                screenshot_name=screenshot_name,
                entry=entry,
                fingerprint=build_match_fingerprint(entry),
                verified=_entry_verified(entry),
            )
        )
    return indexed


def find_similar_match(
    fingerprint: dict[str, Any],
    indexed_matches: list[IndexedMatch],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    min_hero_rank: float = DEFAULT_MIN_HERO_RANK_SIMILARITY,
    min_pairs: float = DEFAULT_MIN_PAIRS_SIMILARITY,
) -> tuple[IndexedMatch | None, dict[str, float] | None]:
    best_match: IndexedMatch | None = None
    best_metrics: dict[str, float] | None = None
    for candidate in indexed_matches:
        is_similar, metrics = is_similar_match(
            fingerprint,
            candidate.fingerprint,
            threshold=threshold,
            min_hero_rank=min_hero_rank,
            min_pairs=min_pairs,
        )
        if not is_similar:
            continue
        if best_metrics is None or metrics["score"] > best_metrics["score"]:
            best_match = candidate
            best_metrics = metrics
    return best_match, best_metrics


def cluster_similar_entries(
    items: list[tuple[str, dict[str, Any]]],
    *,
    threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    min_hero_rank: float = DEFAULT_MIN_HERO_RANK_SIMILARITY,
    min_pairs: float = DEFAULT_MIN_PAIRS_SIMILARITY,
) -> list[list[str]]:
    """Cluster GT-like entries by whole-match similarity (for dry-run reports)."""
    records = [
        (name, build_match_fingerprint(entry))
        for name, entry in items
    ]
    parent = {name: name for name, _ in records}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def union(a: str, b: str) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    for (name_a, fp_a), (name_b, fp_b) in (
        (records[i], records[j])
        for i in range(len(records))
        for j in range(i + 1, len(records))
    ):
        is_similar, _ = is_similar_match(
            fp_a,
            fp_b,
            threshold=threshold,
            min_hero_rank=min_hero_rank,
            min_pairs=min_pairs,
        )
        if is_similar:
            union(name_a, name_b)

    clusters: dict[str, list[str]] = {}
    for name, _ in records:
        clusters.setdefault(find(name), []).append(name)
    grouped = [sorted(names) for names in clusters.values() if len(names) > 1]
    grouped.sort(key=lambda names: (-len(names), names[0]))
    return grouped


def insert_match_entry(conn: sqlite3.Connection, screenshot_name: str, entry: dict[str, Any]) -> int:
    processed_at = datetime.now(timezone.utc).isoformat()
    rel_path = entry.get("path", screenshot_name)
    captured_at = entry.get("captured_at")
    match_date = parse_match_batch(rel_path, captured_at)
    cur = conn.execute(
        """
        INSERT INTO matches
            (screenshot_name, path, captured_at, match_date, labeled_at, verified,
             highlight_player, processed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            screenshot_name,
            rel_path,
            captured_at,
            match_date,
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
    path_prefix: str | None = "",
    force: bool = False,
    dedupe_similar: bool = True,
    similarity_threshold: float = DEFAULT_SIMILARITY_THRESHOLD,
    min_hero_rank: float = DEFAULT_MIN_HERO_RANK_SIMILARITY,
    min_pairs: float = DEFAULT_MIN_PAIRS_SIMILARITY,
) -> dict[str, Any]:
    stats: dict[str, Any] = {
        "inserted": 0,
        "skipped": 0,
        "replaced": 0,
        "skipped_similar": 0,
    }
    similar_skips: list[dict[str, Any]] = []
    indexed_matches = load_indexed_matches(conn) if dedupe_similar else []

    for screenshot_name, entry in gt_data.get("screenshots", {}).items():
        rel_path = entry.get("path", "")
        if path_prefix and not rel_path.replace("\\", "/").startswith(path_prefix):
            continue

        if screenshot_name_exists(conn, screenshot_name):
            if not force:
                stats["skipped"] += 1
                continue
            delete_match_by_name(conn, screenshot_name)
            indexed_matches = [
                item
                for item in indexed_matches
                if item.screenshot_name != screenshot_name
            ]
            stats["replaced"] += 1

        fingerprint = build_match_fingerprint(entry)
        if dedupe_similar:
            similar_match, metrics = find_similar_match(
                fingerprint,
                indexed_matches,
                threshold=similarity_threshold,
                min_hero_rank=min_hero_rank,
                min_pairs=min_pairs,
            )
            if similar_match is not None and metrics is not None:
                current_verified = _entry_verified(entry)
                existing_verified = similar_match.verified
                if current_verified and not existing_verified and force:
                    delete_match_by_name(conn, similar_match.screenshot_name)
                    indexed_matches = [
                        item
                        for item in indexed_matches
                        if item.screenshot_name != similar_match.screenshot_name
                    ]
                else:
                    stats["skipped_similar"] += 1
                    similar_skips.append(
                        {
                            "screenshot_name": screenshot_name,
                            "duplicate_of": similar_match.screenshot_name,
                            "score": round(metrics["score"], 4),
                            "hero_rank": round(metrics["hero_rank"], 4),
                            "verified_current": current_verified,
                            "verified_existing": existing_verified,
                        }
                    )
                    continue

        insert_match_entry(conn, screenshot_name, entry)
        stats["inserted"] += 1
        if dedupe_similar:
            indexed_matches.append(
                IndexedMatch(
                    screenshot_name=screenshot_name,
                    entry=entry,
                    fingerprint=fingerprint,
                    verified=_entry_verified(entry),
                )
            )

    stats["similar_skips"] = similar_skips
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
