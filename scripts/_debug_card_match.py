# -*- coding: utf-8 -*-
"""Debug card ROI vs named templates."""
import os
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "assets" / "templates" / "cards"
IMG_PATH = ROOT / "screenshots" / "MuMu-20260701-201519-791.png"

X_OFFSET = [0, 77, 154]
Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]


def isolate_icon(roi, bg_color=(255, 255, 255), k=2):
    h, w = roi.shape[:2]
    pixels = roi.reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, _ = cv2.kmeans(pixels, k, None, criteria, 10, cv2.KMEANS_PP_CENTERS)
    labels_2d = labels.reshape(h, w)
    border = np.zeros((h, w), dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    bg_label = Counter(labels_2d[border].tolist()).most_common(1)[0][0]
    result = roi.copy()
    result[labels_2d == bg_label] = bg_color
    return result


def load_templates():
    templates = {}
    for path in sorted(TEMPLATE_DIR.glob("*.jpg")):
        if path.name.startswith("player"):
            continue
        buf = np.frombuffer(path.read_bytes(), dtype=np.uint8)
        img = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if img is not None:
            templates[path.name] = img
    return templates


def crop_center(gray, margin_ratio=0.1):
    h, w = gray.shape[:2]
    mh, mw = int(h * margin_ratio), int(w * margin_ratio)
    return gray[mh : h - mh, mw : w - mw]


def match_roi(roi, templates, threshold=0.75, min_gap=0.08, padding=8, margin_ratio=0.1, preprocess=None):
    if preprocess:
        roi = preprocess(roi)
    roi_gray = crop_center(cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY), margin_ratio)
    search = cv2.copyMakeBorder(roi_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE)
    scores = []
    for name, timg in templates.items():
        t = preprocess(timg) if preprocess else timg
        temp_gray = crop_center(cv2.cvtColor(t, cv2.COLOR_BGR2GRAY), margin_ratio)
        th, tw = temp_gray.shape
        if search.shape[0] < th or search.shape[1] < tw:
            continue
        res = cv2.matchTemplate(search, temp_gray, cv2.TM_CCOEFF_NORMED)
        scores.append((float(res.max()), name))
    scores.sort(reverse=True)
    if not scores:
        return None, 0.0, []
    best, second = scores[0][0], scores[1][0] if len(scores) > 1 else 0.0
    if best >= threshold and (best - second) >= min_gap:
        return scores[0][1], best, scores[:5]
    return None, best, scores[:5]


def main():
    img = cv2.imread(str(IMG_PATH))
    templates = load_templates()
    print(f"templates: {len(templates)}")
    for name, t in list(templates.items())[:3]:
        print(f"  {name}: {t.shape}")

    for j in range(8):
        print(f"\nPlayer {j+1}:")
        for i in range(3):
            x1, y1 = 1340 + X_OFFSET[i], 305 + Y_OFFSET[j]
            x2, y2 = 1385 + X_OFFSET[i], 350 + Y_OFFSET[j]
            roi = img[y1:y2, x1:x2]
            print(f"  slot{i+1} roi={roi.shape}", end="")
            for label, prep in [("raw", None), ("isolate", isolate_icon)]:
                name, score, top = match_roi(roi, templates, preprocess=prep)
                print(f" | {label}: {name} ({score:.3f}) top={[(round(s,3), n[:8]) for s,n in top[:3]]}", end="")
            print()


if __name__ == "__main__":
    main()
