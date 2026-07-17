# -*- coding: utf-8 -*-
"""Tests for find_card_matches.py."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.find_card_matches import (  # noqa: E402
    find_matches_for_card,
    main,
    resolve_screenshot_path,
)
from src.match_db import init_match_db, insert_match_entry  # noqa: E402


def _player(
    rank: int,
    *,
    card_name: str = "黄·我来助你",
    slot_index: int = 0,
) -> dict:
    cards = [
        {"slot_index": 0, "card_name": "黄·我来助你"},
        {"slot_index": 1, "card_name": "白·法力专注"},
        {"slot_index": 2, "card_name": "蓝·开攒大亨"},
    ]
    cards[slot_index] = {"slot_index": slot_index, "card_name": card_name}
    return {
        "rank": rank,
        "row_index": rank - 1,
        "partner_player": rank + 1 if rank % 2 == 1 else rank - 1,
        "heroes": [
            {
                "slot_index": 0,
                "hero_name": "好柿连连",
                "tier": 5,
                "stars": 2,
                "equipment_count": "1",
                "equipments": ["守护头盔"],
            }
        ],
        "cards": cards,
    }


def _entry(
    *,
    path: str,
    captured_at: str,
    target_ranks: list[tuple[int, int]] | None = None,
    card_name: str = "蓝·满血才是王道",
) -> dict:
    target_ranks = target_ranks or [(1, 0)]
    rank_to_slot = {rank: slot for rank, slot in target_ranks}
    players = []
    for rank in range(1, 9):
        if rank in rank_to_slot:
            players.append(
                _player(rank, card_name=card_name, slot_index=rank_to_slot[rank])
            )
        else:
            players.append(_player(rank))
    return {
        "path": path,
        "captured_at": captured_at,
        "verified": True,
        "highlight_player": 1,
        "pairs": [[1, 2], [3, 4], [5, 6], [7, 8]],
        "players": players,
    }


class FindCardMatchesTests(unittest.TestCase):
    def test_resolve_screenshot_path(self) -> None:
        root = Path("D:/dzppq.dev")
        resolved = resolve_screenshot_path("screenshots.0705/a.png", root)
        self.assertEqual(resolved, (root / "screenshots.0705" / "a.png").resolve())

    def test_sorts_by_captured_at_ascending(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "matches.db"
            conn = init_match_db(db_path)
            insert_match_entry(
                conn,
                "newer.png",
                _entry(
                    path="screenshots.0705/newer.png",
                    captured_at="2026-07-06T12:00:00",
                ),
            )
            insert_match_entry(
                conn,
                "older.png",
                _entry(
                    path="screenshots.0705/older.png",
                    captured_at="2026-07-05T08:00:00",
                ),
            )
            insert_match_entry(
                conn,
                "middle.png",
                _entry(
                    path="screenshots.0705/middle.png",
                    captured_at="2026-07-05T18:00:00",
                ),
            )
            matches = find_matches_for_card(conn, "蓝·满血才是王道", root=root)
            conn.close()

        self.assertEqual(
            [m.screenshot_name for m in matches],
            ["older.png", "middle.png", "newer.png"],
        )

    def test_dedupes_same_match_with_multiple_hits(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "matches.db"
            conn = init_match_db(db_path)
            insert_match_entry(
                conn,
                "multi.png",
                _entry(
                    path="screenshots.0705/multi.png",
                    captured_at="2026-07-05T10:00:00",
                    target_ranks=[(2, 0), (5, 1)],
                ),
            )
            matches = find_matches_for_card(conn, "蓝·满血才是王道", root=root)
            conn.close()

        self.assertEqual(len(matches), 1)
        self.assertEqual(
            [(hit.rank, hit.slot_index) for hit in matches[0].hits],
            [(2, 0), (5, 1)],
        )

    def test_normalizes_label_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "matches.db"
            conn = init_match_db(db_path)
            insert_match_entry(
                conn,
                "alias.png",
                _entry(
                    path="screenshots.0705/alias.png",
                    captured_at="2026-07-05T10:00:00",
                    card_name="蓝·福袋有钱",
                ),
            )
            matches = find_matches_for_card(conn, "蓝·福袋", root=root)
            conn.close()

        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].screenshot_name, "alias.png")

    def test_no_matches_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            db_path = root / "matches.db"
            conn = init_match_db(db_path)
            insert_match_entry(
                conn,
                "other.png",
                _entry(
                    path="screenshots.0705/other.png",
                    captured_at="2026-07-05T10:00:00",
                    card_name="蓝·福袋有钱",
                ),
            )
            matches = find_matches_for_card(conn, "蓝·满血才是王道", root=root)
            conn.close()

        self.assertEqual(matches, [])

    def test_main_missing_db(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            missing = Path(tmpdir) / "missing.db"
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                code = main(["蓝·满血才是王道", "--db", str(missing)])
        self.assertEqual(code, 1)
        self.assertIn("database not found", stderr.getvalue())

    def test_main_invalid_limit(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            code = main(["蓝·满血才是王道", "--limit", "0"])
        self.assertEqual(code, 1)
        self.assertIn("--limit must be a positive integer", stderr.getvalue())

    def test_main_prints_absolute_paths_and_honors_limit(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            shot_dir = root / "screenshots.0705"
            shot_dir.mkdir()
            older = shot_dir / "older.png"
            newer = shot_dir / "newer.png"
            older.write_bytes(b"png")
            newer.write_bytes(b"png")

            db_path = root / "matches.db"
            conn = init_match_db(db_path)
            insert_match_entry(
                conn,
                "newer.png",
                _entry(
                    path="screenshots.0705/newer.png",
                    captured_at="2026-07-06T12:00:00",
                ),
            )
            insert_match_entry(
                conn,
                "older.png",
                _entry(
                    path="screenshots.0705/older.png",
                    captured_at="2026-07-05T08:00:00",
                ),
            )
            conn.close()

            stdout = io.StringIO()
            stderr = io.StringIO()
            with (
                patch("scripts.find_card_matches.ROOT", root),
                redirect_stdout(stdout),
                redirect_stderr(stderr),
            ):
                code = main(
                    [
                        "蓝·满血才是王道",
                        "--db",
                        str(db_path),
                        "--limit",
                        "1",
                    ]
                )

            output = stdout.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("matches=1", output)
            self.assertIn("older.png", output)
            self.assertNotIn("newer.png", output)
            self.assertIn(str(older.resolve()), output)
            self.assertEqual(stderr.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
