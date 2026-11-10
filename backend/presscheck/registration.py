import cv2
import numpy as np


def register(reference, candidate):
    h, w = reference.shape[:2]
    ch, cw = candidate.shape[:2]
    resized = cv2.resize(candidate, (w, h), interpolation=cv2.INTER_AREA)
    gray = lambda x: cv2.cvtColor(x, cv2.COLOR_RGB2GRAY)
    orb = cv2.ORB_create(nfeatures=4000)
    kr, dr = orb.detectAndCompute(gray(reference), None)
    options = []
    # Orthogonal orientation is evaluated before feature registration.
    for rotation in range(4):
        raw = np.rot90(candidate, rotation).copy()
        trial = cv2.resize(raw, (w, h), interpolation=cv2.INTER_AREA)
        kc, dc = orb.detectAndCompute(gray(trial), None)
        if dr is None or dc is None or len(kr) < 8 or len(kc) < 8:
            continue
        pairs = cv2.BFMatcher(cv2.NORM_HAMMING).knnMatch(dc, dr, k=2)
        good = [
            a
            for pair in pairs
            if len(pair) == 2
            for a, b in [pair]
            if a.distance < 0.72 * b.distance
        ]
        if len(good) < 8:
            continue
        src = np.float32([kc[m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
        dst = np.float32([kr[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
        matrix, mask = cv2.findHomography(src, dst, cv2.RANSAC, 3)
        if matrix is None:
            continue
        ratio = float(mask.mean())
        corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
        projected = cv2.perspectiveTransform(corners, matrix).reshape(4, 2)
        area = abs(cv2.contourArea(projected)) / (w * h)
        if (
            not np.isfinite(matrix).all()
            or not 0.4 < area < 2.5
            or not cv2.isContourConvex(projected)
        ):
            continue
        hull = cv2.convexHull(src[mask.ravel().astype(bool)])
        coverage = abs(cv2.contourArea(hull)) / (w * h)
        quality = ratio * min(1.0, coverage / 0.25) * min(1.0, len(good) / 30)
        options.append((quality, trial, matrix, ratio, len(good), rotation, coverage))
    if not options:
        return (
            resized,
            np.ones((h, w), bool),
            {
                "method": "unregistered",
                "confidence": 0.0,
                "matches": 0,
                "scale_x": w / cw,
                "scale_y": h / ch,
                "warning": "Insufficient distributed features; comparison cannot establish correspondence",
            },
        )
    quality, trial, matrix, ratio, count, rotation, coverage = max(
        options, key=lambda o: o[0]
    )
    # Avoid interpolation from subpixel homography jitter when the direct raster
    # already provides a better fit and corner displacement is under one pixel.
    corners = np.float32([[0, 0], [w, 0], [w, h], [0, h]]).reshape(-1, 1, 2)
    displacement = np.max(
        np.linalg.norm(cv2.perspectiveTransform(corners, matrix) - corners, axis=2)
    )
    warped = cv2.warpPerspective(trial, matrix, (w, h), borderValue=(255, 255, 255))
    if displacement < 1 and np.mean(np.abs(trial.astype(float) - reference)) <= np.mean(
        np.abs(warped.astype(float) - reference)
    ):
        matrix = np.eye(3)
        aligned = trial
    else:
        aligned = warped
    valid = cv2.warpPerspective(np.ones((h, w), np.uint8), matrix, (w, h)).astype(bool)
    return (
        aligned,
        valid,
        {
            "method": "ORB + RANSAC homography",
            "confidence": round(quality, 4),
            "inlier_ratio": ratio,
            "coverage": coverage,
            "matches": count,
            "rotation_degrees": rotation * 90,
            "matrix": matrix.tolist(),
            "translation_px": [float(matrix[0, 2]), float(matrix[1, 2])],
            "scale_x": float(np.linalg.norm(matrix[:2, 0])),
            "scale_y": float(np.linalg.norm(matrix[:2, 1])),
            "source_size_ratio": [cw / w, ch / h],
        },
    )
