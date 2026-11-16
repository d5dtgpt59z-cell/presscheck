"""Local, ICC-managed vector production export with a separate qpdf audit.

The supported PDF/X-4 checks are intentionally narrower than full ISO
certification. No network service, printer profile fallback or provider is used.
"""
import hashlib
import html
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit
import uuid

from PIL import ImageCms

from .pdfx_audit import audit_pdf
from .svg_preflight import PreflightJob

MAX_ICC_BYTES = 16 * 1024 * 1024
MAX_PDF_BYTES = 40 * 1024 * 1024
MAX_JOB_BYTES = 32 * 1024
INTENTS = {"perceptual": 0, "relative_colorimetric": 1,
           "saturation": 2, "absolute_colorimetric": 3}
PDF_KEYS = {"schema_version", "target", "icc_profile", "output_condition_identifier",
            "output_condition", "registry_name", "rendering_intent",
            "black_point_compensation", "black_policy", "text_mode"}


def _text(value, name, limit, *, empty=True, multiline=False):
    if not isinstance(value, str) or len(value) > limit or any((ord(c) < 32 and not (multiline and c in "\n\t")) or ord(c) == 127 for c in value):
        raise ValueError(f"Invalid PDF {name}")
    if not empty and not value.strip():
        raise ValueError(f"PDF {name} is required")
    return value


def validate_pdf_settings(value, require_profile=False):
    """Validate portable profile metadata, never paths, secrets or ICC bytes.

    Profile setup may precede ICC selection; export uses require_profile=True.
    """
    if not isinstance(value, dict) or set(value) - PDF_KEYS:
        raise ValueError("PDF settings contain unsupported fields")
    if value.get("schema_version", 1) != 1 or isinstance(value.get("schema_version"), bool):
        raise ValueError("Unsupported PDF settings schema")
    result = {"schema_version": 1, "target": value.get("target", "cmyk_pdf"),
              "output_condition_identifier": value.get("output_condition_identifier", ""),
              "output_condition": value.get("output_condition", ""),
              "registry_name": value.get("registry_name", ""),
              "rendering_intent": value.get("rendering_intent", "relative_colorimetric"),
              "black_point_compensation": value.get("black_point_compensation", False),
              "black_policy": value.get("black_policy", "icc"),
              "text_mode": value.get("text_mode", "outlines")}
    if result["target"] not in ("cmyk_pdf", "pdfx4"):
        raise ValueError("Unsupported PDF production target")
    if result["rendering_intent"] not in INTENTS:
        raise ValueError("Unsupported PDF rendering intent")
    if type(result["black_point_compensation"]) is not bool:
        raise ValueError("PDF black-point compensation must be boolean")
    if result["black_policy"] != "icc" or result["text_mode"] != "outlines":
        raise ValueError("This export supports ICC black generation and outlined text only")
    for name in ("output_condition_identifier", "output_condition", "registry_name"):
        result[name] = _text(result[name], name, 128 if name == "output_condition_identifier" else 500,
                             empty=name != "output_condition_identifier" or not require_profile)
    registry = result["registry_name"]
    if registry:
        try:
            parsed = urlsplit(registry)
            if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
                    or parsed.username is not None or parsed.password is not None
                    or "?" in registry or "#" in registry or "\\" in registry or any(c.isspace() for c in registry)):
                raise ValueError()
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("Registry name must be an HTTP(S) URL without credentials, query or fragment") from exc
    descriptor = value.get("icc_profile")
    if descriptor is not None:
        if not isinstance(descriptor, dict) or set(descriptor) != {"sha256", "name"}:
            raise ValueError("ICC metadata must contain only name and SHA-256")
        digest = descriptor["sha256"]
        if not isinstance(digest, str) or re.fullmatch(r"[0-9a-fA-F]{64}", digest) is None:
            raise ValueError("Invalid ICC SHA-256")
        name = _text(descriptor["name"], "ICC name", 256, empty=False)
        if name in (".", "..") or name.startswith(("/", "\\", "~")) or re.match(r"^(?:[A-Za-z]:|\.\.?)[\\/]", name) or "://" in name:
            raise ValueError("ICC metadata must contain a descriptive name, without an absolute path or URL")
        result["icc_profile"] = {"name": name, "sha256": digest}
    elif require_profile:
        raise ValueError("Select an actual CMYK printer ICC profile before exporting")
    return result


