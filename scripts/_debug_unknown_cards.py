# -*- coding: utf-8 -*-
"""Debug unknown card matches on MuMu-20260701-210420-941.png."""
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "screenshots" / "MuMu-20260701-210420-941.png"
CARDS = ROOT / "assets" / "templates" / "cards"

X_OFFSET = [0, 77, 154]
Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]

CASES = [
    (0, 0, "重质也重量pro"),
    (1, 0, "重质也重量pro"),
    (2, 0, "最佳拍档"),
    (3, 0, "最强支援"),
    (3, 2, "最佳拍档max"),
    (6, 2, "装备共鸣血pro"),
]


def isolate_icon(roi, bg_color=(255, 255, 255), k=2):
    h, w = roi.shape[:2]
    pixels = roi.reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(pixels, k, None, criteria, 10, cv2.KMEANS_PP_CENTERS)
    labels_2d = labels.reshape(h, w)
    border = np.zeros((h, w), dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    bg_label = Counter(labels_2d[border].tolist()).most_common(1)[0][0]
    result = np.empty_like(roi)
    result[:] = bg_color
    for c in range(k):
        if c == bg_label:
            continue
        result[labels_2d == c] = centers[c].astype(np.uint8)
    return result


def crop_center(gray, margin_ratio=0.1):
    h, w = gray.shape[:2]
    mh, mw = int(h * margin_ratio), int(w * margin_ratio)
    return gray[mh : h - mh, mw : w - mw]


def load_template(name):
    for p in CARDS.glob("*.jpg"):
        if p.stem == name:
            buf = np.frombuffer(p.read_bytes(), dtype=np.uint8)
            return p, cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return None, None


def match_all(roi, templates, threshold=0.75, min_gap=0.08, padding=8, margin_ratio=0.1):
    roi_icon = isolate_icon(roi)
    roi_gray = crop_center(cv2.cvtColor(roi_icon, cv2.COLOR_BGR2GRAY), margin_ratio)
    search = cv2.copyMakeBorder(roi_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE)
    scores = []
    for name, timg in templates.items():
        tmpl_icon = isolate_icon(timg)
        temp_gray = crop_center(cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2GRAY), margin_ratio)
        th, tw = temp_gray.shape
        if search.shape[0] < th or search.shape[1] < tw:
            continue
        res = cv2.matchTemplate(search, temp_gray, cv2.TM_CCOEFF_NORMED)
        scores.append((float(res.max()), name))
    scores.sort(reverse=True)
    if not scores:
        return scores, "unknown", 0.0, 0.0
    best, second = scores[0][0], scores[1][0] if len(scores) > 1 else 0.0
    if best >= threshold and (best - second) >= min_gap:
        label = scores[0][1].replace(".jpg", "")
    else:
        label = "unknown"
    return scores, label, best, second


def main():
    img = cv2.imread(str(IMG))
    if img is None:
        raise SystemExit(f"cannot read {IMG}")

    templates = {}
    for p in CARDS.glob("*.jpg"):
        if p.name.startswith("player"):
            continue
        buf = np.frombuffer(p.read_bytes(), dtype=np.uint8)
        t = cv2.imdecode(buf, cv2.IMREAD_COLOR)
        if t is not None:
            templates[p.name] = t
    print(f"templates: {len(templates)}")

    for j, i, expected in CASES:
        x1, y1 = 1340 + X_OFFSET[i], 305 + Y_OFFSET[j]
        roi = img[y1 : y1 + 45, x1 : x1 + 45]
        scores, label, best, second = match_all(roi, templates)
        print(f"\n=== P{j+1} slot{i+1} expected={expected} => {label} (best={best:.4f} 2nd={second:.4f} gap={best-second:.4f}) ===")
        for s, n in scores[:8]:
            mark = ""
            if n.replace(".jpg", "") == expected:
                mark = " <--expected"
            print(f"  {s:.4f} {n.replace('.jpg','')}{mark}")

        _, tmpl = load_template(expected)
        if tmpl is not None:
            direct = match_all(roi, {f"{expected}.jpg": tmpl})[2]
            print(f"  vs only {expected} template: {direct:.4f}")
        else:
            print(f"  !! template missing: {expected}")

        # compare with similar cards
        similar = {
            "最佳拍档max": ["最佳拍档max", "最佳拍档", "重质也重量pro"],
            "最佳拍档": ["最佳拍档", "最佳拍档max", "重质也重量pro"],
            "装备共鸣血pro": ["装备共鸣血pro", "装备共鸣法pro"],
        }.get(expected, [expected])
        if len(similar) > 1:
            sub = {}
            for n in similar:
                _, t = load_template(n)
                if t is not None:
                    sub[f"{n}.jpg"] = t
            print("  similar pair scores:")
            for s, n in sorted(
                [(match_all(roi, {k: v})[2], k) for k, v in sub.items()], reverse=True
            ):
                print(f"    {s:.4f} {n.replace('.jpg','')}")


if __name__ == "__main__":
    main()
