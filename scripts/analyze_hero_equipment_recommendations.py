# -*- coding: utf-8 -*-
"""Analyze hero equipment recommendations from three-item carry/tank records."""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB_PATH = ROOT / "data" / "matches_0701.db"
JSON_PATH = ROOT / "data" / "hero_equipment_recommendations.json"
MD_PATH = ROOT / "data" / "hero_equipment_recommendations.md"


@dataclass
class RankStats:
    appearances: int = 0
    rank_sum: int = 0
    wins: int = 0
    top2: int = 0
    top4: int = 0
    selected_appearances: int = 0

    def add(self, rank: int, *, selected: bool = False) -> None:
        self.appearances += 1
        self.rank_sum += rank
        if selected:
            self.selected_appearances += 1
        if rank == 1:
            self.wins += 1
        if rank <= 2:
            self.top2 += 1
        if rank <= 4:
            self.top4 += 1

    def to_dict(
        self,
        *,
        baseline_rank: float | None = None,
        prior_weight: int = 0,
    ) -> dict[str, Any]:
        n = self.appearances or 1
        row = {
            "appearances": self.appearances,
            "avg_rank": round(self.rank_sum / n, 2),
            "win_rate": round(100.0 * self.wins / n, 1),
            "top2_rate": round(100.0 * self.top2 / n, 1),
            "top4_rate": round(100.0 * self.top4 / n, 1),
            "selected_appearances": self.selected_appearances,
            "selected_rate": round(100.0 * self.selected_appearances / n, 1),
        }
        if baseline_rank is not None and prior_weight > 0:
            adjusted = (self.rank_sum + baseline_rank * prior_weight) / (
                self.appearances + prior_weight
            )
            row["adjusted_avg_rank"] = round(adjusted, 2)
        return row


def parse_equipment_count(value: str | None) -> int:
    if not value or value == "-":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def normalize_equipment_name(equipment_name: str) -> tuple[str, bool]:
    if equipment_name.startswith("核选"):
        return equipment_name[len("核选") :], True
    return equipment_name, False


def find_bot_player_ids(conn: sqlite3.Connection) -> set[int]:
    """Return rank 7/8 player row ids when they are paired in the same match."""
    rows = conn.execute(
        """
        SELECT p7.id AS player7_id, p8.id AS player8_id
        FROM players p7
        JOIN players p8 ON p8.match_id = p7.match_id AND p8.rank = 8
        WHERE p7.rank = 7
          AND (
              p7.partner_player = 8
              OR p8.partner_player = 7
              OR EXISTS (
                  SELECT 1
                  FROM pairs pair
                  WHERE pair.match_id = p7.match_id
                    AND (
                        (pair.player_a = 7 AND pair.player_b = 8)
                        OR (pair.player_a = 8 AND pair.player_b = 7)
                    )
              )
          )
        """
    ).fetchall()
    bot_ids: set[int] = set()
    for player7_id, player8_id in rows:
        bot_ids.add(int(player7_id))
        bot_ids.add(int(player8_id))
    return bot_ids