def validate_icc(data, descriptor, rendering_intent="relative_colorimetric", black_point_compensation=False):
    if not isinstance(data, bytes) or not 132 <= len(data) <= MAX_ICC_BYTES:
        raise ValueError("ICC profile is missing or exceeds the 16 MiB limit")
    digest = hashlib.sha256(data).hexdigest()
    if digest != descriptor["sha256"].lower():
        raise ValueError("ICC profile SHA-256 differs from the selected printer profile")
    if (int.from_bytes(data[:4], "big") != len(data) or data[36:40] != b"acsp"
            or data[8] not in (2, 4) or data[12:16] != b"prtr" or data[16:20] != b"CMYK"
            or data[20:24] not in (b"XYZ ", b"Lab ")):
        raise ValueError("Use a valid ICC v2/v4 CMYK output/printer profile")
    count = int.from_bytes(data[128:132], "big")
    table_end = 132 + count * 12
    if not count or count > 4096 or table_end > len(data):
        raise ValueError("Invalid ICC tag table")
    for start in range(132, table_end, 12):
        offset = int.from_bytes(data[start+4:start+8], "big")
        size = int.from_bytes(data[start+8:start+12], "big")
        if size < 8 or offset < table_end or offset % 4 or offset + size > len(data):
            raise ValueError("ICC tag lies outside the profile or is misaligned")
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(data))
        if profile.profile.device_class != "prtr" or profile.profile.xcolor_space != "CMYK":
            raise ValueError("ICC profile class or channels differ from its header")
        flags = ImageCms.Flags.BLACKPOINTCOMPENSATION if black_point_compensation else 0
        ImageCms.buildTransform(ImageCms.createProfile("sRGB"), profile, "RGB", "CMYK",
                                renderingIntent=INTENTS[rendering_intent], flags=flags)
    except Exception as exc:
        raise ValueError("LittleCMS could not validate the selected CMYK output profile") from exc
    return digest


def _bounded_file(path, limit, label):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > limit:
        raise ValueError(f"{label} is missing, not a file or exceeds its size limit")
    with path.open("rb") as handle:
        data = handle.read(limit+1)
    if len(data) > limit:
        raise ValueError(f"{label} exceeds its size limit")
    return data


def _environment(workspace):
    return {"HOME": str(workspace), "TMPDIR": str(workspace), "PATH": "/usr/bin:/bin",
            "LANG": "C", "LC_ALL": "C"}


def _terminate_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=.2)
    except subprocess.TimeoutExpired:
        pass
    except ProcessLookupError:
        pass
    # A child can outlive its process-group leader; do not mistake leader exit
    # for cleanup of the whole converter group.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=.3)


def _run(argv, workspace, timeout):
    # Native cancellation terminates the worker after one second. Explicitly
    # reap the separately grouped converter first, including on SIGTERM.
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("Production conversion must run in its dedicated worker process")
    log = tempfile.TemporaryFile(dir=workspace)
    try:
        process = subprocess.Popen(argv, cwd=workspace, env=_environment(workspace),
                                   stdin=subprocess.DEVNULL, stdout=log,
                                   stderr=subprocess.STDOUT, start_new_session=True)
    except BaseException:
        log.close()
        raise
    previous = {}
    def cancelled(signum, frame):
        _terminate_group(process)
        raise SystemExit(128 + signum)
    for name in (signal.SIGTERM, signal.SIGINT):
        previous[name] = signal.signal(name, cancelled)
    try:
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if os.fstat(log.fileno()).st_size > 2*1024*1024:
                raise ValueError("Ghostscript diagnostic output exceeded its limit")
            if time.monotonic() > deadline:
                raise ValueError("Ghostscript conversion exceeded the time limit")
            try:
                process.wait(timeout=.05)
            except subprocess.TimeoutExpired:
                pass
        log.seek(0)
        output = log.read(2*1024*1024 + 1)
        if len(output) > 2*1024*1024:
            raise ValueError("Ghostscript diagnostic output exceeded its limit")
        if process.returncode:
            # Fixed stage filenames ensure diagnostics never expose source paths.
            raise ValueError("Ghostscript conversion failed: " + output.decode("utf-8", "replace")[-2000:])
        return output.decode("utf-8", "replace")
    finally:
        _terminate_group(process)
        log.close()
        for name, handler in previous.items():
            signal.signal(name, handler)


