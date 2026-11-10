# Validation record

Native local validation on 2026-11-10:

- Backend unit/API tests: **19 passing** (ingestion, ICC, multipage PDF, CMYK upload/persistence, registration/rotation, CIEDE2000, OCR substitution, barcode changes, safe bounds, unavailable checks, reports, history, and optional vision HTTP contract/failure handling).
- Browser tests: **3 passing** against real Next.js/FastAPI services, Chromium 158. Checks cover defective and clean jobs, selection, overlay, zoom, PDF download, refresh recovery, file uploads, PDF page selection, and mobile overflow at 390 pixels.
- Frontend production build: passing with TypeScript validation.
- Dependency audits: `pip-audit` and `npm audit` report no known vulnerabilities on the tested dependency graph.
- Synthetic benchmark: 33 cases; actual results and failure analysis in `BENCHMARKS.md`.
- PDF sample rendered and visually inspected; JSON and preserved sources verified.
- Live local console inspected through the in-app browser.

Docker is not installed on the local Mac. The [`containers` GitHub Actions job](https://github.com/d5dtgpt59z-cell/presscheck/actions/runs/42825327532) **passed** for code commit `162630d`: both images built and an actual PostgreSQL-backed clean inspection and PDF export succeeded through the Next.js proxy. Optional vision calls have contract tests only; no actual remote or local model inference was tested without credentials/runtime.

This record does not claim manufacturing validation, general OCR accuracy, full security certification, or public application hosting. Tests currently emit upstream Starlette/httpx and PyMuPDF SWIG deprecation warnings; those do not invalidate the passing assertions.


## GitHub Actions confirmation

[Run 42825327532](https://github.com/d5dtgpt59z-cell/presscheck/actions/runs/42825327532) completed **successfully** for code commit `162630d` on 2026-11-10:

- Linux backend/API suite: 19 passed in 16.58 seconds.
- Production Next.js build and real-service Chromium suite: 3 passed in 15.9 seconds.
- Benchmark: 96.43% precision, 100% recall, 16.67% FPR on the same 33 synthetic pairs. Mean engine time on this shared Linux runner: 1.641 seconds; the Mac mean above is a separate measurement.
- Python and npm vulnerability audits: no known vulnerabilities reported.
- Docker images built; PostgreSQL-backed worker inspection, frontend proxy, and PDF export passed.

Actions reported Node 20 action-runtime deprecation notices for the v4/v5 setup/upload actions; GitHub ran them with Node 24 successfully. These are tooling maintenance notices, not failed application checks.
