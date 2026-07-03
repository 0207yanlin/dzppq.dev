# -*- coding: utf-8 -*-
"""Predict, label, and evaluate equipment-count ground truth."""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.detect_equipment import (  # noqa: E402
    DEFAULT_CACHE_PATH,
    DEFAULT_CLASSIFIER_PATH,
    DEFAULT_GROUND_TRUTH_PATH,
    LABELS,
    EquipmentIndex,
    embed_rois,
    filter_index,
    format_label_rows,
    labels_from_predictions,
    load_classifier,
    load_ground_truth,
    load_model,
    load_or_build_embedding_cache,
    predict_image,
    predict_image_with_classifier,
    save_classifier,
    save_ground_truth,
    set_screenshot_labels,
    train_classifier,
    validate_label_rows,
)
from src.layout import (  # noqa: E402
    NUM_HEROES,
    SCREENSHOT_DIR,
)


def resolve_screenshot(path_or_name: str | Path, screenshot_dir: Path) -> Path:
    path = Path(path_or_name)
    if path.exists():
        return path
    candidate = screenshot_dir / path.name
    if candidate.exists():
        return candidate
    raise FileNotFoundError(f"screenshot not found: {path_or_name}")


def collect_screenshots(screenshot_dir: Path) -> list[Path]:
    return sorted(path for path in screenshot_dir.glob("*.png") if "_debug" not in path.parts)


def parse_label_row(text: str) -> list[str]:
    row = text.strip().split()
    invalid = [label for label in row if label not in LABELS]
    if invalid:
        raise ValueError(f"invalid labels {invalid}; valid labels are {', '.join(LABELS)}")
    if len(row) != NUM_HEROES:
        raise ValueError(f"expected {NUM_HEROES} labels, got {len(row)}: {text}")
    return row


def parse_rows(row_args: list[str] | None) -> list[list[str]] | None:
    if row_args is None:
        return None
    rows = [parse_label_row(text) for text in row_args]
    validate_label_rows(rows)
    return rows


def prompt_rows(default_rows: list[list[str]]) -> list[list[str]]:
    print("Enter corrected rows. Press Enter to keep the predicted row.")
    rows = []
    for idx, default in enumerate(default_rows, start=1):
        prompt = f"player {idx} [{ ' '.join(default) }]: "
        text = input(prompt).strip()
        rows.append(default if not text else parse_label_row(text))
    validate_label_rows(rows)
    return rows


def print_rows(title: str, rows: list[list[str]]) -> None:
    print(title)
    print(format_label_rows(rows))


def predict_screenshot(
    img_path: Path,
    model,
    method: str,
    pad_mode: str,
    index: EquipmentIndex | None = None,
    classifier=None,
    exclude_self: bool = True,
) -> list[list[str]]:
    img = cv2.imread(str(img_path))
    if img is None:
        raise RuntimeError(f"failed to read screenshot: {img_path}")
    if method == "classifier":
        if classifier is None:
            raise RuntimeError("classifier is not loaded; run the train command first")
        predictions = predict_image_with_classifier(
            img,
            classifier=classifier,
            model=model,
            pad_mode=pad_mode,
        )
    else:
        filtered = filter_index(index, {img_path.name} if exclude_self else set()) if index else None
        predictions = predict_image(img, index=filtered, model=model, pad_mode=pad_mode)
    return labels_from_predictions(predictions)


def load_prediction_resources(args: argparse.Namespace, gt_data: dict):
    model = load_model(args.device)
    index = None
    classifier = None
    if args.method == "1nn":
        index = load_or_build_embedding_cache(
            gt_data,
            gt_path=args.gt,
            screenshot_dir=args.screenshot_dir.resolve(),
            model=model,
            cache_path=args.cache,
            pad_mode=args.pad_mode,
            rebuild=args.rebuild_cache,
        )
    else:
        classifier = load_classifier(args.classifier)
    return model, index, classifier


