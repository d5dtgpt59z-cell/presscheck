"""Standalone desktop entry point. No Next.js, database or public server required."""
import argparse
from email.parser import BytesParser
from email.policy import default
import hashlib
import html
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import sys
import tempfile
import threading
from urllib.parse import urlsplit

from .svg_preflight import MAX_BYTES, analyze_svg, render_safe_preview

READ_TIMEOUT_SECONDS = 15
MAX_PROFILE_BYTES = 1024 * 1024


def validate_profiles(value):
    """Bounded portable profiles; never persist arbitrary API fields or secrets."""
    from .svg_preflight import PreflightJob, ROLES
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError("Unsupported profile schema version; expected 1")
    profiles = value.get("profiles")
    if not isinstance(profiles, list) or len(profiles) > 100:
        raise ValueError("Keep at most 100 printer profiles")
    seen, result = set(), []
    extra_fields = {"placement", "profile_name", "printer_notes", "spec_source", "pdf"}
    for profile in profiles:
        if not isinstance(profile, dict): raise ValueError("Each printer profile must be an object")
        ident, name, job = profile.get("id"), profile.get("name"), profile.get("job")
        if not isinstance(ident, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", ident) or ident in seen or ident in {"prototype", "constructor", "toString", "valueOf", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable", "toLocaleString"}:
            raise ValueError("Printer profile IDs must be unique safe identifiers")
        if not isinstance(name, str) or not name.strip() or len(name) > 160 or any(ord(c) < 32 for c in name):
            raise ValueError("Give every printer profile a name of at most 160 characters")
        if not isinstance(job, dict) or set(job) - set(PreflightJob.model_fields) - extra_fields:
            raise ValueError("Printer profile contains unsupported job fields")
        checked = PreflightJob.model_validate(job, strict=True).model_dump()
        if len(set(checked["expected_roles"])) != len(checked["expected_roles"]) or any(role not in ROLES for role in checked["expected_roles"]):
            raise ValueError("Printer profiles must use unique recognized production roles")
        for key, maximum in (("profile_name", 160), ("printer_notes", 10000), ("spec_source", 2000)):
            if key in job and (not isinstance(job[key], str) or len(job[key]) > maximum or any(ord(c) < 32 and c not in "\t\r\n" for c in job[key])):
                raise ValueError(f"Invalid {key} in printer profile")
        if "placement" in job and job["placement"] not in ("fit_trim", "fill_bleed"):
            raise ValueError("Unsupported production placement")
        checked.update({key: job[key] for key in extra_fields if key in job})
        if "pdf" in job:
            if job["pdf"] is None: checked.pop("pdf", None)
            else:
                from .production_pdf import validate_pdf_settings
                checked["pdf"] = validate_pdf_settings(job["pdf"], require_profile=False)
        # Approval belongs to one reviewed job, never a reusable printer preset.
        checked["supplier_confirmed"] = False
        seen.add(ident)
        result.append({"id": ident, "name": name, "job": checked})
    result = {"schema_version": 1, "profiles": result}
    if len(json.dumps(result, allow_nan=False, ensure_ascii=False).encode("utf-8")) > MAX_PROFILE_BYTES:
        raise ValueError("Printer profiles exceed the 1 MiB limit")
    return result


def write_profiles(path, value):
    content = json.dumps(validate_profiles(value), allow_nan=False, ensure_ascii=False).encode("utf-8")
    if path.is_symlink(): raise ValueError("Printer profile storage must not be a symbolic link")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".profiles-", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            os.chmod(temporary, 0o600)
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
    return json.loads(content)


def http_report(report, ident, has_preview):
    """Return HTTP attachment URLs without changing the local handoff report."""
    result = dict(report)
    result["report_url"] = f"/api/preflights/{ident}/report.json"
    result["original_url"] = f"/api/preflights/{ident}/original"
    if has_preview:
        result["preview_url"] = f"/api/preflights/{ident}/preview.png"
    else:
        result.pop("preview_url", None)
    return result