def _ghostscript(explicit=None):
    if explicit is not None:
        candidate = Path(explicit)
    elif getattr(sys, "frozen", False):
        candidate = Path(sys.executable).parent / "ghostscript-gs"
    elif os.environ.get("MAGDOBE_GHOSTSCRIPT"):
        candidate = Path(os.environ["MAGDOBE_GHOSTSCRIPT"])
    else:
        raise ValueError("The bundled Ghostscript production converter is unavailable")
    if not candidate.is_absolute() or not candidate.is_file() or not os.access(candidate, os.X_OK):
        raise ValueError("Ghostscript must be an explicit, executable local file")
    return candidate.resolve()


def _ps_string(value):
    # PDF text strings are encoded rather than interpolated as PostScript code.
    data = value.encode("ascii") if value.isascii() else b"\xfe\xff" + value.encode("utf-16-be")
    return "<" + data.hex() + ">"


def _definition(job):
    settings = job["pdf"]
    version = "/GTS_PDFXVersion (PDF/X-4)" if settings["target"] == "pdfx4" else ""
    return f"""%!
[{version} /Title {_ps_string(job['name'])} /Trapped /False /DOCINFO pdfmark
[/_objdef {{productionICC}} /type /stream /OBJ pdfmark
[{{productionICC}} << /N 4 >> /PUT pdfmark
[{{productionICC}} (destination.icc) (r) file /PUT pdfmark
[/_objdef {{productionIntent}} /type /dict /OBJ pdfmark
[{{productionIntent}} << /Type /OutputIntent /S /GTS_PDFX
 /OutputConditionIdentifier {_ps_string(settings['output_condition_identifier'])}
 /OutputCondition {_ps_string(settings['output_condition'])}
 /RegistryName {_ps_string(settings['registry_name'])}
 /Info {_ps_string(settings['output_condition'] or settings['output_condition_identifier'])}
 /DestOutputProfile {{productionICC}} >> /PUT pdfmark
[{{Catalog}} << /OutputIntents [{{productionIntent}}] >> /PUT pdfmark
"""


def _private_write(path, data):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as handle:
        handle.write(data)


def _html_report(report):
    title = html.escape(report["job"]["name"])
    rows = "".join(f"<tr><td>{html.escape(f['status'])}</td><td>{html.escape(f['message'])}</td></tr>" for f in report["findings"])
    limits = "".join("<li>" + html.escape(value) + "</li>" for value in report["limitations"])
    passed = report["status"] == "pass_supported_checks"
    summary = "Supported checks passed. Supplier approval has not been performed." if passed else "Export failed. No production PDF was published."
    pdf_link = "<a href='production.pdf'>Production PDF</a> · " if passed else ""
    return ("<!doctype html><html lang='en'><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
            "<meta http-equiv='Content-Security-Policy' content=\"default-src 'none'; style-src 'unsafe-inline'\">"
            f"<title>{title} — production PDF checks</title><style>body{{font:16px system-ui;max-width:70rem;margin:3rem auto;padding:1rem;background:#fbf5e9;color:#201f1b}}td{{padding:.6rem;border-bottom:1px solid #ccc}}a{{color:#24479b}}</style>"
            f"<h1>{title}</h1><p>{summary}</p>"
            f"<p>{pdf_link}<a href='report.json'>JSON report</a></p>"
            f"<table><caption>Independent supported-subset checks</caption><tbody>{rows}</tbody></table><h2>Limits</h2><ul>{limits}</ul></html>").encode("utf-8")


