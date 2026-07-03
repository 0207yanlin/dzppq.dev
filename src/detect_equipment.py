# -*- coding: utf-8 -*-
"""Equipment count detection using pretrained image embeddings."""

from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import cv2
import numpy as np
import torch
from PIL import Image
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from torchvision import models

from src.layout import (
    NUM_HEROES,
    NUM_PLAYERS,
    ROOT,
    SCREENSHOT_DIR,
    crop_roi,
    equipment_roi,
    roi_valid,
)

LABELS = ("-", "0", "1", "2", "3")
UNKNOWN_LABEL = "?"
DEFAULT_GROUND_TRUTH_PATH = ROOT / "data" / "equipment_ground_truth.json"
DEFAULT_CACHE_PATH = ROOT / "data" / "equipment_embeddings.npz"
DEFAULT_CLASSIFIER_PATH = ROOT / "data" / "equipment_classifier.joblib"
MODEL_NAME = "torchvision_resnet18_imagenet1k_v1"
ROI_VERSION = "equipment_roi_v1_581_345_72x24"


@dataclass
class EquipmentModel:
    model: torch.nn.Module
    preprocess: object
    device: torch.device


@dataclass
class EquipmentIndex:
    embeddings: np.ndarray
    labels: list[str]
    refs: list[dict]


def _dump_model(obj: object, path: Path) -> None:
    try:
        import joblib

        joblib.dump(obj, path)
    except ImportError:
        with path.open("wb") as f:
            pickle.dump(obj, f)


def _load_model_file(path: Path) -> object:
    try:
        import joblib

        return joblib.load(path)
    except ImportError:
        with path.open("rb") as f:
            return pickle.load(f)


def load_model(device: str | torch.device | None = None) -> EquipmentModel:
    """Load pretrained ResNet18 as a 512-dimensional embedding extractor."""
    torch_device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
    weights = models.ResNet18_Weights.IMAGENET1K_V1
    model = models.resnet18(weights=weights)
    model.fc = torch.nn.Identity()
    model.eval()
    model.to(torch_device)
    return EquipmentModel(model=model, preprocess=weights.transforms(), device=torch_device)


def pad_to_square_rgb(rgb: np.ndarray, pad_mode: str = "black") -> Image.Image:
    """Pad an RGB ROI to a square without changing its aspect ratio."""
    h, w = rgb.shape[:2]
    size = max(h, w)
    if pad_mode == "mean":
        color = np.rint(rgb.reshape(-1, 3).mean(axis=0)).astype(np.uint8)
    elif pad_mode == "black":
        color = np.array([0, 0, 0], dtype=np.uint8)
    else:
        raise ValueError(f"unsupported pad mode: {pad_mode}")

    canvas = np.empty((size, size, 3), dtype=np.uint8)
    canvas[:] = color
    y1 = (size - h) // 2
    x1 = (size - w) // 2
    canvas[y1 : y1 + h, x1 : x1 + w] = rgb
    return Image.fromarray(canvas)


def prepare_roi(roi_bgr: np.ndarray, pad_mode: str = "black") -> Image.Image:
    rgb = cv2.cvtColor(roi_bgr, cv2.COLOR_BGR2RGB)
    return pad_to_square_rgb(rgb, pad_mode)


@torch.inference_mode()
def embed_rois(
    rois: Sequence[np.ndarray],
    model: EquipmentModel,
    pad_mode: str = "black",
    batch_size: int = 64,
) -> np.ndarray:
    """Extract L2-normalized embeddings for BGR ROIs."""
    if not rois:
        return np.empty((0, 512), dtype=np.float32)

    outputs = []
    for start in range(0, len(rois), batch_size):
        batch_rois = rois[start : start + batch_size]
        tensors = [
            model.preprocess(prepare_roi(roi, pad_mode))  # type: ignore[operator]
            for roi in batch_rois
        ]
        batch = torch.stack(tensors).to(model.device)
        embeddings = model.model(batch).detach().cpu().numpy().astype(np.float32)
        outputs.append(embeddings)

    arr = np.vstack(outputs)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return arr / norms


