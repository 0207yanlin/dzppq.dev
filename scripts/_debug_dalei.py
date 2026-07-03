# -*- coding: utf-8 -*-
"""Compare 打雷了 template (from P1) vs P1 slot vs P6 slot2 in same screenshot."""
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "screenshots" / "MuMu-20260701-201519-791.png"
CARDS = ROOT / "assets" / "templates" / "cards"
OUT = ROOT / "debug_rois"
OUT.mkdir(exist_ok=True)

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
    return result, labels_2d, bg_label


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


def load_template(keyword):
    for p in CARDS.glob("*.jpg"):
        if keyword in p.name:
            buf = np.frombuffer(p.read_bytes(), dtype=np.uint8)
            return p.name, cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return None, None


def analyze_slot(name, roi, tmpl_iso):
    roi_iso, labels, bg = isolate_icon(roi)
    s = match_score(roi_iso, tmpl_iso)
    fg_ratio = (labels != bg).mean()
    border_colors = np.unique(
        np.vstack([roi[0, :], roi[-1, :], roi[:, 0], roi[:, -1]]), axis=0
    )
    print(f"\n=== {name} ===")
    print(f"  match vs tmpl: {s:.4f}")
    print(f"  fg_ratio after kmeans: {fg_ratio:.2%}")
    print(f"  border unique colors: {len(border_colors)} sample={border_colors[:3].tolist()}")
    print(f"  roi mean BGR: {roi.mean(axis=(0,1)).round(1).tolist()}")
    print(f"  roi_iso mean BGR: {roi_iso.mean(axis=(0,1)).round(1).tolist()}")
    return roi_iso


def main():
    img = cv2.imread(str(IMG))
    tmpl_name, tmpl = load_template("打雷")
    print("template:", tmpl_name)
    tmpl_iso, _, _ = isolate_icon(tmpl)

    roi_p1s2 = crop_slot(img, 0, 1)
    roi_p6s2 = crop_slot(img, 5, 1)

    cv2.imwrite(str(OUT / "dalei_p1s2_raw.png"), roi_p1s2)
    cv2.imwrite(str(OUT / "dalei_p6s2_raw.png"), roi_p6s2)
    cv2.imwrite(str(OUT / "dalei_tmpl.png"), tmpl)

    p1_iso = analyze_slot("P1 slot2 (template source)", roi_p1s2, tmpl_iso)
    p6_iso = analyze_slot("P6 slot2 (failed match)", roi_p6s2, tmpl_iso)

    cv2.imwrite(str(OUT / "dalei_p1s2_iso.png"), p1_iso)
    cv2.imwrite(str(OUT / "dalei_p6s2_iso.png"), p6_iso)
    cv2.imwrite(str(OUT / "dalei_tmpl_iso.png"), tmpl_iso)

    # cross compare
    print("\n=== Cross scores (isolate vs isolate) ===")
    print(f"  P1s2 vs tmpl: {match_score(p1_iso, tmpl_iso):.4f}")
    print(f"  P6s2 vs tmpl: {match_score(p6_iso, tmpl_iso):.4f}")
    print(f"  P6s2 vs P1s2: {match_score(p6_iso, p1_iso):.4f}")

    # find which P1 slot best matches tmpl (verify template origin)
    print("\n=== P1 all slots vs tmpl ===")
    for i in range(3):
        r = crop_slot(img, 0, i)
        r_iso = isolate_icon(r)[0]
        print(f"  slot{i+1}: {match_score(r_iso, tmpl_iso):.4f}")

    # pixel diff stats
    diff_p1 = cv2.absdiff(p1_iso, tmpl_iso)
    diff_p6 = cv2.absdiff(p6_iso, tmpl_iso)
    print("\n=== absdiff after isolate ===")
    print(f"  P1s2-tmpl mean={diff_p1.mean():.2f} max={diff_p1.max()}")
    print(f"  P6s2-tmpl mean={diff_p6.mean():.2f} max={diff_p6.max()}")
    cv2.imwrite(str(OUT / "dalei_diff_p1.png"), diff_p1)
    cv2.imwrite(str(OUT / "dalei_diff_p6.png"), diff_p6)

    # side-by-side montage for visual
    montage = np.hstack([roi_p1s2, roi_p6s2, tmpl])
    cv2.imwrite(str(OUT / "dalei_montage_raw.png"), montage)
    montage_iso = np.hstack([p1_iso, p6_iso, tmpl_iso])
    cv2.imwrite(str(OUT / "dalei_montage_iso.png"), montage_iso)

    # try matching P6 with template from P1s2 directly (in-screenshot template)
    print(f"\n  P6s2 vs P1s2-as-template: {match_score(p6_iso, p1_iso):.4f}")

    # histogram / structural analysis on gray crop
    for label, iso in [("P1s2", p1_iso), ("P6s2", p6_iso), ("tmpl", tmpl_iso)]:
        g = crop_center(cv2.cvtColor(iso, cv2.COLOR_BGR2GRAY), 0.1)
        print(f"  {label} gray: mean={g.mean():.1f} std={g.std():.1f} min={g.min()} max={g.max()}")


if __name__ == "__main__":
    main()