def _finalize_pdf(input_path, icc_path, job, output_dir, ghostscript_path=None):
    """Convert a native RGB vector draft; publish only independently audited PDF.

    This entry point belongs in a dedicated CLI process, not a server thread.
    No source file is rewritten and an existing result is never overwritten.
    """
    try:
        encoded_job = json.dumps(job, allow_nan=False, ensure_ascii=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Production job must be finite JSON") from exc
    if len(encoded_job) > MAX_JOB_BYTES:
        raise ValueError("Production job exceeds the 32 KiB limit")
    if not isinstance(job, dict):
        raise ValueError("Production job must be an object")
    validated = PreflightJob.model_validate(job).model_dump()
    validated["pdf"] = validate_pdf_settings(job.get("pdf"), require_profile=True)
    if any(validated.get(key) is None for key in ("trim_width_mm", "trim_height_mm", "bleed_mm")):
        raise ValueError("Explicit trim dimensions and bleed are required for production export")
    # Existing profile extras may contain portable supplier notes, never local paths.
    # Only names/notes/roles/physical settings belong in this receipt.
    allowed = set(PreflightJob.model_fields) | {"pdf", "profile_name", "printer_notes", "spec_source", "placement"}
    if set(validated) - allowed:
        raise ValueError("Production job contains unsupported metadata")
    for key in ("profile_name", "printer_notes", "spec_source"):
        if key in validated:
            _text(validated[key], key, 4096, multiline=key == "printer_notes")
    if validated.get("placement", "fit_trim") not in ("fit_trim", "fill_bleed"):
        raise ValueError("Unsupported production placement")
    settings = validated["pdf"]
    source = _bounded_file(input_path, MAX_PDF_BYTES, "Source PDF")
    if not source.startswith(b"%PDF-"):
        raise ValueError("Production source is not a PDF")
    icc = _bounded_file(icc_path, MAX_ICC_BYTES, "Printer ICC profile")
    icc_sha = validate_icc(icc, settings["icc_profile"], settings["rendering_intent"], settings["black_point_compensation"])
    destination = Path(output_dir)
    if destination.is_symlink():
        raise ValueError("Production output directory must not be a symlink")
    if destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Production output directory must be new or empty; existing files are never overwritten")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    converter = _ghostscript(ghostscript_path)
    with tempfile.TemporaryDirectory(prefix=".production-stage-", dir=destination) as temporary:
        stage = Path(temporary)
        _private_write(stage/"source.pdf", source)
        _private_write(stage/"destination.icc", icc)
        source_audit = audit_pdf(stage/"source.pdf", validated, source=True)
        if source_audit["status"] != "pass_supported_checks":
            raise ValueError("Source PDF is outside the supported vector subset: " + "; ".join(f["message"] for f in source_audit["findings"] if f["status"] == "fail"))
        version = _run([str(converter), "--version"], stage, 10).strip()
        # Version-specific behavior is tested against the bundled release; future
        # versions require retesting instead of silently changing color output.
        if version != "10.08.0":
            raise ValueError("Production export requires the verified Ghostscript 10.08.0 converter")
        _private_write(stage/"definition.ps", _definition(validated).encode("ascii"))
        argv = [str(converter), "-q", "-dBATCH", "-dNOPAUSE", "-dSAFER", "-dPDFSTOPONWARNING", "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.6", "-sColorConversionStrategy=CMYK", "-sBlendConversionStrategy=Simple",
                "-dWriteObjStms=false", "-dWriteXRefStm=false",
                "-sDefaultRGBProfile=%rom%iccprofiles/default_rgb.icc", "-sOutputICCProfile=destination.icc",
                f"-dRenderIntent={INTENTS[settings['rendering_intent']]}",
                f"-dBlackPtComp={int(settings['black_point_compensation'])}", "-dKPreserve=0",
                "-dAutoRotatePages=/None", "-dDownsampleColorImages=false", "-dDownsampleGrayImages=false",
                "-dDownsampleMonoImages=false", "-dPreserveAnnots=false", "-dPreserveMarkedContent=false",
                "-dPDFACompatibilityPolicy=2", "--permit-file-read=destination.icc:source.pdf:definition.ps",
                "--permit-file-write=production.pdf", "-sOutputFile=production.pdf"]
        if settings["target"] == "pdfx4":
            argv.append("-dPDFX=4")
        argv.extend(["definition.ps", "source.pdf"])
        _run(argv, stage, 120)
        result = _bounded_file(stage/"production.pdf", MAX_PDF_BYTES, "Converted PDF")
        output_audit = audit_pdf(stage/"production.pdf", validated, icc)
        if output_audit["status"] != "pass_supported_checks":
            raise ValueError("Converted PDF failed independent supported checks: " + "; ".join(f["message"] for f in output_audit["findings"] if f["status"] == "fail"))
        before, after = source_audit["metrics"], output_audit["metrics"]
        if before["alpha_states"] and not after["alpha_states"]:
            raise ValueError("Conversion lost live vector transparency")
        if _bounded_file(input_path, MAX_PDF_BYTES, "Source PDF") != source or _bounded_file(icc_path, MAX_ICC_BYTES, "Printer ICC profile") != icc:
            raise ValueError("Source PDF or selected ICC changed during conversion; export was cancelled")
        findings = source_audit["findings"] + output_audit["findings"]
        findings += [{"rule_id": "pdf.icc_conversion", "status": "pass", "message": "Ghostscript 10.08.0 converted RGB vectors using the selected output ICC, rendering intent and black-point-compensation setting", "object_ids": []}]
        for index, finding in enumerate(findings, 1):
            finding["id"] = f"F{index:04}"
        report = {"id": uuid.uuid4().hex, "schema_version": 1, "status": "pass_supported_checks",
                  "source": {"name": Path(input_path).name, "sha256": hashlib.sha256(source).hexdigest(), "bytes": len(source)},
                  "output": {"name": "production.pdf", "sha256": hashlib.sha256(result).hexdigest(), "bytes": len(result),
                             "icc_sha256": icc_sha, "source_vector_paths": before["vector_paths"], **after}, "job": validated, "findings": findings,
                  "limitations": ["These are independently implemented supported-subset checks, not external ISO PDF/X certification or a complete PDF/X conformance test.",
                                  "The printer must approve the supplied ICC profile, output condition, stock, finishing and physical proof; no manufacturer approval has been performed.",
                                  "Ink coverage limits, spot inks, foil/white separations, overprint and trapping are not validated by this ICC-only solid-vector export.",
                                  "Text is outlined and remains vector geometry. Keep the original Magdobe project for editable text, objects and layers.",
                                  "Vector-path counts measure PDF paint operations, not persistent editor object identities."],
                  "report_url": "report.html", "pdf_url": "production.pdf"}
        _private_write(stage/"report.json", (json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)+"\n").encode("utf-8"))
        _private_write(stage/"report.html", _html_report(report))
        # Exclusive destination creation also prevents a race from overwriting a
        # file placed in the empty directory while conversion was running.
        published = []
        try:
            for name in ("production.pdf", "report.json", "report.html"):
                _private_write(destination/name, (stage/name).read_bytes())
                published.append(destination/name)
        except BaseException:
            for path in published:
                path.unlink(missing_ok=True)
            raise
    return report


