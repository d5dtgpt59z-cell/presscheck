import cv2
import numpy as np


def packaging_checks(a, b, valid, config, text):
    checks = []
    h, w = a.shape[:2]
    clipped = float(1 - valid.mean())
    checks.append(
        {
            "check": "registration coverage",
            "status": "review" if clipped > 0.005 else "clear",
            "uncovered_percent": clipped * 100,
            "note": "Uncovered transformed border suggests clipping or framing change; not proof of ink clipping.",
        }
    )
    if not config.trim:
        checks += [
            {
                "check": name,
                "status": "unavailable",
                "reason": "Configure trim rectangle in reference preview pixels",
            }
            for name in ("bleed", "safe zone")
        ]
        return checks
    x0, y0, x1, y1 = config.trim
    if x1 > w or y1 > h:
        raise ValueError("Trim exceeds reference preview")
    bleed = config.bleed_px
    within = x0 >= bleed and y0 >= bleed and x1 + bleed <= w and y1 + bleed <= h
    checks.append(
        {
            "check": "bleed",
            "status": "review" if not within else "clear",
            "note": "Checks canvas extent around trim only; intended ink continuity requires human review.",
            "required_px": bleed,
        }
    )
    safe = config.safe_px
    boxes = text.get("candidate", {}).get("words", [])
    violations = [
        word
        for word in boxes
        if word["bbox"][0] < x0 + safe
        or word["bbox"][1] < y0 + safe
        or word["bbox"][2] > x1 - safe
        or word["bbox"][3] > y1 - safe
    ]
    checks.append(
        {
            "check": "safe zone",
            "status": ("review" if violations else "clear")
            if text.get("available")
            else "unavailable",
            "words": violations,
            "note": "OCR text bounds only; graphical safe areas and irregular dielines are unsupported.",
        }
    )
    return checks


def barcodes(img):
    try:
        import zxingcpp

        results = zxingcpp.read_barcodes(img)
        return {
            "available": True,
            "codes": [
                {"text": r.text, "format": str(r.format), "position": str(r.position)}
                for r in results
            ],
        }
    except ImportError:
        return {"available": False, "reason": "zxing-cpp unavailable", "codes": []}
