# API

Live OpenAPI: `http://127.0.0.1:8000/docs` (machine schema `/openapi.json`). No authentication: local trusted use only.

| Method | Endpoint | Purpose |
| --- | --- | --- |
| GET | `/api/health` | Actual service version and Tesseract availability |
| POST | `/api/assets` | Multipart `file`; preserve source and generate page previews |
| GET | `/api/assets/{id}` | Source metadata, hash, warnings and page list |
| GET | `/api/assets/{id}/pages/{page}` | Normalized PNG, zero-based page index |
| GET | `/api/assets/{id}/original` | Preserved original download |
| POST | `/api/inspections` | Queue actual work; returns HTTP 202 with ID |
| GET | `/api/inspections` | Recent 50 jobs |
| GET | `/api/inspections/{id}` | Job state, request, result or error |
| GET | `/api/inspections/{id}/files/{name}` | `report.pdf`, `report.json`, `reference.png`, `aligned.png`, `overlay.png`, `heatmap.png` |
| GET | `/api/demos` | Original fictional sample manifest |
| POST | `/api/demos/{cards,label,toy}?clean=false` | Ingest actual demo source pair |

Example inspection request:

```json
{
  "reference": "<32-character asset UUID hex>",
  "candidate": "<32-character asset UUID hex>",
  "reference_page": 0,
  "candidate_page": 0,
  "config": {
    "delta_e": 6,
    "min_area": 45,
    "fail_area_percent": 8,
    "min_registration": 0.55,
    "trim": [30, 30, 770, 1030],
    "safe_px": 20,
    "bleed_px": 12,
    "color_regions": [[240, 520, 290, 570]],
    "ocr": true,
    "barcode": true,
    "ai": false
  }
}
```

Coordinates are reference **preview** pixels; rectangles are `[x0,y0,x1,y1]`. Config bounds are enforced by Pydantic; rectangle/page extent errors are reported. The queue returns 429 when full. Unsupported inputs return 422, missing records 404, unfinished report requests 409. Job state is `queued`, `running`, `complete` or `error`, separate from measurement status `PASS`, `REVIEW REQUIRED`, `FAIL` or `INCONCLUSIVE`.
