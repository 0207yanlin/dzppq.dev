# -*- coding: utf-8 -*-
"""Debug P4s2 克隆技术·白 on MuMu-20260701-213025-596.png."""
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "screenshots" / "MuMu-20260701-213025-596.png"
CARDS = ROOT / "assets" / "templates" / "cards"

SHAPE_WEIGHT, COLOR_WEIGHT, CHROMA_WEIGHT = 0.55, 0.20, 0.25
X_OFFSET = [0, 77, 154]
Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]


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
    total = SHAPE_WEIGHT * sh + COLOR_WEIGHT * col + CHROMA_WEIGHT * chroma
    return total, sh, col, chroma


def adaptive_min_gap(best_score, min_gap=0.08):
    if best_score >= 0.98:
        return 0.02
    if best_score >= 0.95:
        return 0.04
    return min_gap


def load_template(name):
    for p in CARDS.glob("*.jpg"):
        if p.stem == name:
            buf = np.frombuffer(p.read_bytes(), dtype=np.uint8)
            return p, cv2.imdecode(buf, cv2.IMREAD_COLOR)
    return None, None


def main():
    img = cv2.imread(str(IMG))
    j, i = 3, 1  # player4 slot2
    x1, y1 = 1340 + X_OFFSET[i], 305 + Y_OFFSET[j]
    roi = img[y1 : y1 + 45, x1 : x1 + 45]
    roi_icon, roi_fg = prepare_card_icon(roi)

    sigs = {}
    for p in CARDS.glob("*.jpg"):
        if p.name.startswith("player"):
            continue
        t = cv2.imdecode(np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if t is not None:
            icon, fg = prepare_card_icon(t)
            sigs[p.name] = {"icon": icon, "fg": fg}

    scores = []
    for name, sig in sigs.items():
        total, sh, col, chroma = combined_score(
            roi_icon, sig["icon"], roi_fg, sig["fg"]
        )
        scores.append((total, sh, col, chroma, name))
    scores.sort(reverse=True)

    best, second = scores[0][0], scores[1][0]
    mg = adaptive_min_gap(best)
    label = (
        scores[0][4].replace(".jpg", "")
        if best >= 0.75 and (best - second) >= mg
        else "unknown"
    )

    print(f"P4 slot2 expected=克隆技术·白 => {label}")
    print(f"best={best:.4f} second={second:.4f} gap={best-second:.4f} need_gap={mg}")
    print("\nTop 10:")
    for total, sh, col, chroma, name in scores[:10]:
        mark = " <--expected" if name.replace(".jpg", "") == "克隆技术·白" else ""
        print(
            f"  {total:.4f} (sh={sh:.3f} col={col:.3f} chr={chroma:.3f}) "
            f"{name.replace('.jpg', '')}{mark}"
        )

    path, tmpl = load_template("克隆技术·白")
    print(f"\nTemplate exists: {path is not None}, shape: {None if tmpl is None else tmpl.shape}")

    # compare white vs blue clone
    for n in ["克隆技术·白", "克隆技术·蓝"]:
        _, t = load_template(n)
        if t is None:
            print(f"{n}: MISSING")
            continue
        total, sh, col, chroma = combined_score(
            roi_icon, prepare_card_icon(t)[0], roi_fg, prepare_card_icon(t)[1]
        )
        print(f"  vs {n}: total={total:.4f} sh={sh:.3f} col={col:.3f} chr={chroma:.3f}")

    # save roi for visual
    out = ROOT / "debug_rois"
    out.mkdir(exist_ok=True)
    cv2.imencode(".png", roi)[1].tofile(str(out / "p4s2_roi_raw.png"))
    cv2.imencode(".png", roi_icon)[1].tofile(str(out / "p4s2_roi_iso.png"))
    if tmpl is not None:
        cv2.imencode(".png", np.hstack([roi_icon, prepare_card_icon(tmpl)[0]]))[1].tofile(
            str(out / "p4s2_vs_clone_white.png")
        )


if __name__ == "__main__":
    main()
