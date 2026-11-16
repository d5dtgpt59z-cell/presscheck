"""Downloadable locally generated carton studies; no uploaded SVG rendering."""
import json
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import ValidationError

from .storage import ROOT
from .structure import generate_structure

router = APIRouter()


def folder_for(ident):
    if not re.fullmatch(r"[a-f0-9]{32}", ident):
        raise HTTPException(404, "Structure not found")
    folder = ROOT / "structures" / ident
    if not (folder / "spec.json").is_file():
        raise HTTPException(404, "Structure not found")
    return folder


@router.post("/api/structures", status_code=201)
def create_structure(specification: dict):
    try:
        result = generate_structure(specification)
    except (ValueError, ValidationError) as exc:
        raise HTTPException(422, str(exc)[:500]) from exc
    folder = ROOT / "structures" / result["id"]
    try:
        folder.mkdir(parents=True)
        (folder / "structure.svg").write_text(result["svg"], encoding="utf-8")
        result["svg_url"] = f"/api/structures/{result['id']}/structure.svg"
        (folder / "spec.json").write_text(json.dumps(result, allow_nan=False), encoding="utf-8")
    except OSError as exc:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        raise HTTPException(500, "Unable to save the prototype") from exc
    return result


@router.get("/api/structures/{ident}/structure.svg")
def download_structure(ident: str):
    return FileResponse(folder_for(ident) / "structure.svg", filename="presscheck-tuck-carton-prototype.svg", media_type="application/octet-stream", headers={"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox"})


@router.get("/api/structures/{ident}/spec.json")
def download_specification(ident: str):
    return FileResponse(folder_for(ident) / "spec.json", filename="presscheck-structure.json", media_type="application/json")
