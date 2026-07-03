# -*- coding: utf-8 -*-
"""Analyze why 吸吸宝pro becomes unknown — gap / cluster breakdown."""
import sys
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "assets" / "templates" / "cards"
IMG = ROOT / "screenshots" / "MuMu-20260701-222821-674.png"
X_OFFSET = [0, 77, 154]
Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]
SHAPE_WEIGHT, COLOR_WEIGHT, CHROMA_WEIGHT = 0.55, 0.20, 0.25
THRESHOLD, MIN_GAP, SHAPE_CLUSTER_THRESH = 0.75, 0.08, 0.75


def prepare_card_icon(roi, bg_color=(255, 255, 255), k=2):
    h, w = roi.shape[:2]
    pixels = roi.reshape(-1, 3).astype(np.float32)
    criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, labels, centers = cv2.kmeans(
        pixels, k, None, criteria, 10, cv2.KMEANS_PP_CENTERS
    )
    labels_2d = labels.reshape(h, w)
    border = np.zeros((h, w), dtype=bool)
    border[0, :] = border[-1, :] = border[:, 0] = border[:, -1] = True
    bg_label = Counter(labels_2d[border].tolist()).most_common(1)[0][0]
    icon = np.empty_like(roi)
    icon[:] = bg_color
    fg_mask = np.zeros((h, w), dtype=bool)
    for c in range(k):
        if c == bg_label:
            continue
        icon[labels_2d == c] = centers[c].astype(np.uint8)
        fg_mask |= labels_2d == c
    return icon, fg_mask


def crop_center(img, margin_ratio=0.1):
    h, w = img.shape[:2]
    mh, mw = int(h * margin_ratio), int(w * margin_ratio)
    return img[mh : h - mh, mw : w - mw]


def _tm(search, template):
    th, tw = template.shape[:2]
    if search.shape[0] < th or search.shape[1] < tw:
        return 0.0
    return float(cv2.matchTemplate(search, template, cv2.TM_CCOEFF_NORMED).max())


def shape_score(roi_icon, tmpl_icon, padding=8, margin_ratio=0.1):
    roi_gray = crop_center(cv2.cvtColor(roi_icon, cv2.COLOR_BGR2GRAY), margin_ratio)
    tmpl_gray = crop_center(cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2GRAY), margin_ratio)
    search = cv2.copyMakeBorder(
        roi_gray, padding, padding, padding, padding, cv2.BORDER_REPLICATE
    )
    return _tm(search, tmpl_gray)


def chroma_score(roi_icon, tmpl_icon, padding=8, margin_ratio=0.1):
    roi_lab = cv2.cvtColor(roi_icon, cv2.COLOR_BGR2LAB)
    tmpl_lab = cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2LAB)
    roi_ch = (
        crop_center(roi_lab[:, :, 1], margin_ratio).astype(np.float32)
        + crop_center(roi_lab[:, :, 2], margin_ratio).astype(np.float32)
    ) / 2
    tmpl_ch = (
        crop_center(tmpl_lab[:, :, 1], margin_ratio).astype(np.float32)
        + crop_center(tmpl_lab[:, :, 2], margin_ratio).astype(np.float32)
    ) / 2
    search = cv2.copyMakeBorder(
        roi_ch, padding, padding, padding, padding, cv2.BORDER_REPLICATE
    )
    return _tm(search, tmpl_ch)


def color_score(roi_icon, tmpl_icon, roi_fg, tmpl_fg):
    roi_lab = cv2.cvtColor(roi_icon, cv2.COLOR_BGR2LAB)
    tmpl_lab = cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2LAB)
    roi_hsv = cv2.cvtColor(roi_icon, cv2.COLOR_BGR2HSV)
    tmpl_hsv = cv2.cvtColor(tmpl_icon, cv2.COLOR_BGR2HSV)
    lab_dist = np.linalg.norm(
        roi_lab[roi_fg][:, 1:3].mean(axis=0) - tmpl_lab[tmpl_fg][:, 1:3].mean(axis=0)
    )
    lab_sim = max(0.0, 1.0 - lab_dist / 180.0)
    sat_dist = abs(roi_hsv[roi_fg][:, 1].mean() - tmpl_hsv[tmpl_fg][:, 1].mean())
    sat_sim = max(0.0, 1.0 - sat_dist / 255.0)
    return lab_sim * 0.6 + sat_sim * 0.4


def combined_score(roi_icon, tmpl_icon, roi_fg, tmpl_fg, padding=8, margin_ratio=0.1):
    sh = shape_score(roi_icon, tmpl_icon, padding, margin_ratio)
    col = color_score(roi_icon, tmpl_icon, roi_fg, tmpl_fg)
    ch = chroma_score(roi_icon, tmpl_icon, padding, margin_ratio)
    return SHAPE_WEIGHT * sh + COLOR_WEIGHT * col + CHROMA_WEIGHT * ch, sh, col, ch


