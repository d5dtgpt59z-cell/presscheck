import logging, uuid
from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI, File, UploadFile, HTTPException
from fastapi.responses import FileResponse
from .storage import Session, Asset, Inspection, ROOT
from .models import InspectionRequest
from .ingestion import ingest, MAX_BYTES
from .worker import start
from . import __version__
from .preflight_api import router as preflight_router
from .structure_api import router as structure_router

logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app):
    stop, thread = start()
    yield
    stop.set()
    thread.join(timeout=3)


app = FastAPI(
    title="PRESSCHECK Optical Systems", version=__version__, lifespan=lifespan
)
app.include_router(preflight_router)
app.include_router(structure_router)


@app.get("/api/health")
def health():
    import shutil

    return {
        "status": "online",
        "version": __version__,
        "ocr": bool(shutil.which("tesseract")),
    }


@app.post("/api/assets", status_code=201)
def upload(file: UploadFile = File(...)):
    content = file.file.read(MAX_BYTES + 1)
    try:
        info = ingest(content, file.filename or "artwork", ROOT)
    except Exception as exc:
        raise HTTPException(422, str(exc)[:300])
    with Session() as db:
        db.add(Asset(id=info["id"], info=info))
        db.commit()
    return info


@app.get("/api/assets/{ident}")
def asset(ident: str):
    with Session() as db:
        row = db.get(Asset, ident)
        if not row:
            raise HTTPException(404, "Asset not found")
        return row.info


@app.get("/api/assets/{ident}/pages/{page}")
def preview(ident: str, page: int):
    info = asset(ident)
    if page < 0 or page >= len(info["pages"]):
        raise HTTPException(404, "Page not found")
    return FileResponse(ROOT / ident / f"page-{page}.png", media_type="image/png")


@app.get("/api/assets/{ident}/original")
def original(ident: str):
    info = asset(ident)
    return FileResponse(
        ROOT / ident / "original",
        filename=info["name"],
        media_type="application/octet-stream",
    )


@app.post("/api/inspections", status_code=202)
def create(req: InspectionRequest):
    a = asset(req.reference)
    b = asset(req.candidate)
    if req.reference_page >= len(a["pages"]) or req.candidate_page >= len(b["pages"]):
        raise HTTPException(422, "Page index outside file")
    ident = uuid.uuid4().hex
    with Session() as db:
        if (
            db.query(Inspection)
            .filter(Inspection.state.in_(["queued", "running"]))
            .count()
            >= 10
        ):
            raise HTTPException(429, "Inspection queue full")
        db.add(Inspection(id=ident, request=req.model_dump(), state="queued"))
        db.commit()
    return {"id": ident, "state": "queued"}


@app.get("/api/inspections")
def history():
    with Session() as db:
        return [
            {
                "id": j.id,
                "state": j.state,
                "created": j.created.isoformat(),
                "status": j.result["status"] if j.result else None,
            }
            for j in db.query(Inspection).order_by(Inspection.created.desc()).limit(50)
        ]


@app.get("/api/inspections/{ident}")
def get_job(ident: str):
    with Session() as db:
        row = db.get(Inspection, ident)
        if not row:
            raise HTTPException(404, "Inspection not found")
        return {
            "id": row.id,
            "state": row.state,
            "result": row.result,
            "error": row.error,
            "request": row.request,
        }


@app.get("/api/inspections/{ident}/files/{name}")
def report(ident: str, name: str):
    if name not in (
        "overlay.png",
        "aligned.png",
        "reference.png",
        "heatmap.png",
        "report.json",
        "report.pdf",
    ):
        raise HTTPException(404)
    job = get_job(ident)
    if job["state"] != "complete":
        raise HTTPException(409, "Inspection not complete")
    path = ROOT / "inspections" / ident / name
    return FileResponse(path, filename=f"presscheck-{ident[:8]}-{name}")


@app.get("/api/demos")
def demos():
    import json

    path = Path(__file__).resolve().parents[2] / "dataset" / "manifest.json"
    return json.loads(path.read_text()) if path.exists() else []


@app.post("/api/demos/{slug}")
def load_demo(slug: str, clean: bool = False):
    dataset = Path(__file__).resolve().parents[2] / "dataset"
    demo = next((d for d in demos() if d["id"] == slug), None)
    if not demo:
        raise HTTPException(404, "Demo not found")
    items = []
    for name in (demo["reference"], demo["clean"] if clean else demo["candidate"]):
        info = ingest((dataset / name).read_bytes(), name, ROOT)
        with Session() as db:
            db.add(Asset(id=info["id"], info=info))
            db.commit()
        items.append(info)
    return {"reference": items[0], "candidate": items[1], "config": demo["config"]}
