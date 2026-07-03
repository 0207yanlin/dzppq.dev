# -*- coding: utf-8 -*-
"""Meta analysis helpers: hero/trait resolution, player features, aggregations."""

from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

# Heroes in DB that are not exact config keys.
HERO_ALIASES: dict[str, str] = {
    "双面教师林野·前排": "双面教师林野",
    "双面教师林野·后排": "双面教师林野",
}

# Fallback mapping when hero is absent from config_s2 (tier + bond traits only).
HERO_FALLBACK: dict[str, list[Any]] = {
    "转校生坦克": [4, "变装社", "硬抗学霸"],
    "转校生射手": [4, "变装社", "粉笔射手"],
    "转校生战士": [4, "变装社", "志在必得者"],
    "转校生法师": [4, "变装社", "学渣伪装者"],
    "暴龙虾饺": [1, "美食社"],
}

JIujiu_SUFFIX = "啾啾"


@dataclass
class HeroRecord:
    hero_name: str
    tier: int | None
    stars: int
    equipment_count: int
    equipments: list[str]
    hero_score: float | None = None


@dataclass
class PlayerFeatures:
    player_id: int
    match_id: int
    rank: int
    heroes: list[HeroRecord] = field(default_factory=list)
    trait_counts: Counter = field(default_factory=Counter)
    jiujiu_bonus: Counter = field(default_factory=Counter)
    trait_totals: Counter = field(default_factory=Counter)
    active_traits: dict[str, int] = field(default_factory=dict)
    main_trait: str | None = None
    sub_traits: list[str] = field(default_factory=list)
    comp_type: str = "混合"
    core_heroes: list[str] = field(default_factory=list)
    comp_key: str = ""
    cards: list[str] = field(default_factory=list)


def load_game_config() -> tuple[dict[str, list[Any]], dict[str, list[int]]]:
    from config_s2 import dict_bond, dict_character

    return dict_character, dict_bond


def normalize_hero_name(name: str) -> str:
    return HERO_ALIASES.get(name, name)


def resolve_character(
    hero_name: str,
    dict_character: dict[str, list[Any]],
    db_tier: int | None = None,
) -> tuple[int | None, list[str]]:
    canonical = normalize_hero_name(hero_name)
    if canonical in dict_character:
        entry = dict_character[canonical]
        tier = int(entry[0])
        traits = [str(t) for t in entry[1:]]
        return tier, traits
    if hero_name in HERO_FALLBACK:
        entry = HERO_FALLBACK[hero_name]
        tier = int(entry[0])
        traits = [str(t) for t in entry[1:]]
        return tier, traits
    return db_tier, []


def parse_equipment_count(value: str | None) -> int:
    if not value or value == "-":
        return 0
    try:
        return int(value)
    except ValueError:
        return 0


def jiujiu_trait(equipment_name: str) -> str | None:
    if equipment_name.endswith(JIujiu_SUFFIX):
        return equipment_name[: -len(JIujiu_SUFFIX)]
    return None


def active_tier(count: int, thresholds: list[int]) -> int:
    tier = 0
    for threshold in sorted(thresholds):
        if count >= threshold:
            tier = threshold
    return tier


def compute_trait_totals(
    heroes: list[HeroRecord],
    dict_character: dict[str, list[Any]],
    dict_bond: dict[str, list[int]],
) -> tuple[Counter, Counter, Counter]:
    """Return hero-only counts, jiujiu bonus, and combined totals for bond traits."""
    hero_counts: Counter = Counter()
    jiujiu_counts: Counter = Counter()

    for hero in heroes:
        _, traits = resolve_character(hero.hero_name, dict_character, hero.tier)
        for trait in traits:
            if trait in dict_bond:
                hero_counts[trait] += 1
        for eq in hero.equipments:
            trait = jiujiu_trait(eq)
            if trait and trait in dict_bond:
                jiujiu_counts[trait] += 1

    totals = hero_counts + jiujiu_counts
    return hero_counts, jiujiu_counts, totals