def adaptive_min_gap(best_score, min_gap=0.08):
    if best_score > 0.9:
        return 0.0
    return min_gap


def main():
    img = cv2.imread(str(IMG))
    sigs = {}
    for p in CARDS.glob("*.jpg"):
        if p.name.startswith("player"):
            continue
        t = cv2.imdecode(np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        icon, fg = prepare_card_icon(t)
        sigs[p.name] = {"icon": icon, "fg": fg}

    print("=== 模板两两 shape 对比（人眼差异大但算法可能误判的）===")
    pairs = [
        ("吸吸宝pro", "快速成型"),
        ("吸吸宝pro", "天降啾啾pro"),
        ("吸吸宝pro", "吸吸宝"),
        ("快速成型", "天降啾啾pro"),
    ]
    for a, b in pairs:
        sa, sb = sigs[f"{a}.jpg"], sigs[f"{b}.jpg"]
        comb, sh, col, ch = combined_score(sa["icon"], sb["icon"], sa["fg"], sb["fg"])
        print(f"  {a} vs {b}: sh={sh:.3f} col={col:.3f} chr={ch:.3f} comb={comb:.4f}")

    for player, slot in [(5, 1), (6, 1)]:
        x1, y1 = 1340 + X_OFFSET[slot], 305 + Y_OFFSET[player]
        roi = img[y1 : y1 + 45, x1 : x1 + 45]
        roi_icon, roi_fg = prepare_card_icon(roi)
        details = []
        for name, sig in sigs.items():
            comb, sh, col, ch = combined_score(
                roi_icon, sig["icon"], roi_fg, sig["fg"]
            )
            details.append(
                {
                    "name": name,
                    "combined": comb,
                    "shape": sh,
                    "color": col,
                    "chroma": ch,
                }
            )
        details.sort(key=lambda d: d["combined"], reverse=True)
        winner, second = details[0], details[1]
        gap = winner["combined"] - second["combined"]
        gap_threshold = adaptive_min_gap(winner["combined"], MIN_GAP)
        high_shape = [d for d in details if d["shape"] >= SHAPE_CLUSTER_THRESH]

        print(f"\n=== P{player + 1}s{slot + 1} expected=吸吸宝pro ===")
        print(
            f"  #1 {winner['name']}: comb={winner['combined']:.4f} "
            f"sh={winner['shape']:.3f} col={winner['color']:.3f} chr={winner['chroma']:.3f}"
        )
        print(
            f"  #2 {second['name']}: comb={second['combined']:.4f} "
            f"sh={second['shape']:.3f} col={second['color']:.3f} chr={second['chroma']:.3f}"
        )
        print(
            f"  gap={gap:.4f} need={gap_threshold} => "
            f"{'PASS' if winner['combined'] >= THRESHOLD and gap >= gap_threshold else 'UNKNOWN'}"
        )
        print("  shape cluster (shape>=0.75):")
        for d in sorted(high_shape, key=lambda x: x["combined"], reverse=True):
            mark = " <--expected" if d["name"] == "吸吸宝pro.jpg" else ""
            print(
                f"    {d['name'].replace('.jpg','')}: sh={d['shape']:.3f} "
                f"col={d['color']:.3f} chr={d['chroma']:.3f} comb={d['combined']:.4f}{mark}"
            )
        if len(high_shape) >= 2:
            cr = sorted(high_shape, key=lambda d: d["color"], reverse=True)
            print(
                f"  color tiebreak: {cr[0]['name']} col={cr[0]['color']:.3f} vs "
                f"{cr[1]['name']} col={cr[1]['color']:.3f} "
                f"diff={cr[0]['color'] - cr[1]['color']:.4f} (need >=0.03)"
            )

        # 分解 combined 各项贡献
        exp = next(d for d in details if d["name"] == "吸吸宝pro.jpg")
        rival = next(d for d in details if d["name"] == second["name"])
        print("  combined 贡献分解 (#1 vs 吸吸宝pro):")
        for label, d in [("#1", winner), ("吸吸宝pro", exp)]:
            sh_c = SHAPE_WEIGHT * d["shape"]
            col_c = COLOR_WEIGHT * d["color"]
            ch_c = CHROMA_WEIGHT * d["chroma"]
            print(
                f"    {label}: shape={sh_c:.4f} color={col_c:.4f} chroma={ch_c:.4f} "
                f"sum={d['combined']:.4f}"
            )


if __name__ == "__main__":
    main()
