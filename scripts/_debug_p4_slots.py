# -*- coding: utf-8 -*-
"""Debug P4 slot1/slot2 on MuMu-20260701-213025-596.png."""
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
IMG = ROOT / "screenshots" / "MuMu-20260701-213025-596.png"
CARDS = ROOT / "assets" / "templates" / "cards"
X_OFFSET = [0, 77, 154]
Y_OFFSET = [0, 93, 187, 280, 373, 466, 560, 653]

from _debug_clone_white import (  # noqa: E402
    adaptive_min_gap,
    combined_score,
    load_template,
    prepare_card_icon,
)


def analyze(j, i, expected):
    img = cv2.imread(str(IMG))
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
    print(f"P{j+1}s{i+1} expected={expected} => {label}")
    print(f"  best={best:.4f} 2nd={second:.4f} gap={best-second:.4f} need_gap={mg}")
    for total, sh, col, chroma, name in scores[:8]:
        mark = " <--expected" if name.replace(".jpg", "") == expected else ""
        print(
            f"  {total:.4f} (sh={sh:.3f} col={col:.3f} chr={chroma:.3f}) "
            f"{name.replace('.jpg', '')}{mark}"
        )
    print()


def main():
    analyze(3, 0, "最佳拍档")
    analyze(3, 1, "克隆技术·白")

    for n in ["克隆技术·白", "克隆技术·蓝", "最佳拍档"]:
        path, tmpl = load_template(n)
        print(f"template {n}: exists={path is not None}, path={path}")


if __name__ == "__main__":
    main()
