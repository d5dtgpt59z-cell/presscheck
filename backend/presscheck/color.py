import cv2
import numpy as np
from skimage.color import rgb2lab, deltaE_ciede2000


def compare_color(reference, candidate, regions):
    # Mild optical smoothing suppresses resampling noise; values are preview sRGB estimates.
    a = cv2.GaussianBlur(reference, (3, 3), 0).astype(float) / 255
    b = cv2.GaussianBlur(candidate, (3, 3), 0).astype(float) / 255
    delta = deltaE_ciede2000(rgb2lab(a), rgb2lab(b)).astype(np.float32)
    samples = []
    h, w = delta.shape
    for rect in regions:
        x0, y0, x1, y1 = rect
        if x1 > w or y1 > h:
            raise ValueError("Color region exceeds reference preview")
        ra = reference[y0:y1, x0:x1]
        rb = candidate[y0:y1, x0:x1]
        samples.append(
            {
                "bbox": list(rect),
                "reference_rgb": np.mean(ra, axis=(0, 1)).round().astype(int).tolist(),
                "candidate_rgb": np.mean(rb, axis=(0, 1)).round().astype(int).tolist(),
                "mean_delta_e": float(delta[y0:y1, x0:x1].mean()),
            }
        )
    return delta, samples