def command_predict(args: argparse.Namespace) -> None:
    screenshot_dir = args.screenshot_dir.resolve()
    gt_data = load_ground_truth(args.gt)
    model, index, classifier = load_prediction_resources(args, gt_data)
    paths = (
        [resolve_screenshot(args.screenshot, screenshot_dir)]
        if args.screenshot
        else collect_screenshots(screenshot_dir)
    )
    if not paths:
        print(f"No PNG files in {screenshot_dir}")
        return

    for img_path in paths:
        rows = predict_screenshot(
            img_path,
            model,
            args.method,
            args.pad_mode,
            index=index,
            classifier=classifier,
            exclude_self=not args.include_self,
        )
        print(f"\n{img_path.name}")
        print(format_label_rows(rows))


def command_label(args: argparse.Namespace) -> None:
    screenshot_dir = args.screenshot_dir.resolve()
    img_path = resolve_screenshot(args.screenshot, screenshot_dir)
    gt_data = load_ground_truth(args.gt)
    model, index, classifier = load_prediction_resources(args, gt_data)
    predicted_rows = predict_screenshot(
        img_path,
        model,
        args.method,
        args.pad_mode,
        index=index,
        classifier=classifier,
        exclude_self=True,
    )
    print_rows(f"Prediction for {img_path.name}:", predicted_rows)

    row_args = parse_rows(args.row)
    corrected_rows = row_args if row_args is not None else prompt_rows(predicted_rows)
    set_screenshot_labels(gt_data, img_path.name, corrected_rows)
    save_ground_truth(gt_data, args.gt)

    print_rows(f"Saved labels for {img_path.name}:", corrected_rows)
    print(f"Ground truth: {args.gt}")


def refs_as_tuples(index: EquipmentIndex) -> list[tuple[str, int, int]]:
    return [(ref["screenshot"], ref["player"], ref["slot"]) for ref in index.refs]


def nearest_neighbor_predict_matrix(
    index: EquipmentIndex,
    leave_one_screenshot: bool,
) -> list[str]:
    labels = np.array(index.labels)
    screenshots = np.array([ref["screenshot"] for ref in index.refs])
    scores = index.embeddings @ index.embeddings.T
    if leave_one_screenshot:
        for screenshot in sorted(set(screenshots)):
            mask = screenshots == screenshot
            scores[np.ix_(mask, mask)] = -np.inf
    else:
        np.fill_diagonal(scores, -np.inf)
    best = np.argmax(scores, axis=1)
    best_scores = scores[np.arange(scores.shape[0]), best]
    return [
        "?" if not np.isfinite(score) else str(labels[idx])
        for idx, score in zip(best, best_scores)
    ]


def print_confusion(labels: list[str], preds: list[str]) -> None:
    all_labels = list(LABELS) + (["?"] if "?" in preds else [])
    header = "true\\pred " + " ".join(f"{label:>4}" for label in all_labels)
    print(header)
    for true_label in all_labels:
        row = []
        for pred_label in all_labels:
            row.append(sum(t == true_label and p == pred_label for t, p in zip(labels, preds)))
        print(f"{true_label:>9} " + " ".join(f"{value:>4}" for value in row))


def command_eval(args: argparse.Namespace) -> None:
    screenshot_dir = args.screenshot_dir.resolve()
    gt_data = load_ground_truth(args.gt)
    model = load_model(args.device)
    index = load_or_build_embedding_cache(
        gt_data,
        gt_path=args.gt,
        screenshot_dir=screenshot_dir,
        model=model,
        cache_path=args.cache,
        pad_mode=args.pad_mode,
        rebuild=args.rebuild_cache,
    )
    if len(index.labels) < 2:
        raise SystemExit("Need at least two labeled ROIs to evaluate.")

    screenshot_count = len({ref["screenshot"] for ref in index.refs})
    leave_one_screenshot = args.mode == "screenshot" or (
        args.mode == "auto" and screenshot_count >= 2
    )
    labels = index.labels
    refs = refs_as_tuples(index)
    if args.method == "1nn":
        preds = nearest_neighbor_predict_matrix(index, leave_one_screenshot)
    else:
        preds = classifier_leave_one_screenshot_predictions(index)

    correct = sum(t == p for t, p in zip(labels, preds))
    print(f"Samples: {len(labels)}")
    print(f"Screenshots: {screenshot_count}")
    split_name = "leave-one-screenshot-out" if leave_one_screenshot else "leave-one-sample-out"
    print(f"Method: {args.method}")
    print(f"Mode: {split_name}")
    print(f"Counts: {dict(Counter(labels))}")
    print(f"Accuracy: {correct}/{len(labels)} = {correct / len(labels):.4f}")
    print_confusion(labels, preds)

    errors = [
        (ref[0], ref[1], ref[2], true_label, pred_label)
        for ref, true_label, pred_label in zip(refs, labels, preds)
        if true_label != pred_label
    ]
    if errors:
        print("Errors:")
        for screenshot_name, player, slot, true_label, pred_label in errors:
            print(
                f"  {screenshot_name} player={player} slot={slot}: "
                f"{true_label} -> {pred_label}"
            )
    else:
        print("Errors: none")


