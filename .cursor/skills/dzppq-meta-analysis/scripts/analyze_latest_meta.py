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

DEFAULT_JSON = ROOT / "data" / "latest_meta_analysis.json"
DEFAULT_MD = ROOT / "data" / "latest_meta_analysis_report.md"
DEFAULT_HTML = ROOT / "data" / "latest_meta_analysis_report.html"

CARD_GRANTED_HEROES = {"暴龙虾饺"}
PLAY_STYLES = ("赌狗", "高费")

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
        SELECT player_id, card_name
        FROM cards
        WHERE player_id IN ({})
        ORDER BY player_id, slot_index
        """.format(",".join("?" for _ in kept_player_ids) or "NULL"),
        tuple(kept_player_ids),
    ).fetchall()
    for row in card_rows:
        card_name = str(row["card_name"])
        if card_name != "unknown":
            cards_by_player[int(row["player_id"])].append(card_name)

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
                main_carry=carries[0] if carries else None,
                secondary_carry=carries[1] if len(carries) > 1 else None,
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
    active_bond_counter: Counter[str] = Counter()
    for member in members:
        stats.add(member.rank)
        hero_counter.update(member.hero_set)
        if member.main_carry:
            carry_counter[member.main_carry.name] += 1
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

    variants = build_level_variants(members, hero_counter)
    top_bonds = active_bond_counter.most_common(8)
    main_carries = carry_counter.most_common(3)
    label_info = derive_family_label(members, active_bond_counter, main_carries)
    main_bond = subfamily_key or label_info["key"]
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
            {"hero_name": name, "share": round(count * 100.0 / len(members), 1)}
            for name, count in main_carries
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
        "stats": stats.to_dict(),
        "difficulty": {
            "label": difficulty,
            "unfinished_bottom_rate": round(unfinished_rate, 1),
            "carry_complete_rate": round(carry_complete_rate, 1),
            "avg_same_match_contest": round(avg_contest, 2),
        },
        "popularity": {
            "label": popularity,
            "pick_rate": round(pick_rate, 1),
            "match_share": round(match_share, 1),
            "avg_same_match_contest": round(avg_contest, 2),
        },
        "confidence": confidence_label(len(members)),
        "member_player_ids": [member.player_id for member in members],
        "high_cost_three_star_dependency": high_cost_three_star_dependency,
    }
    row["recommendation_score"] = composition_recommendation_score(row)
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
    names = sorted(item["hero_name"] for item in row.get("main_carries", [])[:2])
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
            row["recommendation_score"],
            -row["aggregate_stats"]["appearances"],
            row["aggregate_stats"]["avg_rank"],
        )
    )
    return strategies


def build_composition_recommendations(
    comp_rows: list[dict[str, Any]],
    limit: int = 8,
) -> dict[str, list[dict[str, Any]]]:
    return {
        style: [row for row in comp_rows if row.get("play_style") == style][:limit]
        for style in PLAY_STYLES
    }


def build_level_variants(
    members: list[PlayerFeature],
    family_hero_counter: Counter[str],
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
            best = sorted(exact, key=lambda item: (item.rank, -len(item.hero_set)))[0]
            variants[str(target)] = {
                "source": "sample",
                "confidence": confidence_label(len(exact)),
                "rank": best.rank,
                "heroes": unique_heroes_by_slot(best),
                "main_carry": best.main_carry.name if best.main_carry else None,
            }
        else:
            variants[str(target)] = {
                "source": "derived",
                "confidence": "低",
                "rank": None,
                "heroes": hero_order[:target],
                "main_carry": members[0].main_carry.name if members and members[0].main_carry else None,
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


def carry_for_name(feature: PlayerFeature, hero_name: str) -> Hero | None:
    if feature.main_carry and feature.main_carry.name == hero_name:
        return feature.main_carry
    return None


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
                "median_stars_top4": median_number(stars),
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


def analyze_cards(
    features: list[PlayerFeature],
    comp_rows: list[dict[str, Any]],
    min_apps: int,
    baseline: float,
    team_baseline: float,
) -> dict[str, Any]:
    single_items: list[tuple[str, int]] = []
    first_card_items: list[tuple[str, int]] = []
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

    for feature in features:
        cards = sorted(set(feature.cards))
        for card in cards:
            single_items.append((card, feature.rank))
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

    return {
        "single_cards": aggregate_key_stats(single_items, min_apps, baseline),
        "first_card_rankings": aggregate_key_stats(first_card_items, max(6, min_apps // 2), baseline),
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
            if feature.main_carry and hero.id == feature.main_carry.id:
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

    recommendations = []
    for hero in heroes:
        hero_name = hero["hero_name"]
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
        if items or low_sample_items:
            recommendations.append(
                {
                    "hero_name": hero_name,
                    "hero_stats": hero,
                    "recommended_items": items[:6],
                    "low_sample_observations": low_sample_items[:4],
                    "recommended_sets": sets[:4],
                }
            )

    equipment_rows = []
    for item_name, stat in item_stats.items():
        if stat.appearances >= min_apps:
            equipment_rows.append({"equipment_name": item_name, **stat.to_dict(baseline_rank=baseline, prior=8)})
    equipment_rows.sort(key=lambda row: (-row["appearances"], row["adjusted_avg_rank"], row["avg_rank"], -row["top4_rate"]))

    return {
        "heroes": heroes,
        "carry_equipment_recommendations": recommendations[:30],
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
    is_key_hero = (
        (feature.main_carry and hero.id == feature.main_carry.id)
        or (feature.secondary_carry and hero.id == feature.secondary_carry.id)
    )
    if is_key_hero and feature.rank <= 4 and hero.equipment_count >= 2:
        reasons.append("hero_boost")
    return reasons


def selected_priority_label(selected_rate: float, avg_rank: float, baseline: float) -> str:
    if selected_rate >= 30 and avg_rank < baseline:
        return "高"
    if selected_rate >= 12 and avg_rank <= baseline + 0.2:
        return "中"
    return "低"


def find_traps(
    comp_rows: list[dict[str, Any]],
    hero_rows: list[dict[str, Any]],
    card_rows: list[dict[str, Any]],
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

    strong_traits = {
        trait
        for row in comp_rows
        if (trait := comp_trait(row))
        and row["stats"]["appearances"] >= 20
        and row["stats"]["top4_rate"] >= 60
        and row["stats"]["avg_rank"] <= baseline
    }

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

    return {
        "compositions": comp_traps[:10],
        "heroes": [row for row in hero_rows if row["appearances"] >= 10 and weak(row)][:10],
        "cards": [row for row in card_rows if row["appearances"] >= 12 and weak(row)][:10],
        "bonds": [row for row in bond_rows if row["appearances"] >= 10 and weak(row)][:10],
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
        hero_equipment = analyze_heroes_and_equipment(features, args.min_entity_apps, baseline)
        cards = analyze_cards(features, comp_rows, args.min_card_apps, baseline, team_baseline)
        jiujiu_analysis = analyze_jiujiu(features, comp_rows, baseline)
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
                "cards": cards,
                "heroes_and_equipment": hero_equipment,
                "jiujiu": jiujiu_analysis,
                "traps": traps,
                "balance_targets": balance_targets,
            },
        }
    finally:
        conn.close()


def render_pct(value: float) -> str:
    return f"{value:.1f}%"


def append_comp_markdown(lines: list[str], comp: dict[str, Any]) -> None:
    stats = comp["stats"]
    lines.append(
        f"### {comp['label']}（{comp.get('play_style', '高费')}，{comp['confidence']}置信，n={stats['appearances']}）"
    )
    lines.append("")
    lines.append(
        f"- 表现：avg {stats['avg_rank']:.2f}，top4 {render_pct(stats['top4_rate'])}，吃鸡 {render_pct(stats['win_rate'])}。"
    )
    carries = "、".join(
        f"{item['hero_name']}({render_pct(item['share'])})" for item in comp["main_carries"]
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
            f"（前四中位{row['median_stars_top4']:.1f}星，三件套{render_pct(row['three_item_rate'])}）"
            for row in comp["carry_requirements"][:2]
        )
        lines.append(f"- 主C成型门槛：{req_text}。")
        expensive_note = [
            row["hero_name"]
            for row in comp["carry_requirements"][:2]
            if row.get("high_cost_three_star_dependency")
        ]
        if expensive_note:
            lines.append(
                f"- 成型成本提醒：{ '、'.join(expensive_note) } 的三星高费样本会拉高上限，常规推荐按 2 星门槛评估。"
            )
    if comp.get("carry_equipment_notes"):
        note_parts = []
        for note in comp["carry_equipment_notes"][:2]:
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
    bonds = "、".join(
        f"{item['bond']}({render_pct(item['share'])})" for item in comp["common_bonds"][:5]
    )
    lines.append(f"- 常见羁绊：{bonds or '无稳定羁绊'}。")
    lines.append("")
    lines.append("| 等级 | 来源 | 置信度 | 棋子 |")
    lines.append("| ---: | --- | --- | --- |")
    for level in ("7", "8", "9"):
        variant = comp["variants"][level]
        lines.append(
            f"| {level} | {variant['source']} | {variant['confidence']} | "
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
        top_cards = "、".join(
            f"{row['key']}（修正 {row['adjusted_avg_rank']:.2f}）"
            for row in cards[:5]
        )
        lines.append(f"- 强势卡牌：{top_cards}。")
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
    lines.append("| 阵容 | 类型 | 难度 | 热门 | 后四未成型率 | 同行数 | 出场率 |")
    lines.append("| --- | --- | --- | --- | ---: | ---: | ---: |")
    for comp in comps[:12]:
        difficulty = comp["difficulty"]
        popularity = comp["popularity"]
        lines.append(
            f"| {comp['label']} | {comp.get('play_style', '高费')} | {difficulty['label']} | {popularity['label']} | "
            f"{render_pct(difficulty['unfinished_bottom_rate'])} | "
            f"{difficulty['avg_same_match_contest']:.2f} | {render_pct(popularity['pick_rate'])} |"
        )
    lines.append("")

    lines.append("## 卡牌强度分析")
    lines.append("")
    lines.append(
        "卡牌顺序按 `slot_index` 统计，第一张卡牌视为双人配合重点；队伍排名按每局队伍最高个人名次重新排序为 1-4。"
    )
    lines.append("")
    lines.append("| 卡牌 | 修正名次 | 平均名次 | 前四率 | 吃鸡率 | 样本 |")
    lines.append("| --- | ---: | ---: | ---: | ---: | ---: |")
    for row in cards[:20]:
        lines.append(
            f"| {row['key']} | {row['adjusted_avg_rank']:.2f} | {row['avg_rank']:.2f} | "
            f"{render_pct(row['top4_rate'])} | {render_pct(row['win_rate'])} | {row['appearances']} |"
        )
    lines.append("")
    first_cards = data["rankings"]["cards"]["first_card_rankings"]
    if first_cards:
        lines.append("### 第一张卡牌强度")
        lines.append("")
        lines.append("| 第一卡 | 修正名次 | 平均名次 | 前四率 | 样本 |")
        lines.append("| --- | ---: | ---: | ---: | ---: |")
        for row in first_cards[:12]:
            lines.append(
                f"| {row['key']} | {row['adjusted_avg_rank']:.2f} | {row['avg_rank']:.2f} | "
                f"{render_pct(row['top4_rate'])} | {row['appearances']} |"
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
                f"{card['key']}({card['adjusted_avg_rank']:.2f}, n={card['appearances']})"
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

    lines.append("## 强势棋子与装备推荐")
    lines.append("")
    recommendations = data["rankings"]["heroes_and_equipment"]["carry_equipment_recommendations"]
    for row in recommendations[:15]:
        hero = row["hero_stats"]
        lines.append(
            f"### {row['hero_name']}（主C率 {render_pct(hero['carry_rate'])}，avg {hero['avg_rank']:.2f}，n={hero['appearances']}）"
        )
        if row["recommended_items"]:
            lines.append("")
            lines.append("| 装备 | 修正名次 | 核选占比 | 核选优先级 | 样本 |")
            lines.append("| --- | ---: | ---: | --- | ---: |")
            for item in row["recommended_items"][:6]:
                lines.append(
                    f"| {item['equipment_name']} | {item['adjusted_avg_rank']:.2f} | "
                    f"{render_pct(item['selected_rate'])} | {item['selected_priority']} | {item['appearances']} |"
                )
        if row.get("low_sample_observations"):
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
        lines.append("| 啾啾 | 有效样本 | 有效率 | 有效修正 | 前四率 | 推荐阵容/棋子 |")
        lines.append("| --- | ---: | ---: | ---: | ---: | --- |")
        for row in rankings[:16]:
            comps = "；".join(
                f"{comp['family_label']}({comp['appearances']})"
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
            lines.append(
                f"- {name}：avg {stats['avg_rank']:.2f}，top4 {render_pct(stats['top4_rate'])}，n={stats['appearances']}"
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
        f"{row['hero_name']} {row['recommended_min_stars']}星起"
        for row in comp.get("carry_requirements", [])[:2]
    )
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
      <p><strong>成型：</strong>{esc(req_text or '样本不足')}</p>
      <p><strong>路线：</strong>{esc(unique_route_bonds(comp))}</p>
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


def render_html(data: dict[str, Any]) -> str:
    quality = data["overview"]["quality"]
    recommendations = data["rankings"].get("composition_recommendations", {})
    cards = data["rankings"]["cards"]
    jiujiu_rows = data["rankings"].get("jiujiu", {}).get("jiujiu_rankings", [])[:5]
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

    top_cards = html_list_items(cards["single_cards"][:6])
    duo_cards = html_list_items(cards.get("duo_card_contribution", [])[:4])
    jiujiu_html = "\n".join(
        f"<li><b>{esc(row['equipment_name'])}</b><span>有效 {row['effective_appearances']} / {render_pct(row['effective_rate'])}</span></li>"
        for row in jiujiu_rows
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
      <h3>卡牌优先级</h3>
      <div class="cards">
        <div><h4>单卡</h4><ul>{top_cards}</ul></div>
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
    </div>
  </section>
  <footer>完整数据见 latest_meta_analysis_report.md / latest_meta_analysis.json</footer>
</main>
</body>
</html>
"""


