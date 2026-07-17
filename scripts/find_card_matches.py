# -*- coding: utf-8 -*-
"""Find original match screenshots that contain a given card."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.card_rules import normalize_card_label  # noqa: E402

DEFAULT_DB = ROOT / "data" / "match_latest.db"


@dataclass(frozen=True)
class CardHit:
    rank: int
    slot_index: int


@dataclass(frozen=True)
class MatchHit:
    screenshot_name: str
    relative_path: str
    absolute_path: Path
    captured_at: str | None
    hits: tuple[CardHit, ...]


def resolve_screenshot_path(relative_path: str, root: Path = ROOT) -> Path:
    """Resolve a DB-stored relative screenshot path against the project root."""
    path = Path(relative_path)
    if path.is_absolute():
        return path.resolve()
    return (root / path).resolve()


def find_matches_for_card(
    conn: sqlite3.Connection,
    card_name: str,
    *,
    root: Path = ROOT,
    limit: int | None = None,
) -> list[MatchHit]:
    """Return matches containing ``card_name``, oldest first, one row per match."""
    canonical = normalize_card_label(card_name)
    rows = conn.execute(
        """
        SELECT
            m.id,
            m.screenshot_name,
            m.path,
            m.captured_at,
            p.rank,
            c.slot_index
        FROM cards AS c
        JOIN players AS p ON c.player_id = p.id
        JOIN matches AS m ON p.match_id = m.id
        WHERE c.card_name = ?
        ORDER BY
            m.captured_at ASC,
            m.screenshot_name ASC,
            p.rank ASC,
            c.slot_index ASC
        """,
        (canonical,),
    ).fetchall()

    by_match: dict[int, MatchHit] = {}
    order: list[int] = []
    for match_id, screenshot_name, rel_path, captured_at, rank, slot_index in rows:
        hit = CardHit(rank=int(rank), slot_index=int(slot_index))
        existing = by_match.get(match_id)
        if existing is None:
            by_match[match_id] = MatchHit(
                screenshot_name=screenshot_name,
                relative_path=rel_path,
                absolute_path=resolve_screenshot_path(rel_path, root),
                captured_at=captured_at,
                hits=(hit,),
            )
            order.append(match_id)
        else:
            by_match[match_id] = MatchHit(
                screenshot_name=existing.screenshot_name,
                relative_path=existing.relative_path,
                absolute_path=existing.absolute_path,
                captured_at=existing.captured_at,
                hits=existing.hits + (hit,),
            )

    results = [by_match[match_id] for match_id in order]
    if limit is not None:
        results = results[:limit]
    return results


def format_hit_summary(hits: tuple[CardHit, ...]) -> str:
    return ",".join(f"rank{hit.rank}@slot{hit.slot_index}" for hit in hits)


def print_matches(matches: list[MatchHit], *, card_name: str) -> list[Path]:
    """Print match rows; return absolute paths that are missing on disk."""
    canonical = normalize_card_label(card_name)
    print(f"card={canonical} matches={len(matches)}")
    missing: list[Path] = []
    for index, match in enumerate(matches, start=1):
        captured = match.captured_at or "(no captured_at)"
        summary = format_hit_summary(match.hits)
        print(
            f"[{index}/{len(matches)}] {captured}  {summary}  {match.relative_path}"
        )
        # Absolute path alone so Windows terminals can Ctrl+Click to open the PNG.
        print(str(match.absolute_path))
        if not match.absolute_path.is_file():
            missing.append(match.absolute_path)
    return missing


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Find original match screenshots that contain a given card.",
    )
    parser.add_argument(
        "card_name",
        help="Card label to search (aliases are normalized before exact match).",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=DEFAULT_DB,
        help=f"Match SQLite database (default: {DEFAULT_DB.as_posix()})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of matches to print (oldest first).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.limit is not None and args.limit <= 0:
        print(f"error: --limit must be a positive integer, got {args.limit}", file=sys.stderr)
        return 1

    db_path = args.db if args.db.is_absolute() else (ROOT / args.db)
    db_path = db_path.resolve()
    if not db_path.is_file():
        print(f"error: database not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    try:
        matches = find_matches_for_card(
            conn,
            args.card_name,
            root=ROOT,
            limit=args.limit,
        )
    finally:
        conn.close()

    if not matches:
        canonical = normalize_card_label(args.card_name)
        print(f"error: no matches for card={canonical}", file=sys.stderr)
        return 1

    missing = print_matches(matches, card_name=args.card_name)
    if missing:
        print(
            f"error: {len(missing)} screenshot file(s) missing on disk",
            file=sys.stderr,
        )
        for path in missing:
            print(f"  missing: {path}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
