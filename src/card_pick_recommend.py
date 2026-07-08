# -*- coding: utf-8 -*-
"""Card pick OCR, matching, and recommendation helpers."""

from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import numpy as np

from src.card_rules import CARD_TYPE_PREFIXES, join_card_prefix, normalize_card_label, split_card_prefix
from src.layout import HAND_CARD_BOXES, crop_hand_cards

CARD_PREFIXES: tuple[str, ...] = ("白", "蓝", "黄", "彩")
META_JSON_RELATIVE = Path("data") / "latest_meta_analysis.json"
DEFAULT_META_JSON = Path(__file__).resolve().parent.parent / META_JSON_RELATIVE
MIN_CARD_SAMPLES = 12


@dataclass(frozen=True)
class CardMetrics:
    key: str
    prefix: str
    appearances: int
    adjusted_avg_rank: float | None
    solo_avg_rank: float | None
    solo_top4_rate: float | None
    team_avg_rank: float | None
    team_top2_rate: float | None
    sample_weight_pct: float
    avg_appearances_per_match: float | None = None

    @property
    def has_stats(self) -> bool:
        return self.appearances > 0 and self.adjusted_avg_rank is not None

    @property
    def low_sample(self) -> bool:
        return 0 < self.appearances < MIN_CARD_SAMPLES


@dataclass
class CardMatchResult:
    slot: int
    raw_text: str
    cleaned_text: str
    matched_key: str | None
    match_score: float
    metrics: CardMetrics | None = None


@dataclass
class RecommendationResult:
    prefix: str
    cards: list[CardMatchResult]
    recommended_slot: int | None
    generated_at: str = ""
    data_source: str = ""
    total_card_records: int = 0


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def clean_ocr_text(text: str) -> str:
    """Normalize OCR output for card-name matching."""
    text = text.strip()
    text = re.sub(r"\s+", "", text)
    normalized = normalize_card_label(text)
    prefix, body = split_card_prefix(normalized)
    if prefix in CARD_TYPE_PREFIXES:
        return body

    compact = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9+·]", "", text)
    normalized = normalize_card_label(compact)
    prefix, body = split_card_prefix(normalized)
    if prefix in CARD_TYPE_PREFIXES:
        return body
    return compact or normalized


def _similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def fuzzy_match_card(
    ocr_text: str,
    prefix: str,
    catalog: dict[str, CardMetrics],
    *,
    min_score: float = 0.55,
) -> tuple[str | None, float, str]:
    """Match OCR text to a canonical card key within the selected prefix."""
    cleaned = clean_ocr_text(ocr_text)
    if not cleaned:
        return None, 0.0, cleaned

    direct_key = join_card_prefix(prefix, cleaned)
    if direct_key in catalog:
        return direct_key, 1.0, cleaned

    normalized = normalize_card_label(direct_key)
    if normalized in catalog:
        return normalized, 0.98, cleaned

    best_key: str | None = None
    best_score = 0.0
    for key, metrics in catalog.items():
        if metrics.prefix != prefix:
            continue
        _, body = split_card_prefix(key)
        score = max(
            _similarity(cleaned, body),
            _similarity(cleaned, key),
            _similarity(cleaned.replace("pro", ""), body.replace("pro", "")),
        )
        if score > best_score:
            best_score = score
            best_key = key

    if best_key is not None and best_score >= min_score:
        return best_key, best_score, cleaned
    return None, best_score, cleaned


