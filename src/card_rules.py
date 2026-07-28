# -*- coding: utf-8 -*-
"""Hard-coded disambiguation rules for visually identical card icons."""

from __future__ import annotations

import random
import re
from collections import Counter
from typing import Any, Sequence

CARD_TYPE_PREFIXES = frozenset({"彩", "白", "蓝", "黄"})

QUALITY_PARTNER_SUPPORT_GROUP = frozenset(
    {"重质拍档支援", "重质也重量pro", "最佳拍档", "最强支援", "拍档支援"},
)
QUALITY_WEIGHT_PRO_LABEL = "蓝·重质也重量pro"
QUALITY_PARTNER_SUPPORT_LABEL = "蓝·拍档支援"
FAST_XXB_GROUP = frozenset({"快速成型", "吸吸宝pro"})
FAST_XXB_PRO_LABEL = "黄·吸吸宝pro"
FAST_XXB_LABEL = "黄·快速成型"
MANA_FOCUS_LABEL = "白·法力专注"
KZDH_LABEL = "蓝·开攒大亨"
SSS_LABEL = "蓝·一起刷刷刷+天降揪揪pro"
SSS_NORMAL_LABEL = "蓝·我们全都要+一起刷刷刷"
SSS_PRO_LABEL = "蓝·天降揪揪pro"
SSS_GROUP = frozenset(
    {
        "我们全都要",
        "一起刷刷刷",
        "我们全都要+一起刷刷刷",
        "天降揪揪pro",
        "一起刷刷刷+天降揪揪pro",
    }
)
GROWTH_BOSS_LABEL = "蓝·我是老大+快速成长"
PRO_ADVENTURE_LABEL = "蓝·专业打手+冒险"
BOYL_LABEL = "蓝·波纹利己"
FDYQ_LABEL = "蓝·福袋有钱"
YELLOW_GONGMING_LABEL = "黄·装备共鸣"
YELLOW_DLS_LABEL = "黄·大力巫术守护"
CAI_GONGMING_PRO_LABEL = "彩·装备共鸣pro"
CAI_GIFT_PACK_LABEL = "彩·法师战士射手礼包"

YELLOW_JSB_LABEL = "黄·巨神兵"
YELLOW_XJ_LABEL = "黄·迅迅迅捷双剑"
YELLOW_JSB_XJ_MERGED_LABEL = "黄·巨神兵+迅迅迅捷双剑"
YELLOW_JSB_XJ_GROUP = frozenset({"巨神兵", "迅迅迅捷双剑", "巨神兵+迅迅迅捷双剑"})
JSB_EQUIPMENT = "巨神兵之斧"
XJ_EQUIPMENT = "迅捷双剑"
JSB_XJ_RATIO_SEED = 0x4A53425F584A  # "JSB_XJ"

YELLOW_DEATH_ROCK_LABEL = "黄·死亡摇滚"
YELLOW_SHAKE_BOX_LABEL = "黄·摇盒高手"
YELLOW_DEATH_ROCK_SHAKE_BOX_GROUP = frozenset({"死亡摇滚", "摇盒高手"})
DEATH_ROCK_START_BATCH = "0723"
CONCRETE_CARD_START_BATCH = "0727"
DEATH_ROCK_HERO_NAME = "吉他手卡萝"
_SCREENSHOT_BATCH_RE = re.compile(r"screenshots\.(\d{4})(?:[/\\]|$)", re.IGNORECASE)