def report_html(report):
    """A passive local handoff report: never embed the source SVG or provider output."""
    esc = lambda value: html.escape(str(value), quote=True)
    rows = "".join(
        "<tr><td>" + esc(f["status"]) + "</td><td><strong>" + esc(f["rule_id"])
        + "</strong><p>" + esc(f["message"]) + "</p><small>"
        + esc(", ".join(f.get("object_ids", []))) + "</small></td><td>"
        + esc(json.dumps({k: f[k] for k in ("measured", "expected", "unit") if k in f})) + "</td></tr>"
        for f in report["findings"]
    )
    preview = '<img src="preview.png" alt="Raster preview of the inspected vector source">' if report.get("preview_url") else "<p>A safe preview is unavailable for this source.</p>"
    return """<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; base-uri 'none'">
<title>PressCheck production report</title><style>
body{background:#f3eddf;color:#252623;font:16px/1.55 system-ui;margin:0;padding:32px}main{max-width:1100px;margin:auto}h1{font-size:34px}a{color:#7e351b}img{max-width:100%;max-height:70vh;object-fit:contain}table{border-collapse:collapse;width:100%;overflow-wrap:anywhere}td,th{padding:14px;border:1px solid #bbb2a3;text-align:left;vertical-align:top}p{margin:6px 0}small,code{overflow-wrap:anywhere}section{margin:28px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere}nav{display:flex;gap:20px;flex-wrap:wrap}@media(max-width:600px){body{padding:16px}td,th{padding:8px}h1{font-size:26px}}
</style><main><header><p>BUILT BY MAGNUS · LOCAL PRODUCTION SCREENING</p><h1>PressCheck report</h1><p><strong>""" + esc(report["status"].replace("_", " ")) + "</strong> · " + esc(report["source"]["name"]) + "</p><p>" + esc(report["source"]["vector_objects"]) + " vector objects · SHA-256 <code>" + esc(report["source"]["sha256"]) + "</code></p></header><nav><a href=\"report.json\" download>Download JSON</a><a href=\"original.svg\" download>Preserved SVG source</a><a href=\"job.json\" download>Printer job settings</a></nav><section>" + preview + "</section><section><h2>Findings</h2><table><thead><tr><th>Status</th><th>Check and affected objects</th><th>Measurement</th></tr></thead><tbody>" + rows + "</tbody></table></section><section><h2>Scope and required review</h2><ul>" + "".join("<li>" + esc(v) + "</li>" for v in report["limitations"]) + "</ul></section><section><h2>Job specification</h2><pre>" + esc(json.dumps(report["job"], indent=2)) + "</pre></section></main></html>"


def save_preflight(content, job, folder, filename="artwork.svg"):
    if not isinstance(job, dict) or len(json.dumps(job, allow_nan=False)) > 32000:
        raise ValueError("Enter a valid printer job of no more than 32,000 JSON characters")
    folder = Path(folder)
    if folder.is_symlink():
        raise ValueError("Choose a new output directory; symbolic-link output folders are unsupported")
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(folder.iterdir()):
        raise ValueError("Choose a new output directory; previous reports are preserved")
    report = analyze_svg(content, job)
    report["source"]["name"] = Path(filename.replace("\\", "/")).name[:240]
    preview = render_safe_preview(content, report)
    files = {}
    if preview is not None:
        files["preview.png"] = preview
        report["preview_url"] = "preview.png"
    report["report_url"] = "report.html"
    report["original_url"] = "original.svg"
    files["original.svg"] = content
    files["job.json"] = json.dumps(report["job"], allow_nan=False, indent=2).encode("utf-8")
    files["report.html"] = report_html(report).encode("utf-8")
    # Report is written last. Exclusive creation protects files even if another
    # process adds something after the empty-directory check.
    files["report.json"] = json.dumps(report, allow_nan=False, indent=2).encode("utf-8")
    written = []
    try:
        for name, data in files.items():
            target = folder / name
            descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            written.append(target)
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(data)
    except Exception:
        for target in reversed(written):
            target.unlink(missing_ok=True)
        raise
    return report