def is_background_roi(
    roi: np.ndarray,
    std_threshold: float = 20.0,
    edge_threshold: float = 0.08,
) -> bool:
    """Cheap fallback for empty background slots when no embedding index exists."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    return gray.std() < std_threshold and (edges > 0).mean() < edge_threshold


def load_ground_truth(path: Path | None = None) -> dict:
    gt_path = path or DEFAULT_GROUND_TRUTH_PATH
    if not gt_path.exists():
        return {"version": 1, "labels": {}}
    return json.loads(gt_path.read_text(encoding="utf-8"))


def save_ground_truth(data: dict, path: Path | None = None) -> None:
    gt_path = path or DEFAULT_GROUND_TRUTH_PATH
    gt_path.parent.mkdir(parents=True, exist_ok=True)
    gt_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def validate_label_rows(rows: Sequence[Sequence[str]]) -> None:
    if len(rows) != NUM_PLAYERS:
        raise ValueError(f"expected {NUM_PLAYERS} rows, got {len(rows)}")
    for row_index, row in enumerate(rows):
        if len(row) != NUM_HEROES:
            raise ValueError(
                f"row {row_index + 1}: expected {NUM_HEROES} labels, got {len(row)}"
            )
        invalid = [label for label in row if label not in LABELS]
        if invalid:
            raise ValueError(f"row {row_index + 1}: invalid labels {invalid}")


def set_screenshot_labels(data: dict, screenshot_name: str, rows: Sequence[Sequence[str]]) -> None:
    validate_label_rows(rows)
    data.setdefault("version", 1)
    data.setdefault("labels", {})[screenshot_name] = [list(row) for row in rows]


def iter_ground_truth_labels(data: dict) -> Iterable[tuple[str, int, int, str]]:
    for screenshot_name, rows in data.get("labels", {}).items():
        for player, row in enumerate(rows):
            for slot, label in enumerate(row):
                if label in LABELS:
                    yield screenshot_name, player, slot, label


def collect_labeled_rois(
    data: dict,
    screenshot_dir: Path | None = None,
) -> tuple[list[np.ndarray], list[str], list[dict]]:
    """Collect labeled equipment ROIs from ground truth."""
    directory = screenshot_dir or SCREENSHOT_DIR
    rois: list[np.ndarray] = []
    labels: list[str] = []
    refs: list[dict] = []
    image_cache: dict[str, np.ndarray] = {}

    for screenshot_name, player, slot, label in iter_ground_truth_labels(data):
        if screenshot_name not in image_cache:
            img = cv2.imread(str(directory / screenshot_name))
            if img is None:
                continue
            image_cache[screenshot_name] = img
        img = image_cache[screenshot_name]
        box = equipment_roi(player, slot)
        roi = crop_roi(img, box)
        if not roi_valid(roi, box):
            continue
        rois.append(roi)
        labels.append(label)
        refs.append(
            {
                "screenshot": screenshot_name,
                "player": player + 1,
                "slot": slot + 1,
                "label": label,
            }
        )
    return rois, labels, refs


def cache_metadata(
    data: dict,
    gt_path: Path | None = None,
    screenshot_dir: Path | None = None,
    pad_mode: str = "black",
) -> dict:
    """Build metadata used to decide whether an embedding cache is stale."""
    directory = screenshot_dir or SCREENSHOT_DIR
    gt = gt_path or DEFAULT_GROUND_TRUTH_PATH
    screenshot_names = sorted(data.get("labels", {}))
    screenshot_mtimes = {}
    for name in screenshot_names:
        path = directory / name
        screenshot_mtimes[name] = path.stat().st_mtime if path.exists() else None
    return {
        "version": 1,
        "model_name": MODEL_NAME,
        "roi_version": ROI_VERSION,
        "pad_mode": pad_mode,
        "gt_path": str(gt.resolve()),
        "gt_mtime": gt.stat().st_mtime if gt.exists() else None,
        "screenshot_dir": str(directory.resolve()),
        "screenshot_mtimes": screenshot_mtimes,
        "label_count": sum(1 for _ in iter_ground_truth_labels(data)),
    }


def save_embedding_cache(
    path: Path,
    index: EquipmentIndex,
    metadata: dict,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path,
        embeddings=index.embeddings.astype(np.float32),
        labels=np.array(index.labels),
        refs_json=json.dumps(index.refs, ensure_ascii=False),
        metadata_json=json.dumps(metadata, ensure_ascii=False, sort_keys=True),
    )


def load_embedding_cache(path: Path) -> tuple[EquipmentIndex, dict]:
    with np.load(path, allow_pickle=False) as data:
        embeddings = data["embeddings"].astype(np.float32)
        labels = data["labels"].astype(str).tolist()
        refs = json.loads(str(data["refs_json"]))
        metadata = json.loads(str(data["metadata_json"]))
    return EquipmentIndex(embeddings=embeddings, labels=labels, refs=refs), metadata


def cache_is_valid(path: Path, expected_metadata: dict) -> bool:
    if not path.exists():
        return False
    try:
        _, metadata = load_embedding_cache(path)
    except Exception:
        return False
    return metadata == expected_metadata


def load_or_build_embedding_cache(
    data: dict,
    gt_path: Path | None = None,
    screenshot_dir: Path | None = None,
    model: EquipmentModel | None = None,
    cache_path: Path | None = None,
    pad_mode: str = "black",
    rebuild: bool = False,
) -> EquipmentIndex:
    """Load a valid embedding cache, or rebuild and save it."""
    path = cache_path or DEFAULT_CACHE_PATH
    metadata = cache_metadata(data, gt_path, screenshot_dir, pad_mode)
    if not rebuild and cache_is_valid(path, metadata):
        index, _ = load_embedding_cache(path)
        return index

    extractor = model or load_model()
    rois, labels, refs = collect_labeled_rois(data, screenshot_dir)
    embeddings = embed_rois(rois, extractor, pad_mode=pad_mode)
    index = EquipmentIndex(embeddings=embeddings, labels=labels, refs=refs)
    save_embedding_cache(path, index, metadata)
    return index


def filter_index(
    index: EquipmentIndex,
    exclude_screenshots: set[str] | None = None,
) -> EquipmentIndex:
    exclude = exclude_screenshots or set()
    if not exclude:
        return index
    keep = [i for i, ref in enumerate(index.refs) if ref["screenshot"] not in exclude]
    embeddings = index.embeddings[keep] if keep else np.empty((0, index.embeddings.shape[1]), dtype=np.float32)
    return EquipmentIndex(
        embeddings=embeddings,
        labels=[index.labels[i] for i in keep],
        refs=[index.refs[i] for i in keep],
    )


def build_index(
    data: dict,
    screenshot_dir: Path | None = None,
    model: EquipmentModel | None = None,
    exclude_screenshots: set[str] | None = None,
    pad_mode: str = "black",
) -> EquipmentIndex:
    """Build a nearest-neighbor index from labeled equipment ground truth."""
    directory = screenshot_dir or SCREENSHOT_DIR
    extractor = model or load_model()
    exclude = exclude_screenshots or set()

    rois, labels, refs = collect_labeled_rois(data, directory)
    if exclude:
        keep = [i for i, ref in enumerate(refs) if ref["screenshot"] not in exclude]
        rois = [rois[i] for i in keep]
        labels = [labels[i] for i in keep]
        refs = [refs[i] for i in keep]
    embeddings = embed_rois(rois, extractor, pad_mode=pad_mode)
    return EquipmentIndex(embeddings=embeddings, labels=labels, refs=refs)


def classify_embedding(embedding: np.ndarray, index: EquipmentIndex) -> tuple[str, float, dict | None]:
    if index.embeddings.size == 0:
        return UNKNOWN_LABEL, 0.0, None
    scores = index.embeddings @ embedding
    best_idx = int(np.argmax(scores))
    return index.labels[best_idx], float(scores[best_idx]), index.refs[best_idx]


def predict_image(
    img: np.ndarray,
    index: EquipmentIndex | None = None,
    model: EquipmentModel | None = None,
    pad_mode: str = "black",
) -> list[list[dict]]:
    """Predict labels for every player/hero equipment slot in one screenshot."""
    rois: list[np.ndarray] = []
    positions: list[tuple[int, int]] = []
    results: list[list[dict]] = [
        [
            {"slot_index": slot, "label": UNKNOWN_LABEL, "score": 0.0, "nearest": None}
            for slot in range(NUM_HEROES)
        ]
        for _ in range(NUM_PLAYERS)
    ]

    for player in range(NUM_PLAYERS):
        for slot in range(NUM_HEROES):
            box = equipment_roi(player, slot)
            roi = crop_roi(img, box)
            if not roi_valid(roi, box):
                continue
            if index is None or index.embeddings.size == 0:
                label = "-" if is_background_roi(roi) else UNKNOWN_LABEL
                results[player][slot] = {
                    "slot_index": slot,
                    "label": label,
                    "score": 1.0 if label == "-" else 0.0,
                    "nearest": None,
                }
                continue
            rois.append(roi)
            positions.append((player, slot))

    if rois and index is not None and index.embeddings.size:
        extractor = model or load_model()
        embeddings = embed_rois(rois, extractor, pad_mode=pad_mode)
        for (player, slot), embedding in zip(positions, embeddings):
            label, score, ref = classify_embedding(embedding, index)
            results[player][slot] = {
                "slot_index": slot,
                "label": label,
                "score": score,
                "nearest": ref,
            }
    return results


def labels_from_predictions(predictions: list[list[dict]]) -> list[list[str]]:
    return [[slot["label"] for slot in row] for row in predictions]


def format_label_rows(rows: Sequence[Sequence[str]]) -> str:
    return "\n".join(" ".join(row) for row in rows)


def train_classifier(index: EquipmentIndex):
    """Train the default lightweight classifier on cached embeddings."""
    if index.embeddings.size == 0:
        raise ValueError("cannot train classifier without embeddings")
    return make_pipeline(
        StandardScaler(),
        LogisticRegression(
            C=0.1,
            max_iter=3000,
            class_weight="balanced",
            solver="liblinear",
        ),
    ).fit(index.embeddings, np.array(index.labels))


def save_classifier(classifier, path: Path | None = None) -> None:
    classifier_path = path or DEFAULT_CLASSIFIER_PATH
    classifier_path.parent.mkdir(parents=True, exist_ok=True)
    _dump_model(classifier, classifier_path)


def load_classifier(path: Path | None = None):
    classifier_path = path or DEFAULT_CLASSIFIER_PATH
    if not classifier_path.exists():
        raise FileNotFoundError(f"classifier not found: {classifier_path}")
    return _load_model_file(classifier_path)


def predict_embeddings_with_classifier(
    embeddings: np.ndarray,
    classifier,
) -> tuple[list[str], list[float]]:
    labels = classifier.predict(embeddings).astype(str).tolist()
    scores = [0.0] * len(labels)
    if hasattr(classifier, "predict_proba"):
        probs = classifier.predict_proba(embeddings)
        scores = probs.max(axis=1).astype(float).tolist()
    elif hasattr(classifier, "decision_function"):
        margins = classifier.decision_function(embeddings)
        if margins.ndim == 1:
            scores = np.abs(margins).astype(float).tolist()
        else:
            scores = margins.max(axis=1).astype(float).tolist()
    return labels, scores


def predict_image_with_classifier(
    img: np.ndarray,
    classifier,
    model: EquipmentModel | None = None,
    pad_mode: str = "black",
) -> list[list[dict]]:
    rois: list[np.ndarray] = []
    positions: list[tuple[int, int]] = []
    results: list[list[dict]] = [
        [
            {"slot_index": slot, "label": UNKNOWN_LABEL, "score": 0.0, "nearest": None}
            for slot in range(NUM_HEROES)
        ]
        for _ in range(NUM_PLAYERS)
    ]
    for player in range(NUM_PLAYERS):
        for slot in range(NUM_HEROES):
            box = equipment_roi(player, slot)
            roi = crop_roi(img, box)
            if roi_valid(roi, box):
                rois.append(roi)
                positions.append((player, slot))

    extractor = model or load_model()
    embeddings = embed_rois(rois, extractor, pad_mode=pad_mode)
    labels, scores = predict_embeddings_with_classifier(embeddings, classifier)
    for (player, slot), label, score in zip(positions, labels, scores):
        results[player][slot] = {
            "slot_index": slot,
            "label": label,
            "score": score,
            "nearest": None,
        }
    return results