def finalize_pdf(input_path, icc_path, job, output_dir, ghostscript_path=None):
    """Keep an actionable failure report, but never a failed production PDF.

    Output ownership is established before catching validation failures. Existing
    artifacts and unexpected I/O failures remain errors rather than being hidden.
    """
    destination = Path(output_dir)
    if destination.is_symlink() or destination.exists() and (not destination.is_dir() or any(destination.iterdir())):
        raise ValueError("Production output directory must be new or empty; existing files are never overwritten")
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    try:
        return _finalize_pdf(input_path, icc_path, job, destination, ghostscript_path)
    except ValueError as exc:
        if any(destination.iterdir()):
            raise
        safe_job = {"name": "Production PDF export"}
        try:
            encoded = json.dumps(job, allow_nan=False, ensure_ascii=False).encode("utf-8")
            if len(encoded) <= MAX_JOB_BYTES:
                safe_job = PreflightJob.model_validate(job).model_dump()
                allowed = set(PreflightJob.model_fields) | {"pdf", "profile_name", "printer_notes", "spec_source", "placement"}
                safe_job = {key: value for key, value in safe_job.items() if key in allowed}
                if "pdf" in safe_job:
                    safe_job["pdf"] = validate_pdf_settings(safe_job["pdf"])
        except (TypeError, ValueError):
            safe_job = {"name": "Production PDF export"}
        try:
            source = _bounded_file(input_path, MAX_PDF_BYTES, "Source PDF")
        except ValueError:
            source = b""
        report = {"id": uuid.uuid4().hex, "schema_version": 1, "status": "fail",
                  "source": {"name": Path(input_path).name, "sha256": hashlib.sha256(source).hexdigest(), "bytes": len(source)},
                  "job": safe_job, "findings": [{"id": "F0001", "rule_id": "pdf.export_failed", "status": "fail", "message": str(exc)[:2500], "object_ids": []}],
                  "limitations": ["No production PDF was published. Correct the failed check and export again.",
                                  "No external ISO PDF/X certification or manufacturer approval has been performed."],
                  "report_url": "report.html"}
        _private_write(destination/"report.json", (json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)+"\n").encode("utf-8"))
        _private_write(destination/"report.html", _html_report(report))
        return report
