# -*- coding: utf-8 -*-
"""Unified match ground truth schema, prediction, and persistence."""

from __future__ import annotations

import json
import time
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from src.card_rules import resolve_card_label
from src.template_capture import imread_image
from src.detect_cards import detect_cards, load_template_sigs
from src.detect_equipment import (
    DEFAULT_CLASSIFIER_PATH,
    labels_from_predictions,
    load_classifier,
    load_ground_truth,
    load_model,
    load_or_build_embedding_cache,
    predict_image,
    predict_image_with_classifier,
)
from src.detect_equipment_items import (
    build_equipment_batch_index,
    detect_equipment_items,
    load_equipment_templates,
)
from src.detect_heroes import build_hero_template_cache, detect_lineups, load_templates
from src.detect_pairs import detect_pairs
from src.detect_stars import detect_stars
from src.layout import (
    CARD_TEMPLATE_DIR,
    EQUIPMENT_TEMPLATE_DIR,
    HERO_TEMPLATE_DIR,
    NUM_CARDS,
    NUM_HEROES,
    NUM_PLAYERS,
    ROOT,
)
from src.parse import parse_hero_label, parse_screenshot_timestamp

DEFAULT_GT_PATH = ROOT / "data" / "match_ground_truth.json"
DEFAULT_EQUIPMENT_GT_PATH = ROOT / "data" / "equipment_ground_truth.json"
DEFAULT_SCREENSHOT_DIR = ROOT / "screenshots.0701"


def load_match_ground_truth(path: Path | None = None) -> dict:
    gt_path = path or DEFAULT_GT_PATH
    if not gt_path.exists():
        return {
            "version": 1,
            "description": "Full match ground truth (pairs, heroes, equipment, cards)",
            "screenshots": {},
        }
    return json.loads(gt_path.read_text(encoding="utf-8"))


def save_match_ground_truth(data: dict, path: Path | None = None) -> None:
    gt_path = path or DEFAULT_GT_PATH
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _normalize_equipment_count(value: str | int) -> int:
    if value == "-":
        return -1
    return int(value)


def _equipment_count_label(value: str | int) -> str:
    if value == "-" or value == -1:
        return "-"
    return str(int(value))


def build_hero_record(
    slot_index: int,
    hero_label: str,
    stars: int,
    equipment_count: str | int,
    equipments: list[str],
    *,
    hero_score: float | None = None,
    equipment_scores: list[float] | None = None,
) -> dict[str, Any]:
    tier, hero_name = parse_hero_label(hero_label)
    record: dict[str, Any] = {
        "slot_index": slot_index,
        "hero_name": hero_name,
        "tier": tier,
        "stars": stars,
        "equipment_count": _equipment_count_label(equipment_count),
        "equipments": equipments,
    }
    if hero_score is not None or equipment_scores is not None:
        record["scores"] = {
            "hero": hero_score,
            "equipments": equipment_scores or [],
        }
    return record


def build_player_record(
    row_index: int,
    partner_player: int | None,
    heroes: list[dict],
    cards: list[dict],
) -> dict[str, Any]:
    return {
        "rank": row_index + 1,
        "row_index": row_index,
        "partner_player": partner_player,
        "heroes": heroes,
        "cards": cards,
    }


