# -*- coding: utf-8 -*-
"""One-off migration for quality/partner support card label renames."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPLACEMENTS = {
    "重质也重量pro": "蓝·重质也重量pro",
    "拍档支援": "蓝·拍档支援",
}


def migrate_db(db_path: Path) -> dict[str, int]:
    conn = sqlite3.connect(str(db_path))
    counts: dict[str, int] = {}
    for old, new in REPLACEMENTS.items():
        cur = conn.execute(
            "UPDATE cards SET card_name = ? WHERE card_name = ?",
            (new, old),
        )
        counts[old] = cur.rowcount
    conn.commit()
    conn.close()
    return counts


def migrate_gt(gt_path: Path) -> dict[str, int]:
    data = json.loads(gt_path.read_text(encoding="utf-8"))
    counts = {old: 0 for old in REPLACEMENTS}
    for entry in data.get("screenshots", {}).values():
        for player in entry.get("players", []):
            for card in player.get("cards", []):
                for key in ("card_name", "label"):
                    value = card.get(key)
                    if value in REPLACEMENTS:
                        card[key] = REPLACEMENTS[value]
                        counts[value] += 1
    gt_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return counts


def main() -> None:
    db_counts = migrate_db(ROOT / "data" / "matches_0701.db")
    gt_counts = migrate_gt(ROOT / "data" / "match_ground_truth.json")
    print("DB updates:", db_counts)
    print("GT updates:", gt_counts)


if __name__ == "__main__":
    main()
