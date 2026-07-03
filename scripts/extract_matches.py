# -*- coding: utf-8 -*-
"""Batch extract match data from screenshots into SQLite."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.db import delete_match_by_screenshot, init_db, insert_match, screenshot_exists
from src.detect_cards import detect_cards, load_template_sigs
from src.detect_heroes import detect_lineups, load_templates
from src.detect_stars import detect_stars
from src.layout import ROOT as PROJECT_ROOT, SCREENSHOT_DIR
from src.parse import parse_hero_label, parse_screenshot_timestamp


def merge_player_data(
    lineups: list[dict],
    stars_by_player: list[list[int]],
    cards_by_player: list[dict],
) -> list[dict]:
    players = []
    for lineup, stars, card_row in zip(lineups, stars_by_player, cards_by_player):
        j = lineup["row_index"]
        rank = j + 1
        heroes_out = []
        for idx, hero in enumerate(lineup["heroes"]):
            tier, hero_name = parse_hero_label(hero["label"])
            star_count = stars[idx] if idx < len(stars) else 0
            if star_count == 0:
                print(
                    f"  warning: player {rank} slot {hero['slot_index']} "
                    f"hero {hero_name} has 0 stars",
                    file=sys.stderr,
                )
            heroes_out.append(
                {
                    "slot_index": hero["slot_index"],
                    "hero_name": hero_name,
                    "tier": tier,
                    "stars": star_count,
                    "match_score": hero["score"],
                }
            )
        players.append(
            {
                "rank": rank,
                "row_index": j,
                "heroes": heroes_out,
                "cards": [
                    {
                        "slot_index": c["slot_index"],
                        "card_name": c["label"],
                        "match_score": c["score"],
                    }
                    for c in card_row["cards"]
                ],
            }
        )
    return players


def process_screenshot(
    img_path: Path,
    hero_templates: dict,
    card_sigs: dict,
) -> list[dict]:
    img = cv2.imread(str(img_path))
    if img is None:
        raise RuntimeError(f"failed to read screenshot: {img_path}")

    lineups = detect_lineups(img, hero_templates)
    stars = detect_stars(img)
    cards = detect_cards(img, card_sigs)
    return merge_player_data(lineups, stars, cards)


def collect_screenshots(screenshot_dir: Path) -> list[Path]:
    paths = []
    for path in sorted(screenshot_dir.glob("*.png")):
        if "_debug" in path.parts:
            continue
        paths.append(path)
    return paths


def summarize(players: list[dict]) -> tuple[int, int]:
    hero_total = sum(len(p["heroes"]) for p in players)
    unknown_cards = sum(
        1 for p in players for c in p["cards"] if c["card_name"] == "unknown"
    )
    return hero_total, unknown_cards


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract match data from screenshots")
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=SCREENSHOT_DIR,
        help="Directory containing PNG screenshots",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=PROJECT_ROOT / "data" / "matches.db",
        help="SQLite database path",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reprocess screenshots already in the database",
    )
    args = parser.parse_args()

    screenshot_dir = args.screenshot_dir.resolve()
    if not screenshot_dir.is_dir():
        raise SystemExit(f"screenshot dir not found: {screenshot_dir}")

    print("Loading templates...")
    hero_templates = load_templates()
    card_sigs = load_template_sigs()
    print(f"  heroes: {len(hero_templates)}, cards: {len(card_sigs)}")

    conn = init_db(args.db.resolve())
    paths = collect_screenshots(screenshot_dir)
    if not paths:
        print(f"No PNG files in {screenshot_dir}")
        return

    processed = skipped = failed = 0
    for img_path in paths:
        rel_screenshot = str(img_path.relative_to(PROJECT_ROOT)).replace("\\", "/")
        if screenshot_exists(conn, rel_screenshot) and not args.force:
            skipped += 1
            continue
        try:
            if args.force and screenshot_exists(conn, rel_screenshot):
                delete_match_by_screenshot(conn, rel_screenshot)
            players = process_screenshot(img_path, hero_templates, card_sigs)
            captured_at = parse_screenshot_timestamp(img_path)
            insert_match(conn, rel_screenshot, captured_at, players)
            hero_total, unknown_cards = summarize(players)
            print(
                f"{img_path.name}: 8 players, {hero_total} heroes, "
                f"{unknown_cards} unknown cards"
            )
            processed += 1
        except Exception as exc:
            print(f"ERROR {img_path.name}: {exc}", file=sys.stderr)
            failed += 1

    match_count = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(
        f"\nDone. processed={processed}, skipped={skipped}, failed={failed}, "
        f"total_matches={match_count}, db={args.db}"
    )
    conn.close()


if __name__ == "__main__":
    main()
