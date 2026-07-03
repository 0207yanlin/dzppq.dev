# -*- coding: utf-8 -*-
"""Generate ground truth JSON from current hero detection pipeline."""
import json
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SCREENSHOT_DIR = ROOT / "screenshots"
TEMPLATE_DIR = ROOT / "assets" / "templates" / "heroes"
OUTPUT_PATH = ROOT / "data" / "ground_truth.json"

NUM_PLAYERS = 8
NUM_HEROES = 9
X_OFFSET = [0, 74, 149, 223, 297, 371, 445, 519, 594]
Y_OFFSET = [0, 93, 185, 280, 372, 465, 559, 652]

DETECTION_PARAMS = {
    "threshold": 0.75,
    "min_gap": 0.08,
    "padding": 8,
    "margin_ratio": 0.1,
    "empty_slot_std_threshold": 10,
    "empty_slot_edge_threshold": 0.05,
}


def load_templates():
    templates = {}
    for path in TEMPLATE_DIR.glob("*.jpg"):
        if path.name.startswith("player"):
            continue
        buf = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        templates[path.name] = cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return templates


def crop_center(gray, margin_ratio=0.1):
    h, w = gray.shape[:2]
    mh, mw = int(h * margin_ratio), int(w * margin_ratio)
    return gray[mh : h - mh, mw : w - mw]


def is_empty_slot(roi, std_threshold=10, edge_threshold=0.05):
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 50, 150)
    return gray.std() < std_threshold or edges.mean() / 255 < edge_threshold


def match_roi_to_template(roi, templates, threshold=0.75, min_gap=0.08, padding=8, margin_ratio=0.1):
    roi_gray = crop_center(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), margin_ratio)
    search = cv2.copyMakeBorder(
        roi_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE
    )
    scores = []
    for name, timg in templates.items():
        temp_gray = crop_center(cv2.cvtColor(timg, cv2.COLOR_BGR2GRAY), margin_ratio)
        th, tw = temp_gray.shape
        if search.shape[0] < th or search.shape[1] < tw:
            continue
        res = cv2.matchTemplate(search, temp_gray, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, _ = cv2.minMaxLoc(res)
        scores.append((max_val, name))
    scores.sort(reverse=True)
    if not scores:
        return None, 0.0
    best_score, best_name = scores[0]
    second_score = scores[1][0] if len(scores) > 1 else 0.0
    if best_score >= threshold and (best_score - second_score) >= min_gap:
        return best_name, float(best_score)
    return None, float(best_score)


def detect_lineups(img, templates):
    lineups = []
    for j in range(NUM_PLAYERS):
        heroes = []
        for i in range(NUM_HEROES):
            x1, y1 = 582 + X_OFFSET[i], 306 + Y_OFFSET[j]
            x2, y2 = 652 + X_OFFSET[i], 345 + Y_OFFSET[j]
            roi = img[y1:y2, x1:x2]
            if roi.shape[0] != (y2 - y1) or roi.shape[1] != (x2 - x1):
                break
            if is_empty_slot(
                roi,
                DETECTION_PARAMS["empty_slot_std_threshold"],
                DETECTION_PARAMS["empty_slot_edge_threshold"],
            ):
                break
            tmp_name, score = match_roi_to_template(
                roi,
                templates,
                DETECTION_PARAMS["threshold"],
                DETECTION_PARAMS["min_gap"],
                DETECTION_PARAMS["padding"],
                DETECTION_PARAMS["margin_ratio"],
            )
            if tmp_name is not None:
                heroes.append(tmp_name.replace(".jpg", ""))
            else:
                break
        lineups.append(
            {
                "player": j + 1,
                "heroes": heroes,
            }
        )
    return lineups


def build_ground_truth():
    templates = load_templates()
    screenshots = {}

    for img_path in sorted(SCREENSHOT_DIR.glob("*.png")):
        img = cv2.imread(str(img_path))
        if img is None:
            raise RuntimeError(f"failed to read screenshot: {img_path}")

        lineups = detect_lineups(img, templates)
        screenshots[img_path.name] = {
            "path": str(img_path.relative_to(ROOT)).replace("\\", "/"),
            "players": lineups,
        }

    return {
        "version": 1,
        "description": "Hero lineup ground truth generated from current detection pipeline",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "detection_params": DETECTION_PARAMS,
        "template_count": len(templates),
        "screenshots": screenshots,
    }


def main():
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = build_ground_truth()
    OUTPUT_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {OUTPUT_PATH}")
    for name, entry in data["screenshots"].items():
        counts = [len(p["heroes"]) for p in entry["players"]]
        print(f"  {name}: hero_counts={counts}")


if __name__ == "__main__":
    main()
