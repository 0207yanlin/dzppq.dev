# -*- coding: utf-8 -*-
"""Hard-coded disambiguation rules for visually identical card icons."""

from __future__ import annotations

QUALITY_PARTNER_SUPPORT_GROUP = frozenset(
    {"重质拍档支援", "重质也重量pro", "最佳拍档", "最强支援", "拍档支援"},
)
FAST_XXB_GROUP = frozenset({"快速成型", "吸吸宝pro"})

CARD_LABEL_ALIASES: dict[str, str] = {
    "装备共鸣法pro": "装备共鸣pro",
    "装备共鸣血pro": "装备共鸣pro",
    "装备共鸣攻pro": "装备共鸣pro",
    "装备共鸣法": "装备共鸣",
    "装备共鸣血": "装备共鸣",
    "装备共鸣攻": "装备共鸣",
    "大力": "大力巫术守护",
    "巫术": "大力巫术守护",
    "守护": "大力巫术守护",
    "福袋·蓝": "福袋有钱",
    "有钱同享": "福袋有钱",
}


def normalize_card_label(label: str) -> str:
    """Map legacy variant card names to canonical labels."""
    return CARD_LABEL_ALIASES.get(label, label)


def resolve_card_label(
    label: str,
    slot_index: int,
    heroes: list[dict],
) -> str:
    """Apply static aliases and player-context card disambiguation."""
    label = normalize_card_label(label)
    three_star_count = sum(
        1 for hero in heroes if int(hero.get("stars", 0)) >= 3
    )
    if label in QUALITY_PARTNER_SUPPORT_GROUP:
        if three_star_count >= 3:
            return "重质也重量pro"
        return "拍档支援"
    if slot_index == 1 and label in FAST_XXB_GROUP:
        return "吸吸宝pro"
    return label


def apply_card_context_rules(
    cards: list[dict],
    heroes: list[dict],
) -> list[dict]:
    """Return card dict copies with resolved labels."""
    resolved: list[dict] = []
    for card in cards:
        slot_index = int(card["slot_index"])
        raw_label = card.get("card_name") or card.get("label", "unknown")
        label = resolve_card_label(raw_label, slot_index, heroes)
        updated = dict(card)
        if "card_name" in card:
            updated["card_name"] = label
        if "label" in card:
            updated["label"] = label
        resolved.append(updated)
    return resolved
