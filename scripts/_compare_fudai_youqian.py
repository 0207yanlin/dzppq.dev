# -*- coding: utf-8 -*-
"""Compare 福袋 vs 有钱同享 templates."""
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "assets" / "templates" / "cards"
IMG = ROOT / "screenshots" / "MuMu-20260701-201519-791.png"
OUT = ROOT / "debug_rois"
OUT.mkdir(exist_ok=True)

X_OFFSET = [0, 77, 154]
Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]


def load_jpg(path: Path):
    buf = np.frombuffer(path.read_bytes(), dtype=np.uint8)
    return cv2.imdecode(buf, cv2.IMREAD_COLOR)


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


def match_score(a_bgr, b_bgr, padding=8, margin_ratio=0.1):
    a_gray = crop_center(cv2.cvtColor(a_bgr, cv2.COLOR_BGR2GRAY), margin_ratio)
    search = cv2.copyMakeBorder(a_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE)
    b_gray = crop_center(cv2.cvtColor(b_bgr, cv2.COLOR_BGR2GRAY), margin_ratio)
    th, tw = b_gray.shape
    if search.shape[0] < th or search.shape[1] < tw:
        return None
    return float(cv2.matchTemplate(search, b_gray, cv2.TM_CCOEFF_NORMED).max())


def crop_slot(img, j, i):
    x1, y1 = 1340 + X_OFFSET[i], 305 + Y_OFFSET[j]
    return img[y1 : y1 + 45, x1 : x1 + 45].copy()


def main():
    fudai = load_jpg(CARDS / "福袋.jpg")
    youqian = load_jpg(CARDS / "有钱同享.jpg")

    f_iso = isolate_icon(fudai)
    y_iso = isolate_icon(youqian)

    cv2.imencode(".png", np.hstack([fudai, youqian]))[1].tofile(str(OUT / "fudai_vs_youqian_raw.png"))
    cv2.imencode(".png", np.hstack([f_iso, y_iso]))[1].tofile(str(OUT / "fudai_vs_youqian_iso.png"))
    diff = cv2.absdiff(f_iso, y_iso)
    cv2.imencode(".png", diff)[1].tofile(str(OUT / "fudai_vs_youqian_diff.png"))

    print("=== Template pair ===")
    print(f"shape: {fudai.shape}")
    print(f"raw pixel diff mean: {cv2.absdiff(fudai, youqian).mean():.2f}")
    print(f"iso pixel diff mean: {diff.mean():.2f}, max: {diff.max()}")
    print(f"match 福袋 vs 有钱同享 (iso): {match_score(f_iso, y_iso):.4f}")

    g1 = crop_center(cv2.cvtColor(f_iso, cv2.COLOR_BGR2GRAY), 0.1)
    g2 = crop_center(cv2.cvtColor(y_iso, cv2.COLOR_BGR2GRAY), 0.1)
    if g1.shape == g2.shape:
        corr = np.corrcoef(g1.flatten(), g2.flatten())[0, 1]
        print(f"gray correlation: {corr:.4f}")

    img = cv2.imread(str(IMG))
    roi_p2s1 = crop_slot(img, 1, 0)  # player2 slot1 = 福袋
    roi_p4s1 = crop_slot(img, 3, 0)  # player4 slot1 = 有钱同享

    p2_iso = isolate_icon(roi_p2s1)
    p4_iso = isolate_icon(roi_p4s1)

    print("\n=== Screenshot ROIs ===")
    print(f"P2s1(福袋) vs tmpl福袋: {match_score(p2_iso, f_iso):.4f}")
    print(f"P2s1(福袋) vs tmpl有钱同享: {match_score(p2_iso, y_iso):.4f}")
    print(f"P4s1(有钱同享) vs tmpl福袋: {match_score(p4_iso, f_iso):.4f}")
    print(f"P4s1(有钱同享) vs tmpl有钱同享: {match_score(p4_iso, y_iso):.4f}")
    print(f"P2s1 vs P4s1 (iso): {match_score(p2_iso, p4_iso):.4f}")

    print("\n=== Cross-confusion risk (min_gap=0.08) ===")
    for label, roi_iso in [("P2s1 福袋", p2_iso), ("P4s1 有钱同享", p4_iso)]:
        scores = [
            (match_score(roi_iso, isolate_icon(load_jpg(p))), p.name.replace(".jpg", ""))
            for p in sorted(CARDS.glob("*.jpg"))
            if not p.name.startswith("player")
        ]
        scores = [(s, n) for s, n in scores if s is not None]
        scores.sort(reverse=True)
        best, second = scores[0][0], scores[1][0]
        print(f"{label}: best={scores[0][1]} {best:.4f}, 2nd={scores[1][1]} {second:.4f}, gap={best-second:.4f}")


if __name__ == "__main__":
    main()
