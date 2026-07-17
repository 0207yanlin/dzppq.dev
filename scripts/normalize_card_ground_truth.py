# -*- coding: utf-8 -*-
"""Normalize legacy card names in match ground truth JSON and SQLite DB."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.card_rules import normalize_card_label, resolve_card_label  # noqa: E402
from src.match_ground_truth import (  # noqa: E402
    DEFAULT_GT_PATH,
    load_match_ground_truth,
    save_match_ground_truth,
)

LEGACY_CARD_LABELS = frozenset(
    {
        "装备共鸣法",
        "装备共鸣血",
        "装备共鸣攻",
        "装备共鸣法pro",
        "装备共鸣血pro",
        "装备共鸣攻pro",
        "大力",
        "巫术",
        "守护",
        "重质拍档支援",
        "最佳拍档",
        "最强支援",
        "福袋·蓝",
        "有钱同享",
        "法力专注",
        "快速成型",
        "吸吸宝pro",
        "蓝·开攒",
        "蓝·大亨",
        "蓝·一起刷刷刷",
        "蓝·天降啾啾pro",
        "蓝·重质拍档支援",
    }
)


def normalize_ground_truth(data: dict) -> Counter:
    changes: Counter = Counter()
    for entry in data.get("screenshots", {}).values():
        for player in entry.get("players", []):
            heroes = player.get("heroes", [])
            for card in player.get("cards", []):
                old_name = card.get("card_name", "")
                new_name = resolve_card_label(
                    old_name,
                    int(card["slot_index"]),
                    heroes,
                )
                if new_name != old_name:
                    changes[f"{old_name} -> {new_name}"] += 1
                    card["card_name"] = new_name
    return changes


def normalize_match_db(db_path: Path) -> Counter:
    changes: Counter = Counter()
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT c.id, c.card_name, c.slot_index, c.player_id
        FROM cards c
        """
    ).fetchall()
    for row in rows:
        hero_rows = conn.execute(
            """
            SELECT h.id, h.stars, he.equipment_name
            FROM heroes h
            LEFT JOIN hero_equipments he ON he.hero_id = h.id
            WHERE h.player_id = ?
            ORDER BY h.slot_index, he.item_index
            """,
            (int(row["player_id"]),),
        ).fetchall()
        heroes_by_id: dict[int, dict] = {}
        heroes: list[dict] = []
        for hero_row in hero_rows:
            hero_id = int(hero_row["id"])
            if hero_id not in heroes_by_id:
                hero = {
                    "stars": int(hero_row["stars"] or 0),
                    "equipments": [],
                }
                heroes_by_id[hero_id] = hero
                heroes.append(hero)
            equipment_name = hero_row["equipment_name"]
            if equipment_name and equipment_name != "unknown":
                heroes_by_id[hero_id]["equipments"].append(str(equipment_name))
        old_name = str(row["card_name"])
        new_name = resolve_card_label(old_name, int(row["slot_index"]), heroes)
        if new_name != old_name:
            changes[f"{old_name} -> {new_name}"] += 1
            conn.execute(
                "UPDATE cards SET card_name = ? WHERE id = ?",
                (new_name, int(row["id"])),
            )
    conn.commit()
    conn.close()
    return changes


def command_normalize_db(args: argparse.Namespace) -> None:
    changes = normalize_match_db(args.db)
    if not changes:
        print(f"No card name changes needed in {args.db}")
        return
    print(f"Updated {args.db}")
    print("Card name replacements:")
    for key, count in sorted(changes.items()):
        print(f"  {key}: {count}")


def command_normalize(args: argparse.Namespace) -> None:
    data = load_match_ground_truth(args.gt)
    changes = normalize_ground_truth(data)
    if args.dry_run:
        print("Dry run only; no file written.")
    else:
        save_match_ground_truth(data, args.gt)
        print(f"Updated {args.gt}")
    if not changes:
        print("No card name changes needed.")
        return
    print("Card name replacements:")
    for key, count in sorted(changes.items()):
        print(f"  {key}: {count}")


def command_check(args: argparse.Namespace) -> None:
    data = load_match_ground_truth(args.gt)
    found: Counter = Counter()
    for screenshot_name, entry in data.get("screenshots", {}).items():
        for player in entry.get("players", []):
            for card in player.get("cards", []):
                name = card.get("card_name", "")
                if name in LEGACY_CARD_LABELS or name != normalize_card_label(name):
                    found[f"{screenshot_name} P{player['rank']} slot{card['slot_index']}: {name}"] += 1
    if not found:
        print("No legacy card labels found.")
        return
    print("Legacy or non-canonical card labels:")
    for key, count in sorted(found.items()):
        print(f"  {key}")
    raise SystemExit(1)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gt", type=Path, default=DEFAULT_GT_PATH)
    subparsers = parser.add_subparsers(dest="command", required=True)

    normalize = subparsers.add_parser("normalize", help="Rewrite card names in GT")
    normalize.add_argument("--dry-run", action="store_true")
    normalize.set_defaults(func=command_normalize)

    check = subparsers.add_parser("check", help="Fail if legacy card labels remain")
    check.set_defaults(func=command_check)

    normalize_db = subparsers.add_parser("normalize-db", help="Rewrite card names in SQLite DB")
    normalize_db.add_argument(
        "--db",
        type=Path,
        required=True,
        help="SQLite match database path",
    )
    normalize_db.set_defaults(func=command_normalize_db)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