def _multipart(content, content_type):
    if not content_type.startswith("multipart/form-data;") or len(content_type) > 400:
        raise ValueError("Use multipart SVG and printer job fields")
    message = BytesParser(policy=default).parsebytes(("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + content)
    if not message.is_multipart(): raise ValueError("Invalid upload format")
    fields = {}
    for part in message.iter_parts():
        name = part.get_param("name", header="content-disposition")
        if name not in ("file", "job") or name in fields: raise ValueError("Supply one SVG and one job")
        value = part.get_payload(decode=True)
        if not isinstance(value, bytes): raise ValueError("Invalid multipart data")
        fields[name] = (value, part.get_filename())
    if set(fields) != {"file", "job"}: raise ValueError("Supply one SVG and one job")
    if len(fields["job"][0]) > 32000 or len(fields["file"][0]) > MAX_BYTES: raise ValueError("Upload exceeds the inspection limit")
    return fields["file"][0], json.loads(fields["job"][0]), fields["file"][1] or "artwork.svg"


def serve(data, web, port=0):
    data = Path(data).resolve(); data.mkdir(parents=True, exist_ok=True)
    web = Path(web).resolve()
    if not (web / "production/index.html").is_file(): raise ValueError("The bundled production workspace is missing. Reinstall Magdobe.")
    token = secrets.token_urlsafe(32)
    slots = threading.BoundedSemaphore(2)
    profiles_lock = threading.Lock()
    profiles_path = data / "printer-profiles.json"

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(READ_TIMEOUT_SECONDS)

        def log_message(self, *_): pass

        def reply(self, status, body, kind="application/json", attachment=None):
            if isinstance(body, dict): body = json.dumps(body, allow_nan=False).encode()
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store" if self.path.startswith("/api/") else "no-cache")
            if attachment: self.send_header("Content-Disposition", 'attachment; filename="' + attachment + '"')
            self.end_headers(); self.wfile.write(body)

        def authorized(self):
            return secrets.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

        def valid_host(self):
            return self.headers.get("Host", "") in {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}

        def do_GET(self):
            if not self.valid_host(): self.reply(403, {"detail": "Invalid local host"}); return
            path = urlsplit(self.path).path
            if path.startswith("/api/"):
                if not self.authorized(): self.reply(401, {"detail": "Reopen the Production Workspace from Magdobe"}); return
                if path == "/api/health": self.reply(200, {"status": "online", "mode": "bundled-local"}); return
                if path == "/api/profiles":
                    try:
                        with profiles_lock:
                            if profiles_path.is_symlink(): raise ValueError("Printer profile storage must not be a symbolic link")
                            if not profiles_path.exists(): result = {"schema_version": 1, "profiles": []}
                            else:
                                if profiles_path.stat().st_size > MAX_PROFILE_BYTES: raise ValueError("Stored printer profiles exceed the 1 MiB limit")
                                result = validate_profiles(json.loads(profiles_path.read_text(encoding="utf-8")))
                        self.reply(200, result)
                    except (ValueError, TypeError, RecursionError): self.reply(422, {"detail": "Saved printer profiles could not be read. Restore a valid JSON export."})
                    except OSError: self.reply(500, {"detail": "Saved printer profiles could not be read from local storage"})
                    return
                match = re.fullmatch(r"/api/preflights/([a-f0-9]{32})(?:/(report\.json|original|preview\.png))?", path)
                if match:
                    ident, name = match.groups(); folder = data / "preflights" / ident
                    names = {None: "report.json", "report.json": "report.json", "original": "original.svg", "preview.png": "preview.png"}
                    target = folder / names[name]
                    if not target.is_file(): self.reply(404, {"detail": "Preflight file unavailable"}); return
                    if name in (None, "report.json"):
                        report = http_report(json.loads(target.read_text()), ident, (folder / "preview.png").is_file())
                        self.reply(200, report, attachment="presscheck-report.json" if name else None); return
                    self.reply(200, target.read_bytes(), "image/png" if name == "preview.png" else "application/json" if name == "report.json" else "application/octet-stream", "original.svg" if name == "original" else "presscheck-report.json" if name == "report.json" else None); return
                self.reply(404, {"detail": "Unknown production endpoint"}); return
            relative = path.lstrip("/") or "production/index.html"
            target = (web / relative).resolve()
            if target.is_dir(): target = target / "index.html"
            if not target.is_relative_to(web) or not target.is_file(): self.reply(404, b"Not found", "text/plain"); return
            kind = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            self.reply(200, target.read_bytes(), kind)

        def do_PUT(self):
            if not self.valid_host() or not self.authorized(): self.reply(401, {"detail": "Reopen the Production Workspace from Magdobe"}); return
            if urlsplit(self.path).path != "/api/profiles": self.reply(404, {"detail": "Unknown production endpoint"}); return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= MAX_PROFILE_BYTES: self.reply(413, {"detail": "Printer profiles exceed the 1 MiB limit"}); return
                if self.headers.get("Content-Type", "").split(";")[0].strip().lower() != "application/json": raise ValueError("Use JSON printer profiles")
                content = self.rfile.read(length)
                if len(content) != length: self.close_connection = True; raise ValueError("Profile upload ended before its declared size")
                value = validate_profiles(json.loads(content))
                with profiles_lock: saved = write_profiles(profiles_path, value)
                self.reply(200, saved)
            except TimeoutError:
                self.close_connection = True
                self.reply(408, {"detail": "Profile upload timed out"})
            except (ValueError, TypeError, RecursionError) as error: self.reply(422, {"detail": str(error)[:500]})
            except OSError: self.reply(500, {"detail": "Could not save printer profiles. Existing profiles were kept."})

        def do_POST(self):
            if not self.valid_host() or not self.authorized(): self.reply(401, {"detail": "Reopen the Production Workspace from Magdobe"}); return
            try:
                length = int(self.headers.get("Content-Length", "-1"))
                if not 0 <= length <= MAX_BYTES + 1024 * 1024: self.reply(413, {"detail": "Upload exceeds the inspection limit"}); return
            except ValueError: self.reply(400, {"detail": "Invalid upload size"}); return
            if not slots.acquire(blocking=False): self.reply(429, {"detail": "An inspection is already running. Try again shortly."}); return
            try:
                content = self.rfile.read(length)
                if len(content) != length:
                    self.close_connection = True
                    self.reply(400, {"detail": "Upload ended before its declared size"}); return
                path = urlsplit(self.path).path
                if path == "/api/preflights":
                    svg, job, name = _multipart(content, self.headers.get("Content-Type", ""))
                    import uuid
                    folder = data / "preflights" / uuid.uuid4().hex
                    report = save_preflight(svg, job, folder, name)
                    # Directory IDs are the source of truth for later attachment paths.
                    folder.rename(folder.parent / report["id"])
                    self.reply(201, http_report(report, report["id"], "preview_url" in report))
                elif path == "/api/structures":
                    specification = json.loads(content)
                    from .structure import generate_structure
                    result = generate_structure(specification)
                    self.reply(200, result)
                else: self.reply(404, {"detail": "Unknown production endpoint"})
            except TimeoutError:
                self.close_connection = True
                self.reply(408, {"detail": "Upload timed out; retry with the complete local file"})
            except (ValueError, TypeError, KeyError, RecursionError) as error: self.reply(422, {"detail": str(error)[:500]})
            except Exception: self.reply(500, {"detail": "The local operation failed. Your original artwork was kept."})
            finally: slots.release()

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/production/?token={token}"}), flush=True)
    try: server.serve_forever()
    finally: server.server_close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    check = sub.add_parser("preflight")
    check.add_argument("--input", type=Path, required=True); check.add_argument("--job", type=Path, required=True); check.add_argument("--output", type=Path, required=True)
    finish = sub.add_parser("finalize-pdf", help="Convert a native vector draft using the selected printer ICC profile")
    finish.add_argument("--input", type=Path, required=True)
    finish.add_argument("--icc", type=Path, required=True)
    finish.add_argument("--job", type=Path, required=True)
    finish.add_argument("--output", type=Path, required=True)
    service = sub.add_parser("serve")
    service.add_argument("--data", type=Path, required=True); service.add_argument("--web", type=Path)
    service.add_argument("--port", type=int, default=0)
    args = parser.parse_args(argv)
    if args.command == "preflight":
        if args.input.stat().st_size > MAX_BYTES or args.job.stat().st_size > 32000: raise ValueError("Artwork or job exceeds the inspection limit")
        report = save_preflight(args.input.read_bytes(), json.loads(args.job.read_text()), args.output, args.input.name)
        print(json.dumps({"id": report["id"], "report_path": str((args.output / "report.json").resolve())}), flush=True)
    elif args.command == "finalize-pdf":
        if args.input.stat().st_size > MAX_BYTES or args.icc.stat().st_size > 16 * 1024 * 1024 or args.job.stat().st_size > 32000:
            raise ValueError("The PDF, ICC profile or job exceeds the production export limit")
        from .production_pdf import finalize_pdf
        report = finalize_pdf(args.input, args.icc, json.loads(args.job.read_text(encoding="utf-8")), args.output)
        receipt = {"id": report["id"], "report_path": str((args.output / "report.json").resolve())}
        if report.get("status") == "pass_supported_checks":
            receipt["pdf_path"] = str((args.output / "production.pdf").resolve())
        print(json.dumps(receipt, allow_nan=False), flush=True)
    else:
        if not 0 <= args.port <= 65535: raise ValueError("Invalid local port")
        web = args.web or Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2])) / "web"
        serve(args.data, web, args.port)


if __name__ == "__main__":
    try: main()
    except Exception as error:
        print(json.dumps({"error": str(error)[:500]}), file=sys.stderr)
        raise SystemExit(1)
