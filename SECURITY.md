# Security / local deployment scope

PRESSCHECK is intended for one trusted user on localhost. Bind only to loopback, as the dev script and Compose host ports do. Authentication, authorization, tenant isolation and internet upload hosting are not implemented. Database credentials in Compose are local-development defaults and are never suitable for an exposed service.

Sources are opaque UUID-addressed files; client filenames are display metadata only. The public file route allows only known output filenames. Ingestion checks decoded format (PNG, JPEG, TIFF or PDF), rejects encryption, limits originals to 40 MiB, frames/pages to 20, raster frames to 40 million pixels each / 80 million total, and preview edges to 1600 pixels. PDF render dimensions are bounded; previews never execute JavaScript. Originals are preserved, not served as executable web content. OCR subprocesses have a 25-second timeout. The queue is bounded to ten waiting/running jobs. Container API memory is capped at 2 GiB and uses a non-root user with no-new-privileges.

These controls are not a hostile-upload sandbox. HTTP multipart uploads may spool before validation; deploy behind a request-body limit if adapting this to a network service. Native PDF/image parsing is in process with no hard wall-time limit. Isolate codecs in resource-constrained worker processes, add request limits, rate limits, authentication, storage quotas, retention, TLS and access logs before any public deployment. Run one API process only.

Do not upload confidential client artwork to a shared instance. Local files are not encrypted by the app. AI is disabled by default; enabling it transmits both previews and measured findings to the environment-configured endpoint. Artwork and model responses are treated as untrusted text. AI cannot change inspection status, issue commands or access tools. Never embed credentials in endpoint URLs or commit `.env`, runtime data, or customer artwork.

Dependency audit snapshots are in `docs/python-audit.json` and `docs/npm-audit.json`; no known advisories were reported by the local audit on 2026-11-10. This is not a security certification. Re-run audits after installing or upgrading dependencies.

Report vulnerabilities privately to the repository owner through GitHub's private vulnerability reporting when available. Do not post private artwork, exploit payloads containing secrets, or credentials in public issues.
