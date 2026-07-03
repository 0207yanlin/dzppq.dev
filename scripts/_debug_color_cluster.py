# -*- coding: utf-8 -*-
"""Why color differences don't separate 最佳拍档max cluster."""
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "assets" / "templates" / "cards"
IMG = ROOT / "screenshots" / "MuMu-20260701-210420-941.png"
OUT = ROOT / "debug_rois"
OUT.mkdir(exist_ok=True)

NAMES = ["最佳拍档", "最佳拍档max", "重质也重量pro", "最强支援"]
X_OFFSET = [0, 77, 154]
Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]


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
    return result, labels_2d, bg_label, centers


def isolate_icon_raw_fg(roi, bg_color=(255, 255, 255), k=2):
    """Old style: keep original foreground pixel colors."""
    iso, labels_2d, bg_label, _ = isolate_icon(roi, bg_color, k)
    fg = labels_2d != bg_label
    result = np.full_like(roi, bg_color)
    result[fg] = roi[fg]
    return result


def load(name):
    for p in CARDS.glob("*.jpg"):
        if p.stem == name:
            buf = np.frombuffer(p.read_bytes(), dtype=np.uint8)
            return cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return None


def crop_center(gray, margin_ratio=0.1):
    h, w = gray.shape[:2]
    mh, mw = int(h * margin_ratio), int(w * margin_ratio)
    return gray[mh : h - mh, mw : w - mw]


def match_gray(a, b, padding=8, margin_ratio=0.1):
    a_gray = crop_center(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY), margin_ratio)
    search = cv2.copyMakeBorder(a_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE)
    b_gray = crop_center(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY), margin_ratio)
    return float(cv2.matchTemplate(search, b_gray, cv2.TM_CCOEFF_NORMED).max())


def match_color(a, b, padding=8, margin_ratio=0.1):
    """Match on full BGR (3-channel), average score across channels."""
    ah, aw = a.shape[:2]
    mh, mw = int(ah * margin_ratio), int(aw * margin_ratio)
    a_c = a[mh : ah - mh, mw : aw - mw]
    b_c = b[mh : ah - mh, mw : aw - mw]
    search = cv2.copyMakeBorder(a_c, padding, padding, padding, padding, cv2.BORDER_REPLICATE)
    scores = []
    for ch in range(3):
        res = cv2.matchTemplate(search[:, :, ch], b_c[:, :, ch], cv2.TM_CCOEFF_NORMED)
        scores.append(float(res.max()))
    return sum(scores) / 3


def fg_color_stats(img, iso_result, labels_2d, bg_label):
    fg = labels_2d != bg_label
    if not fg.any():
        return None
    fg_pixels = img[fg]
    return {
        "mean_bgr": fg_pixels.mean(axis=0),
        "unique_count": len(np.unique(fg_pixels.reshape(-1, 3), axis=0)),
    }


def main():
    tmpls = {n: load(n) for n in NAMES}
    missing = [n for n, t in tmpls.items() if t is None]
    if missing:
        print("missing:", missing)

    print("=== 1. Raw template foreground colors (before isolate) ===")
    raw_isos = {}
    for name, img in tmpls.items():
        _, labels, bg, _ = isolate_icon(img)
        stats = fg_color_stats(img, None, labels, bg)
        raw_isos[name] = isolate_icon_raw_fg(img)
        print(f"{name}: mean BGR={stats['mean_bgr'].round(1)} unique_fg_colors={stats['unique_count']}")

    print("\n=== 2. After canonical isolate_icon (current pipeline) ===")
    canon = {}
    for name, img in tmpls.items():
        c, labels, bg, centers = isolate_icon(img)
        canon[name] = c
        fg_centers = [centers[i] for i in range(len(centers)) if i != bg]
        print(f"{name}: fg cluster centers BGR={[c.round(1).tolist() for c in fg_centers]}")

    # montage raw templates
    row_raw = np.hstack([tmpls[n] for n in NAMES])
    row_raw_fg = np.hstack([raw_isos[n] for n in NAMES])
    row_canon = np.hstack([canon[n] for n in NAMES])
    cv2.imencode(".png", row_raw)[1].tofile(str(OUT / "hug_cluster_raw.png"))
    cv2.imencode(".png", row_raw_fg)[1].tofile(str(OUT / "hug_cluster_raw_fg.png"))
    cv2.imencode(".png", row_canon)[1].tofile(str(OUT / "hug_cluster_canonical.png"))

    print("\n=== 3. Pairwise: raw_fg vs canonical (grayscale match) ===")
    for i, a in enumerate(NAMES):
        for b in NAMES[i + 1 :]:
            s_raw = match_gray(raw_isos[a], raw_isos[b])
            s_can = match_gray(canon[a], canon[b])
            s_col = match_color(raw_isos[a], raw_isos[b])
            print(f"  {a} vs {b}: raw_fg={s_raw:.4f}  canonical={s_can:.4f}  color_match={s_col:.4f}")

    # P4 slot3 ROI
    img = cv2.imread(str(IMG))
    x1, y1 = 1340 + X_OFFSET[2], 305 + Y_OFFSET[3]
    roi = img[y1 : y1 + 45, x1 : x1 + 45]

    roi_raw = isolate_icon_raw_fg(roi)
    roi_can = isolate_icon(roi)[0]
    cv2.imencode(".png", np.hstack([roi, roi_raw, roi_can]))[1].tofile(
        str(OUT / "p4s3_roi_raw_fg_canon.png")
    )

    print("\n=== 4. P4 slot3 (最佳拍档max) vs templates ===")
    for name in NAMES:
        print(
            f"  vs {name}: "
            f"raw_fg={match_gray(roi_raw, raw_isos[name]):.4f}  "
            f"canonical={match_gray(roi_can, canon[name]):.4f}  "
            f"color={match_color(roi_raw, raw_isos[name]):.4f}"
        )

    print("\n=== 5. P4 slot3 top-3 with color matching (raw_fg) ===")
    scores = [(match_color(roi_raw, raw_isos[n]), n) for n in raw_isos]
    scores.sort(reverse=True)
    for s, n in scores:
        mark = " <--expected" if n == "最佳拍档max" else ""
        print(f"  {s:.4f} {n}{mark}")


if __name__ == "__main__":
    main()