class CardStatsIndex:
    """Load card stats from latest meta analysis JSON."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.generated_at = str(data.get("generated_at", ""))
        self.data_source = str(data.get("data_source", ""))
        quality = data.get("overview", {}).get("quality", {})
        self.total_matches = int(quality.get("matches", 0) or 0)
        self.total_card_records = int(quality.get("cards", 0) or 0)
        self.by_prefix: dict[str, dict[str, CardMetrics]] = {p: {} for p in CARD_PREFIXES}
        self._load_rows(data)

    @classmethod
    def from_json_path(cls, path: Path | str) -> CardStatsIndex:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(payload)

    @classmethod
    def from_db_path(cls, path: Path | str) -> CardStatsIndex:
        from src.card_stats_db import build_card_stats_payload

        return cls(build_card_stats_payload(path))

    def _load_rows(self, data: dict[str, Any]) -> None:
        cards = data.get("rankings", {}).get("cards", {})
        single_by_prefix = cards.get("single_cards_by_prefix", {})
        blue_team_by_prefix = cards.get("blue_cards_team_rank_by_prefix", {})
        team_map = {
            row["key"]: row
            for row in blue_team_by_prefix.get("蓝", [])
            if isinstance(row, dict) and row.get("key")
        }

        for prefix in CARD_PREFIXES:
            for row in single_by_prefix.get(prefix, []):
                if not isinstance(row, dict):
                    continue
                key = str(row.get("key", ""))
                if not key:
                    continue
                appearances = int(row.get("appearances", 0) or 0)
                team = team_map.get(key, {})
                weight = (
                    appearances * 100.0 / self.total_card_records
                    if self.total_card_records
                    else 0.0
                )
                self.by_prefix[prefix][key] = CardMetrics(
                    key=key,
                    prefix=prefix,
                    appearances=appearances,
                    adjusted_avg_rank=_safe_float(row.get("adjusted_avg_rank")),
                    solo_avg_rank=_safe_float(row.get("avg_rank")),
                    solo_top4_rate=_safe_float(row.get("top4_rate")),
                    team_avg_rank=_safe_float(team.get("avg_rank")),
                    team_top2_rate=_safe_float(team.get("team_top2_rate")),
                    sample_weight_pct=round(weight, 3),
                    avg_appearances_per_match=_safe_float(row.get("avg_appearances_per_match")),
                )

    def prefix_catalog(self, prefix: str) -> dict[str, CardMetrics]:
        return dict(self.by_prefix.get(prefix, {}))

    def get_metrics(self, key: str | None, prefix: str) -> CardMetrics | None:
        if not key:
            return None
        return self.by_prefix.get(prefix, {}).get(key)


def recognize_hand_cards(
    img: np.ndarray,
    ocr_helper: Any,
    prefix: str,
    stats: CardStatsIndex,
    *,
    parallel_ocr: bool = False,
) -> list[CardMatchResult]:
    """OCR three hand-pick card ROIs and match against stats for the given prefix."""
    catalog = stats.prefix_catalog(prefix)
    rois = list(crop_hand_cards(img))

    def _ocr_slot(slot: int) -> tuple[int, str]:
        raw_text = ocr_helper.ocr_text(rois[slot])
        return slot, raw_text

    ocr_texts: dict[int, str] = {}
    if parallel_ocr and len(rois) > 1:
        with ThreadPoolExecutor(max_workers=len(rois)) as executor:
            for slot, raw_text in executor.map(_ocr_slot, range(len(rois))):
                ocr_texts[slot] = raw_text
    else:
        for slot in range(len(rois)):
            _, raw_text = _ocr_slot(slot)
            ocr_texts[slot] = raw_text

    results: list[CardMatchResult] = []
    for slot, box in enumerate(HAND_CARD_BOXES):
        raw_text = ocr_texts.get(slot, "")
        matched_key, score, cleaned = fuzzy_match_card(raw_text, prefix, catalog)
        metrics = stats.get_metrics(matched_key, prefix)
        results.append(
            CardMatchResult(
                slot=slot,
                raw_text=raw_text,
                cleaned_text=cleaned,
                matched_key=matched_key,
                match_score=score,
                metrics=metrics,
            )
        )
        _ = box  # box kept for future debug overlays
    return results


def _rank_key(card: CardMatchResult, prefix: str) -> tuple[float, float, float, float]:
    metrics = card.metrics
    if metrics is None or not metrics.has_stats:
        return (999.0, -1.0, -1.0, card.match_score)

    adjusted = metrics.adjusted_avg_rank if metrics.adjusted_avg_rank is not None else 999.0
    top4 = metrics.solo_top4_rate if metrics.solo_top4_rate is not None else -1.0
    team_top2 = metrics.team_top2_rate if metrics.team_top2_rate is not None else -1.0
    sample = float(metrics.appearances)

    if prefix == "蓝" and metrics.team_top2_rate is not None:
        team_rank = metrics.team_avg_rank if metrics.team_avg_rank is not None else 999.0
        return (team_rank, -team_top2, -top4, sample)

    return (adjusted, -top4, -team_top2, sample)


def build_recommendation(
    prefix: str,
    cards: list[CardMatchResult],
    stats: CardStatsIndex,
) -> RecommendationResult:
    """Rank three cards and pick the recommended slot."""
    ranked = sorted(
        cards,
        key=lambda item: (_rank_key(item, prefix), -item.match_score),
    )
    recommended = ranked[0].slot if ranked else None
    return RecommendationResult(
        prefix=prefix,
        cards=cards,
        recommended_slot=recommended,
        generated_at=stats.generated_at,
        data_source=stats.data_source,
        total_card_records=stats.total_card_records,
    )


def format_pct(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.1f}%"


def format_rank(value: float | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}"


def display_card_name(card: CardMatchResult, *, max_len: int = 14) -> str:
    """Short card name for compact views and multi-dimension sorting."""
    if card.matched_key:
        _, body = split_card_prefix(card.matched_key)
        name = body or card.matched_key
    elif card.cleaned_text:
        name = card.cleaned_text
    elif card.raw_text:
        name = card.raw_text
    else:
        name = f"卡{card.slot + 1}?"
    if len(name) > max_len:
        return name[: max_len - 3] + "..."
    return name


def _card_warning_tags(card: CardMatchResult) -> list[str]:
    tags: list[str] = []
    if card.match_score < 0.75:
        tags.append("低置信")
    if card.metrics is not None and card.metrics.low_sample:
        tags.append("样本少")
    return tags


def _comparison_ranked_cards(
    result: RecommendationResult,
) -> list[CardMatchResult]:
    return sorted(
        result.cards,
        key=lambda item: (_rank_key(item, result.prefix), -item.match_score),
    )


def _comparison_header(prefix: str) -> str:
    if prefix == "蓝":
        return f"对比速览（{prefix}卡按队伍表现优先）"
    return f"对比速览（{prefix}卡按修正名次优先）"


def format_compact_card_line(
    card: CardMatchResult,
    prefix: str,
    *,
    recommended: bool,
) -> str:
    label = card.matched_key or f"(未匹配: {card.cleaned_text or card.raw_text or '空'})"
    prefix_star = "★ " if recommended else "  "
    slot_tag = f"卡{card.slot + 1}"
    metrics = card.metrics
    if metrics is None or not metrics.has_stats:
        warn = " ".join(_card_warning_tags(card))
        warn_text = f" | {warn}" if warn else ""
        return f"{prefix_star}{slot_tag} {label}{warn_text} | 无统计数据"

    warn = " ".join(_card_warning_tags(card))
    warn_text = f" | {warn}" if warn else ""
    return (
        f"{prefix_star}{slot_tag} {label} | "
        f"队排 {format_rank(metrics.team_avg_rank)} | "
        f"前二 {format_pct(metrics.team_top2_rate)} | "
        f"单排 {format_rank(metrics.solo_avg_rank)} | "
        f"样本 {metrics.appearances} | "
        f"OCR {card.match_score:.0%}{warn_text}"
    )


@dataclass(frozen=True)
class _SortDimension:
    label: str
    op: str
    cards: tuple[CardMatchResult, ...]


def _sort_by_numeric(
    cards: list[CardMatchResult],
    value_fn,
    *,
    lower_is_better: bool,
) -> list[CardMatchResult]:
    ranked: list[tuple[float, CardMatchResult]] = []
    for card in cards:
        value = value_fn(card)
        if value is None:
            continue
        ranked.append((float(value), card))
    ranked.sort(key=lambda item: item[0], reverse=not lower_is_better)
    return [card for _, card in ranked]


def _format_sort_chain(cards: list[CardMatchResult], op: str) -> str:
    if not cards:
        return "—"
    return f" {op} ".join(display_card_name(card) for card in cards)


def build_sort_dimensions(result: RecommendationResult) -> list[_SortDimension]:
    cards = result.cards
    dimensions: list[_SortDimension] = []

    team_rank = _sort_by_numeric(
        cards,
        lambda c: c.metrics.team_avg_rank if c.metrics else None,
        lower_is_better=True,
    )
    if len(team_rank) >= 2:
        dimensions.append(_SortDimension("队伍平均名次", "<", tuple(team_rank)))

    team_top2 = _sort_by_numeric(
        cards,
        lambda c: c.metrics.team_top2_rate if c.metrics else None,
        lower_is_better=False,
    )
    if len(team_top2) >= 2:
        dimensions.append(_SortDimension("队伍前二率", ">", tuple(team_top2)))

    solo_rank = _sort_by_numeric(
        cards,
        lambda c: c.metrics.solo_avg_rank if c.metrics else None,
        lower_is_better=True,
    )
    if len(solo_rank) >= 2:
        dimensions.append(_SortDimension("单人平均名次", "<", tuple(solo_rank)))

    solo_top4 = _sort_by_numeric(
        cards,
        lambda c: c.metrics.solo_top4_rate if c.metrics else None,
        lower_is_better=False,
    )
    if len(solo_top4) >= 2:
        dimensions.append(_SortDimension("单人前四率", ">", tuple(solo_top4)))

    samples = _sort_by_numeric(
        cards,
        lambda c: float(c.metrics.appearances) if c.metrics else None,
        lower_is_better=False,
    )
    if len(samples) >= 2:
        dimensions.append(_SortDimension("样本数", ">", tuple(samples)))

    ocr_conf = sorted(cards, key=lambda c: c.match_score, reverse=True)
    if len(ocr_conf) >= 2 and any(c.match_score > 0 for c in ocr_conf):
        dimensions.append(_SortDimension("OCR 置信度", ">", tuple(ocr_conf)))

    return dimensions


def format_multi_sort(result: RecommendationResult) -> list[str]:
    lines = ["多维排序"]
    for dim in build_sort_dimensions(result):
        chain = _format_sort_chain(list(dim.cards), dim.op)
        lines.append(f"- {dim.label}: {chain}")
    return lines


def format_card_line(card: CardMatchResult, prefix: str, *, recommended: bool) -> str:
    label = card.matched_key or f"(未匹配: {card.cleaned_text or card.raw_text or '空'})"
    prefix_tag = f"[{prefix}] "
    match_note = f"OCR={card.raw_text!r} -> {label} ({card.match_score:.0%})"
    if card.metrics is None or not card.metrics.has_stats:
        body = f"{prefix_tag}{'★ ' if recommended else ''}卡{card.slot + 1}: {label}\n  {match_note}\n  无统计数据"
        return body

    metrics = card.metrics
    warnings: list[str] = []
    if card.match_score < 0.75:
        warnings.append("匹配置信度偏低")
    if metrics.low_sample:
        warnings.append("样本偏少")
    warn_text = f" [{' / '.join(warnings)}]" if warnings else ""

    return (
        f"{prefix_tag}{'★ 推荐 ' if recommended else ''}卡{card.slot + 1}: {label}{warn_text}\n"
        f"  {match_note}\n"
        f"  队伍前二率: {format_pct(metrics.team_top2_rate)} | "
        f"队伍平均名次: {format_rank(metrics.team_avg_rank)}\n"
        f"  单人前四率: {format_pct(metrics.solo_top4_rate)} | "
        f"单人平均名次: {format_rank(metrics.solo_avg_rank)}\n"
        f"  样本比例权重: {metrics.sample_weight_pct:.3f}% "
        f"(样本 {metrics.appearances}, 修正名次 {format_rank(metrics.adjusted_avg_rank)})"
    )


def format_recommendation(result: RecommendationResult) -> str:
    lines: list[str] = []
    if result.recommended_slot is not None:
        rec = result.cards[result.recommended_slot]
        rec_name = rec.matched_key or rec.cleaned_text or "未知"
        lines.append(f"建议选取: 卡{result.recommended_slot + 1} · {rec_name}")
    else:
        lines.append("建议选取: 无法确定（请先截图并选择类别）")

    lines.append("")
    lines.append(_comparison_header(result.prefix))
    for card in _comparison_ranked_cards(result):
        lines.append(
            format_compact_card_line(
                card,
                result.prefix,
                recommended=card.slot == result.recommended_slot,
            )
        )

    sort_lines = format_multi_sort(result)
    if len(sort_lines) > 1:
        lines.append("")
        lines.extend(sort_lines)

    lines.append("")
    lines.append("诊断信息")
    lines.append(f"当前类别: {result.prefix}")
    lines.append(f"数据: {result.data_source or 'latest_meta_analysis.json'}")
    lines.append(f"生成时间: {result.generated_at or '未知'}")
    lines.append(f"样本权重分母: 全库卡牌记录 {result.total_card_records}")
    lines.append("")
    for card in result.cards:
        lines.append(
            format_card_line(
                card,
                result.prefix,
                recommended=card.slot == result.recommended_slot,
            )
        )
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
