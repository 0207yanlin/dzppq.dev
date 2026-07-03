import cv2
import numpy as np
from pathlib import Path

img_path = Path("screenshots/MuMu-20260701-193102-703.png")
img = cv2.imread(str(img_path))
x_offset = [0, 74, 149, 223, 297, 371, 445, 519, 594]
y_offset = [0, 93, 186, 279, 373, 466, 560, 653]

expected = [3, 5, 4, 3, 5, 2, 1, 1]
j = 0
for i, exp in enumerate(expected):
    x1, y1 = 582 + x_offset[i], 287 + y_offset[j]
    x2, y2 = 652 + x_offset[i], 304 + y_offset[j]
    roi = img[y1:y2, x1:x2]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    mask_yellow = cv2.inRange(hsv, (15, 80, 80), (35, 255, 255))
    mask_bright = gray > 150
    print(
        f"slot{i} exp={exp} shape={roi.shape} mean={gray.mean():.1f} "
        f"yellow_px={mask_yellow.sum() // 255} bright_px={mask_bright.sum()}"
    )
    cv2.imwrite(f"debug_rois/star_p1_s{i}.png", roi)
