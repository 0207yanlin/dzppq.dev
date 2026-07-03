# -*- coding: utf-8 -*-
"""Preliminary composition strength analysis for matches_0701.db."""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "data" / "matches_0701.db"


def fetchall(conn: sqlite3.Connection, sql: str, params=()):
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params)]


def hero_stats(conn: sqlite3.Connection, min_apps: int = 15):
    return fetchall(
        conn,
        """
        SELECT
            h.hero_name,
            COUNT(*) AS appearances,
            ROUND(AVG(p.rank), 2) AS avg_rank,
            ROUND(100.0 * SUM(CASE WHEN p.rank = 1 THEN 1 ELSE 0 END) / COUNT(*), 1) AS win_rate,
            ROUND(100.0 * SUM(CASE WHEN p.rank <= 2 THEN 1 ELSE 0 END) / COUNT(*), 1) AS top2_rate,
            ROUND(100.0 * SUM(CASE WHEN p.rank <= 4 THEN 1 ELSE 0 END) / COUNT(*), 1) AS top4_rate,
            ROUND(AVG(h.stars), 2) AS avg_stars
        FROM heroes h
        JOIN players p ON h.player_id = p.id
        WHERE h.hero_name != 'unknown'
        GROUP BY h.hero_name
        HAVING COUNT(*) >= ?
        ORDER BY avg_rank ASC, top4_rate DESC
        """,
        (min_apps,),
    )


def duo_stats(conn: sqlite3.Connection, min_apps: int = 8):
    return fetchall(
        conn,
        """
        WITH duo AS (
            SELECT
                m.id AS match_id,
                MIN(pa.rank, pb.rank) AS best_rank,
                AVG(pa.rank + pb.rank) / 2.0 AS avg_rank,
                GROUP_CONCAT(DISTINCT ha.hero_name) AS heroes_a,
                GROUP_CONCAT(DISTINCT hb.hero_name) AS heroes_b
            FROM pairs pr
            JOIN matches m ON m.id = pr.match_id
            JOIN players pa ON pa.match_id = m.id AND pa.row_index = pr.player_a
            JOIN players pb ON pb.match_id = m.id AND pb.row_index = pr.player_b
            JOIN heroes ha ON ha.player_id = pa.id AND ha.hero_name != 'unknown'
            JOIN heroes hb ON hb.player_id = pb.id AND hb.hero_name != 'unknown'
            GROUP BY m.id, pr.player_a, pr.player_b
        )
        SELECT
            ROUND(AVG(best_rank), 2) AS avg_best_rank,
            ROUND(AVG(avg_rank), 2) AS avg_duo_rank,
            COUNT(*) AS appearances,
            ROUND(100.0 * SUM(CASE WHEN best_rank <= 2 THEN 1 ELSE 0 END) / COUNT(*), 1) AS top2_rate,
            ROUND(100.0 * SUM(CASE WHEN best_rank <= 4 THEN 1 ELSE 0 END) / COUNT(*), 1) AS top4_rate
        FROM duo
        """,
    )


def player_compositions(conn: sqlite3.Connection):
    rows = fetchall(
        conn,
        """
        SELECT
            p.id AS player_id,
            p.rank,
            GROUP_CONCAT(h.hero_name, '|') AS comp
        FROM players p
        JOIN heroes h ON h.player_id = p.id
        WHERE h.hero_name != 'unknown'
        GROUP BY p.id
        HAVING COUNT(*) >= 3
        """,
    )
    comp_stats: dict[str, dict] = defaultdict(
        lambda: {"apps": 0, "rank_sum": 0, "top4": 0, "wins": 0}
    )
    for row in rows:
        heroes = sorted(row["comp"].split("|"))
        key = "+".join(heroes)
        stat = comp_stats[key]
        stat["apps"] += 1
        stat["rank_sum"] += row["rank"]
        if row["rank"] <= 4:
            stat["top4"] += 1
        if row["rank"] == 1:
            stat["wins"] += 1
    result = []
    for comp, stat in comp_stats.items():
        apps = stat["apps"]
        if apps < 5:
            continue
        result.append(
            {
                "composition": comp.replace("+", " + "),
                "appearances": apps,
                "avg_rank": round(stat["rank_sum"] / apps, 2),
                "top4_rate": round(100.0 * stat["top4"] / apps, 1),
                "win_rate": round(100.0 * stat["wins"] / apps, 1),
            }
        )
    result.sort(key=lambda x: (x["avg_rank"], -x["top4_rate"]))
    return result


def card_stats(conn: sqlite3.Connection, min_apps: int = 20):
    return fetchall(
        conn,
        """
        SELECT
            c.card_name,
            COUNT(*) AS appearances,
            ROUND(AVG(p.rank), 2) AS avg_rank,
            ROUND(100.0 * SUM(CASE WHEN p.rank <= 4 THEN 1 ELSE 0 END) / COUNT(*), 1) AS top4_rate
        FROM cards c
        JOIN players p ON c.player_id = p.id
        WHERE c.card_name != 'unknown'
        GROUP BY c.card_name
        HAVING COUNT(*) >= ?
        ORDER BY avg_rank ASC
        """,
        (min_apps,),
    )


