# -*- coding: utf-8 -*-
"""Unit tests for match fingerprinting and import-time deduplication."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.match_db import (  # noqa: E402
    build_match_fingerprint,
    cluster_similar_entries,
    import_ground_truth,
    init_match_db,
    insert_match_entry,
    is_similar_match,
    parse_match_batch,
)


def _sample_entry(
    *,
    hero_name: str = "好柿连连",
    card_name: str = "蓝·福袋有钱",
    highlight_player: int = 1,
    verified: bool = False,
) -> dict:
    players = []
    for rank in range(1, 9):
        players.append(
            {
                "rank": rank,
                "row_index": rank - 1,
                "partner_player": rank + 1 if rank % 2 == 1 else rank - 1,
                "heroes": [
                    {
                        "slot_index": 0,
                        "hero_name": hero_name,
                        "tier": 5,
                        "stars": 2,
                        "equipment_count": "1",
                        "equipments": ["守护头盔"],
                    }
                ],
                "cards": [
                    {"slot_index": 0, "card_name": card_name},
                    {"slot_index": 1, "card_name": "黄·我来助你"},
                    {"slot_index": 2, "card_name": "白·法力专注"},
                ],
            }
        )
    return {
        "path": "screenshots.0705/example.png",
        "verified": verified,
        "highlight_player": highlight_player,
        "pairs": [[1, 2], [3, 4], [5, 6], [7, 8]],
        "players": players,
    }


class MatchDedupTests(unittest.TestCase):
    def test_parse_match_batch_prefers_path_over_captured_at(self) -> None:
        batch = parse_match_batch(
            "screenshots.0701/MuMu-20260702-010000-001.png",
            "2026-07-02T01:00:00",
        )
        self.assertEqual(batch, "0701")

    def test_parse_match_batch_falls_back_to_captured_at(self) -> None:
        batch = parse_match_batch(None, "2026-07-03T12:00:00")
        self.assertEqual(batch, "0703")

    def test_insert_match_entry_stores_match_date(self) -> None:
        entry = _sample_entry()
        entry["path"] = "screenshots.0705/example.png"
        entry["captured_at"] = "2026-07-06T01:00:00"
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "matches.db"
            conn = init_match_db(db_path)
            insert_match_entry(conn, "example.png", entry)
            row = conn.execute(
                "SELECT match_date FROM matches WHERE screenshot_name = ?",
                ("example.png",),
            ).fetchone()
            conn.close()
        self.assertEqual(row[0], "0705")

    def test_identical_entries_are_similar(self) -> None:
        left = build_match_fingerprint(_sample_entry())
        right = build_match_fingerprint(_sample_entry(highlight_player=3))
        similar, metrics = is_similar_match(left, right)
        self.assertTrue(similar)
        self.assertEqual(metrics["score"], 1.0)

    def test_different_pairs_are_not_similar(self) -> None:
        left_entry = _sample_entry()
        right_entry = _sample_entry()
        right_entry["pairs"] = [[1, 3], [2, 4], [5, 6], [7, 8]]
        left = build_match_fingerprint(left_entry)
        right = build_match_fingerprint(right_entry)
        similar, _ = is_similar_match(left, right)
        self.assertFalse(similar)

    def test_small_hero_noise_stays_similar(self) -> None:
        left_entry = _sample_entry()
        right_entry = _sample_entry()
        right_entry["players"][0]["heroes"][0]["hero_name"] = "unknown"
        left = build_match_fingerprint(left_entry)
        right = build_match_fingerprint(right_entry)
        similar, metrics = is_similar_match(left, right)
        self.assertTrue(similar)
        self.assertGreaterEqual(metrics["hero_rank"], 0.82)

    def test_import_skips_similar_entries(self) -> None:
        gt_data = {
            "screenshots": {
                "a.png": _sample_entry(),
                "b.png": _sample_entry(highlight_player=4),
            }
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "matches.db"
            conn = init_match_db(db_path)
            stats = import_ground_truth(
                conn,
                gt_data,
                path_prefix="screenshots.0705/",
                dedupe_similar=True,
            )
            conn.close()
        self.assertEqual(stats["inserted"], 1)
        self.assertEqual(stats["skipped_similar"], 1)
        self.assertEqual(stats["similar_skips"][0]["duplicate_of"], "a.png")

    def test_cluster_groups_transitive_duplicates(self) -> None:
        items = [
            ("a.png", _sample_entry()),
            ("b.png", _sample_entry(highlight_player=2)),
            ("c.png", _sample_entry(highlight_player=3)),
        ]
        clusters = cluster_similar_entries(items)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(len(clusters[0]), 3)


if __name__ == "__main__":
    unittest.main()
