# -*- coding: utf-8 -*-
"""Predict, label, and persist full match ground truth for screenshots."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.match_ground_truth import (  # noqa: E402
    DEFAULT_GT_PATH,
    DEFAULT_SCREENSHOT_DIR,
    PredictionContext,
    build_screenshot_entry,
    format_screenshot_summary,
    load_match_ground_truth,
    parse_cards_row,
    parse_hero_line,
    parse_pairs_text,
    prediction_cache_valid,
    save_match_ground_truth,
    set_screenshot_entry,
    strip_scores,
    validate_screenshot_entry,
)
from src.parse import parse_hero_label  # noqa: E402
from src.template_capture import capture_missing_templates  # noqa: E402


def resolve_screenshot(path_or_name: str | Path, screenshot_dir: Path) -> Path:
    path = Path(path_or_name)
    if path.exists():
        return path
    candidate = screenshot_dir / path.name
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"screenshot not found: {path_or_name}")


def collect_screenshots(screenshot_dir: Path) -> list[Path]:
    return sorted(
        path for path in screenshot_dir.glob("*.png") if "_debug" not in path.parts
    )


def load_prediction_context(args: argparse.Namespace) -> PredictionContext:
    ctx = PredictionContext(
        method=args.method,
        pad_mode=args.pad_mode,
        device=args.device,
        rebuild_cache=args.rebuild_cache,
        search_radius=args.search_radius,
        verbose=not args.quiet,
    )
    ctx.initialize(args.screenshot_dir.resolve())
    return ctx


def command_predict(args: argparse.Namespace) -> None:
    screenshot_dir = args.screenshot_dir.resolve()
    ctx = load_prediction_context(args)
    paths = (
        [resolve_screenshot(args.screenshot, screenshot_dir)]
        if args.screenshot
        else collect_screenshots(screenshot_dir)
    )
    if not paths:
        print(f"No PNG files in {screenshot_dir}")
        return

    gt_data = load_match_ground_truth(args.gt) if args.write else None
    for img_path in paths:
        prediction = ctx.predict_screenshot(img_path)
        entry = build_screenshot_entry(
            img_path,
            prediction,
            verified=False,
            template_metadata=ctx.template_metadata,
        )
        print(format_screenshot_summary(img_path.name, entry))
        if args.write:
            set_screenshot_entry(gt_data, img_path.name, entry)
    if args.write and gt_data is not None:
        save_match_ground_truth(gt_data, args.gt)
        print(f"\nWrote predictions to {args.gt}")


def _prompt_pairs(default: list[list[int]]) -> list[list[int]]:
    default_text = " ".join(f"{a}-{b}" for a, b in default)
    text = input(f"pairs [{default_text}]: ").strip()
    return default if not text else parse_pairs_text(text)


def _hero_template_label(hero: dict) -> str:
    hero_name = hero.get("hero_name", "")
    tier = hero.get("tier")
    if tier is None:
        return hero_name
    return f"{tier}{hero_name}"


def _prompt_player_heroes(default: list[dict]) -> list[dict]:
    print(
        "  Heroes (Enter=keep, or one line per hero: "
        "template_name,stars,eq_count,eq1|eq2)"
    )
    heroes = []
    for idx, hero in enumerate(default, start=1):
        default_name = _hero_template_label(hero)
        default_line = (
            f"{default_name},{hero.get('stars', 0)},"
            f"{hero.get('equipment_count', '-')},"
            f"{'|'.join(hero.get('equipments') or [])}"
        )
        text = input(f"    hero {idx} [{default_line}]: ").strip()
        if not text:
            heroes.append(hero)
            continue
        parsed = parse_hero_line(text)
        updated = dict(hero)
        tier, hero_name = parse_hero_label(parsed["hero_name"])
        updated.update(parsed)
        updated["hero_name"] = hero_name
        if tier is not None:
            updated["tier"] = tier
            updated["template_label"] = parsed["hero_name"]
        heroes.append(updated)
    return heroes


def _prompt_player_cards(default: list[dict]) -> list[dict]:
    default_text = ",".join(card["card_name"] for card in default)
    text = input(f"  cards [{default_text}]: ").strip()
    if not text:
        return default
    names = parse_cards_row(text)
    return [
        {"slot_index": idx, "card_name": name}
        for idx, name in enumerate(names)
    ]


def _collect_template_updates(
    img,
    predicted_players: list[dict],
    corrected_players: list[dict],
) -> tuple[list[tuple[int, int, str, str]], list[tuple[int, int, str, str]]]:
    hero_updates: list[tuple[int, int, str, str]] = []
    card_updates: list[tuple[int, int, str, str]] = []

    for player_idx, (pred_player, corr_player) in enumerate(
        zip(predicted_players, corrected_players)
    ):
        pred_heroes = {
            hero["slot_index"]: hero for hero in pred_player.get("heroes", [])
        }
        for hero in corr_player.get("heroes", []):
            slot = hero["slot_index"]
            old = _hero_template_label(pred_heroes.get(slot, {}))
            new = hero.get("template_label") or _hero_template_label(hero)
            if new and new != old:
                hero_updates.append((player_idx, slot, old, new))

        pred_cards = {
            card["slot_index"]: card for card in pred_player.get("cards", [])
        }
        for card in corr_player.get("cards", []):
            slot = card["slot_index"]
            old = pred_cards.get(slot, {}).get("card_name", "")
            new = card.get("card_name", "")
            if new and new != old:
                card_updates.append((player_idx, slot, old, new))
    return hero_updates, card_updates


def prompt_correction(default_entry: dict) -> dict:
    print("\nReview fields. Press Enter to accept defaults.")
    pairs = _prompt_pairs(default_entry["pairs"])
    partner_map = {}
    for a, b in pairs:
        partner_map[a] = b
        partner_map[b] = a

    players = []
    for player in default_entry["players"]:
        rank = player["rank"]
        print(f"\n玩家{rank}:")
        heroes = _prompt_player_heroes(player.get("heroes", []))
        cards = _prompt_player_cards(player.get("cards", []))
        players.append(
            {
                "rank": rank,
                "row_index": player["row_index"],
                "partner_player": partner_map.get(rank),
                "heroes": heroes,
                "cards": cards,
            }
        )

    highlight = default_entry.get("highlight_player")
    highlight_text = input(
        f"highlight_player [{highlight if highlight else ''}]: "
    ).strip()
    if highlight_text:
        highlight = int(highlight_text) if highlight_text else None

    corrected = {
        "path": default_entry["path"],
        "captured_at": default_entry.get("captured_at"),
        "pairs": pairs,
        "highlight_player": highlight,
        "players": players,
        "verified": True,
    }
    validate_screenshot_entry(corrected)
    return corrected


def label_one_screenshot(
    img_path: Path,
    ctx: PredictionContext,
    gt_data: dict,
    gt_path: Path,
    *,
    capture_templates: bool = True,
    force_predict: bool = False,
) -> dict:
    img = cv2.imread(str(img_path))
    if img is None:
        raise RuntimeError(f"failed to read screenshot: {img_path}")

    existing = gt_data.get("screenshots", {}).get(img_path.name)
    if (
        not force_predict
        and prediction_cache_valid(existing, ctx.template_metadata or {})
    ):
        draft_entry = existing
        print(f"Reusing cached prediction for {img_path.name}")
    else:
        prediction = ctx.predict_screenshot(img_path, img)
        draft_entry = build_screenshot_entry(
            img_path,
            prediction,
            verified=False,
            template_metadata=ctx.template_metadata,
        )
        set_screenshot_entry(gt_data, img_path.name, draft_entry)
        save_match_ground_truth(gt_data, gt_path)
    print(format_screenshot_summary(img_path.name, draft_entry))

    corrected = prompt_correction(draft_entry)
    if capture_templates:
        hero_updates, card_updates = _collect_template_updates(
            img,
            draft_entry["players"],
            corrected["players"],
        )
        if hero_updates or card_updates:
            capture_missing_templates(img, hero_updates, card_updates, ask=True)

    from datetime import datetime, timezone

    final_entry = strip_scores(corrected)
    final_entry["labeled_at"] = datetime.now(timezone.utc).isoformat()
    final_entry["verified"] = True
    set_screenshot_entry(gt_data, img_path.name, final_entry)
    save_match_ground_truth(gt_data, gt_path)
    print(f"\nSaved verified labels for {img_path.name}")
    return final_entry


def command_label(args: argparse.Namespace) -> None:
    screenshot_dir = args.screenshot_dir.resolve()
    gt_data = load_match_ground_truth(args.gt)
    ctx = load_prediction_context(args)

    if args.all:
        paths = collect_screenshots(screenshot_dir)
        if not paths:
            print(f"No PNG files in {screenshot_dir}")
            return
        for img_path in paths:
            existing = gt_data.get("screenshots", {}).get(img_path.name)
            if existing and existing.get("verified") and not args.force:
                print(f"Skip verified: {img_path.name}")
                continue
            label_one_screenshot(
                img_path,
                ctx,
                gt_data,
                args.gt,
                capture_templates=not args.no_templates,
                force_predict=args.force,
            )
        print(f"Ground truth: {args.gt}")
        return

    img_path = resolve_screenshot(args.screenshot, screenshot_dir)
    label_one_screenshot(
        img_path,
        ctx,
        gt_data,
        args.gt,
        capture_templates=not args.no_templates,
        force_predict=args.force,
    )
    print(f"Ground truth: {args.gt}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gt",
        type=Path,
        default=DEFAULT_GT_PATH,
        help="Match ground truth JSON path",
    )
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=DEFAULT_SCREENSHOT_DIR,
        help="Directory containing PNG screenshots",
    )
    parser.add_argument(
        "--method",
        choices=("classifier", "1nn"),
        default="classifier",
        help="Equipment count prediction method",
    )
    parser.add_argument(
        "--pad-mode",
        choices=("black", "mean"),
        default="black",
        help="Padding mode for equipment embedding model",
    )
    parser.add_argument("--device", default=None, help="Torch device, e.g. cpu or cuda")
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Force rebuilding equipment embedding cache",
    )
    parser.add_argument(
        "--search-radius",
        type=int,
        default=2,
        help="Pixel radius for equipment item ROI search",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Hide prediction progress and timing output",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    predict = subparsers.add_parser("predict", help="Predict labels for screenshots")
    predict.add_argument("screenshot", nargs="?", help="Screenshot path or filename")
    predict.add_argument(
        "--write",
        action="store_true",
        help="Write predictions into ground truth JSON (unverified)",
    )
    predict.set_defaults(func=command_predict)

    label = subparsers.add_parser("label", help="Correct and save labels")
    label.add_argument("screenshot", nargs="?", help="Screenshot path or filename")
    label.add_argument(
        "--all",
        action="store_true",
        help="Label all unverified screenshots in the directory",
    )
    label.add_argument(
        "--force",
        action="store_true",
        help="Relabel screenshots even if already verified",
    )
    label.add_argument(
        "--no-templates",
        action="store_true",
        help="Do not offer to save new hero/card templates",
    )
    label.set_defaults(func=command_label)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    if args.command == "label" and not args.all and not args.screenshot:
        raise SystemExit("label requires a screenshot name, or use --all")
    args.func(args)


if __name__ == "__main__":
    main()
