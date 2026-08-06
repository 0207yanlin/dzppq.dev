# -*- coding: utf-8 -*-
"""Card catalog built from template assets and same-template candidates."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path

from src.layout import CARD_TEMPLATE_DIR

CARD_COLORS: tuple[str, ...] = ("白", "蓝", "黄", "彩")

# These are game card names, not reporting aliases.  Keep visually identical
# cards distinct here so details can be maintained for each real card.
DEFAULT_SAME_TEMPLATE_GROUPS: dict[str, tuple[str, ...]] = {
    "蓝·半步满级+满级玩家": (
        "蓝·半步满级",
        "蓝·满级玩家",
    ),
    "蓝·重质拍档支援": (
        "蓝·重质也重量pro",
        "蓝·最佳拍档",
        "蓝·最强支援",
    ),
    "蓝·一起刷刷刷+天降揪揪pro": (
        "蓝·我们全都要",
        "蓝·一起刷刷刷",
        "蓝·天降揪揪pro",
    ),
    "黄·吸吸宝pro快速成型": (
        "黄·快速成型",
        "黄·吸吸宝pro",
    ),
    "黄·巨神兵": (
        "黄·巨神兵",
        "黄·迅迅迅捷双剑",
    ),
    "黄·迅迅迅捷双剑": (
        "黄·巨神兵",
        "黄·迅迅迅捷双剑",
    ),
    "黄·摇盒高手": (
        "黄·死亡摇滚",
        "黄·摇盒高手",
    ),
    "黄·终极反击": (
        "黄·终极反击",
        "黄·学术反击",
    ),
    "黄·蛋商银行": (
        "黄·大亨",
        "黄·蛋商银行",
    ),
}


def card_color(card_name: str) -> str | None:
    """Return the color prefix from a real card name."""
    if len(card_name) >= 3 and card_name[0] in CARD_COLORS and card_name[1] == "·":
        return card_name[0]
    return None


def list_card_asset_stems(template_dir: Path = CARD_TEMPLATE_DIR) -> tuple[str, ...]:
    """List raw JPG stems from the card template directory."""
    return tuple(
        path.stem
        for path in sorted(template_dir.glob("*.jpg"), key=lambda item: item.name)
        if not path.name.startswith("player")
    )


def default_same_template_groups(
    template_dir: Path = CARD_TEMPLATE_DIR,
) -> dict[str, tuple[str, ...]]:
    """Return built-in groups whose raw template stems actually exist."""
    stems = set(list_card_asset_stems(template_dir))
    return {
        stem: candidates
        for stem, candidates in DEFAULT_SAME_TEMPLATE_GROUPS.items()
        if stem in stems
    }


def build_asset_candidate_map(
    asset_stems: Iterable[str],
    same_template_groups: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Map each raw asset stem to one or more real card names."""
    groups = same_template_groups or {}
    result: dict[str, tuple[str, ...]] = {}
    for stem in asset_stems:
        candidates = groups.get(stem)
        result[stem] = tuple(candidates) if candidates is not None else (stem,)
    return result


def build_card_catalog(
    template_dir: Path = CARD_TEMPLATE_DIR,
    same_template_groups: Mapping[str, Sequence[str]] | None = None,
) -> dict[str, tuple[str, ...]]:
    """Build color catalogs from direct assets plus grouped candidates."""
    stems = list_card_asset_stems(template_dir)
    asset_candidates = build_asset_candidate_map(stems, same_template_groups)
    by_color: dict[str, set[str]] = {color: set() for color in CARD_COLORS}
    for candidates in asset_candidates.values():
        for name in candidates:
            color = card_color(name)
            if color is not None:
                by_color[color].add(name)
    return {
        color: tuple(sorted(by_color[color]))
        for color in CARD_COLORS
    }
