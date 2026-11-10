"""Sample-level screen detection, not defect classification or real press validation."""

import sys, json, platform, time, statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from presscheck.engine import inspect
from presscheck.models import Config

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dataset"


def main():
    out = ROOT / "work" / "benchmark"
    out.mkdir(parents=True, exist_ok=True)
    cases = json.loads((DATA / "cases.json").read_text())
    rows = []
    for c in cases:
        r = inspect(
            DATA / c["reference"], DATA / c["candidate"], out / c["id"], Config()
        )
        predicted = r["status"] in ("REVIEW REQUIRED", "FAIL")
        row = {
            **c,
            "predicted_positive": predicted,
            "status": r["status"],
            "registration_success": r["registration"]["confidence"] >= 0.55,
            "confidence": r["registration"]["confidence"],
            "seconds": r["metrics"]["processing_seconds"],
            "text_changes": r["text"]["changes"],
            "changed_area_percent": r["metrics"]["changed_area_percent"],
        }
        rows.append(row)
        print(c["id"], r["status"], r["metrics"]["processing_seconds"], flush=True)
    tp = sum(c["positive"] and c["predicted_positive"] for c in rows)
    fp = sum(not c["positive"] and c["predicted_positive"] for c in rows)
    fn = sum(c["positive"] and not c["predicted_positive"] for c in rows)
    tn = sum(not c["positive"] and not c["predicted_positive"] for c in rows)
    text_cases = [r for r in rows if r["id"] == "label-copy"]
    ocr_correct = sum(
        any(
            "SOLAR" in c["reference"] and "LUNAR" in c["candidate"]
            for c in r["text_changes"]
        )
        for r in text_cases
    )
    results = {
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "image_size": [800, 1060],
        "cases": len(rows),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        "registration_success_rate": sum(r["registration_success"] for r in rows)
        / len(rows),
        "ocr_target_substitution_accuracy": ocr_correct / len(text_cases),
        "ocr_target_cases": len(text_cases),
        "mean_processing_seconds": statistics.mean(c["seconds"] for c in rows),
        "median_processing_seconds": statistics.median(c["seconds"] for c in rows),
        "inconclusive": sum(c["status"] == "INCONCLUSIVE" for c in rows),
        "rows": rows,
    }
    (ROOT / "docs" / "benchmark-results.json").write_text(json.dumps(results, indent=2))
    print(json.dumps({k: v for k, v in results.items() if k != "rows"}, indent=2))


if __name__ == "__main__":
    main()