CARD_LABEL_ALIASES: dict[str, str] = {
    "重质也重量pro": QUALITY_WEIGHT_PRO_LABEL,
    "拍档支援": QUALITY_PARTNER_SUPPORT_LABEL,
    "最佳拍档": "拍档支援",
    "最强支援": "拍档支援",
    "我们全都要": SSS_NORMAL_LABEL,
    "一起刷刷刷": SSS_NORMAL_LABEL,
    "天降揪揪pro": SSS_PRO_LABEL,
    "天降啾啾pro": SSS_PRO_LABEL,
    "一起刷刷刷+天降啾啾pro": SSS_LABEL,
    "我是老大": GROWTH_BOSS_LABEL,
    "快速成长": GROWTH_BOSS_LABEL,
    "专业打手": PRO_ADVENTURE_LABEL,
    "冒险": PRO_ADVENTURE_LABEL,
    "开攒": "开攒大亨",
    "大亨": "开攒大亨",
    "福袋": "福袋有钱",
    "福袋·蓝": "福袋有钱",
    "有钱同享": "福袋有钱",
    "利己主义": "波纹利己",
    "装备共鸣法pro": "装备共鸣pro",
    "装备共鸣血pro": "装备共鸣pro",
    "装备共鸣攻pro": "装备共鸣pro",
    "装备共鸣法": "装备共鸣",
    "装备共鸣血": "装备共鸣",
    "装备共鸣攻": "装备共鸣",
    "大力": "大力巫术守护",
    "巫术": "大力巫术守护",
    "守护": "大力巫术守护",
    "法师礼包": "法师战士射手礼包",
    "射手礼包": "法师战士射手礼包",
    "战士礼包": "法师战士射手礼包",
    "法力专注": MANA_FOCUS_LABEL,
    "蓝·最佳拍档": QUALITY_PARTNER_SUPPORT_LABEL,
    "蓝·最强支援": QUALITY_PARTNER_SUPPORT_LABEL,
    "蓝·开攒": KZDH_LABEL,
    "蓝·大亨": KZDH_LABEL,
    "蓝·我们全都要": SSS_NORMAL_LABEL,
    "蓝·一起刷刷刷": SSS_NORMAL_LABEL,
    "蓝·天降揪揪pro": SSS_PRO_LABEL,
    "蓝·天降啾啾pro": SSS_PRO_LABEL,
    "蓝·一起刷刷刷+天降啾啾pro": SSS_LABEL,
    "蓝·我是老大": GROWTH_BOSS_LABEL,
    "蓝·快速成长": GROWTH_BOSS_LABEL,
    "蓝·专业打手": PRO_ADVENTURE_LABEL,
    "蓝·冒险": PRO_ADVENTURE_LABEL,
    "蓝·福袋": FDYQ_LABEL,
    "蓝·有钱同享": FDYQ_LABEL,
    "蓝·利己主义": BOYL_LABEL,
    "蓝·最后的波纹": BOYL_LABEL,
    "黄·装备共鸣法": YELLOW_GONGMING_LABEL,
    "黄·装备共鸣攻": YELLOW_GONGMING_LABEL,
    "黄·装备共鸣血": YELLOW_GONGMING_LABEL,
    "黄·大力": YELLOW_DLS_LABEL,
    "黄·巫术": YELLOW_DLS_LABEL,
    "黄·守护": YELLOW_DLS_LABEL,
    "彩·装备共鸣法pro": CAI_GONGMING_PRO_LABEL,
    "彩·装备共鸣攻pro": CAI_GONGMING_PRO_LABEL,
    "彩·装备共鸣血pro": CAI_GONGMING_PRO_LABEL,
    "彩·法师礼包": CAI_GIFT_PACK_LABEL,
    "彩·射手礼包": CAI_GIFT_PACK_LABEL,
    "彩·战士礼包": CAI_GIFT_PACK_LABEL,
    "迅迅迅捷双剑": YELLOW_XJ_LABEL,
    "巨神兵": YELLOW_JSB_LABEL,
}

# Map merged template bodies to canonical bodies before context rules run.
# Note: 巨神兵 / 迅迅迅捷双剑 stay as distinct labels here so DB lookup and
# fuzzy match keep working; detect_cards.normalize_template_label merges them
# for identical-icon template scoring only.
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


def normalize_equipment_base(equipment: str) -> str:
    """Strip optional 核选 prefix from equipment names."""
    return str(equipment).removeprefix("核选")