def merge_prediction(
    img: np.ndarray,
    *,
    hero_templates: dict | None = None,
    card_sigs: dict | None = None,
    equipment_templates: list | None = None,
    equipment_counts: list[list[str]] | None = None,
    equipment_count_preds: list[list[dict]] | None = None,
    equipment_item_preds: list[list[list[dict]]] | None = None,
    pair_info: dict | None = None,
    lineups: list[dict] | None = None,
    stars_by_player: list | None = None,
    cards_by_player: list[dict] | None = None,
) -> dict[str, Any]:
    """Run all detectors and assemble one screenshot record."""
    if pair_info is None:
        pair_info = detect_pairs(img)
    if hero_templates is None:
        hero_templates = load_templates()
    if card_sigs is None:
        card_sigs = load_template_sigs()
    if equipment_templates is None:
        equipment_templates = load_equipment_templates()

    if lineups is None:
        lineups = detect_lineups(img, hero_templates)
    if stars_by_player is None:
        stars_by_player = detect_stars(img)
    if cards_by_player is None:
        cards_by_player = detect_cards(img, card_sigs)

    if equipment_counts is None:
        if equipment_count_preds is None:
            raise ValueError("equipment_counts or equipment_count_preds required")
        equipment_counts = labels_from_predictions(equipment_count_preds)

    if equipment_item_preds is None:
        equipment_item_preds = detect_equipment_items(
            img, equipment_counts, equipment_templates
        )

    partner_map = pair_info["partner_by_player"]
    players: list[dict] = []

    for row_index in range(NUM_PLAYERS):
        lineup = lineups[row_index]
        stars = stars_by_player[row_index] if row_index < len(stars_by_player) else []
        card_row = cards_by_player[row_index]
        count_row = equipment_counts[row_index]
        item_row = equipment_item_preds[row_index]
        heroes_by_slot = {
            hero["slot_index"]: hero
            for hero in lineup["heroes"]
        }

        heroes_out: list[dict] = []
        for slot, eq_count in enumerate(count_row[:NUM_HEROES]):
            if eq_count == "-":
                continue
            hero = heroes_by_slot.get(slot)
            star_count = stars[slot] if slot < len(stars) else 0
            if _normalize_equipment_count(eq_count) <= 0:
                eq_names: list[str] = []
                eq_scores: list[float] = []
            else:
                items = item_row[slot] if slot < len(item_row) else []
                eq_names = [item["label"] for item in items]
                eq_scores = [float(item.get("score", 0.0)) for item in items]
            hero_label = hero["label"] if hero is not None else "unknown"
            hero_score = float(hero.get("score", 0.0)) if hero is not None else 0.0
            heroes_out.append(
                build_hero_record(
                    slot,
                    hero_label,
                    star_count,
                    eq_count,
                    eq_names,
                    hero_score=hero_score,
                    equipment_scores=eq_scores,
                )
            )

        cards_out = [
            {
                "slot_index": card["slot_index"],
                "card_name": resolve_card_label(
                    card["label"],
                    int(card["slot_index"]),
                    heroes_out,
                ),
                **(
                    {"score": float(card.get("score", 0.0))}
                    if card.get("score") is not None
                    else {}
                ),
            }
            for card in card_row["cards"]
        ]

        rank = row_index + 1
        players.append(
            build_player_record(
                row_index,
                partner_map.get(rank),
                heroes_out,
                cards_out,
            )
        )

    return {
        "pairs": pair_info["pairs"],
        "highlight_player": pair_info.get("highlight_player"),
        "players": players,
    }


def template_dir_fingerprint(directory: Path) -> dict[str, int | float]:
    files = sorted(directory.glob("*.jpg"))
    return {
        "count": len(files),
        "max_mtime_ns": max((f.stat().st_mtime_ns for f in files), default=0),
    }


def compute_template_metadata() -> dict[str, dict[str, int | float]]:
    return {
        "heroes": template_dir_fingerprint(HERO_TEMPLATE_DIR),
        "cards": template_dir_fingerprint(CARD_TEMPLATE_DIR),
        "equipments": template_dir_fingerprint(EQUIPMENT_TEMPLATE_DIR),
    }


def prediction_cache_valid(entry: dict | None, template_metadata: dict) -> bool:
    if not entry or entry.get("verified"):
        return False
    return entry.get("template_metadata") == template_metadata