def compute_active_traits(
    trait_totals: Counter,
    dict_bond: dict[str, list[int]],
) -> dict[str, int]:
    active: dict[str, int] = {}
    for trait, count in trait_totals.items():
        if trait not in dict_bond:
            continue
        tier = active_tier(count, dict_bond[trait])
        if tier > 0:
            active[trait] = tier
    return active


def pick_main_and_sub_traits(active_traits: dict[str, int]) -> tuple[str | None, list[str]]:
    if not active_traits:
        return None, []
    ranked = sorted(
        active_traits.items(),
        key=lambda item: (item[1], item[0]),
        reverse=True,
    )
    main_trait = ranked[0][0]
    sub_traits = [name for name, tier in ranked[1:] if tier >= 2][:4]
    return main_trait, sub_traits


def classify_comp_type(heroes: list[HeroRecord], dict_character: dict[str, list[Any]]) -> str:
    if not heroes:
        return "混合"

    tiers: list[int] = []
    star13 = 0
    star3_low = 0
    count4 = 0
    count5 = 0
    carry4 = 0

    for hero in heroes:
        tier, _ = resolve_character(hero.hero_name, dict_character, hero.tier)
        if tier is None:
            continue
        tiers.append(tier)
        if tier <= 3 and hero.stars >= 3:
            star3_low += 1
        if tier <= 3 and hero.stars >= 1:
            star13 += 1
        if tier == 4:
            count4 += 1
            if hero.equipment_count >= 2 or hero.stars >= 2:
                carry4 += 1
        if tier == 5:
            count5 += 1

    if not tiers:
        return "混合"

    avg_tier = sum(tiers) / len(tiers)
    low_tier_ratio = sum(1 for t in tiers if t <= 3) / len(tiers)

    score_goudou = 0.0
    score_84 = 0.0
    score_95 = 0.0

    if avg_tier <= 2.3 or low_tier_ratio >= 0.65:
        score_goudou += 2.0
    if star3_low >= 2:
        score_goudou += 2.5
    if star3_low >= 1 and avg_tier <= 2.8:
        score_goudou += 1.0

    if count4 >= 2:
        score_84 += 2.5
    if carry4 >= 1:
        score_84 += 2.0
    if count4 >= 1 and avg_tier >= 3.0:
        score_84 += 1.0

    if count5 >= 2:
        score_95 += 3.0
    elif count5 >= 1 and avg_tier >= 3.8:
        score_95 += 2.0
    if count5 >= 1 and count4 >= 1:
        score_95 += 1.0

    scores = {"赌狗": score_goudou, "八四": score_84, "九五": score_95}
    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    if best_score < 1.5:
        return "混合"
    return best_type


def pick_core_heroes(heroes: list[HeroRecord], dict_character: dict[str, list[Any]]) -> list[str]:
    scored: list[tuple[float, str]] = []
    for hero in heroes:
        tier, _ = resolve_character(hero.hero_name, dict_character, hero.tier)
        if tier is None:
            tier = 0
        score = hero.stars * 10 + tier * 3 + hero.equipment_count * 2
        scored.append((score, hero.hero_name))
    scored.sort(reverse=True)
    cores: list[str] = []
    for _, name in scored:
        if name not in cores:
            cores.append(name)
        if len(cores) >= 3:
            break
    return cores


def build_comp_key(
    comp_type: str,
    main_trait: str | None,
    active_traits: dict[str, int],
    core_heroes: list[str],
) -> str:
    main_part = "无羁绊"
    if main_trait:
        main_part = f"{main_trait}-{active_traits.get(main_trait, 0)}"
    core_part = "+".join(core_heroes) if core_heroes else "无核心"
    return f"{comp_type}|{main_part}|{core_part}"