def classifier_leave_one_screenshot_predictions(index: EquipmentIndex) -> list[str]:
    screenshots = np.array([ref["screenshot"] for ref in index.refs])
    preds = np.empty(len(index.labels), dtype=object)
    for screenshot in sorted(set(screenshots)):
        train_mask = screenshots != screenshot
        test_mask = screenshots == screenshot
        train_index = EquipmentIndex(
            embeddings=index.embeddings[train_mask],
            labels=[label for label, keep in zip(index.labels, train_mask) if keep],
            refs=[ref for ref, keep in zip(index.refs, train_mask) if keep],
        )
        classifier = train_classifier(train_index)
        preds[test_mask] = classifier.predict(index.embeddings[test_mask]).astype(str)
    return preds.astype(str).tolist()


def command_train(args: argparse.Namespace) -> None:
    screenshot_dir = args.screenshot_dir.resolve()
    gt_data = load_ground_truth(args.gt)
    model = load_model(args.device)
    index = load_or_build_embedding_cache(
        gt_data,
        gt_path=args.gt,
        screenshot_dir=screenshot_dir,
        model=model,
        cache_path=args.cache,
        pad_mode=args.pad_mode,
        rebuild=args.rebuild_cache,
    )
    classifier = train_classifier(index)
    save_classifier(classifier, args.classifier)
    print(f"Samples: {len(index.labels)}")
    print(f"Counts: {dict(Counter(index.labels))}")
    print(f"Cache: {args.cache}")
    print(f"Classifier: {args.classifier}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--gt",
        type=Path,
        default=DEFAULT_GROUND_TRUTH_PATH,
        help="Equipment ground truth JSON path",
    )
    parser.add_argument(
        "--screenshot-dir",
        type=Path,
        default=SCREENSHOT_DIR,
        help="Directory containing PNG screenshots",
    )
    parser.add_argument(
        "--pad-mode",
        choices=("black", "mean"),
        default="black",
        help="Padding color used before ResNet preprocessing",
    )
    parser.add_argument("--device", default=None, help="Torch device, e.g. cpu or cuda")
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "data" / "equipment_embeddings.npz",
        help="Embedding cache path",
    )
    parser.add_argument(
        "--classifier",
        type=Path,
        default=ROOT / "data" / "equipment_classifier.joblib",
        help="Saved classifier path",
    )
    parser.add_argument(
        "--rebuild-cache",
        action="store_true",
        help="Force rebuilding the embedding cache",
    )
    parser.add_argument(
        "--method",
        choices=("1nn", "classifier"),
        default="1nn",
        help="Prediction/evaluation method",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    predict = subparsers.add_parser("predict", help="Predict labels for screenshots")
    predict.add_argument("screenshot", nargs="?", help="Screenshot path or filename")
    predict.add_argument(
        "--include-self",
        action="store_true",
        help="Allow labels from the same screenshot in nearest-neighbor index",
    )
    predict.set_defaults(func=command_predict)

    label = subparsers.add_parser("label", help="Correct and save labels for one screenshot")
    label.add_argument("screenshot", help="Screenshot path or filename")
    label.add_argument(
        "--row",
        action="append",
        help="Corrected row, repeat exactly 8 times. Example: --row '3 3 3 2 1 0 0 0 -'",
    )
    label.set_defaults(func=command_label)

    train = subparsers.add_parser("train", help="Train and save the classifier head")
    train.set_defaults(func=command_train)

    evaluate = subparsers.add_parser("eval", help="Evaluate nearest-neighbor accuracy")
    evaluate.add_argument(
        "--mode",
        choices=("auto", "sample", "screenshot"),
        default="auto",
        help="Evaluation split mode",
    )
    evaluate.set_defaults(func=command_eval)
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
