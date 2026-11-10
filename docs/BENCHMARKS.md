# Benchmark methodology and measured results

## Scope

The committed `dataset/cases.json` has 33 deterministic image pairs: three original fictional packaging masters, nine one-at-a-time defect transformations per master (27 positives), exact-match copies (three negatives), and quality-92 JPEG re-encodings (three negatives). Masters are 800 × 1060 raster pixels. Generation is reproducible via `scripts/generate_dataset.py`, with a fixed random seed for registration texture. The dataset contains no client artwork.

Injected defects: removed M78 mark, shifted mark, missing legal notice / altered product title, changed illustration color, left-edge crop, changed EAN-13 payload, 15×8-pixel global offset, 6% rescaling, and a removed illustration element. Combined-defect demos are separate from these single-defect pairs. These are deliberately controlled examples, not real press samples.

**Evaluation is sample-level flagging**, not finding localization, semantic defect classification, physical production accuracy, or calibrated severity. Positive prediction means `REVIEW REQUIRED` or `FAIL`; `INCONCLUSIVE` is reported separately and counts as a missed detection on a positive sample. Precision = TP/(TP+FP), recall = TP/(TP+FN), FPR = FP/(FP+TN). Registration success means heuristic support >= 0.55, not ground-truth homography error. Runtime covers deterministic engine execution, previews/evidence writing and OCR; it excludes upload, queue delay and report generation.

## Local measured run, 2026-11-10

Python 3.14.5, macOS 26.5.2 ARM64, Tesseract 5.5.4, the exact dependency versions in `requirements.txt`. Standard configuration: delta E 6, min region 45 px, FAIL at 8%, no trim configured, OCR and barcodes enabled, AI disabled. Raw run record: [benchmark-results.json](benchmark-results.json).

| Measure | Observed result |
| --- | --- |
| True positives / false positives | 27 / 1 |
| True negatives / false negatives | 5 / 0 |
| Precision | 96.43% (27/28) |
| Recall | 100% (27/27) |
| False-positive rate | 16.67% (1/6) |
| Registration-support threshold met | 33/33 |
| Targeted OCR product-title substitution | 1/1 |
| Mean engine time | 0.599 s |
| Median engine time | 0.589 s |
| Inconclusive | 0/33 |

The OCR check specifically tests SOLAR → LUNAR in one label. **1/1 is not evidence of general OCR accuracy**, legal-copy reliability, or multilingual performance. Ground-truth word accuracy and region-level precision/recall are not evaluated.

## False positive and development history

`toy-jpeg` is a false positive. Quality-92 JPEG of an unchanged toy package changes Tesseract's interpretation of a title glyph: a low-confidence copyright-like character (0.40 confidence) appears in the master OCR and disappears in the candidate. There are no qualifying visual regions after noise suppression, yet the system conservatively presents the possible text deletion for review. The report retains the bounds and uncertainty instead of declaring it a confirmed copy error.

The first run had three JPEG false positives: [benchmark-baseline.json](benchmark-baseline.json). It revealed barcode stripes being read as text and resampling noise from near-identity homographies. Changes added barcode-region masking for OCR, a 3×3 opening and guarded identity snapping. The barcode fixture was also corrected from a resized, poorly readable raster to native integer-width modules. Because the fixture changed, **the baseline is not a strictly controlled algorithm-only A/B comparison**. The final committed dataset and final results are the reproduction target.

## Limits and next evaluation

This dataset was used while developing the algorithm. There is no independent held-out split, no confidence interval narrow enough to justify production claims, and only six negative controls. All examples share simple illustration/layout geometry. Tesseract versions and platform can change outputs. Before operational use: acquire permitted real proofs/scans, include low-resolution legal copy, rotations/perspective, severe lighting and halftones, annotate localized defects, broaden clean controls, freeze thresholds, and evaluate on held-out packaging families.

CI reruns the same committed cases and uploads its own result file; do not replace local measurements with CI results without labeling conditions.