def validate_config(conn: sqlite3.Connection) -> dict[str, Any]:
    dict_character, dict_bond = load_game_config()
    db_heroes = [
        r[0]
        for r in conn.execute(
            "SELECT DISTINCT hero_name FROM heroes WHERE hero_name != 'unknown'"
        )
    ]
    missing = []
    resolved = []
    for hero in sorted(db_heroes):
        canonical = normalize_hero_name(hero)
        if canonical in dict_character or hero in HERO_FALLBACK:
            resolved.append(hero)
        else:
            missing.append(hero)

    config_not_in_db = [
        name
        for name in dict_character
        if name not in db_heroes
        and not any(normalize_hero_name(h) == name for h in db_heroes)
    ]

    invalid_traits: list[str] = []
    for name, entry in dict_character.items():
        for trait in entry[1:]:
            trait_str = str(trait)
            if trait_str not in dict_bond:
                invalid_traits.append(f"{name}:{trait_str}")

    jiujiu_in_db = [
        r[0]
        for r in conn.execute(
            """
            SELECT DISTINCT equipment_name FROM hero_equipments
            WHERE equipment_name LIKE '%啾啾%'
            """
        )
    ]
    jiujiu_unmapped = [
        eq for eq in jiujiu_in_db if jiujiu_trait(eq) not in dict_bond
    ]

    return {
        "db_hero_count": len(db_heroes),
        "resolved_hero_count": len(resolved),
        "missing_heroes": missing,
        "config_heroes_not_in_db": config_not_in_db,
        "special_traits_without_threshold": sorted(set(invalid_traits)),
        "jiujiu_equipment_in_db": jiujiu_in_db,
        "jiujiu_unmapped": jiujiu_unmapped,
    }


