from pathlib import Path
from datetime import datetime, timezone
import time
import cv2
import numpy as np
from PIL import Image, ImageDraw
from . import __version__
from .registration import register
from .color import compare_color
from .text import compare_text
from .geometry import packaging_checks, barcodes
from .ai import VisionProvider
from .models import Config


def inspect(reference_path: Path, candidate_path: Path, output: Path, config: Config):
    started = time.perf_counter()
    output.mkdir(parents=True, exist_ok=True)
    a = np.array(Image.open(reference_path).convert("RGB"))
    raw = np.array(Image.open(candidate_path).convert("RGB"))
    b, valid, registration = register(a, raw)
    delta, samples = compare_color(a, b, config.color_regions)
    # Erode correspondence boundary to prevent warp edge artifacts becoming findings.
    interior = cv2.erode(valid.astype(np.uint8), np.ones((5, 5), np.uint8)).astype(bool)
    mask = ((delta > config.delta_e) & interior).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    eligible_area = sum(int(row[4]) for row in stats[1:] if row[4] >= config.min_area)
    findings = []
    for idx in range(1, count):
        x, y, w, h, area = map(int, stats[idx])
        if area < config.min_area:
            continue
        if len(findings) >= 200:
            break
        measured = delta[labels == idx]
        findings.append(
            {
                "id": f"V{len(findings) + 1:03}",
                "kind": "visual",
                "severity": "major" if area > 5000 else "minor",
                "bbox": [x, y, x + w, y + h],
                "area_px": area,
                "mean_delta_e": round(float(measured.mean()), 3),
                "title": "Artwork difference",
                "recommendation": "Compare evidence for missing, added, shifted, or changed artwork. Pixel evidence alone does not identify manufacturing cause.",
            }
        )
    text = (
        compare_text(a, b)
        if config.ocr
        else {"available": False, "reason": "OCR disabled", "changes": []}
    )
    for c in text["changes"]:
        boxes = c["candidate_boxes"] or c["reference_boxes"]
        bbox = [
            min(r[0] for r in boxes),
            min(r[1] for r in boxes),
            max(r[2] for r in boxes),
            max(r[3] for r in boxes),
        ]
        findings.append(
            {
                "id": f"T{len(findings) + 1:03}",
                "kind": "text",
                "severity": "review",
                "bbox": bbox,
                "title": "Possible text " + c["operation"],
                "evidence": c,
                "recommendation": "Visually confirm OCR substitution, addition, or deletion.",
            }
        )
    geometry = packaging_checks(a, b, valid, config, text)
    barcode = {"available": False, "reason": "Barcode inspection disabled"}
    if config.barcode:
        ba, bb = barcodes(a), barcodes(b)
        barcode = {
            "available": ba["available"] and bb["available"],
            "reference": ba,
            "candidate": bb,
            "status": "unavailable",
        }
        if barcode["available"]:
            barcode["status"] = (
                "review"
                if [x["text"] for x in ba["codes"]] != [x["text"] for x in bb["codes"]]
                else ("clear" if ba["codes"] else "unavailable")
            )
            if not ba["codes"]:
                barcode["reason"] = (
                    "No readable reference barcode; absence cannot establish correctness"
                )
            if barcode["status"] == "review":
                findings.append(
                    {
                        "id": "B001",
                        "kind": "barcode",
                        "severity": "review",
                        "bbox": None,
                        "title": "Barcode payload/readability differs",
                        "evidence": barcode,
                        "recommendation": "Verify encoded data and inspect with a calibrated barcode verifier; this is not ISO grading.",
                    }
                )
    for check in geometry:
        if check["status"] == "review":
            findings.append(
                {
                    "id": f"G{len(findings) + 1:03}",
                    "kind": "geometry",
                    "severity": "review",
                    "bbox": config.trim,
                    "title": check["check"] + " needs review",
                    "evidence": check,
                    "recommendation": "Review trim, framing, and source geometry.",
                }
            )
    matrix = registration.get("matrix")
    geometric_deviation = False
    if matrix:
        move = np.linalg.norm(registration["translation_px"])
        scale = max(abs(registration["scale_x"] - 1), abs(registration["scale_y"] - 1))
        geometric_deviation = (
            move > 3
            or scale > 0.015
            or max(abs(r - 1) for r in registration["source_size_ratio"]) > 0.015
        )
        if geometric_deviation:
            findings.append(
                {
                    "id": "G001",
                    "kind": "geometry",
                    "severity": "review",
                    "bbox": None,
                    "title": "Position / scale deviation",
                    "evidence": registration,
                    "recommendation": "Registration corrected a geometric difference. Review original file geometry before approval.",
                }
            )
    changed = eligible_area / max(1, int(interior.sum())) * 100
    reliable = registration["confidence"] >= config.min_registration
    # Missing optional engines make an otherwise clear run inconclusive, never a false pass.
    required_unavailable = (config.ocr and not text["available"]) or (
        config.barcode and not barcode["available"]
    )
    status = (
        "INCONCLUSIVE"
        if not reliable or required_unavailable
        else (
            "FAIL"
            if changed >= config.fail_area_percent
            else ("REVIEW REQUIRED" if findings else "PASS")
        )
    )
    overlay = a.copy().astype(float)
    highlight = mask.astype(bool)
    overlay[highlight] = overlay[highlight] * 0.35 + np.array([216, 93, 75]) * 0.65
    annotated = Image.fromarray(overlay.astype(np.uint8))
    draw = ImageDraw.Draw(annotated)
    for f in findings:
        if f["bbox"]:
            draw.rectangle(f["bbox"], outline="#F47B36", width=3)
    annotated.save(output / "overlay.png")
    Image.fromarray(b).save(output / "aligned.png")
    Image.fromarray(a).save(output / "reference.png")
    heat = cv2.applyColorMap(
        np.uint8(np.clip(delta / 30, 0, 1) * 255), cv2.COLORMAP_INFERNO
    )
    Image.fromarray(cv2.cvtColor(heat, cv2.COLOR_BGR2RGB)).save(output / "heatmap.png")
    result = {
        "version": __version__,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "config": config.model_dump(),
        "registration": registration,
        "metrics": {
            "changed_area_percent": round(changed, 4),
            "mean_delta_e": round(float(delta[interior].mean()), 4)
            if interior.any()
            else None,
            "p95_delta_e": round(float(np.percentile(delta[interior], 95)), 4)
            if interior.any()
            else None,
            "processing_seconds": round(time.perf_counter() - started, 3),
        },
        "color_samples": [
            {
                **s,
                "tolerance": config.delta_e,
                "status": "review" if s["mean_delta_e"] > config.delta_e else "clear",
            }
            for s in samples
        ],
        "findings": findings,
        "text": text,
        "geometry": geometry,
        "barcode": barcode,
        "warnings": [
            "Automated screening is not production approval.",
            "Preview RGB color is not certified Pantone or press-color measurement.",
            "Registration confidence is a heuristic based on feature support, not a calibrated probability.",
            "Global registration cannot isolate plate-to-plate print misregistration.",
        ],
        "ai": {"available": False, "reason": "Not requested"},
    }
    if config.ai:
        result["ai"] = VisionProvider().interpret(
            output / "reference.png", output / "aligned.png", findings
        )
    return result
