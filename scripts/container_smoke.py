"""Exercise actual proxy -> API -> PostgreSQL -> worker -> PDF pipeline."""

import json, time, urllib.request

BASE = "http://127.0.0.1:3000"


def get(path, data=None):
    body = json.dumps(data).encode() if data is not None else None
    req = urllib.request.Request(
        BASE + path, data=body, headers={"Content-Type": "application/json"}
    )
    return json.load(urllib.request.urlopen(req, timeout=20))


demo = get("/api/demos/cards?clean=true", {})
job = get(
    "/api/inspections",
    {"reference": demo["reference"]["id"], "candidate": demo["candidate"]["id"]},
)
for _ in range(120):
    r = get("/api/inspections/" + job["id"])
    if r["state"] in ("complete", "error"):
        break
    time.sleep(1)
assert r["state"] == "complete", r
assert r["result"]["status"] == "PASS", r["result"]["status"]
pdf = urllib.request.urlopen(
    BASE + f"/api/inspections/{job['id']}/files/report.pdf"
).read()
assert pdf.startswith(b"%PDF")
print("PostgreSQL + Docker + Next.js proxy + worker + PDF: PASS")