def load_player_data(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    players = [dict(r) for r in conn.execute("SELECT * FROM players")]
    heroes_by_player: dict[int, list[HeroRecord]] = defaultdict(list)
    hero_rows = conn.execute(
        """
        SELECT h.*, he.equipment_name
        FROM heroes h
        LEFT JOIN hero_equipments he ON he.hero_id = h.id
        WHERE h.hero_name != 'unknown'
        ORDER BY h.player_id, h.slot_index, he.item_index
        """
    )
    hero_map: dict[int, HeroRecord] = {}
    for row in hero_rows:
        hero_id = row["id"]
        if hero_id not in hero_map:
            hero_map[hero_id] = HeroRecord(
                hero_name=row["hero_name"],
                tier=row["tier"],
                stars=row["stars"] or 0,
                equipment_count=parse_equipment_count(row["equipment_count"]),
                equipments=[],
                hero_score=row["hero_score"],
            )
            heroes_by_player[row["player_id"]].append(hero_map[hero_id])
        eq_name = row["equipment_name"]
        if eq_name and eq_name != "unknown":
            hero_map[hero_id].equipments.append(eq_name)

    cards_by_player: dict[int, list[str]] = defaultdict(list)
    for row in conn.execute(
        "SELECT player_id, card_name FROM cards WHERE card_name != 'unknown'"
    ):
        cards_by_player[row[0]].append(row[1])

    result = []
    for player in players:
        result.append(
            {
                "player_id": player["id"],
                "match_id": player["match_id"],
                "rank": player["rank"],
                "heroes": heroes_by_player.get(player["id"], []),
                "cards": cards_by_player.get(player["id"], []),
            }
        )
    return result


def build_player_features(raw_players: list[dict[str, Any]]) -> list[PlayerFeatures]:
    dict_character, dict_bond = load_game_config()
    features: list[PlayerFeatures] = []

    for raw in raw_players:
        heroes: list[HeroRecord] = raw["heroes"]
        hero_counts, jiujiu_counts, totals = compute_trait_totals(
            heroes, dict_character, dict_bond
        )
        active_traits = compute_active_traits(totals, dict_bond)
        main_trait, sub_traits = pick_main_and_sub_traits(active_traits)
        comp_type = classify_comp_type(heroes, dict_character)
        core_heroes = pick_core_heroes(heroes, dict_character)
        comp_key = build_comp_key(comp_type, main_trait, active_traits, core_heroes)

        features.append(
            PlayerFeatures(
                player_id=raw["player_id"],
                match_id=raw["match_id"],
                rank=raw["rank"],
                heroes=heroes,
                trait_counts=hero_counts,
                jiujiu_bonus=jiujiu_counts,
                trait_totals=totals,
                active_traits=active_traits,
                main_trait=main_trait,
                sub_traits=sub_traits,
                comp_type=comp_type,
                core_heroes=core_heroes,
                comp_key=comp_key,
                cards=raw["cards"],
            )
        )
    return features


@dataclass
class RankStats:
    appearances: int = 0
    rank_sum: int = 0
    wins: int = 0
    top2: int = 0
    top4: int = 0

    def add(self, rank: int) -> None:
        self.appearances += 1
        self.rank_sum += rank
        if rank == 1:
            self.wins += 1
        if rank <= 2:
            self.top2 += 1
        if rank <= 4:
            self.top4 += 1

    def to_dict(self) -> dict[str, Any]:
        n = self.appearances or 1
        return {
            "appearances": self.appearances,
            "avg_rank": round(self.rank_sum / n, 2),
            "win_rate": round(100.0 * self.wins / n, 1),
            "top2_rate": round(100.0 * self.top2 / n, 1),
            "top4_rate": round(100.0 * self.top4 / n, 1),
        }


def aggregate_by_key(
    items: list[tuple[str, int]],
    min_apps: int = 1,
) -> list[dict[str, Any]]:
    stats: dict[str, RankStats] = defaultdict(RankStats)
    for key, rank in items:
        stats[key].add(rank)
    result = []
    for key, stat in stats.items():
        if stat.appearances < min_apps:
            continue
        row = {"key": key, **stat.to_dict()}
        result.append(row)
    result.sort(key=lambda row: (row["avg_rank"], -row["top4_rate"]))
    return result


def compute_hero_rankings(
    conn: sqlite3.Connection,
    min_apps: int = 15,
    total_slots: int | None = None,
) -> list[dict[str, Any]]:
    dict_character, _ = load_game_config()
    if total_slots is None:
        total_slots = conn.execute(
            "SELECT COUNT(*) FROM heroes WHERE hero_name != 'unknown'"
        ).fetchone()[0]

    rows = conn.execute(
        """
        SELECT
            h.hero_name,
            h.tier,
            h.stars,
            h.equipment_count,
            p.rank
        FROM heroes h
        JOIN players p ON h.player_id = p.id
        WHERE h.hero_name != 'unknown'
        """
    ).fetchall()

    by_hero: dict[str, RankStats] = defaultdict(RankStats)
    star_sum: Counter = Counter()
    star_count: Counter = Counter()
    eq_sum: Counter = Counter()
    eq_count: Counter = Counter()

    for hero_name, tier, stars, eq_count_raw, rank in rows:
        by_hero[hero_name].add(rank)
        star_sum[hero_name] += stars or 0
        star_count[hero_name] += 1
        eq = parse_equipment_count(eq_count_raw)
        eq_sum[hero_name] += eq
        eq_count[hero_name] += 1

    result = []
    for hero_name, stat in by_hero.items():
        if stat.appearances < min_apps:
            continue
        tier, _ = resolve_character(hero_name, dict_character, None)
        result.append(
            {
                "hero_name": hero_name,
                "tier": tier,
                "appearances": stat.appearances,
                "pick_rate": round(100.0 * stat.appearances / total_slots, 1),
                "avg_rank": round(stat.rank_sum / stat.appearances, 2),
                "win_rate": round(100.0 * stat.wins / stat.appearances, 1),
                "top2_rate": round(100.0 * stat.top2 / stat.appearances, 1),
                "top4_rate": round(100.0 * stat.top4 / stat.appearances, 1),
                "avg_stars": round(star_sum[hero_name] / star_count[hero_name], 2),
                "avg_equipment_count": round(
                    eq_sum[hero_name] / eq_count[hero_name], 2
                ),
            }
        )
    result.sort(key=lambda row: (row["avg_rank"], -row["top4_rate"]))
    return result


def compute_trait_rankings(
    player_features: list[PlayerFeatures],
    min_apps: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    main_items: list[tuple[str, int]] = []
    sub_items: list[tuple[str, int]] = []
    trait_tier_items: list[tuple[str, int]] = []
    jiujiu_items: list[tuple[str, int]] = []

    for pf in player_features:
        if pf.main_trait:
            tier = pf.active_traits.get(pf.main_trait, 0)
            main_items.append((f"{pf.main_trait}-{tier}", pf.rank))
            trait_tier_items.append((f"{pf.main_trait}-{tier}", pf.rank))
        for sub in pf.sub_traits:
            tier = pf.active_traits.get(sub, 0)
            sub_items.append((f"{sub}-{tier}", pf.rank))
            trait_tier_items.append((f"{sub}-{tier}", pf.rank))
        for trait, bonus in pf.jiujiu_bonus.items():
            if bonus > 0:
                jiujiu_items.append((trait, pf.rank))

    return {
        "main_trait_tiers": aggregate_by_key(main_items, min_apps),
        "sub_trait_tiers": aggregate_by_key(sub_items, max(10, min_apps // 2)),
        "all_trait_tiers": aggregate_by_key(trait_tier_items, min_apps),
        "jiujiu_assisted_players": aggregate_by_key(jiujiu_items, 10),
    }


def compute_comp_rankings(
    player_features: list[PlayerFeatures],
    min_apps: int = 5,
) -> dict[str, list[dict[str, Any]]]:
    by_type: list[tuple[str, int]] = []
    by_comp: list[tuple[str, int]] = []
    by_type_trait: list[tuple[str, int]] = []

    comp_meta: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "comp_type": "",
            "main_trait": None,
            "main_tier": 0,
            "core_heroes": [],
            "sub_trait_counter": Counter(),
        }
    )

    for pf in player_features:
        by_type.append((pf.comp_type, pf.rank))
        by_comp.append((pf.comp_key, pf.rank))
        if pf.main_trait:
            by_type_trait.append(
                (f"{pf.comp_type}|{pf.main_trait}-{pf.active_traits.get(pf.main_trait, 0)}", pf.rank)
            )
        meta = comp_meta[pf.comp_key]
        meta["comp_type"] = pf.comp_type
        meta["main_trait"] = pf.main_trait
        meta["main_tier"] = pf.active_traits.get(pf.main_trait or "", 0)
        meta["core_heroes"] = pf.core_heroes
        for sub in pf.sub_traits:
            meta["sub_trait_counter"][sub] += 1

    comp_stats = aggregate_by_key(by_comp, min_apps)
    enriched = []
    for row in comp_stats:
        meta = comp_meta[row["key"]]
        sub_traits = [
            name
            for name, _ in meta["sub_trait_counter"].most_common(3)
        ]
        enriched.append(
            {
                **row,
                "comp_type": meta["comp_type"],
                "main_trait": meta["main_trait"],
                "main_tier": meta["main_tier"],
                "core_heroes": meta["core_heroes"],
                "recommended_sub_traits": sub_traits,
                "label": row["key"].replace("|", " / "),
            }
        )

    return {
        "by_comp_type": aggregate_by_key(by_type, 20),
        "by_comp_key": enriched,
        "by_type_and_main_trait": aggregate_by_key(by_type_trait, 10),
    }


def compute_equipment_rankings(
    conn: sqlite3.Connection,
    min_apps: int = 15,
) -> dict[str, list[dict[str, Any]]]:
    rows = conn.execute(
        """
        SELECT he.equipment_name, h.hero_name, p.rank
        FROM hero_equipments he
        JOIN heroes h ON h.id = he.hero_id
        JOIN players p ON p.id = h.player_id
        WHERE he.equipment_name != 'unknown' AND h.hero_name != 'unknown'
        """
    ).fetchall()

    by_eq: dict[str, RankStats] = defaultdict(RankStats)
    by_pair: dict[str, RankStats] = defaultdict(RankStats)

    for eq_name, hero_name, rank in rows:
        by_eq[eq_name].add(rank)
        by_pair[f"{hero_name}|{eq_name}"].add(rank)

    equipment = []
    for name, stat in by_eq.items():
        if stat.appearances < min_apps:
            continue
        equipment.append({"equipment_name": name, **stat.to_dict()})
    equipment.sort(key=lambda row: (row["avg_rank"], -row["top4_rate"]))

    hero_equipment = []
    for key, stat in by_pair.items():
        if stat.appearances < 8:
            continue
        hero_name, eq_name = key.split("|", 1)
        hero_equipment.append(
            {
                "hero_name": hero_name,
                "equipment_name": eq_name,
                **stat.to_dict(),
            }
        )
    hero_equipment.sort(key=lambda row: (row["avg_rank"], -row["top4_rate"]))
    return {"equipment": equipment, "hero_equipment": hero_equipment}


def compute_hero_equipment_recommendations(
    hero_equipment: list[dict[str, Any]],
    hero_rankings: list[dict[str, Any]],
    top_n: int = 3,
) -> list[dict[str, Any]]:
    strong_heroes = {row["hero_name"] for row in hero_rankings[:20]}
    by_hero: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in hero_equipment:
        if row["hero_name"] not in strong_heroes:
            continue
        by_hero[row["hero_name"]].append(row)

    recommendations = []
    for hero_name, rows in by_hero.items():
        rows.sort(key=lambda item: (item["avg_rank"], -item["top4_rate"]))
        picks = rows[:top_n]
        if not picks:
            continue
        recommendations.append(
            {
                "hero_name": hero_name,
                "recommended_equipment": [
                    {
                        "equipment_name": pick["equipment_name"],
                        "avg_rank": pick["avg_rank"],
                        "top4_rate": pick["top4_rate"],
                        "appearances": pick["appearances"],
                    }
                    for pick in picks
                ],
            }
        )
    recommendations.sort(
        key=lambda row: row["recommended_equipment"][0]["avg_rank"]
        if row["recommended_equipment"]
        else 99
    )
    return recommendations


def compute_card_rankings(conn: sqlite3.Connection, min_apps: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT c.card_name, p.rank
        FROM cards c
        JOIN players p ON c.player_id = p.id
        WHERE c.card_name != 'unknown'
        """
    ).fetchall()
    items = [(name, rank) for name, rank in rows]
    result = aggregate_by_key(items, min_apps)
    return [{"card_name": row["key"], **{k: v for k, v in row.items() if k != "key"}} for row in result]


def compute_cooccurrence(
    conn: sqlite3.Connection,
    min_apps: int = 10,
) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT p.id AS player_id, p.rank, h.hero_name
        FROM players p
        JOIN heroes h ON h.player_id = p.id
        WHERE h.hero_name != 'unknown'
        """
    ).fetchall()
    by_player: dict[int, list[str]] = defaultdict(list)
    ranks: dict[int, int] = {}
    for player_id, rank, hero_name in rows:
        by_player[player_id].append(hero_name)
        ranks[player_id] = rank

    pair_stats: dict[tuple[str, str], RankStats] = defaultdict(RankStats)
    for player_id, heroes in by_player.items():
        heroes = sorted(set(heroes))
        rank = ranks[player_id]
        for i in range(len(heroes)):
            for j in range(i + 1, len(heroes)):
                pair_stats[(heroes[i], heroes[j])].add(rank)

    result = []
    for (a, b), stat in pair_stats.items():
        if stat.appearances < min_apps:
            continue
        result.append(
            {
                "pair": f"{a} + {b}",
                "hero_a": a,
                "hero_b": b,
                **stat.to_dict(),
            }
        )
    result.sort(key=lambda row: (row["avg_rank"], -row["top4_rate"]))
    return result


def find_traps(
    hero_rankings: list[dict[str, Any]],
    pick_threshold: float = 8.0,
    rank_threshold: float = 5.0,
) -> list[dict[str, Any]]:
    traps = [
        row
        for row in hero_rankings
        if row["pick_rate"] >= pick_threshold and row["avg_rank"] >= rank_threshold
    ]
    traps.sort(key=lambda row: (-row["pick_rate"], -row["avg_rank"]))
    return traps[:10]


def db_overview(conn: sqlite3.Connection) -> dict[str, Any]:
    from src.match_db import db_summary

    summary = db_summary(conn)
    rank_dist = {
        str(row[0]): row[1]
        for row in conn.execute("SELECT rank, COUNT(*) FROM players GROUP BY rank")
    }
    return {"summary": summary, "rank_distribution": rank_dist}
