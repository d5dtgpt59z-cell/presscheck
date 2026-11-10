import time
from pathlib import Path
from fastapi.testclient import TestClient
from presscheck.api import app

D = Path(__file__).resolve().parents[2] / "dataset"


def test_demo_inspection_report_and_history():
    with TestClient(app) as client:
        assert client.get("/api/health").json()["status"] == "online"
        demo = client.post("/api/demos/cards?clean=true").json()
        job = client.post(
            "/api/inspections",
            json={
                "reference": demo["reference"]["id"],
                "candidate": demo["candidate"]["id"],
            },
        )
        assert job.status_code == 202
        ident = job.json()["id"]
        for _ in range(200):
            result = client.get("/api/inspections/" + ident).json()
            if result["state"] in ("complete", "error"):
                break
            time.sleep(0.1)
        assert result["state"] == "complete", result
        assert result["result"]["status"] == "PASS"
        pdf = client.get(f"/api/inspections/{ident}/files/report.pdf")
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
        report = client.get(f"/api/inspections/{ident}/files/report.json").json()
        assert report["files"]["reference"]["sha256"]
        assert any(h["id"] == ident for h in client.get("/api/inspections").json())
        assert client.get(f"/api/inspections/{ident}/files/secret").status_code == 404


def test_upload_errors_pages():
    with TestClient(app) as c:
        assert (
            c.post("/api/assets", files={"file": ("x.png", b"bad")}).status_code == 422
        )
        assert (
            c.post(
                "/api/assets",
                files={"file": ("x.tiff", (D / "cmyk.tiff").read_bytes())},
            ).status_code
            == 201
        )
        a = c.post(
            "/api/assets", files={"file": ("x.pdf", (D / "multipage.pdf").read_bytes())}
        ).json()
        assert len(a["pages"]) == 2
        assert c.get(a["pages"][1]["preview"]).status_code == 200
        assert c.get(f"/api/assets/{a['id']}/pages/2").status_code == 404
        assert (
            c.post(
                "/api/inspections",
                json={"reference": a["id"], "candidate": a["id"], "candidate_page": 50},
            ).status_code
            == 422
        )
        assert c.get("/api/assets/not-there").status_code == 404
