# -*- coding: utf-8 -*-
"""Debug why 吸吸宝pro returns unknown on P6s2 / P7s2."""
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "screenshots" / "MuMu-20260701-222821-674.png"
CARDS = ROOT / "assets" / "templates" / "cards"
X_OFFSET = [0, 77, 154]
Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]

SHAPE_WEIGHT = 0.55
COLOR_WEIGHT = 0.20
CHROMA_WEIGHT = 0.25
SHAPE_CLUSTER_THRESH = 0.75
THRESHOLD = 0.75
MIN_GAP = 0.08


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


def _template_match_score(search, template):
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
    return _template_match_score(search, tmpl_gray)


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
    return _template_match_score(search, tmpl_ch)


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
    chroma = chroma_score(roi_icon, tmpl_icon, padding, margin_ratio)
    return SHAPE_WEIGHT * sh + COLOR_WEIGHT * col + CHROMA_WEIGHT * chroma, sh, col, chroma


def adaptive_min_gap(best_score, min_gap=0.08):
    if best_score > 0.9:
        return 0.0
    return min_gap


def match_card_roi_debug(roi_icon, roi_fg, sigs):
    details = []
    for name, sig in sigs.items():
        comb, sh, col, ch = combined_score(
            roi_icon, sig["icon"], roi_fg, sig["fg"]
        )
        details.append(
            {"name": name, "combined": comb, "shape": sh, "color": col, "chroma": ch}
        )
    details.sort(key=lambda d: d["combined"], reverse=True)

    high_shape = [d for d in details if d["shape"] >= SHAPE_CLUSTER_THRESH]
    color_tiebreak = None
    if len(high_shape) >= 2:
        color_ranked = sorted(high_shape, key=lambda d: d["color"], reverse=True)
        winner = color_ranked[0]
        second_color = color_ranked[1]["color"]
        if winner["color"] >= 0.85 and winner["color"] - second_color >= 0.03:
            color_tiebreak = winner

    winner = details[0]
    second = details[1]["combined"] if len(details) > 1 else 0.0
    gap_threshold = adaptive_min_gap(winner["combined"], MIN_GAP)

    reasons = []
    if color_tiebreak:
        return color_tiebreak["name"], color_tiebreak["combined"], "color_tiebreak", details, reasons

    if winner["combined"] < THRESHOLD:
        reasons.append(f"best combined {winner['combined']:.4f} < threshold {THRESHOLD}")
    if winner["combined"] - second < gap_threshold:
        reasons.append(
            f"gap {winner['combined'] - second:.4f} < need {gap_threshold} "
            f"(best={winner['name']}, 2nd={details[1]['name']})"
        )
    if reasons:
        return "unknown", winner["combined"], "rejected", details, reasons
    return winner["name"], winner["combined"], "combined", details, reasons


def main():
    img = cv2.imread(str(IMG))
    sigs = {}
    for p in CARDS.glob("*.jpg"):
        if p.name.startswith("player"):
            continue
        t = cv2.imdecode(np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if t is not None:
            icon, fg = prepare_card_icon(t)
            sigs[p.name] = {"icon": icon, "fg": fg, "orig": t}

    pro = sigs.get("吸吸宝pro.jpg")
    base = sigs.get("吸吸宝.jpg")
    if pro and base:
        comb, sh, col, ch = combined_score(pro["icon"], base["icon"], pro["fg"], base["fg"])
        print("=== Template pair 吸吸宝pro vs 吸吸宝 ===")
        print(f"combined={comb:.4f} shape={sh:.4f} color={col:.4f} chroma={ch:.4f}")
        print(f"pro fg BGR: {pro['orig'][pro['fg']].mean(axis=0).round(1)}")
        print(f"base fg BGR: {base['orig'][base['fg']].mean(axis=0).round(1)}")

    for player, slot in [(5, 1), (6, 1)]:
        x1, y1 = 1340 + X_OFFSET[slot], 305 + Y_OFFSET[player]
        roi = img[y1 : y1 + 45, x1 : x1 + 45]
        roi_icon, roi_fg = prepare_card_icon(roi)
        label, score, mode, details, reasons = match_card_roi_debug(
            roi_icon, roi_fg, sigs
        )
        print(f"\n=== P{player + 1}s{slot + 1} expected=吸吸宝pro => {label} ({mode}, score={score:.4f}) ===")
        if reasons:
            print("  reject:", "; ".join(reasons))
        print(f"  ROI fg BGR: {roi[roi_fg].mean(axis=0).round(1)}")
        for n in ["吸吸宝pro.jpg", "吸吸宝.jpg"]:
            if n not in sigs:
                print(f"  {n}: MISSING")
                continue
            sig = sigs[n]
            comb, sh, col, ch = combined_score(
                roi_icon, sig["icon"], roi_fg, sig["fg"]
            )
            mark = " <--expected" if n == "吸吸宝pro.jpg" else ""
            print(
                f"  {n.replace('.jpg','')}: comb={comb:.4f} sh={sh:.3f} col={col:.3f} chr={ch:.3f}{mark}"
            )
        print("  TOP8 combined:")
        for d in details[:8]:
            mark = " <--expected" if d["name"] == "吸吸宝pro.jpg" else ""
            print(
                f"    {d['combined']:.4f} (sh={d['shape']:.3f} col={d['color']:.3f}) "
                f"{d['name'].replace('.jpg','')}{mark}"
            )
        high = [d for d in details if d["shape"] >= SHAPE_CLUSTER_THRESH]
        if len(high) >= 2:
            cr = sorted(high, key=lambda d: d["color"], reverse=True)
            print(f"  color tiebreak: top={cr[0]['name']} col={cr[0]['color']:.3f}, "
                  f"2nd={cr[1]['name']} col={cr[1]['color']:.3f}, diff={cr[0]['color']-cr[1]['color']:.3f}")


if __name__ == "__main__":
    main()
