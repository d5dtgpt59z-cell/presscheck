# Validation record

Native local validation on 2026-11-10:

- Backend unit/API tests: **19 passing** (ingestion, ICC, multipage PDF, CMYK upload/persistence, registration/rotation, CIEDE2000, OCR substitution, barcode changes, safe bounds, unavailable checks, reports, history, and optional vision HTTP contract/failure handling).
- Browser tests: **3 passing** against real Next.js/FastAPI services, Chromium 158. Checks cover defective and clean jobs, selection, overlay, zoom, PDF download, refresh recovery, file uploads, PDF page selection, and mobile overflow at 390 pixels.
- Frontend production build: passing with TypeScript validation.
- Dependency audits: `pip-audit` and `npm audit` report no known vulnerabilities on the tested dependency graph.
- Synthetic benchmark: 33 cases; actual results and failure analysis in `BENCHMARKS.md`.
- PDF sample rendered and visually inspected; JSON and preserved sources verified.
- Live local console inspected through the in-app browser.

Docker is not installed on the local Mac. The `containers` GitHub Actions job builds both images and exercises an actual PostgreSQL-backed inspection and PDF export through the Next.js proxy. See GitHub Actions for its observed result. Optional vision calls have contract tests only; no actual remote or local model inference was tested without credentials/runtime.

This record does not claim manufacturing validation, general OCR accuracy, full security certification, or public application hosting. Tests currently emit upstream Starlette/httpx and PyMuPDF SWIG deprecation warnings; those do not invalidate the passing assertions.