def load_three_item_heroes(
    conn: sqlite3.Connection,
    bot_player_ids: set[int],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            h.id AS hero_id,
            h.player_id,
            h.hero_name,
            h.tier,
            h.stars,
            h.equipment_count,
            p.rank,
            he.item_index,
            he.equipment_name
        FROM heroes h
        JOIN players p ON p.id = h.player_id
        LEFT JOIN hero_equipments he ON he.hero_id = h.id
        WHERE h.hero_name != 'unknown'
        ORDER BY h.id, he.item_index
        """
    ).fetchall()

    heroes_by_id: dict[int, dict[str, Any]] = {}
    all_known_heroes = 0
    bot_excluded_heroes = 0
    non_three_item_heroes = 0

    for row in rows:
        hero_id = int(row["hero_id"])
        if hero_id not in heroes_by_id:
            equipment_count = parse_equipment_count(row["equipment_count"])
            is_bot = int(row["player_id"]) in bot_player_ids
            all_known_heroes += 1
            if is_bot:
                bot_excluded_heroes += 1
            elif equipment_count != 3:
                non_three_item_heroes += 1
            heroes_by_id[hero_id] = {
                "hero_id": hero_id,
                "player_id": int(row["player_id"]),
                "hero_name": row["hero_name"],
                "tier": row["tier"],
                "stars": row["stars"],
                "rank": int(row["rank"]),
                "equipment_count": equipment_count,
                "is_bot": is_bot,
                "equipments": [],
            }
        equipment_name = row["equipment_name"]
        if equipment_name and equipment_name != "unknown":
            normalized_name, is_selected = normalize_equipment_name(str(equipment_name))
            heroes_by_id[hero_id]["equipments"].append(
                {
                    "raw_name": str(equipment_name),
                    "name": normalized_name,
                    "is_selected": is_selected,
                }
            )

    selected = [
        hero
        for hero in heroes_by_id.values()
        if not hero["is_bot"] and hero["equipment_count"] == 3
    ]
    complete_known_sets = sum(1 for hero in selected if len(hero["equipments"]) == 3)
    incomplete_known_sets = len(selected) - complete_known_sets
    selected_equipment_records = sum(
        1
        for hero in selected
        for equipment in hero["equipments"]
        if equipment["is_selected"]
    )

    quality = {
        "known_hero_records": all_known_heroes,
        "bot_excluded_hero_records": bot_excluded_heroes,
        "non_three_item_hero_records": non_three_item_heroes,
        "selected_three_item_hero_records": len(selected),
        "complete_known_three_item_sets": complete_known_sets,
        "incomplete_known_three_item_sets": incomplete_known_sets,
        "selected_equipment_records": selected_equipment_records,
    }
    return selected, quality


def aggregate_recommendations(
    heroes: list[dict[str, Any]],
    *,
    min_hero_apps: int,
    min_item_apps: int,
    min_set_apps: int,
    prior_weight: int,
    top_n: int,
) -> list[dict[str, Any]]:
    hero_stats: dict[str, RankStats] = defaultdict(RankStats)
    item_stats: dict[tuple[str, str], RankStats] = defaultdict(RankStats)
    set_stats: dict[tuple[str, str], RankStats] = defaultdict(RankStats)
    baseline_rank = (
        sum(int(hero["rank"]) for hero in heroes) / len(heroes)
        if heroes
        else 4.5
    )

    for hero in heroes:
        hero_name = hero["hero_name"]
        rank = hero["rank"]
        hero_stats[hero_name].add(rank)
        for equipment in hero["equipments"]:
            item_stats[(hero_name, equipment["name"])].add(
                rank,
                selected=equipment["is_selected"],
            )
        if len(hero["equipments"]) == 3:
            # Equipment slot order is not meaningful; repeated item names are kept.
            set_key = " + ".join(sorted(equipment["name"] for equipment in hero["equipments"]))
            has_selected = any(equipment["is_selected"] for equipment in hero["equipments"])
            set_stats[(hero_name, set_key)].add(rank, selected=has_selected)

    by_hero_items: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (hero_name, equipment_name), stat in item_stats.items():
        if stat.appearances < min_item_apps:
            continue
        by_hero_items[hero_name].append(
            {
                "equipment_name": equipment_name,
                **stat.to_dict(
                    baseline_rank=baseline_rank,
                    prior_weight=prior_weight,
                ),
            }
        )

    by_hero_sets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (hero_name, set_key), stat in set_stats.items():
        if stat.appearances < min_set_apps:
            continue
        by_hero_sets[hero_name].append(
            {
                "equipment_set": set_key,
                "equipments": set_key.split(" + "),
                **stat.to_dict(
                    baseline_rank=baseline_rank,
                    prior_weight=prior_weight,
                ),
            }
        )

    recommendations: list[dict[str, Any]] = []
    for hero_name, stat in hero_stats.items():
        if stat.appearances < min_hero_apps:
            continue
        items = sorted(
            by_hero_items.get(hero_name, []),
            key=lambda row: (
                row["adjusted_avg_rank"],
                row["avg_rank"],
                -row["top4_rate"],
                -row["appearances"],
            ),
        )[:top_n]
        sets = sorted(
            by_hero_sets.get(hero_name, []),
            key=lambda row: (
                row["adjusted_avg_rank"],
                row["avg_rank"],
                -row["top4_rate"],
                -row["appearances"],
            ),
        )[:top_n]
        if not items and not sets:
            continue
        recommendations.append(
            {
                "hero_name": hero_name,
                "hero_stats": stat.to_dict(
                    baseline_rank=baseline_rank,
                    prior_weight=prior_weight,
                ),
                "recommended_items": items,
                "recommended_sets": sets,
            }
        )

    recommendations.sort(
        key=lambda row: (
            row["hero_stats"]["adjusted_avg_rank"],
            row["hero_stats"]["avg_rank"],
            -row["hero_stats"]["top4_rate"],
            -row["hero_stats"]["appearances"],
        )
    )
    return recommendations


def build_analysis(
    conn: sqlite3.Connection,
    *,
    min_hero_apps: int,
    min_item_apps: int,
    min_set_apps: int,
    prior_weight: int,
    top_n: int,
) -> dict[str, Any]:
    bot_player_ids = find_bot_player_ids(conn)
    total_matches = conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    seven_eight_bot_matches = len(bot_player_ids) // 2
    heroes, quality = load_three_item_heroes(conn, bot_player_ids)
    recommendations = aggregate_recommendations(
        heroes,
        min_hero_apps=min_hero_apps,
        min_item_apps=min_item_apps,
        min_set_apps=min_set_apps,
        prior_weight=prior_weight,
        top_n=top_n,
    )
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data_source": str(DB_PATH.relative_to(ROOT)).replace("\\", "/"),
        "methodology": {
            "bot_filter": "当第7名与第8名互为队友时，排除该局rank=7和rank=8玩家。",
            "hero_filter": "仅统计heroes.equipment_count解析为3的棋子。",
            "equipment_normalization": "核选装备归一为普通装备统计，并保留核选样本占比；核选视为同装备的强化版本。",
            "equipment_order": "三件套按归一后的装备名称排序后合并，装备顺序不影响组合统计。",
            "unknown_filter": "排除unknown棋子；单件装备统计排除unknown装备；三件套需3件装备都为已知。",
            "adjusted_rank": "样本修正名次=(名次总和+全局平均名次*先验权重)/(样本数+先验权重)，用于降低小样本噪声。",
            "prior_weight": prior_weight,
            "min_samples": {
                "hero": min_hero_apps,
                "hero_item": min_item_apps,
                "hero_set": min_set_apps,
            },
        },
        "summary": {
            "matches": total_matches,
            "seven_eight_bot_matches": seven_eight_bot_matches,
            "bot_player_records_excluded": len(bot_player_ids),
            **quality,
            "heroes_with_recommendations": len(recommendations),
        },
        "recommendations": recommendations,
    }


def render_report(data: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 棋子出装推荐分析")
    lines.append("")
    lines.append(f"- 生成时间: `{data['generated_at']}`")
    lines.append(f"- 数据源: `{data['data_source']}`")
    lines.append("")

    summary = data["summary"]
    lines.append("## 数据过滤摘要")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | ---: |")
    for key in (
        "matches",
        "seven_eight_bot_matches",
        "bot_player_records_excluded",
        "known_hero_records",
        "bot_excluded_hero_records",
        "non_three_item_hero_records",
        "selected_three_item_hero_records",
        "complete_known_three_item_sets",
        "incomplete_known_three_item_sets",
        "selected_equipment_records",
        "heroes_with_recommendations",
    ):
        lines.append(f"| {key} | {summary[key]} |")
    lines.append("")

    method = data["methodology"]
    lines.append("## 统计口径")
    lines.append("")
    lines.append(f"- 人机过滤：{method['bot_filter']}")
    lines.append(f"- 棋子过滤：{method['hero_filter']}")
    lines.append(f"- 核选处理：{method['equipment_normalization']}")
    lines.append(f"- 装备顺序：{method['equipment_order']}")
    lines.append(f"- unknown 过滤：{method['unknown_filter']}")
    lines.append(f"- 样本修正：{method['adjusted_rank']} 先验权重={method['prior_weight']}。")
    lines.append(
        "- 最小样本："
        f"棋子 n>={method['min_samples']['hero']}，"
        f"单件装备 n>={method['min_samples']['hero_item']}，"
        f"三件套 n>={method['min_samples']['hero_set']}。"
    )
    lines.append("")

    lines.append("## 棋子推荐")
    lines.append("")
    if not data["recommendations"]:
        lines.append("当前阈值下暂无满足样本量的推荐。")
        lines.append("")
        return "\n".join(lines)

    for row in data["recommendations"]:
        hero_stats = row["hero_stats"]
        lines.append(
            f"### {row['hero_name']} "
            f"(n={hero_stats['appearances']}, 修正={hero_stats['adjusted_avg_rank']:.2f}, "
            f"avg={hero_stats['avg_rank']:.2f}, "
            f"top4={hero_stats['top4_rate']:.1f}%)"
        )
        lines.append("")

        if row["recommended_items"]:
            lines.append("| 推荐单件 | 修正名次 | 平均名次 | 前四率 | 吃鸡率 | 样本 | 核选占比 |")
            lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
            for item in row["recommended_items"]:
                lines.append(
                    f"| {item['equipment_name']} | {item['adjusted_avg_rank']:.2f} | "
                    f"{item['avg_rank']:.2f} | "
                    f"{item['top4_rate']:.1f}% | {item['win_rate']:.1f}% | "
                    f"{item['appearances']} | {item['selected_rate']:.1f}% |"
                )
            lines.append("")

        if row["recommended_sets"]:
            lines.append("| 推荐三件套（无序） | 修正名次 | 平均名次 | 前四率 | 吃鸡率 | 样本 | 含核选占比 |")
            lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
            for item_set in row["recommended_sets"]:
                lines.append(
                    f"| {item_set['equipment_set']} | {item_set['adjusted_avg_rank']:.2f} | "
                    f"{item_set['avg_rank']:.2f} | "
                    f"{item_set['top4_rate']:.1f}% | {item_set['win_rate']:.1f}% | "
                    f"{item_set['appearances']} | {item_set['selected_rate']:.1f}% |"
                )
            lines.append("")

    return "\n".join(lines)


def export_outputs(data: dict[str, Any], json_path: Path, md_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(render_report(data), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH, help="SQLite database path")
    parser.add_argument("--json", type=Path, default=JSON_PATH, help="JSON output path")
    parser.add_argument("--md", type=Path, default=MD_PATH, help="Markdown output path")
    parser.add_argument(
        "--min-hero-apps",
        type=int,
        default=5,
        help="Minimum three-item hero records required to show a hero",
    )
    parser.add_argument(
        "--min-item-apps",
        type=int,
        default=5,
        help="Minimum hero-equipment records required to show one equipment",
    )
    parser.add_argument(
        "--min-set-apps",
        type=int,
        default=5,
        help="Minimum hero-set records required to show a three-item set",
    )
    parser.add_argument(
        "--prior-weight",
        type=int,
        default=8,
        help="Sample-size prior weight used for adjusted average rank",
    )
    parser.add_argument(
        "--top-n",
        type=int,
        default=5,
        help="Number of item and set recommendations to show per hero",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    conn = sqlite3.connect(str(args.db))
    try:
        data = build_analysis(
            conn,
            min_hero_apps=args.min_hero_apps,
            min_item_apps=args.min_item_apps,
            min_set_apps=args.min_set_apps,
            prior_weight=args.prior_weight,
            top_n=args.top_n,
        )
        export_outputs(data, args.json, args.md)
        print(f"Wrote {args.json}")
        print(f"Wrote {args.md}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
