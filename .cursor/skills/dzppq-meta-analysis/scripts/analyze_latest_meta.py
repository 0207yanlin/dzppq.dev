# -*- coding: utf-8 -*-
"""Generate a rebuilt DZPPQ meta report from the latest match database."""

from __future__ import annotations

import argparse
import html
import itertools
import json
import sqlite3
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[4]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.card_rules import normalize_card_label, resolve_card_label, split_card_prefix  # noqa: E402

DEFAULT_JSON = ROOT / "data" / "latest_meta_analysis.json"
DEFAULT_MD = ROOT / "data" / "latest_meta_analysis_report.md"
DEFAULT_HTML = ROOT / "data" / "latest_meta_analysis_report.html"
DEFAULT_XLSX = ROOT / "data" / "latest_meta_analysis_equipment.xlsx"
CARD_HTML_SUFFIXES = {
    "彩": "cai",
    "黄": "yellow",
    "蓝": "blue",
    "白": "white",
}
DEFAULT_CARD_HTML_PATHS = {
    prefix: ROOT / "data" / f"latest_meta_analysis_cards_{suffix}.html"
    for prefix, suffix in CARD_HTML_SUFFIXES.items()
}
DEFAULT_DUO_HTML = ROOT / "data" / "latest_meta_analysis_duo_compositions.html"
DEFAULT_LOW_COST_HTML = ROOT / "data" / "latest_meta_analysis_low_cost_carries.html"
DEFAULT_COMP_RECOMMENDATIONS_HTML = ROOT / "data" / "latest_meta_analysis_compositions.html"
DEFAULT_JIUJIU_COMPS_HTML = ROOT / "data" / "latest_meta_analysis_jiujiu_comps.html"
DEFAULT_JIUJIU_WEARERS_HTML = ROOT / "data" / "latest_meta_analysis_jiujiu_wearers.html"
DEFAULT_EQUIPMENT_HTML = ROOT / "data" / "latest_meta_analysis_equipment.html"
DEFAULT_TRAP_COMPOSITIONS_HTML = ROOT / "data" / "latest_meta_analysis_trap_compositions.html"
CARD_TEMPLATE_DIR = ROOT / "assets" / "templates" / "cards"
MERGED_TEMPLATE_EXPANSIONS: dict[str, list[str]] = {
    "黄·吸吸宝pro快速成型": ["黄·快速成型", "黄·吸吸宝pro"],
    "蓝·重质拍档支援": ["蓝·拍档支援", "蓝·重质也重量pro"],
}
LEGACY_CARD_TEMPLATE_NAMES = frozenset(
    {
        "法力专注",
        "蓝·开攒",
        "蓝·大亨",
        "蓝·一起刷刷刷",
        "蓝·天降啾啾pro",
    }
)

CARD_GRANTED_HEROES = {"暴龙虾饺"}
PLAY_STYLES = ("赌狗", "高费")
CARD_PREFIX_TYPES = ("彩", "黄", "蓝", "白", "其他")
CARD_MERGE_NOTES: dict[str, str] = {
    "蓝": (
        "以下卡牌因图标完全相同做了合并处理："
        "福袋，有钱同享 -> 福袋有钱；"
        "最佳拍档，最强支援 -> 拍档支援；"
        "最后的波纹，利己主义 -> 波纹利己；"
        "开攒，大亨 -> 开攒大亨；"
        "天降啾啾pro，一起刷刷刷 -> 一起刷刷刷+天降啾啾pro。"
    ),
    "彩": (
        "以下卡牌因图标完全相同做了合并处理："
        "法师礼包，战士礼包，射手礼包 -> 法师战士射手礼包。"
    ),
    "黄": (
        "以下卡牌因图标完全相同做了合并处理："
        "大力，巫术，守护 -> 大力巫术守护。"
    ),
}

HERO_ALIASES = {
    "双面教师林野·前排": "双面教师林野",
    "双面教师林野·后排": "双面教师林野",
}


@dataclass
class Equipment:
    raw_name: str
    name: str
    selected: bool


@dataclass
class Hero:
    id: int
    name: str
    canonical_name: str
    slot_index: int
    tier: int | None
    stars: int
    equipment_count: int
    equipments: list[Equipment] = field(default_factory=list)
    traits: list[str] = field(default_factory=list)
    carry_score: float = 0.0

    @property
    def selected_equipment_count(self) -> int:
        return sum(1 for equipment in self.equipments if equipment.selected)


@dataclass
class PlayerFeature:
    player_id: int
    match_id: int
    rank: int
    row_index: int
    partner_player: int | None
    heroes: list[Hero]
    cards: list[str]
    trait_counts: Counter[str]
    jiujiu_bonus: Counter[str]
    trait_totals: Counter[str]
    active_traits: dict[str, int]
    main_bond: str | None
    main_carry: Hero | None
    secondary_carry: Hero | None
    hero_set: set[str]
    level: int
    carry_candidates: list[Hero] = field(default_factory=list)
    family_id: int | None = None
    team_rank: int | None = None
    team_best_rank: int | None = None


@dataclass
class RankStats:
    appearances: int = 0
    rank_sum: int = 0
    wins: int = 0
    top4: int = 0

    def add(self, rank: int) -> None:
        self.appearances += 1
        self.rank_sum += rank
        if rank == 1:
            self.wins += 1
        if rank <= 4:
            self.top4 += 1

    def to_dict(self, baseline_rank: float | None = None, prior: int = 0) -> dict[str, Any]:
        n = max(self.appearances, 1)
        row = {
            "appearances": self.appearances,
            "avg_rank": round(self.rank_sum / n, 2),
            "win_rate": round(self.wins * 100.0 / n, 1),
            "top4_rate": round(self.top4 * 100.0 / n, 1),
        }
        if baseline_rank is not None and prior > 0:
            adjusted = (self.rank_sum + baseline_rank * prior) / (self.appearances + prior)
            row["adjusted_avg_rank"] = round(adjusted, 2)
        return row


def load_game_config() -> tuple[dict[str, list[Any]], dict[str, list[int]]]:
    from config_s2 import dict_bond, dict_character

    return dict_character, dict_bond


def rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def parse_equipment_count(value: Any) -> int:
    if value is None or value == "-":
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def normalize_hero_name(name: str) -> str:
    return HERO_ALIASES.get(name, name)


def normalize_equipment_name(name: str) -> tuple[str, bool]:
    if name.startswith("核选"):
        return name[len("核选") :], True
    return name, False


def card_prefix_type(card_name: str) -> str:
    prefix, _ = split_card_prefix(card_name)
    if prefix:
        return prefix
    return "其他"


def load_report_card_catalog() -> dict[str, list[str]]:
    by_prefix: dict[str, set[str]] = {
        prefix: set() for prefix in CARD_PREFIX_TYPES if prefix != "其他"
    }
    for path in sorted(CARD_TEMPLATE_DIR.glob("*.jpg")):
        if path.name.startswith("player"):
            continue
        raw_name = path.stem
        if raw_name in LEGACY_CARD_TEMPLATE_NAMES:
            continue
        canonical = normalize_card_label(raw_name)
        expansions = MERGED_TEMPLATE_EXPANSIONS.get(raw_name) or MERGED_TEMPLATE_EXPANSIONS.get(
            canonical
        )
        names = expansions if expansions else [canonical]
        for name in names:
            prefix = card_prefix_type(name)
            if prefix in by_prefix:
                by_prefix[prefix].add(name)
    return {prefix: sorted(names) for prefix, names in by_prefix.items()}


def empty_card_row(key: str) -> dict[str, Any]:
    return {
        "key": key,
        "appearances": 0,
        "avg_rank": None,
        "win_rate": None,
        "top4_rate": None,
        "adjusted_avg_rank": None,
    }


def aggregate_key_stats(items: list[tuple[str, int]], min_apps: int, baseline: float) -> list[dict[str, Any]]:
    stats: dict[str, RankStats] = defaultdict(RankStats)
    for key, rank in items:
        if key:
            stats[key].add(rank)
    rows = []
    for key, stat in stats.items():
        if stat.appearances >= min_apps:
            rows.append({"key": key, **stat.to_dict(baseline_rank=baseline, prior=8)})
    rows.sort(key=lambda row: (row["adjusted_avg_rank"], row["avg_rank"], -row["top4_rate"]))
    return rows


