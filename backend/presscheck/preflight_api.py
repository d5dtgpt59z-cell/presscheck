"""Local-only SVG preflight storage and attachment delivery."""

import json
from pathlib import Path
import re

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .storage import ROOT
from .svg_preflight import MAX_BYTES, analyze_svg, render_safe_preview

router = APIRouter()


def report_folder(ident):
    if not re.fullmatch(r"[a-f0-9]{32}", ident):
        raise HTTPException(404, "Preflight not found")
    folder = ROOT / "preflights" / ident
    if not (folder / "report.json").is_file():
        raise HTTPException(404, "Preflight not found")
    return folder


@router.post("/api/preflights", status_code=201)
def preflight(file: UploadFile = File(...), job: str = Form(...)):
    if len(job) > 32_000:
        raise HTTPException(422, "Job specification is too large")
    content = file.file.read(MAX_BYTES + 1)
    try:
        config = json.loads(job)
        if not isinstance(config, dict):
            raise ValueError("Job specification must be a JSON object")
        report = analyze_svg(content, config)
    except (ValueError, ValidationError, RecursionError) as exc:
        raise HTTPException(422, str(exc)[:500]) from exc
    report["source"]["name"] = Path((file.filename or "artwork.svg").replace("\\", "/")).name[:240]
    preview = render_safe_preview(content, report)
    if preview:
        report["preview_url"] = f"/api/preflights/{report['id']}/preview.png"
    else:
        report["limitations"].append("No preview was produced: the SVG uses content outside the passive preview subset or rendering was unavailable.")
    folder = ROOT / "preflights" / report["id"]
    try:
        folder.mkdir(parents=True)
        (folder / "original").write_bytes(content)
        if preview:
            (folder / "preview.png").write_bytes(preview)
        temp = folder / "report.tmp"
        temp.write_text(json.dumps(report, allow_nan=False), encoding="utf-8")
        temp.replace(folder / "report.json")
    except OSError as exc:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(500, "Unable to save the preflight; check local storage") from exc
    return report


@router.get("/api/preflights/{ident}")
def get_preflight(ident: str):
    return json.loads((report_folder(ident) / "report.json").read_text(encoding="utf-8"))


@router.get("/api/preflights/{ident}/original")
def preflight_original(ident: str):
    folder = report_folder(ident)
    report = json.loads((folder / "report.json").read_text(encoding="utf-8"))
    return FileResponse(folder / "original", filename=report["source"]["name"], media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.get("/api/preflights/{ident}/report.json")
def preflight_report(ident: str):
    return FileResponse(report_folder(ident) / "report.json", filename="presscheck-preflight.json", media_type="application/json")


@router.get("/api/preflights/{ident}/preview.png")
def preflight_preview(ident: str):
    path = report_folder(ident) / "preview.png"
    if not path.is_file():
        raise HTTPException(404, "A safe preview is unavailable")
    return FileResponse(path, media_type="image/png", headers={"X-Content-Type-Options": "nosniff"})
