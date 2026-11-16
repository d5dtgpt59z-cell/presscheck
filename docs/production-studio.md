# Production Studio

Open `/production/` for SVG preflight, manual printer profiles and a straight-tuck carton study. The existing Master Proof comparison workspace stays at `/` in the normal web application. The installed desktop workspace includes the new preflight and structure engines; the legacy OCR/comparison service is a separate web workflow.

## Use it

1. Choose an SVG (up to 40 MiB). Its original bytes are preserved and never embedded as active SVG.
2. Enter the supplier's trim, bleed, safe margin and applicable limits. Display units can be mm or inches; job data remains mm without rounding. Blank requirements mean **unknown**, not zero or an industry preset.
3. Record the printer/profile name, specification source, material/finishing notes, colour policy and required production roles. Supplier confirmation is your declaration; editing requirements clears it. Saving, loading or importing a reusable profile also clears confirmation; confirm the specific job again.
4. Run preflight. Review failures, missing specifications and unsupported checks alongside measured/expected values and affected object IDs. Download the complete JSON and untouched source.

Previews are passive PNGs fetched with Authorization and displayed through revocable Blob URLs. Original SVG and JSON downloads also use authenticated fetches. Some SVG features prevent a safe preview; the report and source remain available.

## Saved printer profiles

Browser mode explicitly saves profiles in `localStorage`. Desktop mode uses authenticated `GET`/`PUT /api/profiles` backed by `printer-profiles.json` under the server's `--data` directory, so profiles survive new ports/tokens on later launches. Saves validate before atomic replacement, use private `0600` file permissions, and preserve prior data on failure. Invalid saved data gives an explicit error; import a valid export to restore it.

Both modes import/export schema 1 JSON: `{schema_version:1,profiles:[{id,name,job}]}`. Limits are 100 profiles and 1 MiB UTF-8. Known native metadata (`placement`, `profile_name`, `printer_notes`, `spec_source`) survives; arbitrary fields and provider credentials are not accepted as profile data. Review your supplier references/notes before sharing an export.

The desktop launch token is removed from the URL and retained in session storage, with an in-memory fallback. New UI requests send it only in an Authorization header to same-origin `/api/` paths, not in URLs, profile JSON or downloads.

## Structural handoff

The Structure tab generates one closed cut outline, 12 independent crease paths, a glue guide and separate reference dimensions/legend. Download:

- Genuine SVG with typed `data-production-role` groups.
- Native Magdobe `.mvector` version 2: editable anchors/text, ordered role-labelled layers and an embedded physical production job. Reference dimensions use native `legend` because Magdobe has no separate native dimensions enum. Native crease paths are solid blue; the native model has no dashed strokes.
- Handoff JSON with nominal dimensions, caliper, components, drawing canvas, unconfirmed job and limitations.

A PNG is drawn directly from the generator's own validated geometry. “Use for packaging preflight” loads the SVG and the **annotated drawing canvas** dimensions, not finished-carton trim. References are not cutter commands; display colours are not certified spot separations.

W/D/H are nominal fold-line panel sizes, not certified internal capacity. Caliper affects only experimental flap relief. Bend allowance, grain, crease tooling, glue, stack fit, locking bottoms, thumb notches, nesting and machine allowances remain unverified. Obtain a converter-approved dieline and physical stock/fit/fold/glue sample. This is not CF2/DXF production CAD or tooling certification.

Digital artwork checks also do not certify CMYK/ICC separations, ink coverage, trapping, overprint, foil registration or print readiness. Confirm the actual supplier specification and inspect a physical proof.

## Integration

- `POST /api/preflights`: multipart `file` plus JSON string `job`; use returned report/original/preview URLs with authenticated fetches on desktop.
- `GET /api/preflights/{id}` retrieves a stored report.
- `POST /api/structures`: JSON `{kind:"tuck_carton",name,width_mm,depth_mm,height_mm,caliper_mm,glue_flap_mm}`. Response includes `svg`, `project`, `preview_png`, `dimensions`, `components`, `limitations`, `job`, `specification`. Download from this response; desktop needs no structure GET route.
- Desktop profiles: authenticated `GET` and `PUT /api/profiles`, schema above.

Normal Next.js keeps its existing API rewrite. `PRESSCHECK_DESKTOP_EXPORT=1` omits rewrites, enables static export/trailing slashes, and emits `frontend/out/production/index.html` for the bundled loopback server. Installed users do not need a runtime Node/Next server.

## Native CMYK and PDF/X-4 delivery

Magdobe's Production Setup can now import the user's printer CMYK ICC, choose a CMYK PDF or PDF/X-4 target, and pass a native RGB vector draft to the separately bundled converter. The CLI contract is:

```sh
PressCheckWorker finalize-pdf --input draft.pdf --icc printer.icc --job job.json --output new-output-directory
```

The job's optional `pdf` object contains schema version 1, target (`cmyk_pdf` or `pdfx4`), ICC descriptor (`name` and `sha256` only), output condition identifier/description, optional registry URL, rendering intent, black point compensation, `black_policy: "icc"` and `text_mode: "outlines"`. ICC bytes and paths do not enter job/profile JSON. Printer profile import/export and desktop persistence preserve validated PDF metadata. The web Production Studio continues to check SVG; conversion is the native app's dedicated local-worker operation.

`production_pdf.py` validates the actual ICC and source vector subset, runs pinned Ghostscript 10.08.0 with an isolated environment and explicit file permissions, then asks `pdfx_audit.py` to inspect the written result through pikepdf/qpdf. Rendering intent and BPC are tested against independent LittleCMS conversions. The audit covers supported physical boxes, profile identity, PDF/X metadata where requested, process colors, live transparency and vector/no-raster output. PDF paint operations may be reconstructed; they are not persistent native object IDs.

The small JSON receipt names the report and, on success, checked PDF. The output folder retains `report.json`, passive `report.html` and `production.pdf`. Only `pass_supported_checks` permits the native client to replace a destination. Technical failures retain a report without publishing a PDF; pre-existing output folders/files are preserved. No public HTTP conversion endpoint is introduced.

These checks are not full independent ISO certification, ink-limit/spot/trapping validation, a contract proof or manufacturing approval. Automated examples use a clearly identified test profile. Real jobs require the printer's actual profile/specification and proof review.

## Verification

From repository root:

```sh
PYTHONPATH=backend .venv/bin/python -m pytest backend/tests/test_structure.py backend/tests/test_desktop_worker.py backend/tests/test_svg_preflight.py -q
```

From `frontend` (Node 22.13+ for built-in TypeScript stripping in the transport test; checked on Node 24):

```sh
npm run test:production
npx tsc --noEmit --incremental false
npm run build
PRESSCHECK_DESKTOP_EXPORT=1 npm run build
test -f out/production/index.html
```

Coverage includes native geometry/hierarchy order, physical SVG dimensions, closed sample outlines, safe attachments, invalid parameters, atomic-save failure, persistence across server restarts/new tokens, import bounds, unit precision and authorization transport. Native Swift decoding/validation is tested in Magdobe's integration fixture. These checks do not replace supplier approval or a physical proof.

The PDF suite uses `MAGDOBE_GHOSTSCRIPT` and `MAGDOBE_TEST_ICC` for the pinned test engine and test-only output profile. Linux CI builds them from verified source with `scripts/build_test_ghostscript.sh` and fails if any PDF-specific tests skip. Production installs resolve the signed bundled sidecar and never search the user's PATH for a converter.