def aggregate_single_cards_by_catalog(
    items: list[tuple[str, int]],
    baseline: float,
    catalog: dict[str, list[str]],
    *,
    sample_first: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    stats: dict[str, RankStats] = defaultdict(RankStats)
    for key, rank in items:
        if key:
            stats[key].add(rank)

    annotated: list[dict[str, Any]] = []
    by_prefix: dict[str, list[dict[str, Any]]] = {}
    for prefix_type in CARD_PREFIX_TYPES:
        if prefix_type == "其他":
            continue
        group_rows: list[dict[str, Any]] = []
        for key in catalog.get(prefix_type, []):
            stat = stats.get(key)
            if stat and stat.appearances > 0:
                group_rows.append({"key": key, **stat.to_dict(baseline_rank=baseline, prior=8)})
            else:
                group_rows.append(empty_card_row(key))

        with_data = [row for row in group_rows if row["appearances"] > 0]
        without_data = [row for row in group_rows if row["appearances"] == 0]
        if sample_first:
            with_data.sort(
                key=lambda row: (
                    -row["appearances"],
                    row["adjusted_avg_rank"],
                    row["avg_rank"],
                    -row["top4_rate"],
                )
            )
        else:
            with_data.sort(
                key=lambda row: (row["adjusted_avg_rank"], row["avg_rank"], -row["top4_rate"])
            )
        without_data.sort(key=lambda row: row["key"])
        ordered_rows = with_data + without_data

        ranked_rows: list[dict[str, Any]] = []
        rank = 1
        for row in ordered_rows:
            ranked_row = {
                **row,
                "prefix_type": prefix_type,
                "prefix_rank": rank if row["appearances"] > 0 else None,
            }
            if row["appearances"] > 0:
                rank += 1
            ranked_rows.append(ranked_row)
            annotated.append(ranked_row)
        if ranked_rows:
            by_prefix[prefix_type] = ranked_rows
    return annotated, by_prefix


def aggregate_key_stats_by_prefix(
    items: list[tuple[str, int]],
    min_apps: int,
    baseline: float,
    *,
    sample_first: bool = False,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    rows = aggregate_key_stats(items, min_apps, baseline)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[card_prefix_type(row["key"])].append(row)

    annotated: list[dict[str, Any]] = []
    by_prefix: dict[str, list[dict[str, Any]]] = {}
    for prefix_type in CARD_PREFIX_TYPES:
        group_rows = grouped.get(prefix_type, [])
        if sample_first:
            group_rows.sort(
                key=lambda row: (
                    -row["appearances"],
                    row["adjusted_avg_rank"],
                    row["avg_rank"],
                    -row["top4_rate"],
                )
            )
        else:
            group_rows.sort(
                key=lambda row: (row["adjusted_avg_rank"], row["avg_rank"], -row["top4_rate"])
            )
        ranked_rows: list[dict[str, Any]] = []
        for rank, row in enumerate(group_rows, start=1):
            ranked_row = {**row, "prefix_type": prefix_type, "prefix_rank": rank}
            ranked_rows.append(ranked_row)
            annotated.append(ranked_row)
        if ranked_rows:
            by_prefix[prefix_type] = ranked_rows
    return annotated, by_prefix


def add_avg_appearances_per_match(
    rows: list[dict[str, Any]],
    total_matches: int,
) -> list[dict[str, Any]]:
    denominator = max(total_matches, 1)
    for row in rows:
        row["avg_appearances_per_match"] = round(row["appearances"] / denominator, 2)
    return rows


def add_avg_appearances_to_prefix_groups(
    groups: dict[str, list[dict[str, Any]]],
    total_matches: int,
) -> dict[str, list[dict[str, Any]]]:
    for rows in groups.values():
        add_avg_appearances_per_match(rows, total_matches)
    return groups


def jiujiu_trait(equipment_name: str) -> str | None:
    normalized, _ = normalize_equipment_name(equipment_name)
    if normalized.endswith("啾啾"):
        return normalized[: -len("啾啾")]
    return None


def active_tier(count: int, thresholds: list[int]) -> int:
    tier = 0
    for threshold in sorted(thresholds):
        if count >= threshold:
            tier = threshold
    return tier


def confidence_label(n: int, unknown_rate: float = 0.0) -> str:
    if n >= 30 and unknown_rate <= 0.03:
        return "高"
    if n >= 10 and unknown_rate <= 0.08:
        return "中"
    return "低"


def level_label(level: int) -> int:
    if level >= 9:
        return 9
    if level >= 8:
        return 8
    return 7


def is_lineup_hero(hero_name: str) -> bool:
    return hero_name not in CARD_GRANTED_HEROES


def first_card(feature: PlayerFeature) -> str | None:
    return feature.cards[0] if feature.cards else None


def team_rank_value(feature: PlayerFeature) -> int:
    return feature.team_rank if feature.team_rank is not None else feature.rank


def is_low_cost_hero(hero: Hero | None) -> bool:
    return bool(hero and hero.tier is not None and hero.tier <= 3)


def is_low_cost_three_star(hero: Hero) -> bool:
    return is_low_cost_hero(hero) and hero.stars >= 3 and is_lineup_hero(hero.name)


def classify_play_style(feature: PlayerFeature) -> str:
    lineup_count = len(unique_heroes_by_slot(feature))
    main_carry = feature.main_carry
    has_low_cost_three_star = any(is_low_cost_three_star(hero) for hero in feature.heroes)
    has_low_cost_three_star_main = bool(
        main_carry and is_low_cost_three_star(main_carry)
    )

    if lineup_count <= 6:
        return "赌狗"
    if feature.level >= 8 and not has_low_cost_three_star:
        return "高费"
    if feature.level == 7:
        return "赌狗" if is_low_cost_hero(main_carry) else "高费"
    if has_low_cost_three_star_main:
        return "赌狗"
    return "高费"


def three_star_lineup_count(feature: PlayerFeature) -> int:
    return len(
        {
            hero.name
            for hero in feature.heroes
            if is_lineup_hero(hero.name) and hero.stars >= 3
        }
    )


def play_style_summary(members: list[PlayerFeature]) -> tuple[str, list[dict[str, Any]]]:
    counts = Counter(classify_play_style(member) for member in members)
    total = len(members) or 1
    breakdown = [
        {
            "play_style": style,
            "appearances": counts.get(style, 0),
            "share": round(counts.get(style, 0) * 100.0 / total, 1),
        }
        for style in PLAY_STYLES
        if counts.get(style, 0) > 0
    ]
    primary = max(PLAY_STYLES, key=lambda style: (counts.get(style, 0), -PLAY_STYLES.index(style)))
    return primary, breakdown


def find_latest_db(explicit: Path | None) -> Path:
    if explicit is not None:
        db_path = explicit if explicit.is_absolute() else ROOT / explicit
        if not db_path.exists():
            raise SystemExit(f"DB not found: {db_path}")
        return db_path

    candidates = sorted(
        (ROOT / "data").glob("matches_*.db"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        raise SystemExit(
            "No data/matches_*.db found. Build or provide the latest DB with --db."
        )
    return candidates[0]


def find_bot_player_ids(conn: sqlite3.Connection) -> set[int]:
    rows = conn.execute(
        """
        SELECT p7.id AS p7_id, p8.id AS p8_id
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
    for row in rows:
        bot_ids.add(int(row["p7_id"]))
        bot_ids.add(int(row["p8_id"]))
    return bot_ids


def db_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def data_quality(conn: sqlite3.Connection, bot_ids: set[int]) -> dict[str, Any]:
    unknown_heroes = conn.execute(
        "SELECT COUNT(*) FROM heroes WHERE hero_name = 'unknown'"
    ).fetchone()[0]
    unknown_cards = conn.execute(
        "SELECT COUNT(*) FROM cards WHERE card_name = 'unknown'"
    ).fetchone()[0]
    unknown_equipment = conn.execute(
        "SELECT COUNT(*) FROM hero_equipments WHERE equipment_name = 'unknown'"
    ).fetchone()[0]
    card_granted_heroes = conn.execute(
        "SELECT COUNT(*) FROM heroes WHERE hero_name IN ({})".format(
            ",".join("?" for _ in CARD_GRANTED_HEROES)
        ),
        tuple(CARD_GRANTED_HEROES),
    ).fetchone()[0]
    return {
        "matches": db_count(conn, "matches"),
        "players": db_count(conn, "players"),
        "heroes": db_count(conn, "heroes"),
        "hero_equipments": db_count(conn, "hero_equipments"),
        "cards": db_count(conn, "cards"),
        "unknown_heroes": int(unknown_heroes),
        "unknown_cards": int(unknown_cards),
        "unknown_equipment": int(unknown_equipment),
        "card_granted_heroes": int(card_granted_heroes),
        "bot_player_records_excluded": len(bot_ids),
        "seven_eight_bot_matches": len(bot_ids) // 2,
    }


def validate_config(
    conn: sqlite3.Connection,
    dict_character: dict[str, list[Any]],
    dict_bond: dict[str, list[int]],
) -> dict[str, Any]:
    db_heroes = [
        row[0]
        for row in conn.execute(
            "SELECT DISTINCT hero_name FROM heroes WHERE hero_name != 'unknown'"
        )
    ]
    missing = [
        hero
        for hero in sorted(db_heroes)
        if normalize_hero_name(hero) not in dict_character
        and hero not in CARD_GRANTED_HEROES
    ]
    jiujiu_items = [
        row[0]
        for row in conn.execute(
            """
            SELECT DISTINCT equipment_name
            FROM hero_equipments
            WHERE equipment_name LIKE '%啾啾%' AND equipment_name != 'unknown'
            """
        )
    ]
    unmapped_jiujiu = [
        name for name in jiujiu_items if jiujiu_trait(name) not in dict_bond
    ]
    return {
        "db_hero_count": len(db_heroes),
        "missing_config_heroes": missing,
        "card_granted_heroes": sorted(CARD_GRANTED_HEROES & set(db_heroes)),
        "config_heroes_not_seen": sorted(
            name
            for name in dict_character
            if name not in {normalize_hero_name(hero) for hero in db_heroes}
        ),
        "jiujiu_equipment_seen": sorted(jiujiu_items),
        "jiujiu_unmapped": sorted(unmapped_jiujiu),
    }


def load_player_features(
    conn: sqlite3.Connection,
    bot_ids: set[int],
    dict_character: dict[str, list[Any]],
    dict_bond: dict[str, list[int]],
) -> list[PlayerFeature]:
    conn.row_factory = sqlite3.Row
    player_rows = conn.execute("SELECT * FROM players ORDER BY match_id, rank").fetchall()
    kept_player_ids = {int(row["id"]) for row in player_rows if int(row["id"]) not in bot_ids}

    heroes_by_player: dict[int, list[Hero]] = defaultdict(list)
    hero_by_id: dict[int, Hero] = {}
    hero_rows = conn.execute(
        """
        SELECT h.*, he.equipment_name
        FROM heroes h
        LEFT JOIN hero_equipments he ON he.hero_id = h.id
        WHERE h.player_id IN ({})
        ORDER BY h.player_id, h.slot_index, he.item_index
        """.format(",".join("?" for _ in kept_player_ids) or "NULL"),
        tuple(kept_player_ids),
    ).fetchall()

    for row in hero_rows:
        player_id = int(row["player_id"])
        raw_name = str(row["hero_name"])
        if raw_name == "unknown":
            continue
        hero_id = int(row["id"])
        if hero_id not in hero_by_id:
            canonical = normalize_hero_name(raw_name)
            config_entry = dict_character.get(canonical)
            config_tier = int(config_entry[0]) if config_entry else row["tier"]
            traits = [str(trait) for trait in config_entry[1:]] if config_entry else []
            hero = Hero(
                id=hero_id,
                name=raw_name,
                canonical_name=canonical,
                slot_index=int(row["slot_index"]),
                tier=int(config_tier) if config_tier is not None else None,
                stars=int(row["stars"] or 0),
                equipment_count=parse_equipment_count(row["equipment_count"]),
                traits=traits,
            )
            hero_by_id[hero_id] = hero
            heroes_by_player[player_id].append(hero)
        equipment_name = row["equipment_name"]
        if equipment_name and equipment_name != "unknown":
            normalized_name, selected = normalize_equipment_name(str(equipment_name))
            hero_by_id[hero_id].equipments.append(
                Equipment(raw_name=str(equipment_name), name=normalized_name, selected=selected)
            )

    cards_by_player: dict[int, list[str]] = defaultdict(list)
    card_rows = conn.execute(
        """
        SELECT player_id, card_name, slot_index
        FROM cards
        WHERE player_id IN ({})
        ORDER BY player_id, slot_index
        """.format(",".join("?" for _ in kept_player_ids) or "NULL"),
        tuple(kept_player_ids),
    ).fetchall()
    for row in card_rows:
        card_name = str(row["card_name"])
        if card_name != "unknown":
            player_id = int(row["player_id"])
            slot_index = int(row["slot_index"])
            hero_context = [
                {"stars": hero.stars} for hero in heroes_by_player.get(player_id, [])
            ]
            resolved_name = resolve_card_label(card_name, slot_index, hero_context)
            cards_by_player[player_id].append(resolved_name)

    features: list[PlayerFeature] = []
    for player in player_rows:
        player_id = int(player["id"])
        if player_id in bot_ids:
            continue
        heroes = heroes_by_player.get(player_id, [])
        for hero in heroes:
            tier_score = hero.tier or 0
            hero.carry_score = (
                hero.equipment_count * 30
                + hero.selected_equipment_count * 12
                + hero.stars * 10
                + tier_score * 2
                + max(0, 8 - hero.slot_index) * 1.5
            )

        trait_counts: Counter[str] = Counter()
        jiujiu_bonus: Counter[str] = Counter()
        for hero in heroes:
            for trait in hero.traits:
                if trait in dict_bond:
                    trait_counts[trait] += 1
            for equipment in hero.equipments:
                trait = jiujiu_trait(equipment.raw_name)
                if trait in dict_bond:
                    jiujiu_bonus[trait] += 1
        trait_totals = trait_counts + jiujiu_bonus
        active_traits = {
            trait: active_tier(count, dict_bond[trait])
            for trait, count in trait_totals.items()
            if trait in dict_bond and active_tier(count, dict_bond[trait]) > 0
        }
        main_bond = None
        if active_traits:
            main_bond = max(
                active_traits,
                key=lambda trait: (active_traits[trait], trait_totals[trait], trait),
            )
        carries = sorted(
            heroes,
            key=lambda hero: (hero.carry_score, -hero.slot_index, hero.name),
            reverse=True,
        )
        carry_candidates = carries[:3]
        features.append(
            PlayerFeature(
                player_id=player_id,
                match_id=int(player["match_id"]),
                rank=int(player["rank"]),
                row_index=int(player["row_index"]),
                partner_player=player["partner_player"],
                heroes=heroes,
                cards=cards_by_player.get(player_id, []),
                trait_counts=trait_counts,
                jiujiu_bonus=jiujiu_bonus,
                trait_totals=trait_totals,
                active_traits=active_traits,
                main_bond=main_bond,
                main_carry=carry_candidates[0] if carry_candidates else None,
                secondary_carry=carry_candidates[1] if len(carry_candidates) > 1 else None,
                carry_candidates=carry_candidates,
                hero_set={hero.name for hero in heroes if is_lineup_hero(hero.name)},
                level=level_label(sum(1 for hero in heroes if is_lineup_hero(hero.name))),
            )
        )
    assign_team_ranks(features)
    return features


def assign_team_ranks(features: list[PlayerFeature]) -> None:
    by_match_rank = {
        (feature.match_id, feature.rank): feature
        for feature in features
    }
    features_by_match: dict[int, list[PlayerFeature]] = defaultdict(list)
    for feature in features:
        features_by_match[feature.match_id].append(feature)

    for match_id, match_features in features_by_match.items():
        seen: set[int] = set()
        teams: list[list[PlayerFeature]] = []
        for feature in sorted(match_features, key=lambda item: item.rank):
            if feature.player_id in seen:
                continue
            members = [feature]
            seen.add(feature.player_id)
            if feature.partner_player is not None:
                partner = by_match_rank.get((match_id, int(feature.partner_player)))
                if partner is not None and partner.player_id not in seen:
                    members.append(partner)
                    seen.add(partner.player_id)
            teams.append(members)

        teams.sort(key=lambda members: min(member.rank for member in members))
        for team_rank, members in enumerate(teams, start=1):
            team_best_rank = min(member.rank for member in members)
            for member in members:
                member.team_rank = team_rank
                member.team_best_rank = team_best_rank


def jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def is_similar_to_family(feature: PlayerFeature, members: list[PlayerFeature]) -> bool:
    representative = members[0]
    shared_carry = False
    if feature.main_carry and representative.main_carry:
        family_carries = {
            member.main_carry.name
            for member in members
            if member.main_carry is not None
        }
        shared_carry = feature.main_carry.name in family_carries
    if jaccard(feature.hero_set, representative.hero_set) >= 0.55:
        return True
    return bool(feature.main_bond and feature.main_bond == representative.main_bond and shared_carry)


def parse_trait_tier(key: str) -> tuple[str, int]:
    trait, tier_raw = key.rsplit("-", 1)
    return trait, int(tier_raw)


def carry_trait_names(main_carries: list[tuple[str, int]], members: list[PlayerFeature]) -> set[str]:
    names = {name for name, _ in main_carries[:3]}
    traits: set[str] = set()
    for member in members:
        for hero in member.heroes:
            if hero.name in names:
                traits.update(hero.traits)
    return traits


def derive_family_label(
    members: list[PlayerFeature],
    active_bond_counter: Counter[str],
    main_carries: list[tuple[str, int]],
) -> dict[str, Any]:
    _, dict_bond = load_game_config()
    total = len(members) or 1
    carry_traits = carry_trait_names(main_carries, members)
    candidates = []
    high_tier_member_count = 0
    for member in members:
        if any(
            tier > min(dict_bond.get(trait, [tier]))
            for trait, tier in member.active_traits.items()
            if trait in dict_bond
        ):
            high_tier_member_count += 1

    mostly_first_tier = high_tier_member_count / total < 0.5
    for key, count in active_bond_counter.items():
        trait, tier = parse_trait_tier(key)
        thresholds = dict_bond.get(trait)
        if not thresholds:
            continue
        share = count * 100.0 / total
        if share < 50:
            continue
        first_threshold = min(thresholds)
        second_threshold = sorted(thresholds)[1] if len(thresholds) > 1 else first_threshold
        carry_bonus = 30 if trait in carry_traits else 0
        score = tier * 100 + share + carry_bonus
        candidates.append(
            {
                "key": key,
                "trait": trait,
                "tier": tier,
                "share": round(share, 1),
                "is_first_tier": tier <= first_threshold,
                "carry_aligned": trait in carry_traits,
                "score": score,
                "is_high_tier": tier >= second_threshold,
            }
        )

    if not candidates:
        return {
            "key": "拼多多",
            "label_trait": "拼多多",
            "label_confidence": "低",
            "label_reason": "没有稳定占比足够的主羁绊",
        }

    candidates.sort(key=lambda item: (item["score"], item["share"]), reverse=True)
    best = candidates[0]
    if mostly_first_tier and not (best["carry_aligned"] and best["share"] >= 65):
        return {
            "key": "拼多多",
            "label_trait": "拼多多",
            "label_confidence": "中",
            "label_reason": "激活羁绊主要停留在第一档，按拼多多处理",
        }

    return {
        "key": best["key"],
        "label_trait": best["trait"],
        "label_confidence": "高" if best["share"] >= 60 else "中",
        "label_reason": "主C羁绊主导" if best["carry_aligned"] else "家族稳定高占比羁绊",
        "label_share": best["share"],
    }


def composition_recommendation_score(row: dict[str, Any]) -> float:
    stats = row["stats"]
    n = stats["appearances"]
    score = stats["avg_rank"]
    score -= min(n, 80) / 80.0 * 0.35
    score -= stats["top4_rate"] / 100.0 * 0.25
    score -= row["popularity"]["match_share"] / 100.0 * 0.15
    if n < 10:
        score += 1.8
    elif n < 15:
        score += 0.8
    elif n < 25:
        score += 0.25
    if row.get("high_cost_three_star_dependency"):
        score += 0.45
    return round(score, 4)


def overall_strength_score(row: dict[str, Any]) -> float:
    stats = row["stats"]
    difficulty_score = row["difficulty"].get("score", 0.5)
    n = stats["appearances"]
    score = stats["avg_rank"]
    score -= stats["top4_rate"] / 100.0 * 0.45
    score -= stats["win_rate"] / 100.0 * 0.2
    score += difficulty_score * 0.75
    score -= min(n, 80) / 80.0 * 0.25
    if n < 10:
        score += 1.2
    elif n < 20:
        score += 0.4
    return round(score, 4)


def build_composition_row(
    members: list[PlayerFeature],
    family_id: int,
    total_players: int,
    total_matches: int,
    *,
    is_subfamily: bool = False,
    subfamily_key: str | None = None,
) -> dict[str, Any]:
    stats = RankStats()
    hero_counter: Counter[str] = Counter()
    carry_counter: Counter[str] = Counter()
    carry_score_sums: dict[str, float] = defaultdict(float)
    carry_score_counts: Counter[str] = Counter()
    active_bond_counter: Counter[str] = Counter()
    for member in members:
        stats.add(member.rank)
        hero_counter.update(member.hero_set)
        seen_carries: set[str] = set()
        for hero in member.carry_candidates[:3]:
            if hero.name in seen_carries:
                continue
            seen_carries.add(hero.name)
            carry_counter[hero.name] += 1
            carry_score_sums[hero.name] += hero.carry_score
            carry_score_counts[hero.name] += 1
        for trait, tier in member.active_traits.items():
            active_bond_counter[f"{trait}-{tier}"] += 1

    match_counts = Counter(member.match_id for member in members)
    avg_contest = sum(match_counts.values()) / len(match_counts)
    unfinished = 0
    carry_complete = 0
    for member in members:
        carry = member.main_carry
        if carry and carry.equipment_count >= 3:
            carry_complete += 1
        if member.rank > 4 and carry and (carry.equipment_count < 3 or carry.stars < 2):
            unfinished += 1

    unfinished_rate = unfinished * 100.0 / len(members)
    carry_complete_rate = carry_complete * 100.0 / len(members)
    three_star_counts = [three_star_lineup_count(member) for member in members]
    top4_three_star_counts = [
        three_star_lineup_count(member) for member in members if member.rank <= 4
    ]
    difficulty_score = (
        (unfinished_rate / 100.0) * 0.5
        + min(avg_contest / 3.0, 1.0) * 0.3
        + (1.0 - carry_complete_rate / 100.0) * 0.2
    )
    difficulty = "高" if difficulty_score >= 0.58 else "中" if difficulty_score >= 0.34 else "低"
    pick_rate = len(members) * 100.0 / total_players
    match_share = len(match_counts) * 100.0 / total_matches
    popularity_score = pick_rate / 20.0 + avg_contest / 3.0 + match_share / 80.0
    popularity = "高" if popularity_score >= 1.5 else "中" if popularity_score >= 0.8 else "低"

    top_bonds = active_bond_counter.most_common(8)
    main_carries = carry_counter.most_common(3)
    label_info = derive_family_label(members, active_bond_counter, main_carries)
    main_bond = subfamily_key or label_info["key"]
    variants = build_level_variants(members, hero_counter, main_bond=main_bond)
    if subfamily_key:
        label_info = {
            **label_info,
            "key": subfamily_key,
            "label_trait": parse_trait_tier(subfamily_key)[0],
            "label_confidence": "高",
            "label_reason": "样本充足的高档羁绊子形态",
        }
    if main_bond != "拼多多" and main_bond not in {key for key, _ in top_bonds}:
        top_bonds.append((main_bond, sum(1 for member in members if main_bond in {
            f"{trait}-{tier}" for trait, tier in member.active_traits.items()
        })))
    common_bonds = [
        {"bond": bond, "share": round(count * 100.0 / len(members), 1)}
        for bond, count in top_bonds[:8]
        if count > 0
    ]
    play_style, play_style_breakdown = play_style_summary(members)
    carry_requirements = summarize_carry_requirements(members, main_carries)
    carry_equipment_notes = summarize_comp_carry_equipment(members, main_carries)
    jiujiu_requirements = analyze_comp_jiujiu_dependency(members, main_bond)
    high_cost_three_star_dependency = any(
        row.get("high_cost_three_star_dependency") for row in carry_requirements
    )
    carry_label = "+".join(name for name, _ in main_carries[:2]) or "无核心"
    row = {
        "family_id": family_id,
        "label": f"{main_bond} / {carry_label}",
        "main_bond": main_bond,
        "is_subfamily": is_subfamily,
        "subfamily_key": subfamily_key,
        "label_confidence": label_info.get("label_confidence", "中"),
        "label_reason": label_info.get("label_reason", ""),
        "main_carries": [
            {
                "hero_name": name,
                "share": round(count * 100.0 / len(members), 1),
                "carry_rank": rank,
                "avg_carry_score": round(
                    carry_score_sums[name] / max(carry_score_counts[name], 1),
                    1,
                ),
            }
            for rank, (name, count) in enumerate(main_carries, start=1)
        ],
        "core_heroes": [
            {"hero_name": name, "share": round(count * 100.0 / len(members), 1)}
            for name, count in hero_counter.most_common(10)
        ],
        "common_bonds": common_bonds,
        "play_style": play_style,
        "play_style_breakdown": play_style_breakdown,
        "variants": variants,
        "carry_requirements": carry_requirements,
        "carry_equipment_notes": carry_equipment_notes,
        "jiujiu_requirements": jiujiu_requirements,
        "stats": stats.to_dict(),
        "difficulty": {
            "label": difficulty,
            "score": round(difficulty_score, 3),
            "unfinished_bottom_rate": round(unfinished_rate, 1),
            "carry_complete_rate": round(carry_complete_rate, 1),
            "avg_same_match_contest": round(avg_contest, 2),
            "avg_family_contest": round(avg_contest, 2),
            "avg_three_star_units": round(avg_number(three_star_counts) or 0.0, 2),
            "avg_top4_three_star_units": round(
                avg_number(top4_three_star_counts) or avg_number(three_star_counts) or 0.0,
                2,
            ),
        },
        "popularity": {
            "label": popularity,
            "score": round(popularity_score, 3),
            "pick_rate": round(pick_rate, 1),
            "match_share": round(match_share, 1),
            "avg_same_match_contest": round(avg_contest, 2),
            "avg_family_contest": round(avg_contest, 2),
        },
        "confidence": confidence_label(len(members)),
        "member_player_ids": [member.player_id for member in members],
        "high_cost_three_star_dependency": high_cost_three_star_dependency,
    }
    row["recommendation_score"] = composition_recommendation_score(row)
    row["overall_strength_score"] = overall_strength_score(row)
    return row


def high_tier_subgroups(
    members: list[PlayerFeature],
    min_apps: int,
) -> list[tuple[str, list[PlayerFeature]]]:
    _, dict_bond = load_game_config()
    by_key: dict[str, list[PlayerFeature]] = defaultdict(list)
    for member in members:
        for trait, tier in member.active_traits.items():
            thresholds = sorted(dict_bond.get(trait, []))
            if len(thresholds) < 2:
                continue
            if tier >= thresholds[1]:
                by_key[f"{trait}-{tier}"].append(member)
    result = []
    for key, rows in by_key.items():
        if len(rows) >= max(15, min_apps * 2) and len(rows) < len(members) * 0.92:
            result.append((key, rows))
    result.sort(key=lambda item: (-len(item[1]), item[0]))
    return result[:4]


def cluster_compositions(features: list[PlayerFeature], min_apps: int) -> list[dict[str, Any]]:
    candidates = [
        feature
        for feature in sorted(features, key=lambda item: (item.rank, -len(item.hero_set)))
        if len(feature.hero_set) >= 5
    ]
    families: list[list[PlayerFeature]] = []
    for feature in candidates:
        placed = False
        for members in families:
            if is_similar_to_family(feature, members):
                members.append(feature)
                placed = True
                break
        if not placed:
            families.append([feature])

    output: list[dict[str, Any]] = []
    family_id = 1
    total_players = len(features) or 1
    total_matches = len({feature.match_id for feature in features}) or 1
    for members in families:
        if len(members) < min_apps:
            continue
        for member in members:
            member.family_id = family_id
        base_row = build_composition_row(members, family_id, total_players, total_matches)
        output.append(base_row)
        family_id += 1
        for sub_key, sub_members in high_tier_subgroups(members, min_apps):
            if sub_key == base_row["main_bond"]:
                continue
            output.append(
                build_composition_row(
                    sub_members,
                    family_id,
                    total_players,
                    total_matches,
                    is_subfamily=True,
                    subfamily_key=sub_key,
                )
            )
            family_id += 1

    output.sort(
        key=lambda row: (
            row["recommendation_score"],
            -row["stats"]["appearances"],
            row["stats"]["avg_rank"],
        )
    )
    return output


def trait_name_from_bond_key(key: str) -> str:
    if key == "拼多多":
        return key
    return parse_trait_tier(key)[0]


def strategy_carry_key(row: dict[str, Any]) -> str:
    names = sorted(item["hero_name"] for item in row.get("main_carries", [])[:3])
    return "+".join(names) or row["label"]


def bond_stage_score(row: dict[str, Any]) -> tuple[int, int, float, int]:
    key = row.get("main_bond", "")
    tier = 0
    if key and key != "拼多多" and "-" in key:
        _, tier = parse_trait_tier(key)
    return (tier, row["stats"]["appearances"], row["stats"]["top4_rate"], -int(row["stats"]["avg_rank"] * 100))


def merge_comp_strategies(
    comp_rows: list[dict[str, Any]],
    features: list[PlayerFeature],
) -> list[dict[str, Any]]:
    player_by_id = {feature.player_id: feature for feature in features}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in comp_rows:
        grouped[strategy_carry_key(row)].append(row)

    strategies: list[dict[str, Any]] = []
    strategy_index = 1
    for carry_key, rows in grouped.items():
        member_ids = sorted({pid for row in rows for pid in row["member_player_ids"]})
        members = [player_by_id[pid] for pid in member_ids if pid in player_by_id]
        if not members:
            continue
        mature = sorted(
            rows,
            key=lambda row: (bond_stage_score(row), -row["recommendation_score"]),
            reverse=True,
        )[0]
        transition_rows = [
            {
                "label": row["label"],
                "bond": row["main_bond"],
                "role": "大成" if row is mature else "过渡",
                "stats": row["stats"],
                "difficulty": row["difficulty"],
                "popularity": row["popularity"],
                "play_style": row.get("play_style", "高费"),
                "play_style_breakdown": row.get("play_style_breakdown", []),
                "member_player_ids": row["member_player_ids"],
                "recommendation_score": row["recommendation_score"],
            }
            for row in sorted(rows, key=lambda row: bond_stage_score(row), reverse=True)
        ]
        aggregate = build_composition_row(
            members,
            strategy_index,
            len(features) or 1,
            len({feature.match_id for feature in features}) or 1,
        )
        strategy_id = f"{trait_name_from_bond_key(mature['main_bond'])}|{carry_key}"
        aggregate.update(
            {
                "strategy_id": strategy_id,
                "family_id": strategy_index,
                "label": f"{mature['main_bond']} / {carry_key}",
                "main_bond": mature["main_bond"],
                "mature_stage": {
                    "label": mature["label"],
                    "bond": mature["main_bond"],
                    "stats": mature["stats"],
                    "variants": mature["variants"],
                    "play_style": mature.get("play_style", aggregate.get("play_style", "高费")),
                    "play_style_breakdown": mature.get("play_style_breakdown", []),
                    "carry_requirements": mature.get("carry_requirements", []),
                    "carry_equipment_notes": mature.get("carry_equipment_notes", []),
                },
                "transition_stages": transition_rows,
                "aggregate_stats": aggregate["stats"],
                "stats": aggregate["stats"],
                "variants": mature["variants"],
                "carry_requirements": mature.get("carry_requirements", aggregate.get("carry_requirements", [])),
                "carry_equipment_notes": mature.get("carry_equipment_notes", aggregate.get("carry_equipment_notes", [])),
                "member_player_ids": member_ids,
                "strategy_stage_count": len(rows),
            }
        )
        aggregate["recommendation_score"] = composition_recommendation_score(aggregate)
        strategies.append(aggregate)
        for feature in members:
            feature.family_id = strategy_index
        strategy_index += 1

    strategies.sort(
        key=lambda row: (
            row["overall_strength_score"],
            row["recommendation_score"],
            -row["aggregate_stats"]["appearances"],
            row["aggregate_stats"]["avg_rank"],
        )
    )
    for rank, strategy in enumerate(strategies, start=1):
        strategy["strength_rank"] = rank
    return strategies


def three_star_required_carries(row: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "hero_name": item["hero_name"],
            "tier": item.get("tier"),
            "share": item.get("share"),
            "samples": item.get("samples"),
            "three_star_rate": item.get("three_star_rate"),
            "avg_stars_top4": item.get("avg_stars_top4"),
        }
        for item in row.get("carry_requirements", [])
        if item.get("recommended_min_stars", 0) >= 3
    ]


def relabel_difficulty(score: float) -> str:
    return "高" if score >= 0.58 else "中" if score >= 0.34 else "低"


def relabel_popularity(score: float) -> str:
    return "高" if score >= 1.5 else "中" if score >= 0.8 else "低"


def enrich_three_star_contest(
    comp_rows: list[dict[str, Any]],
    features: list[PlayerFeature],
) -> list[dict[str, Any]]:
    player_to_strategy: dict[int, dict[str, Any]] = {}
    for row in comp_rows:
        required = three_star_required_carries(row)
        row["three_star_required_carries"] = required
        row["low_cost_three_star_required_carries"] = [
            item for item in required if item.get("tier") is not None and item["tier"] <= 3
        ]
        for player_id in row.get("member_player_ids", []):
            player_to_strategy[player_id] = row

    features_by_match: dict[int, list[PlayerFeature]] = defaultdict(list)
    for feature in features:
        features_by_match[feature.match_id].append(feature)

    hero_match_counts: dict[str, Counter[int]] = defaultdict(Counter)
    hero_rank_stats: dict[str, RankStats] = defaultdict(RankStats)
    hero_strategy_labels: dict[str, Counter[str]] = defaultdict(Counter)

    for feature in features:
        strategy = player_to_strategy.get(feature.player_id)
        if not strategy:
            continue
        for required in strategy.get("three_star_required_carries", []):
            hero_name = required["hero_name"]
            hero_match_counts[hero_name][feature.match_id] += 1
            hero_rank_stats[hero_name].add(feature.rank)
            hero_strategy_labels[hero_name][strategy["label"]] += 1

    for row in comp_rows:
        required_names = {
            item["hero_name"] for item in row.get("three_star_required_carries", [])
        }
        overlap_values: list[int] = []
        overlap_label_counter: Counter[str] = Counter()
        for player_id in row.get("member_player_ids", []):
            feature = next((item for item in features if item.player_id == player_id), None)
            if feature is None or not required_names:
                continue
            match_features = features_by_match.get(feature.match_id, [])
            max_overlap = 1
            for hero_name in required_names:
                same_need = [
                    other
                    for other in match_features
                    if (
                        other_strategy := player_to_strategy.get(other.player_id)
                    )
                    and any(
                        req["hero_name"] == hero_name
                        for req in other_strategy.get("three_star_required_carries", [])
                    )
                ]
                if len(same_need) > max_overlap:
                    max_overlap = len(same_need)
                for other in same_need:
                    other_strategy = player_to_strategy.get(other.player_id)
                    if other_strategy and other_strategy is not row:
                        overlap_label_counter[other_strategy["label"]] += 1
            overlap_values.append(max_overlap)

        avg_overlap = round(avg_number(overlap_values) or 0.0, 2)
        difficulty = row["difficulty"]
        popularity = row["popularity"]
        family_contest = difficulty.get("avg_family_contest", difficulty["avg_same_match_contest"])
        combined_contest = max(family_contest, avg_overlap)
        difficulty["avg_required_carry_contest"] = avg_overlap
        difficulty["avg_same_match_contest"] = round(combined_contest, 2)
        difficulty["contest_basis"] = "3星主C重叠" if avg_overlap > family_contest else "阵容相似"
        difficulty["overlap_strategies"] = [
            {"label": label, "samples": count}
            for label, count in overlap_label_counter.most_common(5)
        ]
        difficulty_score = (
            (difficulty["unfinished_bottom_rate"] / 100.0) * 0.5
            + min(combined_contest / 3.0, 1.0) * 0.3
            + (1.0 - difficulty["carry_complete_rate"] / 100.0) * 0.2
        )
        difficulty["score"] = round(difficulty_score, 3)
        difficulty["label"] = relabel_difficulty(difficulty_score)
        popularity["avg_required_carry_contest"] = avg_overlap
        popularity["avg_same_match_contest"] = round(combined_contest, 2)
        popularity["contest_basis"] = difficulty["contest_basis"]
        popularity_score = (
            popularity["pick_rate"] / 20.0
            + combined_contest / 3.0
            + popularity["match_share"] / 80.0
        )
        popularity["score"] = round(popularity_score, 3)
        popularity["label"] = relabel_popularity(popularity_score)
        row["overall_strength_score"] = overall_strength_score(row)
        row["recommendation_score"] = composition_recommendation_score(row)

    rows: list[dict[str, Any]] = []
    for hero_name, match_counts in hero_match_counts.items():
        stat = hero_rank_stats[hero_name]
        if not match_counts:
            continue
        avg_contest = sum(match_counts.values()) / len(match_counts)
        multi_match_rate = (
            sum(1 for count in match_counts.values() if count >= 2)
            * 100.0
            / len(match_counts)
        )
        top_strategy_labels = [
            {"label": label, "samples": count}
            for label, count in hero_strategy_labels[hero_name].most_common(4)
        ]
        tier = None
        for row in comp_rows:
            for item in row.get("three_star_required_carries", []):
                if item["hero_name"] == hero_name:
                    tier = item.get("tier")
                    break
            if tier is not None:
                break
        rows.append(
            {
                "hero_name": hero_name,
                "tier": tier,
                **stat.to_dict(),
                "match_appearances": len(match_counts),
                "avg_same_match_needers": round(avg_contest, 2),
                "max_same_match_needers": max(match_counts.values()),
                "multi_needer_match_rate": round(multi_match_rate, 1),
                "top_strategies": top_strategy_labels,
                "is_low_cost": tier is not None and tier <= 3,
            }
        )
    rows.sort(
        key=lambda row: (
            not row["is_low_cost"],
            -row["avg_same_match_needers"],
            -row["appearances"],
            row["avg_rank"],
        )
    )
    for rank, row in enumerate(sorted(comp_rows, key=lambda item: item["overall_strength_score"]), start=1):
        row["strength_rank"] = rank
    comp_rows.sort(
        key=lambda row: (
            row["overall_strength_score"],
            row["recommendation_score"],
            -row["aggregate_stats"]["appearances"],
            row["aggregate_stats"]["avg_rank"],
        )
    )
    return rows


def build_composition_recommendations(
    comp_rows: list[dict[str, Any]],
    limit: int = 8,
) -> dict[str, list[dict[str, Any]]]:
    return {
        style: [row for row in comp_rows if row.get("play_style") == style][:limit]
        for style in PLAY_STYLES
    }


def avg_number(values: list[int | float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def carry_for_name(feature: PlayerFeature, hero_name: str) -> Hero | None:
    for hero in feature.carry_candidates[:3]:
        if hero.name == hero_name:
            return hero
    if feature.main_carry and feature.main_carry.name == hero_name:
        return feature.main_carry
    return None


def compute_variant_bond_status(
    feature: PlayerFeature,
    main_bond: str | None,
) -> dict[str, Any] | None:
    if not main_bond or main_bond == "拼多多" or "-" not in main_bond:
        return None
    _, dict_bond = load_game_config()
    trait, target_tier = parse_trait_tier(main_bond)
    thresholds = dict_bond.get(trait, [])
    if not thresholds:
        return None
    hero_only_tier = active_tier(feature.trait_counts.get(trait, 0), thresholds)
    actual_tier = feature.active_traits.get(trait, 0)
    jiujiu_wearers: list[dict[str, str]] = []
    for hero in feature.heroes:
        for equipment in hero.equipments:
            if jiujiu_trait(equipment.raw_name) == trait:
                jiujiu_wearers.append(
                    {"hero_name": hero.name, "equipment_name": equipment.name}
                )
    return {
        "trait": trait,
        "target_tier": target_tier,
        "hero_only_tier": hero_only_tier,
        "actual_tier": actual_tier,
        "needs_jiujiu": hero_only_tier < target_tier and actual_tier >= target_tier,
        "meets_title_bond": actual_tier >= target_tier,
        "jiujiu_wearers": jiujiu_wearers,
        "active_traits": dict(feature.active_traits),
        "jiujiu_bonus": dict(feature.jiujiu_bonus),
    }


def analyze_comp_jiujiu_dependency(
    members: list[PlayerFeature],
    main_bond: str,
) -> list[dict[str, Any]]:
    if not main_bond or main_bond == "拼多多" or "-" not in main_bond:
        return []
    _, dict_bond = load_game_config()
    trait, target_tier = parse_trait_tier(main_bond)
    thresholds = dict_bond.get(trait, [])
    if not thresholds:
        return []

    dependency_samples = 0
    wearer_counter: Counter[str] = Counter()
    jiujiu_item_counter: Counter[str] = Counter()
    for member in members:
        hero_only_tier = active_tier(member.trait_counts.get(trait, 0), thresholds)
        actual_tier = member.active_traits.get(trait, 0)
        if hero_only_tier < target_tier and actual_tier >= target_tier:
            dependency_samples += 1
            for hero in member.heroes:
                for equipment in hero.equipments:
                    if jiujiu_trait(equipment.raw_name) == trait:
                        wearer_counter[hero.name] += 1
                        jiujiu_item_counter[equipment.name] += 1

    if dependency_samples == 0:
        return []

    dependency_rate = dependency_samples * 100.0 / len(members)
    recommended_jiujiu = (
        jiujiu_item_counter.most_common(1)[0][0]
        if jiujiu_item_counter
        else f"{trait}啾啾"
    )
    return [
        {
            "trait": trait,
            "target_tier": target_tier,
            "dependency_rate": round(dependency_rate, 1),
            "dependency_samples": dependency_samples,
            "recommended_jiujiu": recommended_jiujiu,
            "recommended_wearers": [
                {
                    "hero_name": name,
                    "share": round(count * 100.0 / dependency_samples, 1),
                }
                for name, count in wearer_counter.most_common(4)
            ],
        }
    ]


def variant_bond_note(bond_status: dict[str, Any] | None) -> str:
    if not bond_status:
        return "—"
    trait = bond_status["trait"]
    target = bond_status["target_tier"]
    if bond_status["meets_title_bond"]:
        if bond_status["needs_jiujiu"]:
            wearers = "、".join(
                item["hero_name"] for item in bond_status.get("jiujiu_wearers", [])[:2]
            )
            return f"需{trait}啾啾({wearers or '待观察'})"
        return f"已达成{trait}-{target}"
    return f"未达{trait}-{target}(纯{trait}{bond_status['hero_only_tier']})"


def build_level_variants(
    members: list[PlayerFeature],
    family_hero_counter: Counter[str],
    *,
    main_bond: str | None = None,
) -> dict[str, dict[str, Any]]:
    variants: dict[str, dict[str, Any]] = {}
    hero_order = [name for name, _ in family_hero_counter.most_common()]
    for target in (7, 8, 9):
        exact = [
            member
            for member in members
            if len(unique_heroes_by_slot(member)) == target
        ]
        if exact:
            candidates = [
                (member, compute_variant_bond_status(member, main_bond))
                for member in exact
            ]
            best, bond_status = sorted(
                candidates,
                key=lambda item: (
                    not (
                        item[1] is None
                        or item[1].get("meets_title_bond", False)
                    ),
                    item[0].rank,
                    -len(item[0].hero_set),
                ),
            )[0]
            variants[str(target)] = {
                "source": "sample",
                "confidence": confidence_label(len(exact)),
                "rank": best.rank,
                "heroes": unique_heroes_by_slot(best),
                "main_carry": best.main_carry.name if best.main_carry else None,
                "sample_count": len(exact),
                "bond_status": bond_status,
                "bond_note": variant_bond_note(bond_status),
                "jiujiu_wearers": bond_status.get("jiujiu_wearers", []) if bond_status else [],
            }
        else:
            variants[str(target)] = {
                "source": "derived",
                "confidence": "低",
                "rank": None,
                "heroes": hero_order[:target],
                "main_carry": members[0].main_carry.name if members and members[0].main_carry else None,
                "sample_count": 0,
                "bond_status": None,
                "bond_note": "推导阵容，未绑定样本羁绊",
                "jiujiu_wearers": [],
            }
    return variants


def unique_heroes_by_slot(feature: PlayerFeature) -> list[str]:
    seen: set[str] = set()
    heroes: list[str] = []
    for hero in sorted(feature.heroes, key=lambda item: item.slot_index):
        if not is_lineup_hero(hero.name):
            continue
        if hero.name in seen:
            continue
        seen.add(hero.name)
        heroes.append(hero.name)
    return heroes


def median_number(values: list[int]) -> float | None:
    if not values:
        return None
    values = sorted(values)
    mid = len(values) // 2
    if len(values) % 2 == 1:
        return float(values[mid])
    return (values[mid - 1] + values[mid]) / 2.0


def summarize_carry_requirements(
    members: list[PlayerFeature],
    main_carries: list[tuple[str, int]],
) -> list[dict[str, Any]]:
    requirements = []
    for hero_name, count in main_carries[:3]:
        carry_samples = [
            carry
            for member in members
            if (carry := carry_for_name(member, hero_name)) is not None
        ]
        top4_samples = [
            carry
            for member in members
            if member.rank <= 4
            and (carry := carry_for_name(member, hero_name)) is not None
        ]
        if not carry_samples:
            continue

        stars = [carry.stars for carry in top4_samples] or [carry.stars for carry in carry_samples]
        equipment_counts = [
            carry.equipment_count for carry in top4_samples
        ] or [carry.equipment_count for carry in carry_samples]
        two_star_rate = sum(1 for value in stars if value >= 2) * 100.0 / len(stars)
        three_star_rate = sum(1 for value in stars if value >= 3) * 100.0 / len(stars)
        three_item_rate = sum(1 for value in equipment_counts if value >= 3) * 100.0 / len(equipment_counts)
        bottom_underbuilt = 0
        bottom_samples = 0
        for member in members:
            carry = carry_for_name(member, hero_name)
            if carry is None or member.rank <= 4:
                continue
            bottom_samples += 1
            if carry.stars < 2 or carry.equipment_count < 3:
                bottom_underbuilt += 1
        bottom_underbuilt_rate = (
            bottom_underbuilt * 100.0 / bottom_samples
            if bottom_samples
            else 0.0
        )
        tier = carry_samples[0].tier or 0
        high_cost_three_star_dependency = tier >= 4 and three_star_rate >= 45
        if tier >= 4:
            recommended_star = 2 if two_star_rate >= 45 else max(1, min(stars))
        else:
            recommended_star = 3 if three_star_rate >= 45 else 2 if two_star_rate >= 60 else max(1, min(stars))
        requirements.append(
            {
                "hero_name": hero_name,
                "tier": tier,
                "share": round(count * 100.0 / len(members), 1),
                "samples": len(carry_samples),
                "top4_samples": len(top4_samples),
                "min_stars_top4": min(stars),
                "avg_stars_top4": round(avg_number(stars) or 0, 2),
                "recommended_min_stars": recommended_star,
                "high_cost_three_star_dependency": high_cost_three_star_dependency,
                "two_star_rate": round(two_star_rate, 1),
                "three_star_rate": round(three_star_rate, 1),
                "three_item_rate": round(three_item_rate, 1),
                "bottom_underbuilt_rate": round(bottom_underbuilt_rate, 1),
            }
        )
    return requirements


def summarize_comp_carry_equipment(
    members: list[PlayerFeature],
    main_carries: list[tuple[str, int]],
) -> list[dict[str, Any]]:
    notes = []
    for hero_name, _ in main_carries[:3]:
        samples: list[tuple[PlayerFeature, Hero]] = []
        item_counter: Counter[str] = Counter()
        for member in members:
            carry = carry_for_name(member, hero_name)
            if carry is None:
                continue
            samples.append((member, carry))
            item_counter.update(equipment.name for equipment in carry.equipments)
        if len(samples) < 4:
            continue

        item_rows = []
        for item_name, appearances in item_counter.most_common():
            if appearances < 3:
                continue
            with_ranks = [
                member.rank
                for member, carry in samples
                if item_name in {equipment.name for equipment in carry.equipments}
            ]
            without_ranks = [
                member.rank
                for member, carry in samples
                if item_name not in {equipment.name for equipment in carry.equipments}
            ]
            if not with_ranks:
                continue
            with_avg = sum(with_ranks) / len(with_ranks)
            without_avg = sum(without_ranks) / len(without_ranks) if without_ranks else None
            with_top4 = sum(1 for rank in with_ranks if rank <= 4) * 100.0 / len(with_ranks)
            without_top4 = (
                sum(1 for rank in without_ranks if rank <= 4) * 100.0 / len(without_ranks)
                if without_ranks
                else None
            )
            penalty = (
                round(without_avg - with_avg, 2)
                if without_avg is not None
                else None
            )
            use_rate = appearances * 100.0 / len(samples)
            selected_rate = (
                sum(
                    1
                    for _, carry in samples
                    for equipment in carry.equipments
                    if equipment.name == item_name and equipment.selected
                )
                * 100.0
                / appearances
            )
            if appearances >= 8 and penalty is not None and penalty >= 0.45 and with_top4 >= 60:
                label = "疑似刚需"
            elif appearances >= 8 and penalty is not None and penalty >= 0.25:
                label = "高价值"
            else:
                label = "观察"
            item_rows.append(
                {
                    "equipment_name": item_name,
                    "label": label,
                    "appearances": appearances,
                    "use_rate": round(use_rate, 1),
                    "with_avg_rank": round(with_avg, 2),
                    "without_avg_rank": round(without_avg, 2) if without_avg is not None else None,
                    "without_item_penalty": penalty,
                    "with_top4_rate": round(with_top4, 1),
                    "without_top4_rate": round(without_top4, 1) if without_top4 is not None else None,
                    "selected_rate": round(selected_rate, 1),
                }
            )
        item_rows.sort(
            key=lambda row: (
                {"疑似刚需": 0, "高价值": 1, "观察": 2}[row["label"]],
                -row["appearances"],
                -(row["without_item_penalty"] or 0),
                -row["use_rate"],
            )
        )
        notes.append(
            {
                "hero_name": hero_name,
                "sample_count": len(samples),
                "items": item_rows[:5],
            }
        )
    return notes


def analyze_cards(
    features: list[PlayerFeature],
    comp_rows: list[dict[str, Any]],
    min_apps: int,
    baseline: float,
    team_baseline: float,
) -> dict[str, Any]:
    single_items: list[tuple[str, int]] = []
    first_card_items: list[tuple[str, int]] = []
    blue_team_rank_items: list[tuple[str, int]] = []
    blue_team_top2: Counter[str] = Counter()
    pair_items: list[tuple[str, int]] = []
    triple_items: list[tuple[str, int]] = []
    teammate_pair_items: list[tuple[str, int]] = []
    first_card_duo_items: list[tuple[str, int]] = []
    first_with_partner_any_items: list[tuple[str, int]] = []
    comp_card_items: dict[int, list[tuple[str, int]]] = defaultdict(list)
    family_labels = {row["family_id"]: row["label"] for row in comp_rows}
    by_match_rank = {
        (feature.match_id, feature.rank): feature
        for feature in features
    }
    seen_partner_pairs: set[tuple[int, int]] = set()
    duo_contribution: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "appearances": 0,
            "holder_rank_sum": 0.0,
            "team_rank_sum": 0.0,
            "team_top2": 0,
            "rank_gap_sum": 0.0,
        }
    )
    total_matches = len({feature.match_id for feature in features}) or 1

    for feature in features:
        cards = sorted(set(feature.cards))
        for card in cards:
            single_items.append((card, feature.rank))
            if card_prefix_type(card) == "蓝":
                blue_team_rank_items.append((card, team_rank_value(feature)))
                if team_rank_value(feature) <= 2:
                    blue_team_top2[card] += 1
            if feature.family_id:
                comp_card_items[feature.family_id].append((card, feature.rank))
        if (card := first_card(feature)) is not None:
            first_card_items.append((card, feature.rank))
        for pair in itertools.combinations(cards, 2):
            pair_items.append((" + ".join(pair), feature.rank))
        for triple in itertools.combinations(cards, 3):
            triple_items.append((" + ".join(triple), feature.rank))
        if feature.partner_player is not None:
            partner = by_match_rank.get((feature.match_id, int(feature.partner_player)))
            if partner is not None:
                pair_key = tuple(sorted((feature.player_id, partner.player_id)))
                if pair_key not in seen_partner_pairs:
                    seen_partner_pairs.add(pair_key)
                    team_rank = min(team_rank_value(feature), team_rank_value(partner))
                    for left in set(feature.cards):
                        for right in set(partner.cards):
                            teammate_pair_items.append(
                                (" + ".join(sorted((left, right))), team_rank)
                            )
                    left_first = first_card(feature)
                    right_first = first_card(partner)
                    if left_first and right_first:
                        first_key = " + ".join(sorted((left_first, right_first)))
                        first_card_duo_items.append((first_key, team_rank))
                        contribution = duo_contribution[first_key]
                        contribution["appearances"] += 1
                        contribution["holder_rank_sum"] += (feature.rank + partner.rank) / 2.0
                        contribution["team_rank_sum"] += team_rank
                        if team_rank <= 2:
                            contribution["team_top2"] += 1
                        contribution["rank_gap_sum"] += abs(feature.rank - partner.rank)
                    if left_first:
                        for card in set(partner.cards):
                            first_with_partner_any_items.append(
                                (f"{left_first} + 队友{card}", team_rank)
                            )
                    if right_first:
                        for card in set(feature.cards):
                            first_with_partner_any_items.append(
                                (f"{right_first} + 队友{card}", team_rank)
                            )

    by_comp = []
    for family_id, items in comp_card_items.items():
        rows = aggregate_key_stats(items, max(4, min_apps // 3), baseline)[:8]
        if rows:
            add_avg_appearances_per_match(rows, total_matches)
            by_comp.append(
                {
                    "family_id": family_id,
                    "family_label": family_labels.get(family_id, str(family_id)),
                    "cards": rows,
                }
            )

    contribution_rows = []
    for key, row in duo_contribution.items():
        n = row["appearances"]
        if n < max(5, min_apps // 2):
            continue
        holder_avg = row["holder_rank_sum"] / n
        team_avg = row["team_rank_sum"] / n
        contribution_rows.append(
            {
                "key": key,
                "appearances": n,
                "holder_avg_rank": round(holder_avg, 2),
                "team_avg_rank": round(team_avg, 2),
                "team_top2_rate": round(row["team_top2"] * 100.0 / n, 1),
                "team_lift_vs_baseline": round(team_baseline - team_avg, 2),
                "team_lift_vs_holder": round((holder_avg / 2.0) - team_avg, 2),
                "partner_delta": round(row["rank_gap_sum"] / n, 2),
            }
        )
    contribution_rows.sort(
        key=lambda item: (
            -item["team_lift_vs_baseline"],
            item["team_avg_rank"],
            -item["appearances"],
        )
    )

    single_cards, single_cards_by_prefix = aggregate_single_cards_by_catalog(
        single_items,
        baseline,
        load_report_card_catalog(),
        sample_first=True,
    )
    first_card_rankings, first_card_rankings_by_prefix = aggregate_key_stats_by_prefix(
        first_card_items, max(6, min_apps // 2), baseline, sample_first=True
    )
    blue_cards_team_rank, blue_cards_team_rank_by_prefix = aggregate_key_stats_by_prefix(
        blue_team_rank_items,
        max(6, min_apps // 2),
        team_baseline,
        sample_first=True,
    )
    add_avg_appearances_per_match(single_cards, total_matches)
    add_avg_appearances_to_prefix_groups(single_cards_by_prefix, total_matches)
    add_avg_appearances_per_match(first_card_rankings, total_matches)
    add_avg_appearances_to_prefix_groups(first_card_rankings_by_prefix, total_matches)
    add_avg_appearances_per_match(blue_cards_team_rank, total_matches)
    add_avg_appearances_to_prefix_groups(blue_cards_team_rank_by_prefix, total_matches)
    for row in blue_cards_team_rank:
        row["team_top2_rate"] = round(
            blue_team_top2[row["key"]] * 100.0 / max(row["appearances"], 1),
            1,
        )
    for rows in blue_cards_team_rank_by_prefix.values():
        for row in rows:
            row["team_top2_rate"] = round(
                blue_team_top2[row["key"]] * 100.0 / max(row["appearances"], 1),
                1,
            )

    return {
        "single_cards": single_cards,
        "single_cards_by_prefix": single_cards_by_prefix,
        "first_card_rankings": first_card_rankings,
        "first_card_rankings_by_prefix": first_card_rankings_by_prefix,
        "blue_cards_team_rank": blue_cards_team_rank,
        "blue_cards_team_rank_by_prefix": blue_cards_team_rank_by_prefix,
        "card_pairs_observation": aggregate_key_stats(pair_items, max(6, min_apps // 2), baseline)[:20],
        "card_triples_observation": aggregate_key_stats(triple_items, max(5, min_apps // 2), baseline)[:20],
        "teammate_card_pairs_observation": aggregate_key_stats(
            teammate_pair_items,
            max(6, min_apps // 2),
            team_baseline,
        )[:20],
        "first_card_duo_synergy": aggregate_key_stats(
            first_card_duo_items,
            max(5, min_apps // 2),
            team_baseline,
        )[:20],
        "first_with_partner_any_observation": aggregate_key_stats(
            first_with_partner_any_items,
            max(6, min_apps // 2),
            team_baseline,
        )[:20],
        "duo_card_contribution": contribution_rows[:20],
        "composition_cards": by_comp,
    }


def analyze_heroes_and_equipment(
    features: list[PlayerFeature],
    min_apps: int,
    baseline: float,
) -> dict[str, Any]:
    hero_stats: dict[str, RankStats] = defaultdict(RankStats)
    carry_stats: dict[str, RankStats] = defaultdict(RankStats)
    hero_item_stats: dict[tuple[str, str], RankStats] = defaultdict(RankStats)
    hero_item_selected: Counter[tuple[str, str]] = Counter()
    item_stats: dict[str, RankStats] = defaultdict(RankStats)
    set_stats: dict[tuple[str, str], RankStats] = defaultdict(RankStats)
    trait_items: list[tuple[str, int]] = []
    jiujiu_items: list[tuple[str, int]] = []
    hero_tiers: dict[str, int | None] = {}

    for feature in features:
        for trait, tier in feature.active_traits.items():
            trait_items.append((f"{trait}-{tier}", feature.rank))
        for trait, count in feature.jiujiu_bonus.items():
            if count > 0:
                jiujiu_items.append((trait, feature.rank))
        for hero in feature.heroes:
            hero_stats[hero.name].add(feature.rank)
            hero_tiers.setdefault(hero.name, hero.tier)
            if any(hero.id == candidate.id for candidate in feature.carry_candidates[:3]):
                carry_stats[hero.name].add(feature.rank)
            equipment_names = []
            for equipment in hero.equipments:
                item_stats[equipment.name].add(feature.rank)
                hero_item_stats[(hero.name, equipment.name)].add(feature.rank)
                equipment_names.append(equipment.name)
                if equipment.selected:
                    hero_item_selected[(hero.name, equipment.name)] += 1
            if len(equipment_names) == 3:
                set_key = " + ".join(sorted(equipment_names))
                set_stats[(hero.name, set_key)].add(feature.rank)

    heroes = []
    for hero_name, stat in hero_stats.items():
        if stat.appearances < min_apps:
            continue
        carry_stat = carry_stats.get(hero_name, RankStats())
        heroes.append(
            {
                "hero_name": hero_name,
                "tier": hero_tiers.get(hero_name),
                **stat.to_dict(baseline_rank=baseline, prior=8),
                "carry_appearances": carry_stat.appearances,
                "carry_rate": round(carry_stat.appearances * 100.0 / stat.appearances, 1),
            }
        )
    heroes.sort(
        key=lambda row: (
            -row["carry_appearances"],
            row["adjusted_avg_rank"],
            -row["top4_rate"],
        )
    )

    dict_character, _ = load_game_config()
    recommendations = []
    for hero in heroes:
        hero_name = hero["hero_name"]
        config_entry = dict_character.get(hero_name)
        hero_traits = [str(trait) for trait in config_entry[1:]] if config_entry else []
        items = []
        low_sample_items = []
        reliable_item_min = max(8, int(hero["appearances"] * 0.05))
        for (item_hero, item_name), stat in hero_item_stats.items():
            if item_hero != hero_name or stat.appearances < max(4, min_apps // 3):
                continue
            selected_rate = hero_item_selected[(item_hero, item_name)] * 100.0 / stat.appearances
            row = {
                "equipment_name": item_name,
                **stat.to_dict(baseline_rank=baseline, prior=8),
                "selected_rate": round(selected_rate, 1),
                "sample_quality": "高样本" if stat.appearances >= reliable_item_min else "低样本观察",
                "selected_priority": selected_priority_label(selected_rate, stat.to_dict()["avg_rank"], baseline)
                if stat.appearances >= reliable_item_min
                else "低",
            }
            if stat.appearances >= reliable_item_min:
                items.append(row)
            else:
                low_sample_items.append(row)
        sets = []
        for (set_hero, set_name), stat in set_stats.items():
            if set_hero == hero_name and stat.appearances >= max(3, min_apps // 4):
                sets.append({"equipment_set": set_name, **stat.to_dict(baseline_rank=baseline, prior=8)})
        items.sort(key=lambda row: (-row["appearances"], row["adjusted_avg_rank"], -row["top4_rate"]))
        low_sample_items.sort(key=lambda row: (row["adjusted_avg_rank"], -row["top4_rate"], -row["appearances"]))
        sets.sort(key=lambda row: (-row["appearances"], row["adjusted_avg_rank"], -row["top4_rate"]))
        recommendations.append(
            {
                "hero_name": hero_name,
                "hero_traits": hero_traits,
                "hero_stats": hero,
                "recommended_items": items[:6],
                "low_sample_observations": low_sample_items[:4],
                "recommended_sets": sets[:4],
                "has_equipment_data": bool(items or low_sample_items),
            }
        )

    recommendations.sort(
        key=lambda row: (
            row["hero_stats"].get("tier") or 99,
            -row["hero_stats"].get("carry_appearances", 0),
            row["hero_name"],
        )
    )

    equipment_rows = []
    for item_name, stat in item_stats.items():
        if stat.appearances >= min_apps:
            equipment_rows.append({"equipment_name": item_name, **stat.to_dict(baseline_rank=baseline, prior=8)})
    equipment_rows.sort(key=lambda row: (-row["appearances"], row["adjusted_avg_rank"], row["avg_rank"], -row["top4_rate"]))

    return {
        "heroes": heroes,
        "carry_equipment_recommendations": recommendations,
        "equipment": equipment_rows,
        "bonds": aggregate_key_stats(trait_items, min_apps, baseline),
        "jiujiu_bonds": aggregate_key_stats(jiujiu_items, max(5, min_apps // 3), baseline),
    }


def analyze_jiujiu(
    features: list[PlayerFeature],
    comp_rows: list[dict[str, Any]],
    baseline: float,
    min_apps: int = 5,
) -> dict[str, Any]:
    total_stats: dict[str, RankStats] = defaultdict(RankStats)
    effective_stats: dict[str, RankStats] = defaultdict(RankStats)
    incidental_stats: dict[str, RankStats] = defaultdict(RankStats)
    reason_counts: dict[str, Counter[str]] = defaultdict(Counter)
    player_to_comps: dict[int, dict[str, dict[str, Any]]] = defaultdict(dict)
    comp_item_stats: dict[tuple[str, str], RankStats] = defaultdict(RankStats)
    comp_item_wearers: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    hero_item_stats: dict[tuple[str, str], RankStats] = defaultdict(RankStats)
    item_strategy_seen: dict[str, set[str]] = defaultdict(set)

    for comp in comp_rows:
        for player_id in comp["member_player_ids"]:
            player_to_comps[player_id].setdefault(comp["label"], comp)

    pending_generalist: dict[str, list[tuple[PlayerFeature, str]]] = defaultdict(list)
    for feature in features:
        comp_values = list(player_to_comps.get(feature.player_id, {}).values())
        seen_items: set[str] = set()
        for hero in feature.heroes:
            for equipment in hero.equipments:
                trait = jiujiu_trait(equipment.name)
                if not trait:
                    continue
                item_name = equipment.name
                if item_name in seen_items:
                    continue
                seen_items.add(item_name)
                total_stats[item_name].add(feature.rank)
                reasons = classify_jiujiu_sample(feature, hero, item_name, trait, comp_values)
                if reasons:
                    for reason in reasons:
                        reason_counts[item_name][reason] += 1
                    effective_stats[item_name].add(feature.rank)
                    if "final_bond" in reasons:
                        for comp in comp_values:
                            if jiujiu_matches_strategy_bond(trait, comp):
                                comp_item_stats[(item_name, comp["label"])].add(feature.rank)
                                comp_item_wearers[(item_name, comp["label"])][hero.name] += 1
                    if "hero_boost" in reasons:
                        hero_item_stats[(item_name, hero.name)].add(feature.rank)
                else:
                    incidental_stats[item_name].add(feature.rank)
                    pending_generalist[item_name].append((feature, item_name))
                for comp in comp_values:
                    item_strategy_seen[item_name].add(comp["label"])

    baseline_rank = baseline
    for item_name, samples in pending_generalist.items():
        if len(item_strategy_seen[item_name]) < 4:
            continue
        total = total_stats[item_name]
        if total.appearances < 12 or total.to_dict()["avg_rank"] > baseline_rank - 0.25:
            continue
        for feature, _ in samples:
            reason_counts[item_name]["generalist"] += 1
            effective_stats[item_name].add(feature.rank)

    rankings = []
    recommended: dict[str, list[dict[str, Any]]] = {}
    hero_recommendations: dict[str, list[dict[str, Any]]] = {}
    for item_name, stat in total_stats.items():
        if stat.appearances < min_apps:
            continue
        comps = []
        for (comp_item, comp_label), comp_stat in comp_item_stats.items():
            if comp_item != item_name or comp_stat.appearances < 3:
                continue
            comps.append(
                {
                    "family_label": comp_label,
                    **comp_stat.to_dict(baseline_rank=baseline, prior=6),
                    "share": round(comp_stat.appearances * 100.0 / stat.appearances, 1),
                    "recommended_wearers": [
                        {
                            "hero_name": hero_name,
                            "appearances": count,
                            "share": round(count * 100.0 / comp_stat.appearances, 1),
                        }
                        for hero_name, count in comp_item_wearers[
                            (comp_item, comp_label)
                        ].most_common(3)
                    ],
                }
            )
        comps.sort(key=lambda row: (-row["appearances"], row["adjusted_avg_rank"], -row["top4_rate"]))
        recommended[item_name] = comps[:4]
        hero_rows = []
        for (hero_item, hero_name), hero_stat in hero_item_stats.items():
            if hero_item != item_name or hero_stat.appearances < 3:
                continue
            hero_rows.append(
                {
                    "hero_name": hero_name,
                    **hero_stat.to_dict(baseline_rank=baseline, prior=6),
                }
            )
        hero_rows.sort(key=lambda row: (-row["appearances"], row["adjusted_avg_rank"], -row["top4_rate"]))
        hero_recommendations[item_name] = hero_rows[:4]
        effective = effective_stats.get(item_name, RankStats())
        incidental = incidental_stats.get(item_name, RankStats())
        rankings.append(
            {
                "equipment_name": item_name,
                **stat.to_dict(baseline_rank=baseline, prior=8),
                "effective_appearances": effective.appearances,
                "effective_rate": round(effective.appearances * 100.0 / stat.appearances, 1),
                "effective_stats": effective.to_dict(baseline_rank=baseline, prior=8) if effective.appearances else None,
                "incidental_stats": incidental.to_dict(baseline_rank=baseline, prior=8) if incidental.appearances else None,
                "reason_counts": dict(reason_counts[item_name]),
                "recommended_comps": recommended[item_name],
                "recommended_heroes": hero_recommendations[item_name],
            }
        )
    rankings.sort(key=lambda row: (-row["effective_appearances"], row["effective_stats"]["adjusted_avg_rank"] if row["effective_stats"] else 99, -row["top4_rate"]))
    return {
        "jiujiu_rankings": rankings,
        "jiujiu_recommended_comps": recommended,
        "jiujiu_recommended_heroes": hero_recommendations,
    }


def jiujiu_matches_strategy_bond(trait: str, comp: dict[str, Any]) -> bool:
    main_bond = comp.get("main_bond", "")
    if main_bond != "拼多多" and "-" in main_bond and parse_trait_tier(main_bond)[0] == trait:
        return True
    for bond in comp.get("common_bonds", [])[:4]:
        if "-" in bond["bond"]:
            bond_trait, _ = parse_trait_tier(bond["bond"])
            if bond_trait == trait and bond.get("share", 0) >= 50:
                return True
    return False


def classify_jiujiu_sample(
    feature: PlayerFeature,
    hero: Hero,
    item_name: str,
    trait: str,
    comps: list[dict[str, Any]],
) -> list[str]:
    reasons: list[str] = []
    if any(jiujiu_matches_strategy_bond(trait, comp) for comp in comps):
        reasons.append("final_bond")
    is_key_hero = any(hero.id == candidate.id for candidate in feature.carry_candidates[:3])
    if is_key_hero and feature.rank <= 4 and hero.equipment_count >= 2:
        reasons.append("hero_boost")
    return reasons


def analyze_duo_composition_synergy(
    features: list[PlayerFeature],
    comp_rows: list[dict[str, Any]],
    team_baseline: float,
    min_apps: int = 5,
) -> list[dict[str, Any]]:
    player_to_comp: dict[int, dict[str, Any]] = {}
    for comp in comp_rows:
        for player_id in comp.get("member_player_ids", []):
            player_to_comp[player_id] = comp

    by_match_rank = {
        (feature.match_id, feature.rank): feature
        for feature in features
    }
    pair_stats: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {
            "appearances": 0,
            "team_rank_sum": 0.0,
            "team_top2": 0,
            "team_wins": 0,
            "individual_rank_sum": 0.0,
            "strategy_labels": None,
        }
    )
    seen_pairs: set[tuple[int, int]] = set()
    for feature in features:
        if feature.partner_player is None:
            continue
        partner = by_match_rank.get((feature.match_id, int(feature.partner_player)))
        if partner is None:
            continue
        pair_key = tuple(sorted((feature.player_id, partner.player_id)))
        if pair_key in seen_pairs:
            continue
        seen_pairs.add(pair_key)
        left = player_to_comp.get(feature.player_id)
        right = player_to_comp.get(partner.player_id)
        if not left or not right:
            continue
        labels = tuple(sorted((left["label"], right["label"])))
        team_rank = min(team_rank_value(feature), team_rank_value(partner))
        row = pair_stats[labels]
        row["appearances"] += 1
        row["team_rank_sum"] += team_rank
        row["individual_rank_sum"] += (feature.rank + partner.rank) / 2.0
        row["strategy_labels"] = labels
        if team_rank <= 2:
            row["team_top2"] += 1
        if team_rank == 1:
            row["team_wins"] += 1

    rows: list[dict[str, Any]] = []
    for labels, row in pair_stats.items():
        n = row["appearances"]
        if n < min_apps:
            continue
        team_avg = row["team_rank_sum"] / n
        holder_avg = row["individual_rank_sum"] / n
        rows.append(
            {
                "strategy_a": labels[0],
                "strategy_b": labels[1],
                "key": " + ".join(labels),
                "appearances": n,
                "team_avg_rank": round(team_avg, 2),
                "team_top2_rate": round(row["team_top2"] * 100.0 / n, 1),
                "team_win_rate": round(row["team_wins"] * 100.0 / n, 1),
                "team_lift_vs_baseline": round(team_baseline - team_avg, 2),
                "holder_avg_rank": round(holder_avg, 2),
                "confidence": confidence_label(n),
            }
        )
    rows.sort(
        key=lambda row: (
            -row["appearances"],
            row["team_avg_rank"],
            -row["team_top2_rate"],
        )
    )
    return rows[:20]


def selected_priority_label(selected_rate: float, avg_rank: float, baseline: float) -> str:
    if selected_rate >= 30 and avg_rank < baseline:
        return "高"
    if selected_rate >= 12 and avg_rank <= baseline + 0.2:
        return "中"
    return "低"


def find_card_traps(
    cards_by_prefix: dict[str, list[dict[str, Any]]],
    baseline: float,
) -> list[dict[str, Any]]:
    def weak(row: dict[str, Any]) -> bool:
        return row.get("adjusted_avg_rank", row.get("avg_rank", 0)) >= baseline + 0.45 or row.get(
            "top4_rate", 100
        ) <= 42

    traps: list[dict[str, Any]] = []
    for prefix_type in CARD_PREFIX_TYPES:
        for row in cards_by_prefix.get(prefix_type, []):
            if row["appearances"] >= 12 and weak(row):
                traps.append(
                    {
                        **row,
                        "trap_reason": f"{prefix_type}类内样本充足但表现偏弱",
                    }
                )
    traps.sort(key=lambda row: (-row["appearances"], -row["adjusted_avg_rank"]))
    return traps[:10]


def find_traps(
    comp_rows: list[dict[str, Any]],
    hero_rows: list[dict[str, Any]],
    card_rows: list[dict[str, Any]],
    cards_by_prefix: dict[str, list[dict[str, Any]]],
    bond_rows: list[dict[str, Any]],
    equipment_rows: list[dict[str, Any]],
    baseline: float,
) -> dict[str, list[dict[str, Any]]]:
    def weak(row: dict[str, Any]) -> bool:
        return row.get("adjusted_avg_rank", row.get("avg_rank", 0)) >= baseline + 0.45 or row.get("top4_rate", 100) <= 42

    def comp_trait(row: dict[str, Any]) -> str | None:
        key = row.get("main_bond", "")
        if not key or key == "拼多多" or "-" not in key:
            return None
        return parse_trait_tier(key)[0]

    strong_trait_tiers: dict[str, int] = {}
    for row in comp_rows:
        trait = comp_trait(row)
        if (
            trait
            and row["stats"]["appearances"] >= 20
            and row["stats"]["top4_rate"] >= 60
            and row["stats"]["avg_rank"] <= baseline
        ):
            _, tier = parse_trait_tier(row["main_bond"])
            strong_trait_tiers[trait] = max(strong_trait_tiers.get(trait, 0), tier)
    strong_traits = set(strong_trait_tiers)

    comp_traps = [
        row
        for row in comp_rows
        if row["stats"]["appearances"] >= 5
        and (row["stats"]["avg_rank"] >= baseline + 0.45 or row["stats"]["top4_rate"] <= 42)
        and comp_trait(row) not in strong_traits
    ]
    for row in comp_traps:
        row["trap_reason"] = "策略整体表现偏弱，且没有同羁绊强势大成形态覆盖"
    comp_traps.sort(key=lambda row: (-row["popularity"]["pick_rate"], -row["stats"]["avg_rank"]))

    bond_traps: list[dict[str, Any]] = []
    covered_bond_pressure: list[dict[str, Any]] = []
    for row in bond_rows:
        if row["appearances"] < 10 or not weak(row):
            continue
        key = row.get("key", "")
        if "-" in key:
            trait, tier = parse_trait_tier(key)
            mature_tier = strong_trait_tiers.get(trait)
            if mature_tier and tier < mature_tier:
                covered_bond_pressure.append(
                    {
                        **row,
                        "covered_by": f"{trait}-{mature_tier}",
                        "trap_reason": f"更像{trait}-{mature_tier}的未成型阶段，计入成型压力而非独立陷阱",
                    }
                )
                continue
        bond_traps.append(row)

    return {
        "compositions": comp_traps[:10],
        "heroes": [row for row in hero_rows if row["appearances"] >= 10 and weak(row)][:10],
        "cards": find_card_traps(cards_by_prefix, baseline),
        "bonds": bond_traps[:10],
        "formation_pressure_bonds": covered_bond_pressure[:10],
        "equipment": [row for row in equipment_rows if row["appearances"] >= 10 and weak(row)][:10],
    }


def extract_balance_targets(
    notes_path: Path | None,
    dict_character: dict[str, list[Any]],
    dict_bond: dict[str, list[int]],
    observed_names: set[str],
) -> dict[str, list[str]]:
    if notes_path is None:
        return {"heroes": [], "bonds": [], "equipment_or_cards": []}
    path = notes_path if notes_path.is_absolute() else ROOT / notes_path
    text = path.read_text(encoding="utf-8")
    heroes = sorted(name for name in dict_character if name in text)
    bonds = sorted(name for name in dict_bond if name in text)
    others = sorted(name for name in observed_names if name in text and name not in heroes and name not in bonds)
    return {"heroes": heroes, "bonds": bonds, "equipment_or_cards": others}


def build_analysis(args: argparse.Namespace) -> dict[str, Any]:
    db_path = find_latest_db(args.db)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        dict_character, dict_bond = load_game_config()
        bot_ids = find_bot_player_ids(conn)
        quality = data_quality(conn, bot_ids)
        validation = validate_config(conn, dict_character, dict_bond)
        features = load_player_features(conn, bot_ids, dict_character, dict_bond)
        if not features:
            raise SystemExit("No usable player records after filtering.")
        baseline = sum(feature.rank for feature in features) / len(features)
        team_baseline = (
            sum(team_rank_value(feature) for feature in features) / len(features)
        )
        stage_rows = cluster_compositions(features, args.min_comp_apps)
        comp_rows = merge_comp_strategies(stage_rows, features)
        low_cost_carry_difficulty = enrich_three_star_contest(comp_rows, features)
        hero_equipment = analyze_heroes_and_equipment(features, args.min_entity_apps, baseline)
        cards = analyze_cards(features, comp_rows, args.min_card_apps, baseline, team_baseline)
        jiujiu_analysis = analyze_jiujiu(features, comp_rows, baseline)
        duo_compositions = analyze_duo_composition_synergy(
            features,
            comp_rows,
            team_baseline,
            min_apps=max(4, args.min_comp_apps // 2),
        )
        observed_names = {
            hero.name for feature in features for hero in feature.heroes
        } | {
            card for feature in features for card in feature.cards
        } | {
            equipment.name for feature in features for hero in feature.heroes for equipment in hero.equipments
        }
        balance_targets = extract_balance_targets(
            args.balance_notes,
            dict_character,
            dict_bond,
            observed_names,
        )
        traps = find_traps(
            comp_rows,
            hero_equipment["heroes"],
            cards["single_cards"],
            cards["single_cards_by_prefix"],
            hero_equipment["bonds"],
            hero_equipment["equipment"],
            baseline,
        )
        return {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "data_source": rel(db_path),
            "methodology": {
                "implementation": "rebuilt skill analyzer",
                "bot_filter": "rank 7/8 paired players excluded from all rankings",
                "unknown_filter": "unknown heroes/cards/equipment excluded from reference statistics",
                "equipment_normalization": "核选 prefix removed for equipment identity; selected rate retained",
                "jiujiu_rule": "X啾啾 adds +1 to bond X when X exists in dict_bond",
                "carry_score": "equipment_count*30 + selected_count*12 + stars*10 + tier*2 + max(0, 8-slot_index)*1.5",
                "play_style_rule": "level<=6 is reroll; level>=8 without any low-cost 3-star is high-cost; level 7 follows low-cost main carry",
                "card_order": "cards are ordered by slot_index; cards[0] is treated as the first/duo card",
                "card_prefix_ranking": "single/first-card rankings are grouped by card template prefix (彩/黄/蓝/白/其他) and ranked within each group",
                "team_rank": "per match, teams are ranked 1..N by each team's best individual rank",
                "card_granted_heroes": sorted(CARD_GRANTED_HEROES),
                "min_samples": {
                    "composition": args.min_comp_apps,
                    "entity": args.min_entity_apps,
                    "card": args.min_card_apps,
                },
            },
            "overview": {
                "quality": quality,
                "filtered_players": len(features),
                "baseline_rank": round(baseline, 3),
                "team_baseline_rank": round(team_baseline, 3),
                "validation": validation,
            },
            "rankings": {
                "compositions": comp_rows,
                "composition_recommendations": build_composition_recommendations(comp_rows),
                "composition_stages": stage_rows,
                "low_cost_carry_three_star_difficulty": low_cost_carry_difficulty,
                "duo_composition_synergy": duo_compositions,
                "cards": cards,
                "heroes_and_equipment": hero_equipment,
                "jiujiu": jiujiu_analysis,
                "traps": traps,
                "balance_targets": balance_targets,
            },
        }
    finally:
        conn.close()


def append_hero_equipment_block(lines: list[str], row: dict[str, Any]) -> None:
    hero = row["hero_stats"]
    tier = hero.get("tier")
    tier_label = f"{tier}费，" if tier is not None else ""
    lines.append(
        f"### {row['hero_name']}（{tier_label}主C率 {render_pct(hero['carry_rate'])}，"
        f"avg {hero['avg_rank']:.2f}，n={hero['appearances']}）"
    )
    if row.get("recommended_items"):
        lines.append("")
        lines.append("| 装备 | 修正名次 | 核选占比 | 核选优先级 | 样本 |")
        lines.append("| --- | ---: | ---: | --- | ---: |")
        for item in row["recommended_items"][:6]:
            lines.append(
                f"| {item['equipment_name']} | {item['adjusted_avg_rank']:.2f} | "
                f"{render_pct(item['selected_rate'])} | {item['selected_priority']} | {item['appearances']} |"
            )
    elif row.get("low_sample_observations"):
        lines.append("")
        lines.append("| 装备 | 修正名次 | 核选占比 | 核选优先级 | 样本 |")
        lines.append("| --- | ---: | ---: | --- | ---: |")
        for item in row["low_sample_observations"][:6]:
            lines.append(
                f"| {item['equipment_name']} | {item['adjusted_avg_rank']:.2f} | "
                f"{render_pct(item['selected_rate'])} | {item['selected_priority']} | {item['appearances']} |"
            )
    else:
        lines.append("- 出装样本不足：该棋子在过滤后样本中缺少足够单件出装记录，多为副C/前排或出装不完整。")
    if row.get("recommended_items") and row.get("low_sample_observations"):
        obs = " / ".join(
            f"{item['equipment_name']}(修正{item['adjusted_avg_rank']:.2f}, n={item['appearances']})"
            for item in row["low_sample_observations"][:3]
        )
        lines.append(f"- 低样本观察：{obs}")
    if row["recommended_sets"]:
        sets = "；".join(
            f"{item['equipment_set']}({item['adjusted_avg_rank']:.2f}, n={item['appearances']})"
            for item in row["recommended_sets"][:3]
        )
        lines.append(f"- 常见三件套：{sets}")
    lines.append("")


def render_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def render_card_metric(value: float | None, *, digits: int = 2) -> str:
    if value is None:
        return "—"
    return f"{value:.{digits}f}"


def append_comp_markdown(lines: list[str], comp: dict[str, Any]) -> None:
    stats = comp["stats"]
    lines.append(
        f"### {comp['label']}（{comp.get('play_style', '高费')}，{comp['confidence']}置信，n={stats['appearances']}）"
    )
    lines.append("")
    lines.append(
        f"- 表现：avg {stats['avg_rank']:.2f}，top4 {render_pct(stats['top4_rate'])}，吃鸡 {render_pct(stats['win_rate'])}。"
    )
    difficulty = comp.get("difficulty", {})
    if difficulty:
        lines.append(
            f"- 三星压力：阵容平均{difficulty.get('avg_three_star_units', 0):.2f}个三星棋子，"
            f"前四样本平均{difficulty.get('avg_top4_three_star_units', 0):.2f}个；"
            f"同行数{difficulty.get('avg_same_match_contest', 0):.2f}"
            f"（{difficulty.get('contest_basis', '阵容相似')}）。"
        )
    carries = "、".join(
        f"P{item.get('carry_rank', idx)} {item['hero_name']}({render_pct(item['share'])})"
        for idx, item in enumerate(comp["main_carries"], start=1)
    )
    lines.append(f"- 主C判断：{carries or '样本不足'}。")
    breakdown = "、".join(
        f"{row['play_style']}{render_pct(row['share'])}"
        for row in comp.get("play_style_breakdown", [])
    )
    if breakdown:
        lines.append(f"- 类型样本：{breakdown}。")
    if comp.get("carry_requirements"):
        req_text = "；".join(
            f"{row['hero_name']}建议至少{row['recommended_min_stars']}星"
            f"（前四平均{row['avg_stars_top4']:.1f}星，三件套{render_pct(row['three_item_rate'])}）"
            for row in comp["carry_requirements"][:3]
        )
        lines.append(f"- 主C成型门槛：{req_text}。")
        expensive_note = [
            row["hero_name"]
            for row in comp["carry_requirements"][:3]
            if row.get("high_cost_three_star_dependency")
        ]
        if expensive_note:
            lines.append(
                f"- 成型成本提醒：{'、'.join(expensive_note)} 的三星高费样本会拉高上限，常规推荐按 2 星门槛评估。"
            )
    if comp.get("carry_equipment_notes"):
        note_parts = []
        for note in comp["carry_equipment_notes"][:3]:
            important = [
                item
                for item in note["items"]
                if item["label"] in ("疑似刚需", "高价值")
            ][:3]
            if important:
                note_parts.append(
                    f"{note['hero_name']}："
                    + "、".join(
                        f"{item['equipment_name']}({item['label']}, 不带惩罚{item['without_item_penalty']})"
                        for item in important
                    )
                )
        if note_parts:
            lines.append(f"- 主C关键装备：{'；'.join(note_parts)}。")
    if comp.get("jiujiu_requirements"):
        jiujiu_parts = []
        for req in comp["jiujiu_requirements"]:
            wearers = "、".join(
                f"{item['hero_name']}({render_pct(item['share'])})"
                for item in req.get("recommended_wearers", [])[:3]
            )
            jiujiu_parts.append(
                f"{req['recommended_jiujiu']}（{render_pct(req['dependency_rate'])}样本需啾啾开"
                f"{req['trait']}-{req['target_tier']}，推荐穿戴：{wearers or '待观察'}）"
            )
        if jiujiu_parts:
            lines.append(f"- 啾啾成型：{'；'.join(jiujiu_parts)}。")
    bonds = "、".join(
        f"{item['bond']}({render_pct(item['share'])})" for item in comp["common_bonds"][:5]
    )
    lines.append(f"- 常见羁绊：{bonds or '无稳定羁绊'}。")
    lines.append("")
    lines.append("| 等级 | 来源 | 置信度 | 羁绊达成 | 棋子 |")
    lines.append("| ---: | --- | --- | --- | --- |")
    for level in ("7", "8", "9"):
        variant = comp["variants"][level]
        lines.append(
            f"| {level} | {variant['source']} | {variant['confidence']} | "
            f"{variant.get('bond_note', '—')} | "
            f"{'、'.join(variant['heroes'])} |"
        )
    lines.append("")


def render_md(data: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# 蛋仔派对当前环境分析报告")
    lines.append("")
    lines.append(f"- 生成时间: `{data['generated_at']}`")
    lines.append(f"- 数据源: `{data['data_source']}`")
    lines.append(f"- 分析器: `{data['methodology']['implementation']}`")
    lines.append("")

    quality = data["overview"]["quality"]
    lines.append("## 数据概览与过滤摘要")
    lines.append("")
    lines.append("| 指标 | 数值 |")
    lines.append("| --- | ---: |")
    for key in (
        "matches",
        "players",
        "heroes",
        "hero_equipments",
        "cards",
        "unknown_heroes",
        "unknown_cards",
        "unknown_equipment",
        "card_granted_heroes",
        "seven_eight_bot_matches",
        "bot_player_records_excluded",
    ):
        lines.append(f"| {key} | {quality[key]} |")
    lines.append(f"| filtered_players | {data['overview']['filtered_players']} |")
    lines.append("")

    comps = data["rankings"]["compositions"]
    heroes = data["rankings"]["heroes_and_equipment"]["heroes"]
    cards = data["rankings"]["cards"]["single_cards"]
    lines.append("## 当前环境结论摘要")
    lines.append("")
    if comps:
        top_comp = comps[0]
        lines.append(
            f"- 当前最优阵容族群：**{top_comp['label']}**，avg {top_comp['stats']['avg_rank']:.2f}，"
            f"top4 {render_pct(top_comp['stats']['top4_rate'])}，n={top_comp['stats']['appearances']}。"
        )
        recommendations = data["rankings"].get("composition_recommendations", {})
        for style in PLAY_STYLES:
            rows = recommendations.get(style, [])
            if not rows:
                continue
            top_style_comp = rows[0]
            lines.append(
                f"- {style}推荐首选：**{top_style_comp['label']}**，avg {top_style_comp['stats']['avg_rank']:.2f}，"
                f"top4 {render_pct(top_style_comp['stats']['top4_rate'])}，n={top_style_comp['stats']['appearances']}。"
            )
    if heroes:
        top_heroes = "、".join(
            f"{row['hero_name']}（carry {render_pct(row['carry_rate'])}, avg {row['avg_rank']:.2f}）"
            for row in heroes[:5]
        )
        lines.append(f"- 高投入核心棋子：{top_heroes}。")
    if cards:
        cards_by_prefix = data["rankings"]["cards"].get("single_cards_by_prefix", {})
        if cards_by_prefix:
            top_cards = "、".join(
                f"{prefix_type}类 {rows[0]['key']}（修正 {rows[0]['adjusted_avg_rank']:.2f}）"
                for prefix_type in CARD_PREFIX_TYPES
                if (rows := cards_by_prefix.get(prefix_type)) and rows[0]["appearances"] > 0
            )
        else:
            top_cards = "、".join(
                f"{row['key']}（修正 {row['adjusted_avg_rank']:.2f}）"
                for row in cards[:5]
            )
        lines.append(f"- 强势卡牌（分类型）：{top_cards}。")
    lines.append("")

    lines.append("## 赌狗阵容推荐")
    lines.append("")
    recommendations = data["rankings"].get("composition_recommendations", {})
    reroll_comps = recommendations.get("赌狗", [])
    if not reroll_comps:
        lines.append("当前样本下没有达到阈值的稳定赌狗阵容。")
        lines.append("")
    for comp in reroll_comps[:8]:
        append_comp_markdown(lines, comp)

    lines.append("## 高费阵容推荐")
    lines.append("")
    high_cost_comps = recommendations.get("高费", [])
    if not high_cost_comps:
        lines.append("当前样本下没有达到阈值的稳定高费阵容。")
        lines.append("")
    for comp in high_cost_comps[:8]:
        append_comp_markdown(lines, comp)

    lines.append("## 阵容成型难度与热门程度")
    lines.append("")
    lines.append(
        "强度排名综合成型后表现（平均名次、前四率、吃鸡率）与成型难度（未成型后四率、同行压力、装备完整率）。"
    )
    lines.append("")
    strength_sorted = sorted(
        comps,
        key=lambda row: (
            row.get("strength_rank", 999),
            row.get("overall_strength_score", 99),
        ),
    )
    lines.append("| 强度排名 | 阵容 | 类型 | 难度 | 热门 | 平均三星 | 后四未成型率 | 同行数 | 同行口径 | 出场率 |")
    lines.append("| ---: | --- | --- | --- | --- | ---: | ---: | ---: | --- | ---: |")
    for comp in strength_sorted[:12]:
        difficulty = comp["difficulty"]
        popularity = comp["popularity"]
        lines.append(
            f"| {comp.get('strength_rank', '—')} | {comp['label']} | {comp.get('play_style', '高费')} | "
            f"{difficulty['label']} | {popularity['label']} | "
            f"{difficulty.get('avg_three_star_units', 0):.2f} | "
            f"{render_pct(difficulty['unfinished_bottom_rate'])} | "
            f"{difficulty['avg_same_match_contest']:.2f} | "
            f"{difficulty.get('contest_basis', '阵容相似')} | {render_pct(popularity['pick_rate'])} |"
        )
    lines.append("")

    low_cost_difficulty = data["rankings"].get("low_cost_carry_three_star_difficulty", [])
    if low_cost_difficulty:
        lines.append("### 低费主C三星难度")
        lines.append("")
        lines.append("同场多家阵容需要同一个低费3星主C时，即使阵容路线不同，也计入同行压力。")
        lines.append("")
        lines.append("| 棋子 | 费用 | 平均同场需求 | 最高同场需求 | 多家需求对局率 | 平均名次 | 样本 | 主要阵容 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
        for row in [item for item in low_cost_difficulty if item.get("is_low_cost")][:12]:
            strategies = "；".join(
                f"{item['label']}({item['samples']})" for item in row.get("top_strategies", [])[:3]
            )
            lines.append(
                f"| {row['hero_name']} | {row.get('tier') or '—'} | "
                f"{row['avg_same_match_needers']:.2f} | {row['max_same_match_needers']} | "
                f"{render_pct(row['multi_needer_match_rate'])} | {row['avg_rank']:.2f} | "
                f"{row['appearances']} | {strategies or '样本不足'} |"
            )
        lines.append("")
        lines.append("### 主C三星需求热门程度")
        lines.append("")
        lines.append("| 棋子 | 费用 | 平均同场需求 | 最高同场需求 | 多家需求对局率 | 需要它三星的阵容 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
        for row in low_cost_difficulty[:12]:
            strategies = "；".join(
                f"{item['label']}({item['samples']})" for item in row.get("top_strategies", [])[:3]
            )
            lines.append(
                f"| {row['hero_name']} | {row.get('tier') or '—'} | "
                f"{row['avg_same_match_needers']:.2f} | {row['max_same_match_needers']} | "
                f"{render_pct(row['multi_needer_match_rate'])} | {strategies or '样本不足'} |"
            )
        lines.append("")

    lines.append("## 卡牌强度分析")
    lines.append("")
    lines.append(
        "卡牌顺序按 `slot_index` 统计，第一张卡牌视为双人配合重点；队伍排名按每局队伍最高个人名次重新排序为 1-4。"
    )
    lines.append("单卡与第一卡强度按模板前缀类型（彩/黄/蓝/白/其他）分组，并在各组内优先按样本数排序。")
    lines.append("")
    cards_by_prefix = data["rankings"]["cards"].get("single_cards_by_prefix", {})
    if cards_by_prefix:
        for prefix_type in CARD_PREFIX_TYPES:
            prefix_rows = cards_by_prefix.get(prefix_type, [])
            if not prefix_rows:
                continue
            lines.append(f"### {prefix_type}类单卡")
            lines.append("")
            lines.append("| 组内排名 | 卡牌 | 样本 | 每局平均 | 修正名次 | 平均名次 | 前四率 | 吃鸡率 |")
            lines.append("| ---: | --- | ---: | ---: | ---: | ---: | ---: | ---: |")
            for row in prefix_rows:
                rank_label = "—" if row.get("prefix_rank") is None else str(row["prefix_rank"])
                lines.append(
                    f"| {rank_label} | {row['key']} | {row['appearances']} | "
                    f"{row.get('avg_appearances_per_match', 0):.2f} | "
                    f"{render_card_metric(row.get('adjusted_avg_rank'))} | "
                    f"{render_card_metric(row.get('avg_rank'))} | "
                    f"{render_pct(row.get('top4_rate'))} | "
                    f"{render_pct(row.get('win_rate'))} |"
                )
            lines.append("")
    else:
        lines.append("| 卡牌 | 样本 | 每局平均 | 修正名次 | 平均名次 | 前四率 | 吃鸡率 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | ---: |")
        for row in cards[:20]:
            lines.append(
                f"| {row['key']} | {row['appearances']} | {row.get('avg_appearances_per_match', 0):.2f} | "
                f"{row['adjusted_avg_rank']:.2f} | {row['avg_rank']:.2f} | "
                f"{render_pct(row['top4_rate'])} | {render_pct(row['win_rate'])} |"
            )
        lines.append("")
    first_cards = data["rankings"]["cards"]["first_card_rankings"]
    first_cards_by_prefix = data["rankings"]["cards"].get("first_card_rankings_by_prefix", {})
    if first_cards_by_prefix:
        lines.append("### 第一张卡牌强度（分类型）")
        lines.append("")
        for prefix_type in CARD_PREFIX_TYPES:
            prefix_rows = first_cards_by_prefix.get(prefix_type, [])
            if not prefix_rows:
                continue
            lines.append(f"#### {prefix_type}类第一卡")
            lines.append("")
            lines.append("| 组内排名 | 第一卡 | 样本 | 每局平均 | 修正名次 | 平均名次 | 前四率 |")
            lines.append("| ---: | --- | ---: | ---: | ---: | ---: | ---: |")
            for row in prefix_rows[:8]:
                lines.append(
                    f"| {row['prefix_rank']} | {row['key']} | {row['appearances']} | "
                    f"{row.get('avg_appearances_per_match', 0):.2f} | {row['adjusted_avg_rank']:.2f} | "
                    f"{row['avg_rank']:.2f} | {render_pct(row['top4_rate'])} |"
                )
            lines.append("")
    elif first_cards:
        lines.append("### 第一张卡牌强度")
        lines.append("")
        lines.append("| 第一卡 | 样本 | 每局平均 | 修正名次 | 平均名次 | 前四率 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for row in first_cards[:12]:
            lines.append(
                f"| {row['key']} | {row['appearances']} | {row.get('avg_appearances_per_match', 0):.2f} | "
                f"{row['adjusted_avg_rank']:.2f} | {row['avg_rank']:.2f} | {render_pct(row['top4_rate'])} |"
            )
        lines.append("")
    blue_team_cards = data["rankings"]["cards"].get("blue_cards_team_rank", [])
    if blue_team_cards:
        lines.append("### 蓝卡队伍排名视角")
        lines.append("")
        lines.append("蓝卡按双人卡牌处理，额外使用队伍排名评估；队伍排名按每局队伍最高个人名次重新排序。")
        lines.append("")
        lines.append("| 蓝卡 | 样本 | 每局平均 | 修正队伍名次 | 队伍名次 | 队伍前二率 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for row in blue_team_cards[:12]:
            lines.append(
                f"| {row['key']} | {row['appearances']} | {row.get('avg_appearances_per_match', 0):.2f} | "
                f"{row['adjusted_avg_rank']:.2f} | {row['avg_rank']:.2f} | {render_pct(row.get('team_top2_rate', 0))} |"
            )
        lines.append("")
    first_duos = data["rankings"]["cards"]["first_card_duo_synergy"]
    if first_duos:
        lines.append("### 双人第一卡配合")
        lines.append("")
        lines.append("| 第一卡组合 | 修正队伍名次 | 队伍名次 | 样本 |")
        lines.append("| --- | ---: | ---: | ---: |")
        for row in first_duos[:12]:
            lines.append(
                f"| {row['key']} | {row['adjusted_avg_rank']:.2f} | {row['avg_rank']:.2f} | {row['appearances']} |"
            )
        lines.append("")
    contribution = data["rankings"]["cards"]["duo_card_contribution"]
    if contribution:
        lines.append("### 第一卡贡献增量观察")
        lines.append("")
        lines.append("| 第一卡组合 | 队伍名次 | 相对基线提升 | 持有者折算提升 | 队伍前二率 | 样本 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
        for row in contribution[:12]:
            lines.append(
                f"| {row['key']} | {row['team_avg_rank']:.2f} | {row['team_lift_vs_baseline']:.2f} | "
                f"{row['team_lift_vs_holder']:.2f} | {render_pct(row['team_top2_rate'])} | {row['appearances']} |"
            )
        lines.append("")
    comp_cards = data["rankings"]["cards"]["composition_cards"]
    if comp_cards:
        lines.append("### 阵容内卡牌观察")
        lines.append("")
        for row in comp_cards[:5]:
            picks = "、".join(
                f"{card['key']}({card['adjusted_avg_rank']:.2f}, n={card['appearances']}, 每局{card.get('avg_appearances_per_match', 0):.2f})"
                for card in row["cards"][:5]
            )
            lines.append(f"- {row['family_label']}：{picks}")
        lines.append("")
    teammate_cards = data["rankings"]["cards"]["teammate_card_pairs_observation"]
    if teammate_cards:
        lines.append("### 队友卡牌配合观察")
        lines.append("")
        lines.append("以下组合为低置信观察，需要结合样本量判断：")
        lines.append("")
        for row in teammate_cards[:10]:
            lines.append(
                f"- {row['key']}：修正 {row['adjusted_avg_rank']:.2f}，top4 {render_pct(row['top4_rate'])}，n={row['appearances']}"
            )
        lines.append("")

    duo_comps = data["rankings"].get("duo_composition_synergy", [])
    if duo_comps:
        lines.append("## 双人阵容配合推荐")
        lines.append("")
        lines.append("基于同队两家的最终策略组合与重算队伍排名，仅作双排分工参考。")
        lines.append("")
        lines.append("| 阵容组合 | 队伍名次 | 相对基线提升 | 队伍前二率 | 队伍吃鸡率 | 样本 | 置信度 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | ---: | --- |")
        for row in duo_comps[:12]:
            lines.append(
                f"| {row['strategy_a']} + {row['strategy_b']} | {row['team_avg_rank']:.2f} | "
                f"{row['team_lift_vs_baseline']:.2f} | {render_pct(row['team_top2_rate'])} | "
                f"{render_pct(row['team_win_rate'])} | {row['appearances']} | {row['confidence']} |"
            )
        lines.append("")

    lines.append("## 强势棋子与装备推荐")
    lines.append("")
    recommendations = data["rankings"]["heroes_and_equipment"]["carry_equipment_recommendations"]
    equipment_xlsx = data.get("outputs", {}).get("equipment_xlsx", "data/latest_meta_analysis_equipment.xlsx")
    equipment_html = data.get("outputs", {}).get("equipment_html", "data/latest_meta_analysis_equipment.html")
    with_items = sum(1 for row in recommendations if row.get("has_equipment_data"))
    lines.append(
        f"每位英雄的详细出装推荐已导出至 **`{equipment_xlsx}`**（Excel）与 **`{equipment_html}`**（可筛选 HTML），"
        f"覆盖过滤后样本中出现的全部 **{len(recommendations)}** 个棋子；"
        f"其中 **{with_items}** 个有可靠或低样本出装记录。"
    )
    lines.append("")
    lines.append("排序：费用从低到高，同费按主C投入与名称。")
    lines.append("")
    top_carries = sorted(
        recommendations,
        key=lambda row: (
            -row["hero_stats"].get("carry_appearances", 0),
            row["hero_stats"].get("adjusted_avg_rank", 99),
        ),
    )[:8]
    if top_carries:
        lines.append("### 高投入主C速览")
        lines.append("")
        for row in top_carries:
            hero = row["hero_stats"]
            top_items = row.get("recommended_items") or row.get("low_sample_observations") or []
            item_hint = "、".join(item["equipment_name"] for item in top_items[:3]) or "出装样本不足"
            lines.append(
                f"- **{row['hero_name']}**：主C率 {render_pct(hero['carry_rate'])}，"
                f"avg {hero['avg_rank']:.2f}，优先 {item_hint}（详见 Excel）"
            )
        lines.append("")

    lines.append("## 羁绊表现与啾啾影响")
    lines.append("")
    bonds = data["rankings"]["heroes_and_equipment"]["bonds"]
    lines.append("| 羁绊档位 | 修正名次 | 平均名次 | 前四率 | 样本 |")
    lines.append("| --- | ---: | ---: | ---: | ---: |")
    for row in bonds[:20]:
        lines.append(
            f"| {row['key']} | {row['adjusted_avg_rank']:.2f} | {row['avg_rank']:.2f} | "
            f"{render_pct(row['top4_rate'])} | {row['appearances']} |"
        )
    jiujiu = data["rankings"]["heroes_and_equipment"]["jiujiu_bonds"]
    if jiujiu:
        lines.append("")
        lines.append("### 啾啾辅助羁绊")
        lines.append("")
        for row in jiujiu[:12]:
            lines.append(
                f"- {row['key']}：修正 {row['adjusted_avg_rank']:.2f}，top4 {render_pct(row['top4_rate'])}，n={row['appearances']}"
            )
    jiujiu_analysis = data["rankings"].get("jiujiu", {})
    rankings = jiujiu_analysis.get("jiujiu_rankings", [])
    if rankings:
        lines.append("")
        lines.append("### 啾啾强度排名")
        lines.append("")
        lines.append("| 啾啾 | 有效样本 | 有效率 | 有效修正 | 前四率 | 推荐阵容/穿戴棋子 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
        for row in rankings[:16]:
            comps = "；".join(
                f"{comp['family_label']}→"
                f"{'、'.join(wearer['hero_name'] for wearer in comp.get('recommended_wearers', [])[:2]) or '待观察'}"
                f"({comp['appearances']})"
                for comp in row["recommended_comps"][:2]
            )
            heroes = "；".join(
                f"{hero['hero_name']}({hero['appearances']})"
                for hero in row.get("recommended_heroes", [])[:2]
            )
            effective_stats = row.get("effective_stats") or row
            targets = comps or heroes or "有效样本不足"
            lines.append(
                f"| {row['equipment_name']} | {row['effective_appearances']} | {render_pct(row['effective_rate'])} | "
                f"{effective_stats['adjusted_avg_rank']:.2f} | {render_pct(effective_stats['top4_rate'])} | {targets} |"
            )
        lines.append("")
    lines.append("")

    lines.append("## 版本陷阱分析")
    lines.append("")
    traps = data["rankings"]["traps"]
    for label, key in (
        ("阵容", "compositions"),
        ("棋子", "heroes"),
        ("卡牌", "cards"),
        ("羁绊", "bonds"),
        ("装备", "equipment"),
    ):
        rows = traps[key]
        if not rows:
            continue
        lines.append(f"### {label}")
        lines.append("")
        for row in rows[:8]:
            name = row.get("label") or row.get("hero_name") or row.get("key") or row.get("equipment_name")
            stats = row.get("stats", row)
            prefix_note = ""
            if key == "cards" and row.get("prefix_type"):
                prefix_note = f"[{row['prefix_type']}类内] "
            trap_reason = row.get("trap_reason")
            reason_note = f"，{trap_reason}" if trap_reason else ""
            lines.append(
                f"- {prefix_note}{name}：avg {stats['avg_rank']:.2f}，top4 {render_pct(stats['top4_rate'])}，"
                f"n={stats['appearances']}{reason_note}"
            )
        lines.append("")
    pressure_bonds = traps.get("formation_pressure_bonds", [])
    if pressure_bonds:
        lines.append("### 未成型压力（并入成熟阵容）")
        lines.append("")
        for row in pressure_bonds[:8]:
            lines.append(
                f"- {row['key']}：avg {row['avg_rank']:.2f}，top4 {render_pct(row['top4_rate'])}，"
                f"n={row['appearances']}，{row.get('trap_reason', '计入成型难度')}"
            )
        lines.append("")

    balance = data["rankings"]["balance_targets"]
    lines.append("## 平衡性调整追踪")
    lines.append("")
    if any(balance.values()):
        for key, values in balance.items():
            lines.append(f"- {key}: {', '.join(values) if values else '无'}")
    else:
        lines.append("本次未提供平衡性调整文本，跳过定向追踪。")
    lines.append("")

    lines.append("## 数据质量与可信度说明")
    lines.append("")
    validation = data["overview"]["validation"]
    lines.append(f"- 配置未映射棋子：{', '.join(validation['missing_config_heroes']) or '无'}。")
    lines.append(f"- 啾啾未映射装备：{', '.join(validation['jiujiu_unmapped']) or '无'}。")
    lines.append("- 低样本阵容、卡牌组合和队友配合只作为观察，不应单独作为上分结论。")
    return "\n".join(lines) + "\n"


def esc(value: Any) -> str:
    return html.escape(str(value), quote=True)


def unique_route_bonds(comp: dict[str, Any]) -> str:
    route: list[str] = []
    mature_bond = comp.get("mature_stage", {}).get("bond") or comp.get("main_bond")
    for stage in comp.get("transition_stages", []):
        bond = stage.get("bond")
        if not bond or bond == mature_bond or bond in route:
            continue
        route.append(bond)
    if mature_bond and mature_bond not in route:
        route.append(mature_bond)
    return " → ".join(route[:4]) or str(comp.get("main_bond", "样本不足"))


def html_comp_card(comp: dict[str, Any]) -> str:
    mature = comp.get("mature_stage", {})
    variants = mature.get("variants", comp.get("variants", {}))
    variant = variants.get("8") or variants.get("9") or variants.get("7") or {}
    heroes = " / ".join(variant.get("heroes", [])[:9]) or "样本不足"
    req_text = "；".join(
        f"{row['hero_name']} {row['recommended_min_stars']}星起(前四均{row['avg_stars_top4']:.1f})"
        for row in comp.get("carry_requirements", [])[:3]
    )
    carry_text = " > ".join(
        f"P{item.get('carry_rank', idx)} {item['hero_name']}"
        for idx, item in enumerate(comp.get("main_carries", [])[:3], start=1)
    )
    jiujiu_text = "；".join(
        f"{req['recommended_jiujiu']}→"
        + "、".join(item["hero_name"] for item in req.get("recommended_wearers", [])[:2])
        for req in comp.get("jiujiu_requirements", [])
    )
    difficulty = comp.get("difficulty", {})
    star_pressure = (
        f"三星均{difficulty.get('avg_three_star_units', 0):.2f} / "
        f"同行{difficulty.get('avg_same_match_contest', 0):.2f}"
        f"({difficulty.get('contest_basis', '阵容相似')})"
        if difficulty
        else "样本不足"
    )
    bond_note = variant.get("bond_note", "")
    breakdown = " · ".join(
        f"{row['play_style']}{render_pct(row['share'])}"
        for row in comp.get("play_style_breakdown", [])[:2]
    )
    return f"""
    <article class="comp-card">
      <div class="comp-head">
        <span class="badge">{esc(comp.get('play_style', '高费'))}</span>
        <h2>{esc(comp['label'])}</h2>
      </div>
      <div class="metrics">
        <b>Avg {comp['stats']['avg_rank']:.2f}</b>
        <b>Top4 {render_pct(comp['stats']['top4_rate'])}</b>
        <b>n={comp['stats']['appearances']}</b>
      </div>
      <p><strong>主C：</strong>{esc(carry_text or '样本不足')}</p>
      <p><strong>成型：</strong>{esc(req_text or '样本不足')}</p>
      <p><strong>压力：</strong>{esc(star_pressure)}</p>
      <p><strong>路线：</strong>{esc(unique_route_bonds(comp))}</p>
      <p><strong>羁绊：</strong>{esc(bond_note or '—')}</p>
      <p><strong>啾啾：</strong>{esc(jiujiu_text or '无明确依赖')}</p>
      <p><strong>类型样本：</strong>{esc(breakdown or comp.get('play_style', '高费'))}</p>
      <p class="lineup">{esc(heroes)}</p>
    </article>
    """


def html_list_items(rows: list[dict[str, Any]], value_key: str = "key") -> str:
    if not rows:
        return "<li>样本不足</li>"
    items = []
    for row in rows:
        label = row.get(value_key) or row.get("label") or row.get("hero_name") or row.get("equipment_name")
        items.append(f"<li>{esc(label)}</li>")
    return "\n".join(items)


def html_trap_group(title: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        items = "<li>暂无稳定陷阱</li>"
    else:
        items = "\n".join(
            f"<li><b>{esc(row.get('label') or row.get('hero_name') or row.get('key'))}</b>"
            f"<span>avg {row.get('stats', row)['avg_rank']:.2f}</span></li>"
            for row in rows[:2]
        )
    return f"<div><h4>{esc(title)}</h4><ul>{items}</ul></div>"


def html_prefix_card_sections(cards_by_prefix: dict[str, list[dict[str, Any]]], limit: int = 3) -> str:
    sections: list[str] = []
    for prefix_type in CARD_PREFIX_TYPES:
        rows = cards_by_prefix.get(prefix_type, [])
        rows = [row for row in rows if row["appearances"] > 0][:limit]
        if not rows:
            continue
        items = "\n".join(
            f"<li><b>{esc(row['key'])}</b><span>n={row['appearances']} / 每局 {row.get('avg_appearances_per_match', 0):.2f}</span></li>"
            for row in rows
        )
        sections.append(f"<div><h4>{esc(prefix_type)}类</h4><ul>{items}</ul></div>")
    return "".join(sections) or "<div><h4>单卡</h4><ul><li>样本不足</li></ul></div>"


def split_strategy_label(label: str) -> tuple[str, str]:
    if " / " in label:
        bond, carries = label.split(" / ", 1)
        return bond.strip(), carries.strip()
    return label.strip(), ""


def html_strategy_cell(label: str) -> str:
    bond, carries = split_strategy_label(label)
    carries_html = (
        f'<div class="carries">{esc(carries)}</div>' if carries else ""
    )
    return (
        f'<div class="strategy-cell">'
        f'<div class="bond">{esc(bond)}</div>'
        f"{carries_html}"
        f"</div>"
    )


def html_top_strategies_cell(strategies: list[dict[str, Any]], limit: int = 3) -> str:
    if not strategies:
        return '<span class="muted">样本不足</span>'
    items: list[str] = []
    for item in strategies[:limit]:
        bond, _ = split_strategy_label(item["label"])
        title = esc(f"{item['label']}({item['samples']})")
        items.append(
            f'<span class="strategy-brief" title="{title}">'
            f"{esc(bond)}({item['samples']})</span>"
        )
    return '<div class="strategy-list">' + "".join(items) + "</div>"


def _html_table_cell(
    text: str,
    *,
    sort_value: str | float | int | None = None,
    html: str | None = None,
) -> dict[str, Any]:
    return {
        "text": text,
        "sort": str(sort_value if sort_value is not None else text),
        "html": html,
    }


def html_sortable_table_page(
    *,
    title: str,
    subtitle: str,
    note: str,
    headers: list[tuple[str, str]],
    rows: list[list[dict[str, Any]]],
) -> str:
    header_parts: list[str] = []
    for index, (label, sort_type) in enumerate(headers):
        th_class = "sortable sort-asc" if index == 0 else "sortable"
        data_dir = ' data-dir="asc"' if index == 0 else ""
        header_parts.append(
            f'<th class="{th_class}" data-sort="{esc(sort_type)}"{data_dir}>{esc(label)}</th>'
        )
    header_html = "\n".join(header_parts)
    body_rows: list[str] = []
    for row in rows:
        cells = "\n".join(
            (
                f'<td data-sort="{esc(cell["sort"])}">{cell["html"]}</td>'
                if cell.get("html")
                else f'<td data-sort="{esc(cell["sort"])}">{esc(cell["text"])}</td>'
            )
            for cell in row
        )
        body_rows.append(f"<tr>{cells}</tr>")
    body_html = "\n".join(body_rows) or '<tr><td colspan="{0}">样本不足</td></tr>'.format(
        len(headers)
    )
    generated = esc(subtitle.split("·")[-1].strip() if "·" in subtitle else subtitle)
    initial_sort_label = esc(headers[0][0]) if headers else ""
    note_html = f'<div class="note">{esc(note)}</div>' if note.strip() else ""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #10131f;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
      color: #f8fafc;
    }}
    .poster {{
      width: 1080px;
      max-width: 100%;
      margin: 0 auto;
      padding: 36px 32px 42px;
      background:
        radial-gradient(circle at 10% 0%, rgba(91,141,239,.45), transparent 28%),
        radial-gradient(circle at 90% 4%, rgba(255,189,89,.32), transparent 24%),
        linear-gradient(145deg, #151a2d 0%, #0c1020 100%);
    }}
    header {{ margin-bottom: 18px; }}
    .eyebrow {{ color: #fbbf24; font-weight: 800; letter-spacing: 4px; font-size: 14px; }}
    .title-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 12px 20px;
      margin: 8px 0;
    }}
    h1 {{ font-size: 40px; margin: 0; line-height: 1.1; }}
    .sort-status {{
      color: #93c5fd;
      font-size: 36px;
      font-weight: 600;
      line-height: 1.1;
      white-space: nowrap;
    }}
    .sub {{ color: #cbd5e1; font-size: 18px; line-height: 1.45; }}
    .note {{ color: #94a3b8; font-size: 15px; margin: 10px 0 16px; line-height: 1.45; }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 20px;
      background: rgba(255,255,255,.06);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      font-size: 14px;
    }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid rgba(255,255,255,.08);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      position: sticky;
      top: 0;
      background: rgba(21,26,45,.96);
      color: #fde68a;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }}
    th.sort-asc::after {{ content: " ▲"; color: #93c5fd; }}
    th.sort-desc::after {{ content: " ▼"; color: #93c5fd; }}
    tr:hover td {{ background: rgba(255,255,255,.04); }}
    td {{ color: #dbeafe; line-height: 1.35; }}
    .strategy-cell .bond {{ color: #fde68a; font-weight: 700; }}
    .strategy-cell .carries {{
      color: #bfdbfe;
      font-size: 13px;
      margin-top: 4px;
      line-height: 1.3;
      overflow-wrap: anywhere;
    }}
    .strategy-list {{
      display: flex;
      flex-direction: column;
      gap: 4px;
      min-width: 120px;
      max-width: 220px;
    }}
    .strategy-brief {{
      display: inline-block;
      background: rgba(255,255,255,.08);
      border-radius: 8px;
      padding: 3px 8px;
      font-size: 13px;
      color: #e0f2fe;
      overflow-wrap: anywhere;
    }}
    .muted {{ color: #94a3b8; }}
    footer {{ margin-top: 18px; color: #94a3b8; font-size: 14px; text-align: center; }}
  </style>
</head>
<body>
<main class="poster">
  <header>
    <div class="eyebrow">DZPPQ META TABLE</div>
    <div class="title-row">
      <h1>{esc(title)}</h1>
      <span class="sort-status" id="sort-status">当前按 {initial_sort_label} 升序</span>
    </div>
    <div class="sub">{esc(subtitle)}</div>
    {note_html}
  </header>
  <div class="table-wrap">
    <table class="sortable-table">
      <thead><tr>{header_html}</tr></thead>
      <tbody>{body_html}</tbody>
    </table>
  </div>
  <footer>{generated}</footer>
</main>
<script>
const sortStatusEl = document.getElementById("sort-status");

function updateSortStatus(th, dir) {{
  if (!sortStatusEl || !th) {{
    return;
  }}
  const label = th.textContent.replace(/\\s*[▲▼]\\s*$/, "").trim();
  const direction = dir === "desc" ? "降序" : "升序";
  sortStatusEl.textContent = `当前按 ${{label}} ${{direction}}`;
}}

document.querySelectorAll("th.sortable").forEach((th, colIndex) => {{
  th.addEventListener("click", () => {{
    const table = th.closest("table");
    const tbody = table.querySelector("tbody");
    const rows = Array.from(tbody.querySelectorAll("tr"));
    const sortType = th.dataset.sort || "text";
    const isActive = th.classList.contains("sort-asc") || th.classList.contains("sort-desc");
    const newDir = isActive && th.dataset.dir === "asc" ? "desc" : "asc";
    table.querySelectorAll("th.sortable").forEach((header) => {{
      header.dataset.dir = "";
      header.classList.remove("sort-asc", "sort-desc");
    }});
    th.dataset.dir = newDir;
    th.classList.add(newDir === "asc" ? "sort-asc" : "sort-desc");
    updateSortStatus(th, newDir);
    rows.sort((left, right) => {{
      const leftVal = left.cells[colIndex]?.dataset.sort ?? "";
      const rightVal = right.cells[colIndex]?.dataset.sort ?? "";
      if (sortType === "numeric") {{
        const leftNum = parseFloat(leftVal);
        const rightNum = parseFloat(rightVal);
        const safeLeft = Number.isFinite(leftNum) ? leftNum : Number.MAX_VALUE;
        const safeRight = Number.isFinite(rightNum) ? rightNum : Number.MAX_VALUE;
        return newDir === "asc" ? safeLeft - safeRight : safeRight - safeLeft;
      }}
      const cmp = String(leftVal).localeCompare(String(rightVal), "zh-CN");
      return newDir === "asc" ? cmp : -cmp;
    }});
    rows.forEach((row) => tbody.appendChild(row));
  }});
}});

const initialSortHeader = document.querySelector("th.sort-asc, th.sort-desc");
if (initialSortHeader) {{
  updateSortStatus(initialSortHeader, initialSortHeader.dataset.dir || "asc");
}}
</script>
</body>
</html>
"""


def render_card_prefix_table_html(data: dict[str, Any], prefix_type: str) -> str:
    quality = data["overview"]["quality"]
    generated = data["generated_at"].split("T")[0]
    cards = data["rankings"]["cards"]
    prefix_rows = cards.get("single_cards_by_prefix", {}).get(prefix_type, [])
    subtitle = (
        f"基于 {quality['matches']} 局 / {data['overview']['filtered_players']} 条过滤后玩家记录 · {generated}"
    )
    note = CARD_MERGE_NOTES.get(prefix_type, "")

    headers: list[tuple[str, str]] = [
        ("组内排名", "numeric"),
        ("卡牌", "text"),
        ("样本", "numeric"),
        ("每局平均", "numeric"),
        ("修正名次", "numeric"),
        ("平均名次", "numeric"),
        ("前四率", "numeric"),
        ("吃鸡率", "numeric"),
    ]
    if prefix_type == "蓝":
        headers.extend(
            [
                ("修正队伍名次", "numeric"),
                ("队伍名次", "numeric"),
                ("队伍前二率", "numeric"),
            ]
        )

    team_rank_map = {
        row["key"]: row
        for row in cards.get("blue_cards_team_rank_by_prefix", {}).get("蓝", [])
    }
    table_rows: list[list[dict[str, Any]]] = []
    for row in prefix_rows:
        rank_label = "—" if row.get("prefix_rank") is None else str(row["prefix_rank"])
        cells = [
            _html_table_cell(rank_label, sort_value=row.get("prefix_rank", 9999)),
            _html_table_cell(row["key"], sort_value=row["key"]),
            _html_table_cell(str(row["appearances"]), sort_value=row["appearances"]),
            _html_table_cell(
                f"{row.get('avg_appearances_per_match', 0):.2f}",
                sort_value=row.get("avg_appearances_per_match", 0),
            ),
            _html_table_cell(
                render_card_metric(row.get("adjusted_avg_rank")),
                sort_value=row.get("adjusted_avg_rank", 999),
            ),
            _html_table_cell(
                render_card_metric(row.get("avg_rank")),
                sort_value=row.get("avg_rank", 999),
            ),
            _html_table_cell(
                render_pct(row.get("top4_rate")),
                sort_value=row.get("top4_rate", -1),
            ),
            _html_table_cell(
                render_pct(row.get("win_rate")),
                sort_value=row.get("win_rate", -1),
            ),
        ]
        if prefix_type == "蓝":
            team = team_rank_map.get(row["key"], {})
            cells.extend(
                [
                    _html_table_cell(
                        render_card_metric(team.get("adjusted_avg_rank")) if team else "—",
                        sort_value=team.get("adjusted_avg_rank", 999),
                    ),
                    _html_table_cell(
                        render_card_metric(team.get("avg_rank")) if team else "—",
                        sort_value=team.get("avg_rank", 999),
                    ),
                    _html_table_cell(
                        render_pct(team.get("team_top2_rate")) if team else "—",
                        sort_value=team.get("team_top2_rate", -1),
                    ),
                ]
            )
        table_rows.append(cells)

    return html_sortable_table_page(
        title=f"{prefix_type}类单卡排名",
        subtitle=subtitle,
        note=note,
        headers=headers,
        rows=table_rows,
    )


def render_duo_composition_table_html(data: dict[str, Any]) -> str:
    quality = data["overview"]["quality"]
    generated = data["generated_at"].split("T")[0]
    duo_rows = data["rankings"].get("duo_composition_synergy", [])
    subtitle = (
        f"基于 {quality['matches']} 局 / {data['overview']['filtered_players']} 条过滤后玩家记录 · {generated}"
    )
    note = "基于同队两家的最终策略组合与重算队伍排名，仅作双排分工参考；阵容分列展示以避免拥挤。"

    headers = [
        ("阵容A", "text"),
        ("阵容B", "text"),
        ("队伍名次", "numeric"),
        ("相对基线提升", "numeric"),
        ("队伍前二率", "numeric"),
        ("队伍吃鸡率", "numeric"),
        ("样本", "numeric"),
        ("置信度", "text"),
    ]
    table_rows: list[list[dict[str, Any]]] = []
    for row in duo_rows:
        table_rows.append(
            [
                {
                    "text": row["strategy_a"],
                    "sort": row["strategy_a"],
                    "html": html_strategy_cell(row["strategy_a"]),
                },
                {
                    "text": row["strategy_b"],
                    "sort": row["strategy_b"],
                    "html": html_strategy_cell(row["strategy_b"]),
                },
                _html_table_cell(
                    f"{row['team_avg_rank']:.2f}",
                    sort_value=row["team_avg_rank"],
                ),
                _html_table_cell(
                    f"{row['team_lift_vs_baseline']:.2f}",
                    sort_value=row["team_lift_vs_baseline"],
                ),
                _html_table_cell(
                    render_pct(row["team_top2_rate"]),
                    sort_value=row["team_top2_rate"],
                ),
                _html_table_cell(
                    render_pct(row["team_win_rate"]),
                    sort_value=row["team_win_rate"],
                ),
                _html_table_cell(str(row["appearances"]), sort_value=row["appearances"]),
                _html_table_cell(row["confidence"], sort_value=row["confidence"]),
            ]
        )

    return html_sortable_table_page(
        title="双人阵容配合推荐",
        subtitle=subtitle,
        note=note,
        headers=headers,
        rows=table_rows,
    )


def render_low_cost_carry_table_html(data: dict[str, Any]) -> str:
    quality = data["overview"]["quality"]
    generated = data["generated_at"].split("T")[0]
    rows = [
        row
        for row in data["rankings"].get("low_cost_carry_three_star_difficulty", [])
        if row.get("is_low_cost")
    ]
    subtitle = (
        f"基于 {quality['matches']} 局 / {data['overview']['filtered_players']} 条过滤后玩家记录 · {generated}"
    )
    note = (
        "同场多家阵容需要同一个低费3星主C时，即使阵容路线不同，也计入同行压力；"
        "主要阵容仅展示羁绊标题与样本数，完整阵容可悬停查看。"
    )

    headers = [
        ("棋子", "text"),
        ("费用", "numeric"),
        ("平均同场需求", "numeric"),
        ("最高同场需求", "numeric"),
        ("多家需求对局率", "numeric"),
        ("平均名次", "numeric"),
        ("样本", "numeric"),
        ("主要阵容", "text"),
    ]
    table_rows: list[list[dict[str, Any]]] = []
    for row in rows:
        strategies = row.get("top_strategies", [])
        table_rows.append(
            [
                _html_table_cell(row["hero_name"], sort_value=row["hero_name"]),
                _html_table_cell(
                    str(row.get("tier") or "—"),
                    sort_value=row.get("tier") or 99,
                ),
                _html_table_cell(
                    f"{row['avg_same_match_needers']:.2f}",
                    sort_value=row["avg_same_match_needers"],
                ),
                _html_table_cell(
                    str(row["max_same_match_needers"]),
                    sort_value=row["max_same_match_needers"],
                ),
                _html_table_cell(
                    render_pct(row["multi_needer_match_rate"]),
                    sort_value=row["multi_needer_match_rate"],
                ),
                _html_table_cell(f"{row['avg_rank']:.2f}", sort_value=row["avg_rank"]),
                _html_table_cell(str(row["appearances"]), sort_value=row["appearances"]),
                {
                    "text": "；".join(
                        f"{item['label']}({item['samples']})" for item in strategies[:3]
                    )
                    or "样本不足",
                    "sort": "；".join(item["label"] for item in strategies[:3]) or "",
                    "html": html_top_strategies_cell(strategies),
                },
            ]
        )

    return html_sortable_table_page(
        title="低费主C热门程度",
        subtitle=subtitle,
        note=note,
        headers=headers,
        rows=table_rows,
    )


def html_variant_board_cards(comp: dict[str, Any]) -> str:
    variants = comp.get("variants", {})
    cards: list[str] = []
    for level in ("7", "8", "9"):
        variant = variants.get(level, {})
        heroes = variant.get("heroes", [])
        hero_chips = "".join(f'<span class="hero-chip">{esc(hero)}</span>' for hero in heroes)
        sample_note = (
            f"样本 {variant.get('sample_count', 0)}"
            if variant.get("sample_count")
            else "推导阵容"
            if variant.get("source") == "derived"
            else ""
        )
        cards.append(
            f"""
            <article class="board-card">
              <div class="board-head">
                <span class="level-badge">Lv{level}</span>
                <span class="source-badge">{esc(variant.get("source", "—"))}</span>
                <span class="conf-badge">{esc(variant.get("confidence", "—"))}</span>
                {f'<span class="sample-badge">{esc(sample_note)}</span>' if sample_note else ""}
              </div>
              <p class="bond-note">{esc(variant.get("bond_note", "—"))}</p>
              <div class="hero-chips">{hero_chips or '<span class="muted">样本不足</span>'}</div>
            </article>
            """
        )
    return f'<div class="board-grid">{"".join(cards)}</div>'


def html_comp_detail_page(comp: dict[str, Any], *, style: str, page_index: int) -> str:
    stats = comp["stats"]
    difficulty = comp.get("difficulty", {})
    carry_text = " > ".join(
        f"P{item.get('carry_rank', idx)} {item['hero_name']}({render_pct(item['share'])})"
        for idx, item in enumerate(comp.get("main_carries", [])[:3], start=1)
    )
    req_text = "；".join(
        f"{row['hero_name']} {row['recommended_min_stars']}星起(前四均{row['avg_stars_top4']:.1f}，三件套{render_pct(row['three_item_rate'])})"
        for row in comp.get("carry_requirements", [])[:3]
    )
    equip_parts: list[str] = []
    for note in comp.get("carry_equipment_notes", [])[:3]:
        important = [
            item for item in note["items"] if item["label"] in ("疑似刚需", "高价值")
        ][:3]
        if important:
            equip_parts.append(
                f"{note['hero_name']}："
                + "、".join(
                    f"{item['equipment_name']}({item['label']})"
                    for item in important
                )
            )
    jiujiu_parts: list[str] = []
    for req in comp.get("jiujiu_requirements", []):
        wearers = "、".join(
            f"{item['hero_name']}({render_pct(item['share'])})"
            for item in req.get("recommended_wearers", [])[:3]
        )
        jiujiu_parts.append(
            f"{req['recommended_jiujiu']}（{render_pct(req['dependency_rate'])}需啾啾开"
            f"{req['trait']}-{req['target_tier']}，推荐：{wearers or '待观察'}）"
        )
    bonds = "、".join(
        f"{item['bond']}({render_pct(item['share'])})" for item in comp.get("common_bonds", [])[:5]
    )
    breakdown = " · ".join(
        f"{row['play_style']}{render_pct(row['share'])}"
        for row in comp.get("play_style_breakdown", [])[:3]
    )
    star_pressure = (
        f"三星均{difficulty.get('avg_three_star_units', 0):.2f} / "
        f"前四均{difficulty.get('avg_top4_three_star_units', 0):.2f} / "
        f"同行{difficulty.get('avg_same_match_contest', 0):.2f}"
        f"({difficulty.get('contest_basis', '阵容相似')})"
        if difficulty
        else "样本不足"
    )
    popularity = comp.get("popularity", {})
    pop_text = (
        f"玩家占比 {render_pct(popularity.get('pick_rate', 0))} / "
        f"对局出现 {render_pct(popularity.get('match_rate', 0))}"
        if popularity
        else "—"
    )
    return f"""
    <section class="comp-page" data-style="{esc(style)}" data-index="{page_index}">
      <div class="comp-page-inner">
        <header class="comp-page-head">
          <div class="comp-head">
            <span class="badge">{esc(style)}</span>
            <span class="conf-pill">{esc(comp.get('confidence', '—'))}置信</span>
          </div>
          <h2>{esc(comp['label'])}</h2>
          <div class="metrics">
            <b>Avg {stats['avg_rank']:.2f}</b>
            <b>Top4 {render_pct(stats['top4_rate'])}</b>
            <b>吃鸡 {render_pct(stats['win_rate'])}</b>
            <b>n={stats['appearances']}</b>
          </div>
        </header>
        <div class="detail-grid">
          <div class="detail-panel">
            <h3>阵容概览</h3>
            <p><strong>主C：</strong>{esc(carry_text or '样本不足')}</p>
            <p><strong>成型门槛：</strong>{esc(req_text or '样本不足')}</p>
            <p><strong>三星压力：</strong>{esc(star_pressure)}</p>
            <p><strong>热门程度：</strong>{esc(pop_text)}</p>
            <p><strong>路线：</strong>{esc(unique_route_bonds(comp))}</p>
            <p><strong>类型样本：</strong>{esc(breakdown or style)}</p>
            <p><strong>常见羁绊：</strong>{esc(bonds or '无稳定羁绊')}</p>
          </div>
          <div class="detail-panel">
            <h3>关键装备与啾啾</h3>
            <p><strong>主C关键装备：</strong>{esc('；'.join(equip_parts) or '样本不足')}</p>
            <p><strong>啾啾成型：</strong>{esc('；'.join(jiujiu_parts) or '无明确依赖')}</p>
          </div>
        </div>
        <div class="board-section">
          <h3>7 / 8 / 9 级推荐阵容</h3>
          {html_variant_board_cards(comp)}
        </div>
      </div>
    </section>
    """


def render_composition_recommendations_html(data: dict[str, Any]) -> str:
    quality = data["overview"]["quality"]
    generated = data["generated_at"].split("T")[0]
    recommendations = data["rankings"].get("composition_recommendations", {})
    pages: list[str] = []
    page_index = 0
    style_counts = {style: len(recommendations.get(style, [])) for style in PLAY_STYLES}
    for style in PLAY_STYLES:
        for comp in recommendations.get(style, []):
            pages.append(html_comp_detail_page(comp, style=style, page_index=page_index))
            page_index += 1
    pages_html = "\n".join(pages) or '<section class="comp-page active"><p class="empty">当前样本不足。</p></section>'
    total_pages = max(page_index, 1)
    subtitle = (
        f"基于 {quality['matches']} 局 / {data['overview']['filtered_players']} 条过滤后玩家记录 · {generated}"
    )
    style_filter = "".join(
        f'<button type="button" class="style-filter" data-style="{esc(style)}">{esc(style)} ({count})</button>'
        for style, count in style_counts.items()
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>阵容推荐详情</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #10131f;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
      color: #f8fafc;
    }}
    .poster {{
      width: 1080px;
      max-width: 100%;
      margin: 0 auto;
      padding: 36px 32px 42px;
      background:
        radial-gradient(circle at 10% 0%, rgba(91,141,239,.45), transparent 28%),
        radial-gradient(circle at 90% 4%, rgba(255,189,89,.32), transparent 24%),
        linear-gradient(145deg, #151a2d 0%, #0c1020 100%);
      min-height: 100vh;
    }}
    header {{ margin-bottom: 18px; }}
    .eyebrow {{ color: #fbbf24; font-weight: 800; letter-spacing: 4px; font-size: 14px; }}
    h1 {{ font-size: 40px; margin: 8px 0; line-height: 1.1; }}
    .sub {{ color: #cbd5e1; font-size: 18px; line-height: 1.45; }}
    .pager-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 12px;
      align-items: center;
      justify-content: space-between;
      margin: 18px 0;
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255,255,255,.08);
      border: 1px solid rgba(255,255,255,.14);
    }}
    .pager-controls {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    .pager-btn, .style-filter {{
      border: 1px solid rgba(255,255,255,.18);
      background: rgba(255,255,255,.08);
      color: #e0f2fe;
      border-radius: 999px;
      padding: 8px 14px;
      cursor: pointer;
      font-size: 14px;
    }}
    .pager-btn:hover, .style-filter:hover {{ background: rgba(255,255,255,.14); }}
    .style-filter.active, .pager-btn:disabled {{ opacity: .55; cursor: default; }}
    .style-filter.active {{ background: rgba(251,191,36,.22); color: #fde68a; border-color: rgba(251,191,36,.45); }}
    .page-status {{ color: #93c5fd; font-size: 18px; font-weight: 600; }}
    .comp-page {{ display: none; }}
    .comp-page.active {{ display: block; }}
    .comp-page-inner {{
      background: rgba(255,255,255,.09);
      border: 1px solid rgba(255,255,255,.16);
      border-radius: 28px;
      padding: 24px;
      box-shadow: 0 18px 50px rgba(0,0,0,.24);
    }}
    .comp-head {{ display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }}
    .badge {{ background: #fbbf24; color: #111827; border-radius: 999px; padding: 6px 12px; font-weight: 900; }}
    .conf-pill {{ background: rgba(147,197,253,.18); color: #bfdbfe; border-radius: 999px; padding: 6px 12px; font-size: 13px; }}
    h2 {{ margin: 10px 0 0; font-size: 30px; line-height: 1.2; overflow-wrap: anywhere; }}
    .metrics {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 14px 0; color: #bfdbfe; }}
    .detail-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 16px; }}
    .detail-panel {{
      background: rgba(255,255,255,.05);
      border: 1px solid rgba(255,255,255,.1);
      border-radius: 20px;
      padding: 16px;
    }}
    .detail-panel h3, .board-section h3 {{ margin: 0 0 10px; color: #fde68a; font-size: 20px; }}
    p {{ margin: 7px 0; color: #dbeafe; font-size: 15px; line-height: 1.45; overflow-wrap: anywhere; }}
    .board-section {{ margin-top: 18px; }}
    .board-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .board-card {{
      background: rgba(255,255,255,.06);
      border: 1px solid rgba(255,255,255,.12);
      border-radius: 18px;
      padding: 14px;
      min-height: 220px;
    }}
    .board-head {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }}
    .level-badge, .source-badge, .conf-badge, .sample-badge {{
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      font-weight: 700;
    }}
    .level-badge {{ background: rgba(251,191,36,.22); color: #fde68a; }}
    .source-badge {{ background: rgba(147,197,253,.18); color: #bfdbfe; }}
    .conf-badge {{ background: rgba(255,255,255,.08); color: #cbd5e1; }}
    .sample-badge {{ background: rgba(248,113,113,.18); color: #fecaca; }}
    .bond-note {{ color: #cbd5e1; font-size: 13px; line-height: 1.4; min-height: 38px; }}
    .hero-chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .hero-chip {{
      background: rgba(255,255,255,.08);
      border-radius: 10px;
      padding: 4px 8px;
      font-size: 13px;
      color: #fef3c7;
      line-height: 1.3;
    }}
    .muted, .empty {{ color: #94a3b8; }}
    footer {{ margin-top: 18px; color: #94a3b8; font-size: 14px; text-align: center; }}
    @media (max-width: 900px) {{
      .detail-grid, .board-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main class="poster">
  <header>
    <div class="eyebrow">DZPPQ COMP GUIDE</div>
    <h1>阵容推荐详情</h1>
    <div class="sub">{esc(subtitle)} · 每页一套阵容，含 7/8/9 级推荐</div>
  </header>
  <div class="pager-bar">
    <div class="pager-controls">
      <button type="button" class="pager-btn" id="prev-page">上一页</button>
      <button type="button" class="pager-btn" id="next-page">下一页</button>
      <span class="page-status" id="page-status">第 1 / {total_pages} 页</span>
    </div>
    <div class="pager-controls">
      <button type="button" class="style-filter active" data-style="all">全部</button>
      {style_filter}
    </div>
  </div>
  <div id="comp-pages">
    {pages_html}
  </div>
  <footer>{esc(generated)}</footer>
</main>
<script>
const pages = Array.from(document.querySelectorAll(".comp-page"));
let activeStyle = "all";
let activeIndex = 0;

function visiblePages() {{
  return pages.filter((page) => activeStyle === "all" || page.dataset.style === activeStyle);
}}

function showPage(index) {{
  const filtered = visiblePages();
  if (!filtered.length) {{
    pages.forEach((page) => page.classList.remove("active"));
    document.getElementById("page-status").textContent = "当前类型暂无阵容";
    return;
  }}
  activeIndex = Math.max(0, Math.min(index, filtered.length - 1));
  pages.forEach((page) => page.classList.remove("active"));
  filtered[activeIndex].classList.add("active");
  const current = filtered[activeIndex];
  document.getElementById("page-status").textContent =
    `第 ${{activeIndex + 1}} / ${{filtered.length}} 页 · ${{current.dataset.style}}`;
  document.getElementById("prev-page").disabled = activeIndex <= 0;
  document.getElementById("next-page").disabled = activeIndex >= filtered.length - 1;
}}

document.getElementById("prev-page").addEventListener("click", () => showPage(activeIndex - 1));
document.getElementById("next-page").addEventListener("click", () => showPage(activeIndex + 1));
document.querySelectorAll(".style-filter").forEach((button) => {{
  button.addEventListener("click", () => {{
    document.querySelectorAll(".style-filter").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    activeStyle = button.dataset.style;
    showPage(0);
  }});
}});
if (pages.length) {{
  pages[0].classList.add("active");
  showPage(0);
}}
</script>
</body>
</html>
"""


def build_jiujiu_comp_table_rows(data: dict[str, Any]) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    recommendations = data["rankings"].get("composition_recommendations", {})
    for style in PLAY_STYLES:
        for comp in recommendations.get(style, []):
            if not comp.get("jiujiu_requirements"):
                continue
            stats = comp["stats"]
            for req in comp["jiujiu_requirements"]:
                key = (comp["label"], req.get("recommended_jiujiu", ""))
                if key in seen:
                    continue
                seen.add(key)
                wearers = "、".join(
                    f"{item['hero_name']}({render_pct(item['share'])})"
                    for item in req.get("recommended_wearers", [])[:3]
                ) or "待观察"
                target_bond = f"{req.get('trait', '—')}-{req.get('target_tier', '—')}"
                rows.append(
                    [
                        {
                            "text": comp["label"],
                            "sort": comp["label"],
                            "html": html_strategy_cell(comp["label"]),
                        },
                        _html_table_cell(style, sort_value=style),
                        _html_table_cell(req.get("recommended_jiujiu", "—"), sort_value=req.get("recommended_jiujiu", "")),
                        _html_table_cell(target_bond, sort_value=target_bond),
                        _html_table_cell(
                            render_pct(req.get("dependency_rate", 0)),
                            sort_value=req.get("dependency_rate", 0),
                        ),
                        _html_table_cell(wearers, sort_value=wearers),
                        _html_table_cell(f"{stats['avg_rank']:.2f}", sort_value=stats["avg_rank"]),
                        _html_table_cell(render_pct(stats["top4_rate"]), sort_value=stats["top4_rate"]),
                        _html_table_cell(str(stats["appearances"]), sort_value=stats["appearances"]),
                        _html_table_cell(comp.get("confidence", "—"), sort_value=comp.get("confidence", "")),
                    ]
                )
    return rows


def render_jiujiu_comps_table_html(data: dict[str, Any]) -> str:
    quality = data["overview"]["quality"]
    generated = data["generated_at"].split("T")[0]
    subtitle = (
        f"基于 {quality['matches']} 局 / {data['overview']['filtered_players']} 条过滤后玩家记录 · {generated}"
    )
    note = "仅展示推荐阵容中存在明确啾啾成型依赖的策略；点击表头可排序。"
    headers = [
        ("阵容", "text"),
        ("类型", "text"),
        ("啾啾", "text"),
        ("目标羁绊", "text"),
        ("依赖率", "numeric"),
        ("推荐穿戴", "text"),
        ("平均名次", "numeric"),
        ("前四率", "numeric"),
        ("样本", "numeric"),
        ("置信度", "text"),
    ]
    return html_sortable_table_page(
        title="带啾啾阵容推荐",
        subtitle=subtitle,
        note=note,
        headers=headers,
        rows=build_jiujiu_comp_table_rows(data),
    )


def build_jiujiu_wearer_table_rows(data: dict[str, Any]) -> list[list[dict[str, Any]]]:
    rows: list[list[dict[str, Any]]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for item in data["rankings"].get("jiujiu", {}).get("jiujiu_rankings", []):
        item_name = item["equipment_name"]
        for comp in item.get("recommended_comps", []):
            for wearer in comp.get("recommended_wearers", []):
                key = (item_name, wearer["hero_name"], "阵容绑定", comp.get("family_label", ""))
                if key in seen:
                    continue
                seen.add(key)
                rows.append(
                    [
                        _html_table_cell(item_name, sort_value=item_name),
                        _html_table_cell(wearer["hero_name"], sort_value=wearer["hero_name"]),
                        _html_table_cell("阵容绑定", sort_value="阵容绑定"),
                        {
                            "text": comp.get("family_label", "—"),
                            "sort": comp.get("family_label", ""),
                            "html": html_strategy_cell(comp.get("family_label", "—")),
                        },
                        _html_table_cell(str(wearer["appearances"]), sort_value=wearer["appearances"]),
                        _html_table_cell(render_pct(wearer.get("share", 0)), sort_value=wearer.get("share", 0)),
                        _html_table_cell(
                            f"{comp['avg_rank']:.2f}" if comp.get("avg_rank") is not None else "—",
                            sort_value=comp.get("avg_rank", 999),
                        ),
                        _html_table_cell(
                            render_pct(comp.get("top4_rate", 0)),
                            sort_value=comp.get("top4_rate", -1),
                        ),
                    ]
                )
        for hero in item.get("recommended_heroes", []):
            key = (item_name, hero["hero_name"], "棋子增益", "")
            if key in seen:
                continue
            seen.add(key)
            rows.append(
                [
                    _html_table_cell(item_name, sort_value=item_name),
                    _html_table_cell(hero["hero_name"], sort_value=hero["hero_name"]),
                    _html_table_cell("棋子增益", sort_value="棋子增益"),
                    _html_table_cell("—", sort_value=""),
                    _html_table_cell(str(hero["appearances"]), sort_value=hero["appearances"]),
                    _html_table_cell("—", sort_value=-1),
                    _html_table_cell(f"{hero['avg_rank']:.2f}", sort_value=hero["avg_rank"]),
                    _html_table_cell(render_pct(hero.get("top4_rate", 0)), sort_value=hero.get("top4_rate", -1)),
                ]
            )
    return rows


def render_jiujiu_wearers_table_html(data: dict[str, Any]) -> str:
    quality = data["overview"]["quality"]
    generated = data["generated_at"].split("T")[0]
    subtitle = (
        f"基于 {quality['matches']} 局 / {data['overview']['filtered_players']} 条过滤后玩家记录 · {generated}"
    )
    note = "汇总啾啾在阵容绑定与棋子增益两类证据下的推荐穿戴棋子；点击表头可排序。"
    headers = [
        ("啾啾", "text"),
        ("棋子", "text"),
        ("证据类型", "text"),
        ("关联阵容", "text"),
        ("样本", "numeric"),
        ("占比", "numeric"),
        ("平均名次", "numeric"),
        ("前四率", "numeric"),
    ]
    return html_sortable_table_page(
        title="佩戴啾啾棋子推荐",
        subtitle=subtitle,
        note=note,
        headers=headers,
        rows=build_jiujiu_wearer_table_rows(data),
    )


def html_filterable_equipment_page(
    *,
    title: str,
    subtitle: str,
    note: str,
    headers: list[tuple[str, str]],
    rows: list[dict[str, Any]],
) -> str:
    header_parts: list[str] = []
    for index, (label, sort_type) in enumerate(headers):
        th_class = "sortable sort-asc" if index == 0 else "sortable"
        data_dir = ' data-dir="asc"' if index == 0 else ""
        header_parts.append(
            f'<th class="{th_class}" data-sort="{esc(sort_type)}"{data_dir}>{esc(label)}</th>'
        )
    header_html = "\n".join(header_parts)
    body_rows: list[str] = []
    trait_options: set[str] = set()
    for row in rows:
        traits = row.get("traits", [])
        trait_options.update(traits)
        trait_attr = esc(",".join(traits))
        cells = "\n".join(
            (
                f'<td data-sort="{esc(cell["sort"])}">{cell["html"]}</td>'
                if cell.get("html")
                else f'<td data-sort="{esc(cell["sort"])}">{esc(cell["text"])}</td>'
            )
            for cell in row["cells"]
        )
        body_rows.append(
            f'<tr data-tier="{esc(str(row.get("tier") or ""))}" '
            f'data-traits="{trait_attr}" '
            f'data-search="{esc(row.get("search_text", ""))}">{cells}</tr>'
        )
    body_html = "\n".join(body_rows) or f'<tr><td colspan="{len(headers)}">样本不足</td></tr>'
    trait_buttons = "".join(
        f'<button type="button" class="trait-filter" data-trait="{esc(trait)}">{esc(trait)}</button>'
        for trait in sorted(trait_options)
    )
    initial_sort_label = esc(headers[0][0]) if headers else ""
    note_html = f'<div class="note">{esc(note)}</div>' if note.strip() else ""
    generated = esc(subtitle.split("·")[-1].strip() if "·" in subtitle else subtitle)

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{esc(title)}</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #10131f;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
      color: #f8fafc;
    }}
    .poster {{
      width: 1080px;
      max-width: 100%;
      margin: 0 auto;
      padding: 36px 32px 42px;
      background:
        radial-gradient(circle at 10% 0%, rgba(91,141,239,.45), transparent 28%),
        radial-gradient(circle at 90% 4%, rgba(255,189,89,.32), transparent 24%),
        linear-gradient(145deg, #151a2d 0%, #0c1020 100%);
    }}
    header {{ margin-bottom: 18px; }}
    .eyebrow {{ color: #fbbf24; font-weight: 800; letter-spacing: 4px; font-size: 14px; }}
    .title-row {{
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 12px 20px;
      margin: 8px 0;
    }}
    h1 {{ font-size: 40px; margin: 0; line-height: 1.1; }}
    .sort-status {{
      color: #93c5fd;
      font-size: 28px;
      font-weight: 600;
      line-height: 1.1;
      white-space: nowrap;
    }}
    .sub {{ color: #cbd5e1; font-size: 18px; line-height: 1.45; }}
    .note {{ color: #94a3b8; font-size: 15px; margin: 10px 0 16px; line-height: 1.45; }}
    .filter-bar {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 14px;
      padding: 14px 16px;
      border-radius: 18px;
      background: rgba(255,255,255,.08);
      border: 1px solid rgba(255,255,255,.14);
    }}
    .filter-group {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
    .filter-label {{ color: #fde68a; font-size: 14px; font-weight: 700; margin-right: 4px; }}
    .filter-btn, .trait-filter {{
      border: 1px solid rgba(255,255,255,.18);
      background: rgba(255,255,255,.08);
      color: #e0f2fe;
      border-radius: 999px;
      padding: 7px 12px;
      cursor: pointer;
      font-size: 13px;
    }}
    .filter-btn.active, .trait-filter.active {{
      background: rgba(251,191,36,.22);
      color: #fde68a;
      border-color: rgba(251,191,36,.45);
    }}
    .search-input {{
      min-width: 220px;
      flex: 1 1 220px;
      border: 1px solid rgba(255,255,255,.18);
      background: rgba(255,255,255,.08);
      color: #f8fafc;
      border-radius: 12px;
      padding: 8px 12px;
      font-size: 14px;
    }}
    .filter-status {{ color: #93c5fd; font-size: 14px; margin-left: auto; }}
    .table-wrap {{
      overflow-x: auto;
      border: 1px solid rgba(255,255,255,.14);
      border-radius: 20px;
      background: rgba(255,255,255,.06);
    }}
    table {{ width: 100%; border-collapse: collapse; font-size: 14px; }}
    th, td {{
      padding: 10px 12px;
      border-bottom: 1px solid rgba(255,255,255,.08);
      text-align: left;
      vertical-align: top;
    }}
    th {{
      position: sticky;
      top: 0;
      background: rgba(21,26,45,.96);
      color: #fde68a;
      cursor: pointer;
      user-select: none;
      white-space: nowrap;
    }}
    th.sort-asc::after {{ content: " ▲"; color: #93c5fd; }}
    th.sort-desc::after {{ content: " ▼"; color: #93c5fd; }}
    tr:hover td {{ background: rgba(255,255,255,.04); }}
    tr.hidden {{ display: none; }}
    td {{ color: #dbeafe; line-height: 1.35; overflow-wrap: anywhere; }}
    .item-list {{ display: flex; flex-direction: column; gap: 4px; }}
    .item-chip {{
      display: inline-block;
      background: rgba(255,255,255,.08);
      border-radius: 8px;
      padding: 3px 8px;
      font-size: 13px;
      color: #e0f2fe;
    }}
    footer {{ margin-top: 18px; color: #94a3b8; font-size: 14px; text-align: center; }}
  </style>
</head>
<body>
<main class="poster">
  <header>
    <div class="eyebrow">DZPPQ EQUIPMENT GUIDE</div>
    <div class="title-row">
      <h1>{esc(title)}</h1>
      <span class="sort-status" id="sort-status">当前按 {initial_sort_label} 升序</span>
    </div>
    <div class="sub">{esc(subtitle)}</div>
    {note_html}
  </header>
  <div class="filter-bar">
    <div class="filter-group">
      <span class="filter-label">费用</span>
      <button type="button" class="filter-btn active" data-tier="all">全部</button>
      <button type="button" class="filter-btn" data-tier="1">1费</button>
      <button type="button" class="filter-btn" data-tier="2">2费</button>
      <button type="button" class="filter-btn" data-tier="3">3费</button>
      <button type="button" class="filter-btn" data-tier="4">4费</button>
      <button type="button" class="filter-btn" data-tier="5">5费</button>
    </div>
    <div class="filter-group">
      <span class="filter-label">羁绊</span>
      <button type="button" class="trait-filter active" data-trait="all">全部</button>
      {trait_buttons}
    </div>
    <input type="search" class="search-input" id="search-input" placeholder="搜索棋子、装备、三件套…">
    <span class="filter-status" id="filter-status">显示全部</span>
  </div>
  <div class="table-wrap">
    <table class="sortable-table" id="equipment-table">
      <thead><tr>{header_html}</tr></thead>
      <tbody>{body_html}</tbody>
    </table>
  </div>
  <footer>{generated}</footer>
</main>
<script>
const sortStatusEl = document.getElementById("sort-status");
const filterStatusEl = document.getElementById("filter-status");
const searchInput = document.getElementById("search-input");
const table = document.getElementById("equipment-table");
const tbody = table.querySelector("tbody");
let activeTier = "all";
let activeTrait = "all";

function updateSortStatus(th, dir) {{
  if (!sortStatusEl || !th) return;
  const label = th.textContent.replace(/\\s*[▲▼]\\s*$/, "").trim();
  sortStatusEl.textContent = `当前按 ${{label}} ${{dir === "desc" ? "降序" : "升序"}}`;
}}

function applyFilters() {{
  const keyword = (searchInput.value || "").trim().toLowerCase();
  let visible = 0;
  tbody.querySelectorAll("tr").forEach((row) => {{
    const tierMatch = activeTier === "all" || row.dataset.tier === activeTier;
    const traits = (row.dataset.traits || "").split(",").filter(Boolean);
    const traitMatch = activeTrait === "all" || traits.includes(activeTrait);
    const searchMatch = !keyword || (row.dataset.search || "").toLowerCase().includes(keyword);
    const show = tierMatch && traitMatch && searchMatch;
    row.classList.toggle("hidden", !show);
    if (show) visible += 1;
  }});
  filterStatusEl.textContent = keyword
    ? `筛选后 ${{visible}} 条 · 关键词「${{keyword}}」`
    : `筛选后 ${{visible}} 条`;
}}

document.querySelectorAll(".filter-btn").forEach((button) => {{
  button.addEventListener("click", () => {{
    document.querySelectorAll(".filter-btn").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    activeTier = button.dataset.tier;
    applyFilters();
  }});
}});
document.querySelectorAll(".trait-filter").forEach((button) => {{
  button.addEventListener("click", () => {{
    document.querySelectorAll(".trait-filter").forEach((item) => item.classList.remove("active"));
    button.classList.add("active");
    activeTrait = button.dataset.trait;
    applyFilters();
  }});
}});
searchInput.addEventListener("input", applyFilters);

document.querySelectorAll("th.sortable").forEach((th, colIndex) => {{
  th.addEventListener("click", () => {{
    const rows = Array.from(tbody.querySelectorAll("tr:not(.hidden)"));
    const hiddenRows = Array.from(tbody.querySelectorAll("tr.hidden"));
    const sortType = th.dataset.sort || "text";
    const isActive = th.classList.contains("sort-asc") || th.classList.contains("sort-desc");
    const newDir = isActive && th.dataset.dir === "asc" ? "desc" : "asc";
    table.querySelectorAll("th.sortable").forEach((header) => {{
      header.dataset.dir = "";
      header.classList.remove("sort-asc", "sort-desc");
    }});
    th.dataset.dir = newDir;
    th.classList.add(newDir === "asc" ? "sort-asc" : "sort-desc");
    updateSortStatus(th, newDir);
    rows.sort((left, right) => {{
      const leftVal = left.cells[colIndex]?.dataset.sort ?? "";
      const rightVal = right.cells[colIndex]?.dataset.sort ?? "";
      if (sortType === "numeric") {{
        const leftNum = parseFloat(leftVal);
        const rightNum = parseFloat(rightVal);
        const safeLeft = Number.isFinite(leftNum) ? leftNum : Number.MAX_VALUE;
        const safeRight = Number.isFinite(rightNum) ? rightNum : Number.MAX_VALUE;
        return newDir === "asc" ? safeLeft - safeRight : safeRight - safeLeft;
      }}
      const cmp = String(leftVal).localeCompare(String(rightVal), "zh-CN");
      return newDir === "asc" ? cmp : -cmp;
    }});
    rows.forEach((row) => tbody.appendChild(row));
    hiddenRows.forEach((row) => tbody.appendChild(row));
  }});
}});

const initialSortHeader = document.querySelector("th.sort-asc, th.sort-desc");
if (initialSortHeader) {{
  updateSortStatus(initialSortHeader, initialSortHeader.dataset.dir || "asc");
}}
applyFilters();
</script>
</body>
</html>
"""


def render_equipment_recommendations_html(data: dict[str, Any]) -> str:
    quality = data["overview"]["quality"]
    generated = data["generated_at"].split("T")[0]
    subtitle = (
        f"基于 {quality['matches']} 局 / {data['overview']['filtered_players']} 条过滤后玩家记录 · {generated}"
    )
    note = "可按费用、羁绊和关键词筛选；点击表头排序。完整明细仍见 Excel 工作簿。"
    recommendations = [
        row
        for row in data["rankings"]["heroes_and_equipment"]["carry_equipment_recommendations"]
        if row.get("has_equipment_data")
    ]
    headers = [
        ("棋子", "text"),
        ("费用", "numeric"),
        ("羁绊", "text"),
        ("主C样本", "numeric"),
        ("主C率", "numeric"),
        ("推荐装备", "text"),
        ("常见三件套", "text"),
        ("低样本观察", "text"),
    ]
    table_rows: list[dict[str, Any]] = []
    for rec in recommendations:
        hero_stats = rec["hero_stats"]
        tier = hero_stats.get("tier")
        traits = rec.get("hero_traits", [])
        items = rec.get("recommended_items", [])
        item_html = "".join(
            f'<span class="item-chip">{esc(item["equipment_name"])} '
            f'({render_pct(item.get("selected_rate", 0))}核选, n={item["appearances"]})</span>'
            for item in items[:4]
        ) or "—"
        sets = rec.get("recommended_sets", [])
        set_text = "；".join(set_row["equipment_set"] for set_row in sets[:2]) or "—"
        low_sample = rec.get("low_sample_observations", [])
        low_text = "；".join(item["equipment_name"] for item in low_sample[:2]) or "—"
        search_text = " ".join(
            [
                rec["hero_name"],
                " ".join(traits),
                " ".join(item["equipment_name"] for item in items),
                set_text,
                low_text,
            ]
        )
        table_rows.append(
            {
                "tier": tier,
                "traits": traits,
                "search_text": search_text,
                "cells": [
                    _html_table_cell(rec["hero_name"], sort_value=rec["hero_name"]),
                    _html_table_cell(str(tier or "—"), sort_value=tier or 99),
                    _html_table_cell("、".join(traits) or "—", sort_value="、".join(traits)),
                    _html_table_cell(
                        str(hero_stats.get("carry_appearances", 0)),
                        sort_value=hero_stats.get("carry_appearances", 0),
                    ),
                    _html_table_cell(
                        render_pct(hero_stats.get("carry_rate", 0)),
                        sort_value=hero_stats.get("carry_rate", 0),
                    ),
                    {"text": "装备", "sort": " ".join(item["equipment_name"] for item in items), "html": f'<div class="item-list">{item_html}</div>'},
                    _html_table_cell(set_text, sort_value=set_text),
                    _html_table_cell(low_text, sort_value=low_text),
                ],
            }
        )
    return html_filterable_equipment_page(
        title="棋子装备推荐",
        subtitle=subtitle,
        note=note,
        headers=headers,
        rows=table_rows,
    )


def html_trap_comp_card(comp: dict[str, Any]) -> str:
    stats = comp["stats"]
    popularity = comp.get("popularity", {})
    return f"""
    <article class="trap-card">
      <header class="trap-head">
        <h2>{esc(comp['label'])}</h2>
        <span class="trap-badge">版本陷阱</span>
      </header>
      <p class="trap-reason">{esc(comp.get('trap_reason', '策略整体表现偏弱'))}</p>
      <div class="metrics">
        <b>Avg {stats['avg_rank']:.2f}</b>
        <b>Top4 {render_pct(stats['top4_rate'])}</b>
        <b>n={stats['appearances']}</b>
        <b>热度 {render_pct(popularity.get('pick_rate', 0))}</b>
        <b>{esc(comp.get('confidence', '—'))}置信</b>
      </div>
      <p><strong>类型：</strong>{esc(comp.get('play_style', '高费'))}</p>
      <p><strong>路线：</strong>{esc(unique_route_bonds(comp))}</p>
      <div class="board-section">
        <h3>7 / 8 / 9 级观察阵容</h3>
        {html_variant_board_cards(comp)}
      </div>
    </article>
    """


def render_trap_compositions_html(data: dict[str, Any]) -> str:
    quality = data["overview"]["quality"]
    generated = data["generated_at"].split("T")[0]
    traps = data["rankings"].get("traps", {}).get("compositions", [])
    cards_html = "".join(html_trap_comp_card(comp) for comp in traps) or '<p class="empty">暂无稳定陷阱阵容。</p>'
    subtitle = (
        f"基于 {quality['matches']} 局 / {data['overview']['filtered_players']} 条过滤后玩家记录 · {generated}"
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>版本陷阱阵容</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #10131f;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
      color: #f8fafc;
    }}
    .poster {{
      width: 1080px;
      max-width: 100%;
      margin: 0 auto;
      padding: 36px 32px 42px;
      background:
        radial-gradient(circle at 10% 0%, rgba(248,113,113,.18), transparent 24%),
        radial-gradient(circle at 90% 4%, rgba(255,189,89,.18), transparent 20%),
        linear-gradient(145deg, #151a2d 0%, #0c1020 100%);
    }}
    header {{ margin-bottom: 18px; }}
    .eyebrow {{ color: #fca5a5; font-weight: 800; letter-spacing: 4px; font-size: 14px; }}
    h1 {{ font-size: 40px; margin: 8px 0; line-height: 1.1; }}
    .sub {{ color: #cbd5e1; font-size: 18px; line-height: 1.45; }}
    .trap-list {{ display: flex; flex-direction: column; gap: 18px; }}
    .trap-card {{
      background: rgba(255,255,255,.09);
      border: 1px solid rgba(248,113,113,.24);
      border-radius: 28px;
      padding: 22px;
      box-shadow: 0 18px 50px rgba(0,0,0,.24);
    }}
    .trap-head {{ display: flex; justify-content: space-between; gap: 12px; align-items: flex-start; flex-wrap: wrap; }}
    h2 {{ margin: 0; font-size: 28px; line-height: 1.2; overflow-wrap: anywhere; }}
    .trap-badge {{
      background: rgba(248,113,113,.22);
      color: #fecaca;
      border-radius: 999px;
      padding: 6px 12px;
      font-weight: 800;
      white-space: nowrap;
    }}
    .trap-reason {{ color: #fecaca; margin: 10px 0; line-height: 1.45; }}
    .metrics {{ display: flex; flex-wrap: wrap; gap: 10px; margin: 10px 0; color: #bfdbfe; }}
    p {{ margin: 7px 0; color: #dbeafe; font-size: 15px; line-height: 1.45; overflow-wrap: anywhere; }}
    .board-section h3 {{ margin: 14px 0 10px; color: #fde68a; font-size: 20px; }}
    .board-grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }}
    .board-card {{
      background: rgba(255,255,255,.06);
      border: 1px solid rgba(255,255,255,.12);
      border-radius: 18px;
      padding: 14px;
      min-height: 210px;
    }}
    .board-head {{ display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }}
    .level-badge, .source-badge, .conf-badge, .sample-badge {{
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 12px;
      font-weight: 700;
    }}
    .level-badge {{ background: rgba(251,191,36,.22); color: #fde68a; }}
    .source-badge {{ background: rgba(147,197,253,.18); color: #bfdbfe; }}
    .conf-badge {{ background: rgba(255,255,255,.08); color: #cbd5e1; }}
    .sample-badge {{ background: rgba(248,113,113,.18); color: #fecaca; }}
    .bond-note {{ color: #cbd5e1; font-size: 13px; line-height: 1.4; min-height: 38px; }}
    .hero-chips {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .hero-chip {{
      background: rgba(255,255,255,.08);
      border-radius: 10px;
      padding: 4px 8px;
      font-size: 13px;
      color: #fef3c7;
      line-height: 1.3;
    }}
    .empty, .muted {{ color: #94a3b8; }}
    footer {{ margin-top: 18px; color: #94a3b8; font-size: 14px; text-align: center; }}
    @media (max-width: 900px) {{
      .board-grid {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
<main class="poster">
  <header>
    <div class="eyebrow">DZPPQ TRAP COMPS</div>
    <h1>版本陷阱阵容</h1>
    <div class="sub">{esc(subtitle)} · 每个陷阱阵容展示 7/8/9 级观察阵容</div>
  </header>
  <div class="trap-list">
    {cards_html}
  </div>
  <footer>{esc(generated)}</footer>
</main>
</body>
</html>
"""


def render_table_html_outputs(data: dict[str, Any]) -> dict[str, str]:
    return {
        "cards_cai": render_card_prefix_table_html(data, "彩"),
        "cards_yellow": render_card_prefix_table_html(data, "黄"),
        "cards_blue": render_card_prefix_table_html(data, "蓝"),
        "cards_white": render_card_prefix_table_html(data, "白"),
        "duo_compositions": render_duo_composition_table_html(data),
        "low_cost_carries": render_low_cost_carry_table_html(data),
        "composition_recommendations": render_composition_recommendations_html(data),
        "jiujiu_comps": render_jiujiu_comps_table_html(data),
        "jiujiu_wearers": render_jiujiu_wearers_table_html(data),
        "equipment": render_equipment_recommendations_html(data),
        "trap_compositions": render_trap_compositions_html(data),
    }


def render_html(data: dict[str, Any]) -> str:
    quality = data["overview"]["quality"]
    recommendations = data["rankings"].get("composition_recommendations", {})
    cards = data["rankings"]["cards"]
    jiujiu_rows = data["rankings"].get("jiujiu", {}).get("jiujiu_rankings", [])[:5]
    duo_comp_rows = data["rankings"].get("duo_composition_synergy", [])[:4]
    traps = data["rankings"]["traps"]
    generated = esc(data["generated_at"].split("T")[0])

    comp_sections = []
    for style in PLAY_STYLES:
        rows = recommendations.get(style, [])[:2]
        if not rows:
            cards_html = '<p class="empty">当前样本不足。</p>'
        else:
            cards_html = "".join(html_comp_card(comp) for comp in rows)
        comp_sections.append(
            f"""
            <section class="style-section">
              <div class="section-title">{esc(style)}阵容推荐</div>
              <div class="comp-grid">{cards_html}</div>
            </section>
            """
        )

    top_cards = html_prefix_card_sections(cards.get("single_cards_by_prefix", {}))
    duo_cards = html_list_items(cards.get("duo_card_contribution", [])[:4])
    jiujiu_html = "\n".join(
        f"<li><b>{esc(row['equipment_name'])}</b><span>"
        f"{esc((row.get('recommended_comps') or [{}])[0].get('family_label', '待观察'))}"
        f"→{esc('、'.join(item['hero_name'] for item in (row.get('recommended_comps') or [{}])[0].get('recommended_wearers', [])[:2]) or '待观察')}"
        f"</span></li>"
        for row in jiujiu_rows
    ) or "<li>样本不足</li>"
    duo_comp_html = "\n".join(
        f"<li><b>{esc(row['strategy_a'])} + {esc(row['strategy_b'])}</b>"
        f"<span>队伍 {row['team_avg_rank']:.2f} / n={row['appearances']}</span></li>"
        for row in duo_comp_rows
    ) or "<li>样本不足</li>"
    trap_html = "".join(
        html_trap_group(label, traps.get(key, []))
        for label, key in (("阵容", "compositions"), ("棋子", "heroes"), ("卡牌", "cards"))
    )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>DZPPQ 当前环境一图流</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: #10131f;
      font-family: "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
      color: #f8fafc;
    }}
    .poster {{
      width: 1080px;
      min-height: 1440px;
      max-width: 100%;
      margin: 0 auto;
      padding: 42px;
      background:
        radial-gradient(circle at 10% 0%, rgba(91,141,239,.45), transparent 28%),
        radial-gradient(circle at 90% 4%, rgba(255,189,89,.32), transparent 24%),
        linear-gradient(145deg, #151a2d 0%, #0c1020 100%);
    }}
    header {{ margin-bottom: 22px; }}
    .eyebrow {{ color: #fbbf24; font-weight: 800; letter-spacing: 4px; }}
    h1 {{ font-size: 52px; margin: 10px 0; line-height: 1.08; }}
    .sub {{ color: #cbd5e1; font-size: 21px; }}
    .stats {{
      display: grid; grid-template-columns: repeat(4,1fr); gap: 14px; margin: 20px 0;
    }}
    .stat {{
      background: rgba(255,255,255,.08); border: 1px solid rgba(255,255,255,.14);
      border-radius: 22px; padding: 16px;
    }}
    .stat b {{ display:block; font-size: 28px; color:#fff; }}
    .stat span {{ color:#a8b3c7; }}
    .style-section {{ margin-top: 18px; }}
    .section-title {{
      color:#fde68a; font-weight:900; font-size:27px; margin:0 0 10px;
    }}
    .comp-grid {{ display:grid; grid-template-columns: 1fr 1fr; gap: 16px; }}
    .comp-card, .panel {{
      background: rgba(255,255,255,.09);
      border: 1px solid rgba(255,255,255,.16);
      border-radius: 28px;
      padding: 19px;
      box-shadow: 0 18px 50px rgba(0,0,0,.24);
    }}
    .comp-head {{ display:flex; align-items:center; gap:10px; flex-wrap:wrap; }}
    .badge {{ background:#fbbf24; color:#111827; border-radius:999px; padding:6px 12px; font-weight:900; flex:0 0 auto; }}
    h2 {{ margin:0; font-size:24px; line-height:1.22; min-width:0; overflow-wrap:anywhere; }}
    .metrics {{ display:flex; flex-wrap:wrap; gap:10px; margin:12px 0; color:#bfdbfe; }}
    p {{ margin:7px 0; color:#dbeafe; font-size:16px; line-height:1.38; overflow-wrap:anywhere; }}
    .lineup {{ color:#fef3c7; }}
    .empty {{ color:#94a3b8; }}
    .bottom {{ display:grid; grid-template-columns: repeat(2, 1fr); gap:16px; margin-top:18px; }}
    .panel h3 {{ margin:0 0 10px; font-size:24px; color:#fde68a; }}
    .panel h4 {{ margin:8px 0 4px; color:#bfdbfe; font-size:17px; }}
    ul {{ list-style:none; padding:0; margin:0; }}
    .panel li {{ margin:8px 0; display:flex; justify-content:space-between; gap:16px; color:#dbeafe; line-height:1.35; }}
    .panel li b {{ overflow-wrap:anywhere; }}
    .cards {{ display:grid; grid-template-columns:1fr 1fr; gap:16px; font-size:18px; line-height:1.45; color:#e0f2fe; }}
    footer {{ margin-top:22px; color:#94a3b8; font-size:16px; text-align:center; }}
  </style>
</head>
<body>
<main class="poster">
  <header>
    <div class="eyebrow">DZPPQ META REPORT</div>
    <h1>当前版本上分阵容一图流</h1>
    <div class="sub">基于 {quality['matches']} 局 / {data['overview']['filtered_players']} 条过滤后玩家记录 · {generated}</div>
  </header>
  <section class="stats">
    <div class="stat"><b>{quality['matches']}</b><span>对局样本</span></div>
    <div class="stat"><b>{quality['bot_player_records_excluded']}</b><span>人机记录过滤</span></div>
    <div class="stat"><b>{quality['unknown_heroes']}</b><span>unknown 棋子</span></div>
    <div class="stat"><b>{quality['cards']}</b><span>卡牌记录</span></div>
  </section>
  {''.join(comp_sections)}
  <section class="bottom">
    <div class="panel">
      <h3>卡牌优先级（分类型）</h3>
      <div class="cards">
        <div>{top_cards}</div>
        <div><h4>第一卡贡献</h4><ul>{duo_cards}</ul></div>
      </div>
    </div>
    <div class="panel">
      <h3>啾啾观察</h3>
      <ul>{jiujiu_html}</ul>
    </div>
    <div class="panel">
      <h3>版本陷阱</h3>
      <div class="cards">{trap_html}</div>
    </div>
    <div class="panel">
      <h3>读法提醒</h3>
      <p>赌狗看低费主C与三星信号，高费看8级以上无低费三星的大成样本；低样本高胜只作观察。</p>
      <h4>双人阵容配合</h4>
      <ul>{duo_comp_html}</ul>
    </div>
  </section>
  <footer>完整数据见 latest_meta_analysis_report.md / latest_meta_analysis.json / latest_meta_analysis_equipment.xlsx</footer>
</main>
</body>
</html>
"""


def _xlsx_header_style():
    try:
        from openpyxl.styles import Font, PatternFill

        return Font(bold=True), PatternFill("solid", fgColor="E8EEF7")
    except ImportError:
        return None, None


def _write_xlsx_sheet(ws, headers: list[str], rows: list[list[Any]]) -> None:
    header_font, header_fill = _xlsx_header_style()
    ws.append(headers)
    if header_font and header_fill:
        for cell in ws[1]:
            cell.font = header_font
            cell.fill = header_fill
    for row in rows:
        ws.append(row)
    for column in ws.columns:
        max_len = 0
        column_letter = column[0].column_letter
        for cell in column:
            if cell.value is not None:
                max_len = max(max_len, len(str(cell.value)))
        ws.column_dimensions[column_letter].width = min(max_len + 2, 48)


def render_xlsx(data: dict[str, Any], xlsx_path: Path) -> None:
    try:
        from openpyxl import Workbook
    except ImportError as exc:
        raise SystemExit(
            "Excel export requires openpyxl. Install with: pip install openpyxl"
        ) from exc

    wb = Workbook()
    hero_rows: list[list[Any]] = []
    set_rows: list[list[Any]] = []
    low_sample_rows: list[list[Any]] = []
    recommendations = data["rankings"]["heroes_and_equipment"]["carry_equipment_recommendations"]
    for rec in recommendations:
        hero = rec["hero_stats"]
        for rank, item in enumerate(rec.get("recommended_items", []), start=1):
            hero_rows.append(
                [
                    rec["hero_name"],
                    hero.get("tier"),
                    hero.get("carry_rate"),
                    hero.get("avg_rank"),
                    hero.get("appearances"),
                    rank,
                    item["equipment_name"],
                    item.get("adjusted_avg_rank"),
                    item.get("avg_rank"),
                    item.get("top4_rate"),
                    item.get("appearances"),
                    item.get("selected_rate"),
                    item.get("selected_priority"),
                    item.get("sample_quality"),
                ]
            )
        for rank, item in enumerate(rec.get("low_sample_observations", []), start=1):
            low_sample_rows.append(
                [
                    rec["hero_name"],
                    hero.get("tier"),
                    rank,
                    item["equipment_name"],
                    item.get("adjusted_avg_rank"),
                    item.get("avg_rank"),
                    item.get("top4_rate"),
                    item.get("appearances"),
                    item.get("selected_rate"),
                    item.get("selected_priority"),
                ]
            )
        for rank, item in enumerate(rec.get("recommended_sets", []), start=1):
            set_rows.append(
                [
                    rec["hero_name"],
                    hero.get("tier"),
                    rank,
                    item.get("equipment_set"),
                    item.get("adjusted_avg_rank"),
                    item.get("avg_rank"),
                    item.get("top4_rate"),
                    item.get("appearances"),
                ]
            )

    ws_hero = wb.active
    ws_hero.title = "全英雄出装"
    _write_xlsx_sheet(
        ws_hero,
        [
            "英雄",
            "费用",
            "主C率(%)",
            "英雄平均名次",
            "英雄样本",
            "装备顺位",
            "装备",
            "修正名次",
            "平均名次",
            "前四率(%)",
            "样本",
            "核选占比(%)",
            "核选优先级",
            "样本质量",
        ],
        hero_rows,
    )

    ws_comp = wb.create_sheet("阵容主C关键装备")
    comp_rows: list[list[Any]] = []
    for comp in data["rankings"].get("compositions", []):
        for note in comp.get("carry_equipment_notes", []):
            for rank, item in enumerate(note.get("items", []), start=1):
                comp_rows.append(
                    [
                        comp.get("label"),
                        comp.get("play_style"),
                        note.get("hero_name"),
                        rank,
                        item.get("equipment_name"),
                        item.get("label"),
                        item.get("appearances"),
                        item.get("use_rate"),
                        item.get("with_avg_rank"),
                        item.get("without_avg_rank"),
                        item.get("without_item_penalty"),
                        item.get("with_top4_rate"),
                        item.get("selected_rate"),
                    ]
                )
    _write_xlsx_sheet(
        ws_comp,
        [
            "阵容",
            "类型",
            "主C",
            "装备顺位",
            "装备",
            "标签",
            "样本",
            "使用率(%)",
            "带装平均名次",
            "不带平均名次",
            "不带惩罚",
            "带装前四率(%)",
            "核选占比(%)",
        ],
        comp_rows,
    )

    ws_sets = wb.create_sheet("常见三件套")
    _write_xlsx_sheet(
        ws_sets,
        ["英雄", "费用", "组合顺位", "三件套", "修正名次", "平均名次", "前四率(%)", "样本"],
        set_rows,
    )

    ws_low = wb.create_sheet("低样本观察")
    _write_xlsx_sheet(
        ws_low,
        [
            "英雄",
            "费用",
            "观察顺位",
            "装备",
            "修正名次",
            "平均名次",
            "前四率(%)",
            "样本",
            "核选占比(%)",
            "核选优先级",
        ],
        low_sample_rows,
    )

    xlsx_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(xlsx_path)


def write_outputs(
    data: dict[str, Any],
    json_path: Path,
    md_path: Path,
    html_path: Path,
    xlsx_path: Path,
    *,
    card_html_paths: dict[str, Path] | None = None,
    duo_html_path: Path | None = None,
    low_cost_html_path: Path | None = None,
    comp_recommendations_html_path: Path | None = None,
    jiujiu_comps_html_path: Path | None = None,
    jiujiu_wearers_html_path: Path | None = None,
    equipment_html_path: Path | None = None,
    trap_compositions_html_path: Path | None = None,
) -> None:
    card_html_paths = card_html_paths or DEFAULT_CARD_HTML_PATHS
    duo_html_path = duo_html_path or DEFAULT_DUO_HTML
    low_cost_html_path = low_cost_html_path or DEFAULT_LOW_COST_HTML
    comp_recommendations_html_path = (
        comp_recommendations_html_path or DEFAULT_COMP_RECOMMENDATIONS_HTML
    )
    jiujiu_comps_html_path = jiujiu_comps_html_path or DEFAULT_JIUJIU_COMPS_HTML
    jiujiu_wearers_html_path = jiujiu_wearers_html_path or DEFAULT_JIUJIU_WEARERS_HTML
    equipment_html_path = equipment_html_path or DEFAULT_EQUIPMENT_HTML
    trap_compositions_html_path = (
        trap_compositions_html_path or DEFAULT_TRAP_COMPOSITIONS_HTML
    )

    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    duo_html_path.parent.mkdir(parents=True, exist_ok=True)
    low_cost_html_path.parent.mkdir(parents=True, exist_ok=True)
    comp_recommendations_html_path.parent.mkdir(parents=True, exist_ok=True)
    jiujiu_comps_html_path.parent.mkdir(parents=True, exist_ok=True)
    jiujiu_wearers_html_path.parent.mkdir(parents=True, exist_ok=True)
    equipment_html_path.parent.mkdir(parents=True, exist_ok=True)
    trap_compositions_html_path.parent.mkdir(parents=True, exist_ok=True)
    for path in card_html_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)

    table_html_outputs = render_table_html_outputs(data)
    card_output_keys = {
        "彩": "cards_cai",
        "黄": "cards_yellow",
        "蓝": "cards_blue",
        "白": "cards_white",
    }
    data["outputs"] = {
        "equipment_xlsx": rel(xlsx_path),
        "json": rel(json_path),
        "markdown": rel(md_path),
        "html": rel(html_path),
        "cards_html": {
            prefix: rel(card_html_paths[prefix]) for prefix in CARD_HTML_SUFFIXES
        },
        "duo_compositions_html": rel(duo_html_path),
        "low_cost_carries_html": rel(low_cost_html_path),
        "composition_recommendations_html": rel(comp_recommendations_html_path),
        "jiujiu_comps_html": rel(jiujiu_comps_html_path),
        "jiujiu_wearers_html": rel(jiujiu_wearers_html_path),
        "equipment_html": rel(equipment_html_path),
        "trap_compositions_html": rel(trap_compositions_html_path),
    }
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_md(data), encoding="utf-8")
    html_path.write_text(render_html(data), encoding="utf-8")
    render_xlsx(data, xlsx_path)

    for prefix, output_key in card_output_keys.items():
        card_html_paths[prefix].write_text(
            table_html_outputs[output_key],
            encoding="utf-8",
        )
    duo_html_path.write_text(table_html_outputs["duo_compositions"], encoding="utf-8")
    low_cost_html_path.write_text(table_html_outputs["low_cost_carries"], encoding="utf-8")
    comp_recommendations_html_path.write_text(
        table_html_outputs["composition_recommendations"],
        encoding="utf-8",
    )
    jiujiu_comps_html_path.write_text(table_html_outputs["jiujiu_comps"], encoding="utf-8")
    jiujiu_wearers_html_path.write_text(
        table_html_outputs["jiujiu_wearers"],
        encoding="utf-8",
    )
    equipment_html_path.write_text(table_html_outputs["equipment"], encoding="utf-8")
    trap_compositions_html_path.write_text(
        table_html_outputs["trap_compositions"],
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="SQLite match DB path")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="JSON output path")
    parser.add_argument("--md", type=Path, default=DEFAULT_MD, help="Markdown output path")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="HTML poster output path")
    parser.add_argument("--xlsx", type=Path, default=DEFAULT_XLSX, help="Excel equipment output path")
    parser.add_argument(
        "--cards-html-cai",
        type=Path,
        default=DEFAULT_CARD_HTML_PATHS["彩"],
        help="HTML output path for 彩类单卡排名",
    )
    parser.add_argument(
        "--cards-html-yellow",
        type=Path,
        default=DEFAULT_CARD_HTML_PATHS["黄"],
        help="HTML output path for 黄类单卡排名",
    )
    parser.add_argument(
        "--cards-html-blue",
        type=Path,
        default=DEFAULT_CARD_HTML_PATHS["蓝"],
        help="HTML output path for 蓝类单卡排名",
    )
    parser.add_argument(
        "--cards-html-white",
        type=Path,
        default=DEFAULT_CARD_HTML_PATHS["白"],
        help="HTML output path for 白类单卡排名",
    )
    parser.add_argument(
        "--duo-html",
        type=Path,
        default=DEFAULT_DUO_HTML,
        help="HTML output path for duo composition synergy",
    )
    parser.add_argument(
        "--low-cost-html",
        type=Path,
        default=DEFAULT_LOW_COST_HTML,
        help="HTML output path for low-cost carry popularity",
    )
    parser.add_argument(
        "--compositions-html",
        type=Path,
        default=DEFAULT_COMP_RECOMMENDATIONS_HTML,
        help="HTML output path for paginated composition recommendations",
    )
    parser.add_argument(
        "--jiujiu-comps-html",
        type=Path,
        default=DEFAULT_JIUJIU_COMPS_HTML,
        help="HTML output path for jiujiu-dependent composition table",
    )
    parser.add_argument(
        "--jiujiu-wearers-html",
        type=Path,
        default=DEFAULT_JIUJIU_WEARERS_HTML,
        help="HTML output path for jiujiu wearer recommendations",
    )
    parser.add_argument(
        "--equipment-html",
        type=Path,
        default=DEFAULT_EQUIPMENT_HTML,
        help="HTML output path for filterable hero equipment recommendations",
    )
    parser.add_argument(
        "--trap-compositions-html",
        type=Path,
        default=DEFAULT_TRAP_COMPOSITIONS_HTML,
        help="HTML output path for trap composition detail pages",
    )
    parser.add_argument("--balance-notes", type=Path, default=None, help="Optional balance notes file")
    parser.add_argument("--min-comp-apps", type=int, default=5)
    parser.add_argument("--min-entity-apps", type=int, default=10)
    parser.add_argument("--min-card-apps", type=int, default=12)
    return parser


def resolve_output_path(path: Path) -> Path:
    return path if path.is_absolute() else ROOT / path


def main() -> None:
    args = build_parser().parse_args()
    data = build_analysis(args)
    json_path = resolve_output_path(args.json)
    md_path = resolve_output_path(args.md)
    html_path = resolve_output_path(args.html)
    xlsx_path = resolve_output_path(args.xlsx)
    card_html_paths = {
        "彩": resolve_output_path(args.cards_html_cai),
        "黄": resolve_output_path(args.cards_html_yellow),
        "蓝": resolve_output_path(args.cards_html_blue),
        "白": resolve_output_path(args.cards_html_white),
    }
    duo_html_path = resolve_output_path(args.duo_html)
    low_cost_html_path = resolve_output_path(args.low_cost_html)
    comp_recommendations_html_path = resolve_output_path(args.compositions_html)
    jiujiu_comps_html_path = resolve_output_path(args.jiujiu_comps_html)
    jiujiu_wearers_html_path = resolve_output_path(args.jiujiu_wearers_html)
    equipment_html_path = resolve_output_path(args.equipment_html)
    trap_compositions_html_path = resolve_output_path(args.trap_compositions_html)
    write_outputs(
        data,
        json_path,
        md_path,
        html_path,
        xlsx_path,
        card_html_paths=card_html_paths,
        duo_html_path=duo_html_path,
        low_cost_html_path=low_cost_html_path,
        comp_recommendations_html_path=comp_recommendations_html_path,
        jiujiu_comps_html_path=jiujiu_comps_html_path,
        jiujiu_wearers_html_path=jiujiu_wearers_html_path,
        equipment_html_path=equipment_html_path,
        trap_compositions_html_path=trap_compositions_html_path,
    )
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(f"Wrote {rel(html_path)}")
    print(f"Wrote {rel(xlsx_path)}")
    for prefix in CARD_HTML_SUFFIXES:
        print(f"Wrote {rel(card_html_paths[prefix])}")
    print(f"Wrote {rel(duo_html_path)}")
    print(f"Wrote {rel(low_cost_html_path)}")
    print(f"Wrote {rel(comp_recommendations_html_path)}")
    print(f"Wrote {rel(jiujiu_comps_html_path)}")
    print(f"Wrote {rel(jiujiu_wearers_html_path)}")
    print(f"Wrote {rel(equipment_html_path)}")
    print(f"Wrote {rel(trap_compositions_html_path)}")


if __name__ == "__main__":
    main()
