# -*- coding: utf-8 -*-
"""Offline benchmark for hand-card OCR strategies on sample screenshot(s)."""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.adb_capture import OcrHelper  # noqa: E402
from src.layout import HAND_CARD_BOXES, crop_hand_cards, crop_roi  # noqa: E402

DEFAULT_SAMPLE = Path(
    r"C:\Users\wrlin\Documents\MuMu共享文件夹\Screenshots\MuMu-20260705-193347-395.png"
)
EXPECTED_TITLES = ("克隆技术", "攻防联合", "亲密武装")
TITLE_FRACTIONS = (0.35, 0.45, 0.55, 0.65)


def title_boxes(fraction: float) -> tuple[tuple[int, int, int, int], ...]:
    return tuple(
        (x1, y1, x2, y1 + int((y2 - y1) * fraction))
        for x1, y1, x2, y2 in HAND_CARD_BOXES
    )


def crop_title_rois(img: np.ndarray, fraction: float = 0.35) -> list[np.ndarray]:
    return [crop_roi(img, box) for box in title_boxes(fraction)]


def crop_title_strip(img: np.ndarray, fraction: float = 0.35, *, gap: int = 24) -> np.ndarray:
    rois = crop_title_rois(img, fraction)
    if gap <= 0:
        return cv2.hconcat(rois)
    parts: list[np.ndarray] = []
    spacer = np.full((rois[0].shape[0], gap, 3), 255, dtype=np.uint8)
    for idx, roi in enumerate(rois):
        parts.append(roi)
        if idx < len(rois) - 1:
            parts.append(spacer)
    return cv2.hconcat(parts)


def _ocr_serial(ocr: OcrHelper, rois: list[np.ndarray]) -> tuple[list[str], float]:
    t0 = time.perf_counter()
    texts = [ocr.ocr_text(roi) for roi in rois]
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return texts, elapsed_ms


def _ocr_parallel(ocr: OcrHelper, rois: list[np.ndarray]) -> tuple[list[str], float]:
    t0 = time.perf_counter()

    def _one(roi: np.ndarray) -> str:
        return ocr.ocr_text(roi)

    with ThreadPoolExecutor(max_workers=len(rois)) as executor:
        texts = list(executor.map(_one, rois))
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return texts, elapsed_ms


def _ocr_strip(ocr: OcrHelper, strip: np.ndarray) -> tuple[list[str], float]:
    t0 = time.perf_counter()
    text = ocr.ocr_text(strip)
    elapsed_ms = (time.perf_counter() - t0) * 1000
    return [text], elapsed_ms


def _contains_expected(texts: list[str]) -> dict[str, bool]:
    joined = " ".join(texts)
    return {title: title in joined for title in EXPECTED_TITLES}


def _read_bgr(path: Path) -> np.ndarray:
    data = path.read_bytes()
    arr = np.frombuffer(data, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise FileNotFoundError(f"Cannot decode image: {path}")
    return img


def _run_strategy(
    name: str,
    fn,
    *,
    runs: int,
    strategies: dict[str, dict],
) -> None:
    times: list[float] = []
    last_texts: list[str] = []
    for _ in range(runs):
        texts, ms = fn()
        times.append(ms)
        last_texts = texts
    strategies[name] = {
        "avg_ms": round(sum(times) / len(times), 1),
        "texts": last_texts,
        "expected_hits": _contains_expected(last_texts),
    }


def benchmark_image(image_path: Path, *, runs: int = 3, use_cls: bool = False) -> dict:
    img = _read_bgr(image_path)
    ocr = OcrHelper(use_cls=use_cls)
    _ = ocr.ocr_text(crop_hand_cards(img)[0])

    strategies: dict[str, dict] = {}

    _run_strategy(
        "full_roi_serial",
        lambda: _ocr_serial(ocr, crop_hand_cards(img)),
        runs=runs,
        strategies=strategies,
    )
    _run_strategy(
        "full_roi_parallel",
        lambda: _ocr_parallel(ocr, crop_hand_cards(img)),
        runs=runs,
        strategies=strategies,
    )

    for fraction in TITLE_FRACTIONS:
        pct = int(fraction * 100)
        _run_strategy(
            f"title_{pct}_serial",
            lambda frac=fraction: _ocr_serial(ocr, crop_title_rois(img, frac)),
            runs=runs,
            strategies=strategies,
        )

    for fraction in TITLE_FRACTIONS:
        pct = int(fraction * 100)
        _run_strategy(
            f"title_{pct}_strip_once",
            lambda frac=fraction: _ocr_strip(ocr, crop_title_strip(img, frac)),
            runs=runs,
            strategies=strategies,
        )

    _run_strategy(
        "title_45_strip_gap24",
        lambda: _ocr_strip(ocr, crop_title_strip(img, 0.45, gap=24)),
        runs=runs,
        strategies=strategies,
    )

    baseline_ms = strategies["full_roi_serial"]["avg_ms"]
    for item in strategies.values():
        item["speedup_vs_baseline"] = round(baseline_ms / item["avg_ms"], 2)
        hits = item["expected_hits"]
        item["accuracy_ok"] = all(hits.values()) if hits else False

    return {
        "image": str(image_path),
        "use_cls": use_cls,
        "expected_titles": list(EXPECTED_TITLES),
        "strategies": strategies,
    }


def benchmark(
    image_paths: list[Path],
    *,
    runs: int = 3,
    use_cls: bool = False,
) -> dict:
    reports = [benchmark_image(path, runs=runs, use_cls=use_cls) for path in image_paths]
    if len(reports) == 1:
        return reports[0]

    summary: dict[str, dict] = {}
    for report in reports:
        for name, item in report["strategies"].items():
            bucket = summary.setdefault(
                name,
                {"avg_ms": [], "accuracy_ok": [], "speedup_vs_baseline": []},
            )
            bucket["avg_ms"].append(item["avg_ms"])
            bucket["accuracy_ok"].append(item["accuracy_ok"])
            bucket["speedup_vs_baseline"].append(item["speedup_vs_baseline"])

    aggregated: dict[str, dict] = {}
    for name, bucket in summary.items():
        aggregated[name] = {
            "avg_ms": round(sum(bucket["avg_ms"]) / len(bucket["avg_ms"]), 1),
            "accuracy_ok": all(bucket["accuracy_ok"]),
            "speedup_vs_baseline": round(
                sum(bucket["speedup_vs_baseline"]) / len(bucket["speedup_vs_baseline"]),
                2,
            ),
        }

    return {
        "images": [str(p) for p in image_paths],
        "use_cls": use_cls,
        "expected_titles": list(EXPECTED_TITLES),
        "per_image": reports,
        "aggregated_strategies": aggregated,
    }


def resolve_image_paths(image: Path | None, image_dir: Path | None) -> list[Path]:
    if image_dir is not None:
        paths = sorted(image_dir.glob("*.png"))
        if not paths:
            raise FileNotFoundError(f"No PNG files under {image_dir}")
        return paths
    path = image or DEFAULT_SAMPLE
    if not path.is_file():
        raise FileNotFoundError(f"Cannot find image: {path}")
    return [path]


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark hand-card OCR strategies")
    parser.add_argument("--image", type=Path, default=None)
    parser.add_argument("--image-dir", type=Path, default=None)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--use-cls", action="store_true", help="Keep angle classifier enabled")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "data" / "hand_card_ocr_benchmark.json",
    )
    args = parser.parse_args()

    image_paths = resolve_image_paths(args.image, args.image_dir)
    report = benchmark(image_paths, runs=args.runs, use_cls=args.use_cls)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nWrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