def write_outputs(data: dict[str, Any], json_path: Path, md_path: Path, html_path: Path) -> None:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    md_path.parent.mkdir(parents=True, exist_ok=True)
    html_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_md(data), encoding="utf-8")
    html_path.write_text(render_html(data), encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=None, help="SQLite match DB path")
    parser.add_argument("--json", type=Path, default=DEFAULT_JSON, help="JSON output path")
    parser.add_argument("--md", type=Path, default=DEFAULT_MD, help="Markdown output path")
    parser.add_argument("--html", type=Path, default=DEFAULT_HTML, help="HTML poster output path")
    parser.add_argument("--balance-notes", type=Path, default=None, help="Optional balance notes file")
    parser.add_argument("--min-comp-apps", type=int, default=5)
    parser.add_argument("--min-entity-apps", type=int, default=10)
    parser.add_argument("--min-card-apps", type=int, default=12)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    data = build_analysis(args)
    json_path = args.json if args.json.is_absolute() else ROOT / args.json
    md_path = args.md if args.md.is_absolute() else ROOT / args.md
    html_path = args.html if args.html.is_absolute() else ROOT / args.html
    write_outputs(data, json_path, md_path, html_path)
    print(f"Wrote {rel(json_path)}")
    print(f"Wrote {rel(md_path)}")
    print(f"Wrote {rel(html_path)}")


if __name__ == "__main__":
    main()
