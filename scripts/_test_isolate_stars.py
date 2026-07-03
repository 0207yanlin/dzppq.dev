import cv2
import numpy as np
from pathlib import Path

ROOT = Path(r"d:\dzppq_data_analysis")
img = cv2.imread(str(ROOT / "screenshots/MuMu-20260701-193102-703.png"))
x_offset = [0, 74, 149, 223, 297, 371, 445, 519, 594]
y_offset = [0, 93, 186, 279, 373, 466, 560, 653]
out_dir = ROOT / "debug_rois"
out_dir.mkdir(exist_ok=True)


def isolate_stars(roi, bg_color=(255, 255, 255)):
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    mask = cv2.inRange(hsv, (15, 60, 100), (40, 255, 255))
    kernel = np.ones((2, 2), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    result = np.full_like(roi, bg_color, dtype=np.uint8)
    result[mask > 0] = roi[mask > 0]
    return result, mask


for si, name in [(0, "3star"), (1, "2star"), (4, "1star_a"), (7, "1star_b")]:
    x1, y1 = 582 + x_offset[si], 287 + y_offset[0]
    x2, y2 = 652 + x_offset[si], 304 + y_offset[0]
    roi = img[y1:y2, x1:x2]
    isolated, mask = isolate_stars(roi)
    big = cv2.resize(isolated, None, fx=8, fy=8, interpolation=cv2.INTER_NEAREST)
    cv2.imwrite(str(out_dir / f"isolated_{name}.png"), big)
    print(name, "star_px=", mask.sum() // 255)