def count_jsb_xj_equipment(heroes: Sequence[dict] | None) -> tuple[int, int]:
    """Count 巨神兵之斧 / 迅捷双剑 equipment instances on the final board."""
    jsb = 0
    xj = 0
    for hero in heroes or []:
        for equipment in hero.get("equipments", []) or []:
            base = normalize_equipment_base(equipment)
            if base == JSB_EQUIPMENT:
                jsb += 1
            elif base == XJ_EQUIPMENT:
                xj += 1
    return jsb, xj


def resolve_jsb_xj_from_counts(jsb_count: int, xj_count: int) -> str | None:
    """Resolve by majority when counts differ; None means a tie (incl. both zero)."""
    if jsb_count > xj_count:
        return YELLOW_JSB_LABEL
    if xj_count > jsb_count:
        return YELLOW_XJ_LABEL
    return None


def is_jsb_xj_ambiguous_label(label: str) -> bool:
    _, body = split_card_prefix(normalize_card_label(label))
    return body in YELLOW_JSB_XJ_GROUP


def card_rule_batch(
    match_path: str | None = None,
    match_batch: str | None = None,
) -> str | None:
    """Return an MMDD batch key from explicit batch or screenshot path."""
    if match_batch:
        batch = str(match_batch).strip()
        if len(batch) == 4 and batch.isdigit():
            return batch
    match = _SCREENSHOT_BATCH_RE.search(str(match_path or "").replace("\\", "/"))
    return match.group(1) if match else None


def uses_concrete_card_labels(
    match_path: str | None = None,
    match_batch: str | None = None,
) -> bool:
    """Return whether this screenshot batch uses detail-confirmed concrete names."""
    batch = card_rule_batch(match_path, match_batch)
    return batch is not None and batch >= CONCRETE_CARD_START_BATCH


def resolve_card_label(
    label: str,
    slot_index: int,
    heroes: list[dict] | None = None,
    *,
    match_path: str | None = None,
    match_batch: str | None = None,
) -> str:
    """Apply static aliases and player-context card disambiguation.

    For 巨神兵 / 迅迅迅捷双剑 ties, returns the merged pending label.
    Database consumers should call ``resolve_jsb_xj_card_labels`` for ratio-based
    seeded assignment of those ties.
    """
    if uses_concrete_card_labels(match_path, match_batch):
        # From 0727 onward every shared icon is resolved by detail OCR.  Keep
        # the concrete workbook label instead of applying legacy aliases or
        # inferring a different card from the final board.
        return label

    label = normalize_card_label(label)
    prefix, body = split_card_prefix(label)
    heroes = heroes or []
    three_star_count = sum(
        1 for hero in heroes if int(hero.get("stars", 0)) >= 3
    )
    if body in QUALITY_PARTNER_SUPPORT_GROUP:
        if three_star_count >= 3:
            return QUALITY_WEIGHT_PRO_LABEL
        return QUALITY_PARTNER_SUPPORT_LABEL
    if slot_index == 1 and body in FAST_XXB_GROUP:
        return FAST_XXB_PRO_LABEL
    if body in FAST_XXB_GROUP:
        return FAST_XXB_LABEL
    if body in SSS_GROUP:
        jiujiu_count = sum(
            1
            for hero in heroes
            for equipment in hero.get("equipments", []) or []
            if str(equipment).removeprefix("核选").endswith("啾啾")
        )
        if jiujiu_count >= 2:
            return SSS_PRO_LABEL
        return SSS_NORMAL_LABEL
    if label == YELLOW_SHAKE_BOX_LABEL:
        batch = card_rule_batch(match_path, match_batch)
        if batch is None:
            return label
        if batch < DEATH_ROCK_START_BATCH:
            return YELLOW_SHAKE_BOX_LABEL
        has_carol = any(
            str(hero.get("hero_name", "")) == DEATH_ROCK_HERO_NAME
            for hero in heroes
        )
        return YELLOW_DEATH_ROCK_LABEL if has_carol else YELLOW_SHAKE_BOX_LABEL
    if body in YELLOW_JSB_XJ_GROUP:
        jsb_count, xj_count = count_jsb_xj_equipment(heroes)
        resolved = resolve_jsb_xj_from_counts(jsb_count, xj_count)
        if resolved is not None:
            return resolved
        return YELLOW_JSB_XJ_MERGED_LABEL
    return label