class PredictionContext:
    """Lazy-loaded models and templates for batch prediction."""

    def __init__(
        self,
        *,
        equipment_gt_path: Path | None = None,
        method: str = "classifier",
        pad_mode: str = "black",
        device: str | None = None,
        rebuild_cache: bool = False,
        search_radius: int = 2,
        verbose: bool = False,
    ):
        self.equipment_gt_path = equipment_gt_path or DEFAULT_EQUIPMENT_GT_PATH
        self.method = method
        self.pad_mode = pad_mode
        self.device = device
        self.rebuild_cache = rebuild_cache
        self.search_radius = search_radius
        self.verbose = verbose

        self.hero_templates: dict | None = None
        self.hero_template_gray: dict[str, np.ndarray] | None = None
        self.card_sigs: dict | None = None
        self.equipment_templates: list | None = None
        self.equipment_batch_index = None
        self.template_metadata: dict | None = None
        self.equipment_gt_data: dict | None = None
        self.model = None
        self.index = None
        self.classifier = None
        self._initialized = False

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message, flush=True)

    def initialize(self, screenshot_dir: Path) -> None:
        if self._initialized:
            return
        started = time.perf_counter()
        self._log("Loading templates and models...")
        self.hero_templates = load_templates()
        self.hero_template_gray = build_hero_template_cache(self.hero_templates)
        self.card_sigs = load_template_sigs()
        self.equipment_templates = load_equipment_templates()
        self.equipment_batch_index = build_equipment_batch_index(self.equipment_templates)
        self.template_metadata = compute_template_metadata()
        self.equipment_gt_data = load_ground_truth(self.equipment_gt_path)
        self.model = load_model(self.device)

        if self.method == "classifier":
            if DEFAULT_CLASSIFIER_PATH.exists():
                self.classifier = load_classifier(DEFAULT_CLASSIFIER_PATH)
            else:
                self.method = "1nn"
                print(
                    f"Classifier not found at {DEFAULT_CLASSIFIER_PATH}; falling back to 1nn.",
                )

        if self.method == "1nn":
            self.index = load_or_build_embedding_cache(
                self.equipment_gt_data,
                gt_path=self.equipment_gt_path,
                screenshot_dir=screenshot_dir,
                model=self.model,
                pad_mode=self.pad_mode,
                rebuild=self.rebuild_cache,
            )
        self._initialized = True
        self._log(f"Resources loaded in {time.perf_counter() - started:.1f}s.")

    def predict_equipment_counts(self, img: np.ndarray, screenshot_name: str) -> list[list[dict]]:
        if self.equipment_gt_data is not None:
            gt_rows = self.equipment_gt_data.get("labels", {}).get(screenshot_name)
            if gt_rows is not None:
                self._log("Using equipment-count ground truth.")
                return [
                    [
                        {
                            "slot_index": slot,
                            "label": label,
                            "score": 1.0,
                            "nearest": None,
                        }
                        for slot, label in enumerate(row)
                    ]
                    for row in gt_rows
                ]

        assert self.model is not None
        if self.method == "classifier" and self.classifier is not None:
            self._log("Predicting equipment counts...")
            return predict_image_with_classifier(
                img,
                classifier=self.classifier,
                model=self.model,
                pad_mode=self.pad_mode,
            )
        from src.detect_equipment import filter_index

        assert self.index is not None
        filtered = filter_index(self.index, {screenshot_name})
        self._log("Predicting equipment counts...")
        return predict_image(img, index=filtered, model=self.model, pad_mode=self.pad_mode)

    def predict_screenshot(
        self,
        img_path: Path,
        img: np.ndarray | None = None,
        *,
        use_legacy_equipment: bool = False,
        return_timings: bool = False,
    ) -> dict[str, Any]:
        if img is None:
            img = imread_image(img_path)
        if img is None:
            raise RuntimeError(f"failed to read screenshot: {img_path}")

        assert self.hero_templates is not None
        assert self.card_sigs is not None
        assert self.equipment_templates is not None

        timings: dict[str, float] = {}
        total_started = time.perf_counter()

        stage_started = time.perf_counter()
        equipment_count_preds = self.predict_equipment_counts(img, img_path.name)
        equipment_counts = labels_from_predictions(equipment_count_preds)
        timings["equipment_counts"] = time.perf_counter() - stage_started

        self._log("Matching equipment items...")
        stage_started = time.perf_counter()
        equipment_item_preds = detect_equipment_items(
            img,
            equipment_counts,
            self.equipment_templates,
            search_radius=self.search_radius,
            batch_index=self.equipment_batch_index,
            use_legacy=use_legacy_equipment,
        )
        timings["equipment_items"] = time.perf_counter() - stage_started

        self._log("Detecting heroes, stars, cards, and pairs...")
        stage_started = time.perf_counter()
        pair_info = detect_pairs(img)
        timings["pairs"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        lineups = detect_lineups(
            img,
            self.hero_templates,
            template_gray_cache=self.hero_template_gray,
        )
        timings["heroes"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        stars_by_player = detect_stars(img)
        timings["stars"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        cards_by_player = detect_cards(img, self.card_sigs)
        timings["cards"] = time.perf_counter() - stage_started

        stage_started = time.perf_counter()
        prediction = merge_prediction(
            img,
            hero_templates=self.hero_templates,
            card_sigs=self.card_sigs,
            equipment_templates=self.equipment_templates,
            equipment_counts=equipment_counts,
            equipment_count_preds=equipment_count_preds,
            equipment_item_preds=equipment_item_preds,
            pair_info=pair_info,
            lineups=lineups,
            stars_by_player=stars_by_player,
            cards_by_player=cards_by_player,
        )
        timings["merge"] = time.perf_counter() - stage_started
        timings["total"] = time.perf_counter() - total_started
        self._log(f"Prediction finished in {timings['total']:.1f}s.")
        if return_timings:
            prediction["_timings"] = timings
        return prediction


def build_screenshot_entry(
    img_path: Path,
    prediction: dict[str, Any],
    *,
    verified: bool = False,
    template_metadata: dict | None = None,
) -> dict[str, Any]:
    try:
        rel_path = str(img_path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        rel_path = str(img_path).replace("\\", "/")
    entry: dict[str, Any] = {
        "path": rel_path,
        "captured_at": parse_screenshot_timestamp(img_path),
        "labeled_at": datetime.now(timezone.utc).isoformat() if verified else None,
        "verified": verified,
        "pairs": prediction["pairs"],
        "highlight_player": prediction.get("highlight_player"),
        "players": prediction["players"],
    }
    if template_metadata is not None:
        entry["template_metadata"] = template_metadata
    return entry


def set_screenshot_entry(
    data: dict,
    screenshot_name: str,
    entry: dict[str, Any],
) -> None:
    data.setdefault("version", 1)
    data.setdefault("screenshots", {})[screenshot_name] = entry


def validate_player_record(player: dict) -> None:
    if "rank" not in player or "row_index" not in player:
        raise ValueError("player must have rank and row_index")
    for hero in player.get("heroes", []):
        if "slot_index" not in hero or "hero_name" not in hero:
            raise ValueError("hero missing required fields")
    cards = player.get("cards", [])
    if len(cards) != NUM_CARDS:
        raise ValueError(f"expected {NUM_CARDS} cards per player")
    for card in cards:
        if "slot_index" not in card or "card_name" not in card:
            raise ValueError("card missing required fields")


def validate_screenshot_entry(entry: dict) -> None:
    if len(entry.get("players", [])) != NUM_PLAYERS:
        raise ValueError(f"expected {NUM_PLAYERS} players")
    pairs = entry.get("pairs", [])
    if len(pairs) != NUM_PLAYERS // 2:
        raise ValueError(f"expected {NUM_PLAYERS // 2} pairs")
    for player in entry["players"]:
        validate_player_record(player)


def format_hero_line(hero: dict) -> str:
    tier = hero.get("tier")
    tier_text = f"{tier}费" if tier is not None else "?费"
    stars = hero.get("stars", 0)
    star_text = "*" * stars if stars > 0 else "0星"
    eq_count = hero.get("equipment_count", "-")
    eq_names = hero.get("equipments") or []
    eq_text = ", ".join(eq_names) if eq_names else "-"
    return (
        f"{hero['hero_name']}({tier_text}, {star_text}, 装备{eq_count}:{eq_text})"
    )


def format_screenshot_summary(name: str, entry: dict) -> str:
    lines = [f"=== {name} ==="]
    lines.append(f"pairs: {entry.get('pairs')}")
    if entry.get("highlight_player"):
        lines.append(f"highlight_player: {entry['highlight_player']}")
    for player in entry["players"]:
        rank = player["rank"]
        partner = player.get("partner_player")
        partner_text = f" <-> P{partner}" if partner else ""
        lines.append(f"\n玩家{rank} / 排名{rank}{partner_text}")
        lines.append("  英雄:")
        for idx, hero in enumerate(player.get("heroes", []), start=1):
            lines.append(f"    {idx}. {format_hero_line(hero)}")
        if not player.get("heroes"):
            lines.append("    (无)")
        lines.append("  卡牌:")
        for idx, card in enumerate(player.get("cards", []), start=1):
            lines.append(f"    {idx}. {card.get('card_name', 'unknown')}")
    return "\n".join(lines)


def strip_scores(entry: dict) -> dict:
    """Return a copy without prediction score fields for verified storage."""
    cleaned = deepcopy(entry)
    for player in cleaned.get("players", []):
        for hero in player.get("heroes", []):
            hero.pop("scores", None)
            hero.pop("template_label", None)
        for card in player.get("cards", []):
            card.pop("score", None)
    return cleaned


def parse_pairs_text(text: str) -> list[list[int]]:
    """Parse pairs like '1-4 2-7 3-5 6-8' or '1,4|2,7|3,5|6,8'."""
    text = text.strip()
    if not text:
        raise ValueError("empty pairs text")
    pairs: list[list[int]] = []
    chunks = text.replace("|", " ").split()
    for chunk in chunks:
        chunk = chunk.replace(",", "-")
        if "-" not in chunk:
            raise ValueError(f"invalid pair token: {chunk}")
        a, b = chunk.split("-", 1)
        pairs.append([int(a), int(b)])
    if len(pairs) != NUM_PLAYERS // 2:
        raise ValueError(f"expected {NUM_PLAYERS // 2} pairs")
    return pairs


def parse_cards_row(text: str) -> list[str]:
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if len(parts) != NUM_CARDS:
        raise ValueError(f"expected {NUM_CARDS} card names")
    return parts


def parse_hero_line(text: str) -> dict[str, Any]:
    """Parse hero line: '英雄名,星级,装备数,装备1|装备2'."""
    parts = [part.strip() for part in text.split(",")]
    if len(parts) < 3:
        raise ValueError(
            "hero format: hero_name,stars,equipment_count[,equip1|equip2...]"
        )
    hero_name = parts[0]
    stars = int(parts[1])
    eq_count = parts[2]
    equipments: list[str] = []
    if len(parts) >= 4 and parts[3]:
        equipments = [name.strip() for name in parts[3].split("|") if name.strip()]
    return {
        "hero_name": hero_name,
        "stars": stars,
        "equipment_count": eq_count,
        "equipments": equipments,
    }
