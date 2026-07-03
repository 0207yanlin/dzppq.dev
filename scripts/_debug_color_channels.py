# -*- coding: utf-8 -*-
from collections import Counter
from pathlib import Path
import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
CARDS = ROOT / "assets" / "templates" / "cards"
NAMES = ["最佳拍档", "最佳拍档max", "重质也重量pro", "最强支援"]


def load(n):
    for p in CARDS.glob("*.jpg"):
        if p.stem == n:
            return cv2.imdecode(np.frombuffer(p.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)


def isolate_icon(roi, bg=(255, 255, 255), k=2):
    h, w = roi.shape[:2]
    px = roi.reshape(-1, 3).astype(np.float32)
    crit = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 20, 1.0)
    _, lab, cent = cv2.kmeans(px, k, None, crit, 10, cv2.KMEANS_PP_CENTERS)
    lab2 = lab.reshape(h, w)
    bd = np.zeros((h, w), bool)
    bd[0, :] = bd[-1, :] = bd[:, 0] = bd[:, -1] = True
    bg = Counter(lab2[bd].tolist()).most_common(1)[0][0]
    out = np.empty_like(roi)
    out[:] = bg
    for c in range(k):
        if c != bg:
            out[lab2 == c] = cent[c].astype(np.uint8)
    return out, lab2 != bg


def cc(img, m=0.1):
    h, w = img.shape[:2]
    mh, mw = int(h * m), int(w * m)
    return img[mh : h - mh, mw : w - mw]


def mt(a, b):
    s = cv2.copyMakeBorder(a, 8, 8, 8, 8, cv2.BORDER_REPLICATE)
    return float(cv2.matchTemplate(s, b, cv2.TM_CCOEFF_NORMED).max())


tmpls = {n: load(n) for n in NAMES}
print("=== Foreground color (canonical template) ===")
for n, t in tmpls.items():
    iso, fg = isolate_icon(t)
    hsv = cv2.cvtColor(iso, cv2.COLOR_BGR2HSV)
    g = cv2.cvtColor(iso, cv2.COLOR_BGR2GRAY)
    print(
        f"{n}: BGR={iso[fg].mean(axis=0).round(1)} "
        f"gray={g[fg].mean():.1f} hue={hsv[:,:,0][fg].mean():.1f} sat={hsv[:,:,1][fg].mean():.1f}"
    )

print("\n=== 最佳拍档max vs others (template-template) ===")
a = isolate_icon(tmpls["最佳拍档max"])[0]
for n in ["最佳拍档", "重质也重量pro", "最强支援"]:
    b = isolate_icon(tmpls[n])[0]
    gray = mt(cc(cv2.cvtColor(a, cv2.COLOR_BGR2GRAY)), cc(cv2.cvtColor(b, cv2.COLOR_BGR2GRAY)))
    hue = mt(cc(cv2.cvtColor(a, cv2.COLOR_BGR2HSV)[:, :, 0]), cc(cv2.cvtColor(b, cv2.COLOR_BGR2HSV)[:, :, 0]))
    sat = mt(cc(cv2.cvtColor(a, cv2.COLOR_BGR2HSV)[:, :, 1]), cc(cv2.cvtColor(b, cv2.COLOR_BGR2HSV)[:, :, 1]))
    print(f"  vs {n}: gray={gray:.4f}  hue={hue:.4f}  sat={sat:.4f}")

img = cv2.imread(str(ROOT / "screenshots" / "MuMu-20260701-210420-941.png"))
r = isolate_icon(img[585:630, 1494:1539])[0]
print("\n=== P4s3 ROI ranking by channel ===")
for mode, fn in [
    ("gray", lambda x: cc(cv2.cvtColor(x, cv2.COLOR_BGR2GRAY))),
    ("hue", lambda x: cc(cv2.cvtColor(x, cv2.COLOR_BGR2HSV)[:, :, 0])),
    ("sat", lambda x: cc(cv2.cvtColor(x, cv2.COLOR_BGR2HSV)[:, :, 1])),
]:
    sc = [(mt(fn(r), fn(isolate_icon(tmpls[n])[0])), n) for n in NAMES]
    sc.sort(reverse=True)
    print(f"{mode}: {[(round(s, 3), n) for s, n in sc]}")