def resolve_card_labels(
    items: Sequence[dict[str, Any]],
    *,
    seed: int = JSB_XJ_RATIO_SEED,
) -> list[str]:
    """Resolve all contextual labels across a database snapshot.

    Each item must provide:
    - ``label``: raw or normalized card label
    - ``slot_index``: card slot
    - ``heroes``: player hero context with ``equipments``
    - optional ``match_path``/``path`` or ``match_batch``/``batch``
    - optional ``is_ground_truth`` or ``source`` provenance

    巨神兵 / 迅迅迅捷双剑 clear samples determine the ratio used for their
    ties. Authoritative items (``is_ground_truth=True`` or
    ``source="detail_ocr"``) keep their labels byte-for-byte and bypass every
    normalization/context rule, while authoritative 巨神兵 / 迅迅迅捷双剑
    labels may still contribute clear counts. Ties retain fixed-seed,
    stable-input-order assignment.
    """
    if not items:
        return []

    preliminary: list[str] = []
    clear_counts: Counter[str] = Counter()
    tie_indexes: list[int] = []

    for index, item in enumerate(items):
        label = str(item["label"])
        is_authoritative = bool(item.get("is_ground_truth")) or (
            item.get("source") == "detail_ocr"
        )
        if is_authoritative:
            preliminary.append(label)
            if label in {YELLOW_JSB_LABEL, YELLOW_XJ_LABEL}:
                clear_counts[label] += 1
            continue
        slot_index = int(item["slot_index"])
        heroes = item.get("heroes") or []
        resolved = resolve_card_label(
            label,
            slot_index,
            heroes,
            match_path=item.get("match_path") or item.get("path"),
            match_batch=item.get("match_batch") or item.get("batch"),
        )
        preliminary.append(resolved)
        if resolved == YELLOW_JSB_XJ_MERGED_LABEL:
            tie_indexes.append(index)
        elif resolved in {YELLOW_JSB_LABEL, YELLOW_XJ_LABEL} and is_jsb_xj_ambiguous_label(
            label
        ):
            clear_counts[resolved] += 1

    if not tie_indexes:
        return preliminary

    jsb_weight = clear_counts.get(YELLOW_JSB_LABEL, 0)
    xj_weight = clear_counts.get(YELLOW_XJ_LABEL, 0)
    if jsb_weight <= 0 and xj_weight <= 0:
        jsb_weight = 1
        xj_weight = 1

    rng = random.Random(seed)
    # Stable order: already follow input order; shuffle assignment targets by ratio.
    total = jsb_weight + xj_weight
    for index in tie_indexes:
        pick = rng.randrange(total)
        preliminary[index] = (
            YELLOW_JSB_LABEL if pick < jsb_weight else YELLOW_XJ_LABEL
        )
    return preliminary


def resolve_jsb_xj_card_labels(
    items: Sequence[dict[str, Any]],
    *,
    seed: int = JSB_XJ_RATIO_SEED,
) -> list[str]:
    """Compatibility wrapper for the former JSB/XJ-specific bulk entrypoint."""
    return resolve_card_labels(items, seed=seed)


def apply_card_context_rules(
    cards: list[dict],
    heroes: list[dict],
    *,
    match_path: str | None = None,
    match_batch: str | None = None,
) -> list[dict]:
    """Return card dict copies with resolved labels."""
    resolved: list[dict] = []
    for card in cards:
        slot_index = int(card["slot_index"])
        raw_label = card.get("card_name") or card.get("label", "unknown")
        label = resolve_card_label(
            raw_label,
            slot_index,
            heroes,
            match_path=match_path,
            match_batch=match_batch,
        )
        updated = dict(card)
        if "card_name" in card:
            updated["card_name"] = label
        if "label" in card:
            updated["label"] = label
        resolved.append(updated)
    return resolved
