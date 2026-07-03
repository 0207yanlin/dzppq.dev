# -*- coding: utf-8 -*-
"""Debug 卡牌宝袋 vs 最后的波纹·蓝 confusion."""
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "screenshots" / "MuMu-20260701-214536-734.png"
CARDS = ROOT / "assets" / "templates" / "cards"
OUT = ROOT / "debug_rois"
OUT.mkdir(exist_ok=True)

X_OFFSET = [0, 77, 154]
Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]
SHAPE_WEIGHT, COLOR_WEIGHT, CHROMA_WEIGHT = 0.55, 0.20, 0.25


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


def prepare_raw_fg(roi, bg_color=(255, 255, 255), k=2):
    icon, fg_mask = prepare_card_icon(roi, bg_color, k)
    raw = np.full_like(roi, bg_color)
    raw[fg_mask] = roi[fg_mask]
    return raw, fg_mask


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


def color_match_raw(a, b, padding=8, margin_ratio=0.1):
    ah, aw = a.shape[:2]
    mh, mw = int(ah * margin_ratio), int(aw * margin_ratio)
    a_c = a[mh : ah - mh, mw : aw - mw]
    b_c = b[mh : ah - mh, mw : aw - mw]
    search = cv2.copyMakeBorder(
        a_c, padding, padding, padding, padding, cv2.BORDER_REPLICATE
    )
    scores = []
    for ch in range(3):
        res = cv2.matchTemplate(search[:, :, ch], b_c[:, :, ch], cv2.TM_CCOEFF_NORMED)
        scores.append(float(res.max()))
    return sum(scores) / 3


def combined_score(roi_icon, tmpl_icon, roi_fg, tmpl_fg, padding=8, margin_ratio=0.1):
    sh = shape_score(roi_icon, tmpl_icon, padding, margin_ratio)
    col = color_score(roi_icon, tmpl_icon, roi_fg, tmpl_fg)
    chroma = chroma_score(roi_icon, tmpl_icon, padding, margin_ratio)
    total = SHAPE_WEIGHT * sh + COLOR_WEIGHT * col + CHROMA_WEIGHT * chroma
    return total, sh, col, chroma


def load(name):
    for p in CARDS.glob("*.jpg"):
        if p.stem == name:
            return cv2.imdecode(np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    return None


def main():
    img = cv2.imread(str(IMG))
    sigs = {}
    for p in CARDS.glob("*.jpg"):
        if p.name.startswith("player"):
            continue
        t = cv2.imdecode(np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if t is not None:
            icon, fg = prepare_card_icon(t)
            raw, _ = prepare_raw_fg(t)
            sigs[p.stem] = {"icon": icon, "fg": fg, "raw": raw, "orig": t}

    a, b = sigs["卡牌宝袋"], sigs["最后的波纹·蓝"]
    print("=== Template pair ===")
    print(f"shape: {shape_score(a['icon'], b['icon']):.4f}")
    print(f"chroma: {chroma_score(a['icon'], b['icon']):.4f}")
    print(f"color: {color_score(a['icon'], b['icon'], a['fg'], b['fg']):.4f}")
    print(f"color_match_raw: {color_match_raw(a['raw'], b['raw']):.4f}")
    print(f"fg mean BGR 卡牌宝袋: {a['orig'][a['fg']].mean(axis=0).round(1)}")
    print(f"fg mean BGR 最后的波纹·蓝: {b['orig'][b['fg']].mean(axis=0).round(1)}")

    baodai = load("卡牌宝袋")
    bowen = load("最后的波纹·蓝")
    cv2.imencode(".png", np.hstack([baodai, bowen]))[1].tofile(
        str(OUT / "baodai_vs_bowen_raw.png")
    )
    cv2.imencode(".png", np.hstack([a["icon"], b["icon"]]))[1].tofile(
        str(OUT / "baodai_vs_bowen_iso.png")
    )

    for player, slot, expected in [(3, 2, "卡牌宝袋"), (6, 2, "卡牌宝袋")]:
        x1, y1 = 1340 + X_OFFSET[slot], 305 + Y_OFFSET[player]
        roi = img[y1 : y1 + 45, x1 : x1 + 45]
        roi_icon, roi_fg = prepare_card_icon(roi)
        roi_raw, _ = prepare_raw_fg(roi)
        print(f"\n=== P{player + 1}s{slot + 1} expected={expected} ===")
        for n in ["卡牌宝袋", "最后的波纹·蓝", "最后的波纹"]:
            if n not in sigs:
                continue
            sig = sigs[n]
            total, sh, col, chroma = combined_score(
                roi_icon, sig["icon"], roi_fg, sig["fg"]
            )
            cm = color_match_raw(roi_raw, sig["raw"])
            print(
                f"  {n}: total={total:.4f} sh={sh:.3f} col={col:.3f} "
                f"chr={chroma:.3f} raw_col={cm:.3f}"
            )
        scores = []
        for name, sig in sigs.items():
            total, sh, col, chroma = combined_score(
                roi_icon, sig["icon"], roi_fg, sig["fg"]
            )
            scores.append((total, sh, col, chroma, name))
        scores.sort(reverse=True)
        print("  TOP5:")
        for total, sh, col, chroma, name in scores[:5]:
            mark = " <--expected" if name == expected else ""
            print(
                f"    {total:.4f} (sh={sh:.3f} col={col:.3f} chr={chroma:.3f}) "
                f"{name}{mark}"
            )


if __name__ == "__main__":
    main()
