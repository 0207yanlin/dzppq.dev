# -*- coding: utf-8 -*-
"""Hard-coded disambiguation rules for visually identical card icons."""

from __future__ import annotations

CARD_TYPE_PREFIXES = frozenset({"彩", "白", "蓝", "黄"})

QUALITY_PARTNER_SUPPORT_GROUP = frozenset(
    {"重质拍档支援", "重质也重量pro", "最佳拍档", "最强支援", "拍档支援"},
)
QUALITY_WEIGHT_PRO_LABEL = "蓝·重质也重量pro"
QUALITY_PARTNER_SUPPORT_LABEL = "蓝·拍档支援"
FAST_XXB_GROUP = frozenset({"快速成型", "吸吸宝pro"})

CARD_LABEL_ALIASES: dict[str, str] = {
    "重质也重量pro": QUALITY_WEIGHT_PRO_LABEL,
    "拍档支援": QUALITY_PARTNER_SUPPORT_LABEL,
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

# Map merged template bodies to canonical bodies before context rules run.
TEMPLATE_BODY_ALIASES: dict[str, str] = {
    "吸吸宝pro快速成型": "快速成型",
}


def split_card_prefix(label: str) -> tuple[str | None, str]:
    """Split `黄·下雨了` into (`黄`, `下雨了`)."""
    if "·" not in label:
        return None, label
    prefix, body = label.split("·", 1)
    if prefix in CARD_TYPE_PREFIXES:
        return prefix, body
    return None, label


def join_card_prefix(prefix: str | None, body: str) -> str:
    if prefix:
        return f"{prefix}·{body}"
    return body


def normalize_card_label(label: str) -> str:
    """Map legacy variant card names to canonical labels."""
    label = CARD_LABEL_ALIASES.get(label, label)
    prefix, body = split_card_prefix(label)
    body = TEMPLATE_BODY_ALIASES.get(body, body)
    return join_card_prefix(prefix, body)


def resolve_card_label(
    label: str,
    slot_index: int,
    heroes: list[dict],
) -> str:
    """Apply static aliases and player-context card disambiguation."""
    label = normalize_card_label(label)
    prefix, body = split_card_prefix(label)
    three_star_count = sum(
        1 for hero in heroes if int(hero.get("stars", 0)) >= 3
    )
    if body in QUALITY_PARTNER_SUPPORT_GROUP:
        if three_star_count >= 3:
            return QUALITY_WEIGHT_PRO_LABEL
        return QUALITY_PARTNER_SUPPORT_LABEL
    if slot_index == 1 and body in FAST_XXB_GROUP:
        return "吸吸宝pro"
    if body in FAST_XXB_GROUP:
        return "快速成型"
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