def cooccurrence_top_pairs(conn: sqlite3.Connection, min_apps: int = 10):
    rows = fetchall(
        conn,
        """
        SELECT p.id AS player_id, p.rank, h.hero_name
        FROM players p
        JOIN heroes h ON h.player_id = p.id
        WHERE h.hero_name != 'unknown'
        """,
    )
    by_player: dict[int, list[str]] = defaultdict(list)
    ranks: dict[int, int] = {}
    for row in rows:
        by_player[row["player_id"]].append(row["hero_name"])
        ranks[row["player_id"]] = row["rank"]

    pair_stats: dict[tuple[str, str], dict] = defaultdict(
        lambda: {"apps": 0, "rank_sum": 0, "top4": 0}
    )
    for player_id, heroes in by_player.items():
        heroes = sorted(set(heroes))
        rank = ranks[player_id]
        for i in range(len(heroes)):
            for j in range(i + 1, len(heroes)):
                key = (heroes[i], heroes[j])
                stat = pair_stats[key]
                stat["apps"] += 1
                stat["rank_sum"] += rank
                if rank <= 4:
                    stat["top4"] += 1

    result = []
    for (a, b), stat in pair_stats.items():
        apps = stat["apps"]
        if apps < min_apps:
            continue
        result.append(
            {
                "pair": f"{a} + {b}",
                "appearances": apps,
                "avg_rank": round(stat["rank_sum"] / apps, 2),
                "top4_rate": round(100.0 * stat["top4"] / apps, 1),
            }
        )
    result.sort(key=lambda x: (x["avg_rank"], -x["top4_rate"]))
    return result


def main() -> None:
    conn = sqlite3.connect(DB_PATH)
    summary = fetchall(
        conn,
        """
        SELECT
            (SELECT COUNT(*) FROM matches) AS matches,
            (SELECT COUNT(*) FROM players) AS players,
            (SELECT COUNT(DISTINCT hero_name) FROM heroes WHERE hero_name != 'unknown') AS heroes,
            (SELECT COUNT(*) FROM heroes WHERE hero_name = 'unknown') AS unknown_heroes,
            (SELECT COUNT(*) FROM cards WHERE card_name = 'unknown') AS unknown_cards
        """,
    )[0]

    print("=== Database overview ===")
    for k, v in summary.items():
        print(f"  {k}: {v}")

    print("\n=== Rank distribution (1=best) ===")
    for row in fetchall(conn, "SELECT rank, COUNT(*) AS c FROM players GROUP BY rank ORDER BY rank"):
        print(f"  rank {row['rank']}: {row['c']} players")

    print("\n=== Strongest individual heroes (min 15 appearances) ===")
    for row in hero_stats(conn, 15)[:15]:
        print(
            f"  {row['hero_name']:8s}  avg_rank={row['avg_rank']:.2f}  "
            f"top4={row['top4_rate']:.1f}%  win={row['win_rate']:.1f}%  "
            f"n={row['appearances']}  avg_stars={row['avg_stars']}"
        )

    print("\n=== Weakest individual heroes (min 15 appearances) ===")
    weak = sorted(hero_stats(conn, 15), key=lambda x: (-x["avg_rank"], x["top4_rate"]))[:10]
    for row in weak:
        print(
            f"  {row['hero_name']:8s}  avg_rank={row['avg_rank']:.2f}  "
            f"top4={row['top4_rate']:.1f}%  win={row['win_rate']:.1f}%  n={row['appearances']}"
        )

    print("\n=== Strongest hero pairs on same board (min 10 co-appearances) ===")
    for row in cooccurrence_top_pairs(conn, 10)[:12]:
        print(
            f"  {row['pair']:20s}  avg_rank={row['avg_rank']:.2f}  "
            f"top4={row['top4_rate']:.1f}%  n={row['appearances']}"
        )

    print("\n=== Repeated full compositions (min 5 appearances) ===")
    comps = player_compositions(conn)
    if comps:
        for row in comps[:8]:
            print(
                f"  avg_rank={row['avg_rank']:.2f}  top4={row['top4_rate']:.1f}%  "
                f"win={row['win_rate']:.1f}%  n={row['appearances']}\n    {row['composition']}"
            )
    else:
        print("  (no composition repeated >=5 times)")

    print("\n=== Strongest cards (min 20 appearances) ===")
    for row in card_stats(conn, 20)[:12]:
        print(
            f"  {row['card_name']:12s}  avg_rank={row['avg_rank']:.2f}  "
            f"top4={row['top4_rate']:.1f}%  n={row['appearances']}"
        )

    conn.close()


if __name__ == "__main__":
    main()
