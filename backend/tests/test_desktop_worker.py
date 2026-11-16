"""Offline black-box coverage of the worker embedded in the Mac download."""

from contextlib import contextmanager
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import select
import socket
import subprocess
import sys
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest

from presscheck.desktop_worker import http_report, report_html, save_preflight

REPO = Path(__file__).resolve().parents[2]


def source(body='<rect id="art" width="698.5" height="952.5" fill="#ee7621"/>'):
    return ('<svg xmlns="http://www.w3.org/2000/svg" version="1.1" overflow="hidden" '
            'width="69.85mm" height="95.25mm" viewBox="0 0 698.5 952.5">' + body + '</svg>').encode()


def job():
    return {"schema_version": 1, "workflow": "trading_card", "name": "Custom / unverified",
            "trim_width_mm": 63.5, "trim_height_mm": 88.9, "bleed_mm": 3.175,
            "safe_mm": 3.175, "bleed_intent": "full_bleed", "color_policy": "review_rgb",
            "supplier_confirmed": False, "placement": "fill_bleed"}


def process_env():
    # Tests do not forward provider secrets, proxy settings or developer Python configuration.
    return {"PYTHONPATH": str(REPO / "backend"), "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "en_US.UTF-8", "PYTHONNOUSERSITE": "1"}


def cli(arguments):
    return subprocess.run([sys.executable, "-m", "presscheck.desktop_worker", *arguments],
                          cwd=REPO, env=process_env(), capture_output=True, timeout=45)


@contextmanager
def local_server(tmp_path, read_timeout=15):
    web, data = tmp_path / "web", tmp_path / "data"
    (web / "production").mkdir(parents=True, exist_ok=True)
    (web / "production" / "index.html").write_text('<!doctype html><title>Local production workspace</title>')
    (web / "asset.txt").write_text("bundled asset")
    outside = tmp_path / "outside.txt"
    outside.write_text("must not be served")
    if not (web / "escape.txt").exists(): (web / "escape.txt").symlink_to(outside)
    # Short timeout is used only by the resource-limit regression test.
    entry = f"from presscheck import desktop_worker; desktop_worker.READ_TIMEOUT_SECONDS={read_timeout!r}; desktop_worker.main()"
    process = subprocess.Popen([sys.executable, "-c", entry, "serve", "--data", str(data), "--web", str(web)],
                               cwd=REPO, env=process_env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        readable, _, _ = select.select([process.stdout], [], [], 10)
        if not readable:
            pytest.fail("The isolated desktop server did not start in time")
        line = process.stdout.readline()
        if not line:
            pytest.fail("The isolated desktop server exited before startup")
        startup = json.loads(line)
        parsed = urlsplit(startup["url"])
        token = parse_qs(parsed.query)["token"][0]
        with httpx.Client(base_url=f"http://127.0.0.1:{parsed.port}", trust_env=False, timeout=10) as client:
            yield client, {"Authorization": "Bearer " + token}, data, parsed.port
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)


def upload(client, headers, content=None, config=None, filename="Artwork.svg"):
    return client.post("/api/preflights", headers=headers,
                       files={"file": (filename, content if content is not None else source(), "image/svg+xml"),
                              "job": (None, json.dumps(config if config is not None else job()))})


def test_cli_receipt_matches_native_contract_and_keeps_source(tmp_path):
    input_path, job_path = tmp_path / "Artwork.svg", tmp_path / "job.json"
    input_path.write_bytes(source())
    job_path.write_text(json.dumps(job()))
    output = tmp_path / "report"
    output.mkdir()  # The native client creates an empty output folder in advance.
    result = cli(["preflight", "--input", str(input_path), "--job", str(job_path), "--output", str(output)])
    assert result.returncode == 0, result.stderr.decode()
    receipt = json.loads(result.stdout)
    report_path = Path(receipt["report_path"])
    assert report_path == (output / "report.json").resolve()
    report = json.loads(report_path.read_text())
    assert report["id"] == receipt["id"]
    assert report["source"]["name"] == "Artwork.svg"
    assert report["source"]["sha256"] == hashlib.sha256(source()).hexdigest()
    assert report["report_url"] == "report.html"
    assert report["original_url"] == "original.svg"
    assert (output / report["report_url"]).is_file()
    assert (output / "original.svg").read_bytes() == input_path.read_bytes() == source()
    assert json.loads((output / "job.json").read_text()) == report["job"]
    assert (output / "preview.png").read_bytes().startswith(b"\x89PNG")
    assert report_path.stat().st_size < 4*1024*1024  # Native report-response bound.
    assert report["status"] == "incomplete"
    assert all((output / name).stat().st_mode & 0o077 == 0 for name in ("original.svg", "job.json", "report.json", "report.html"))


@pytest.mark.parametrize("filename", ["job.json", "report.html", "preview.png", "original.svg", "report.json", "notes.txt"])
def test_existing_output_is_never_overwritten(tmp_path, filename):
    output = tmp_path / "report"
    output.mkdir()
    previous = b"previous user file"
    (output / filename).write_bytes(previous)
    with pytest.raises(ValueError, match="previous reports are preserved"):
        save_preflight(source(), job(), output)
    assert list(output.iterdir()) == [output / filename]
    assert (output / filename).read_bytes() == previous


def test_output_symlink_is_rejected(tmp_path):
    actual, linked = tmp_path / "actual", tmp_path / "linked"
    actual.mkdir()
    linked.symlink_to(actual, target_is_directory=True)
    with pytest.raises(ValueError, match="symbolic-link"):
        save_preflight(source(), job(), linked)
    assert not list(actual.iterdir())


def test_html_escapes_source_job_findings_and_never_embeds_svg(tmp_path):
    config = job()
    config["name"] = '<script>alert("job")</script>'
    report = save_preflight(source(), config, tmp_path / "report", '<script>source</script>.svg')
    report["findings"][0]["message"] = '<img src=x onerror="alert(1)">'
    report["findings"][0]["object_ids"] = ['<script>object</script>']
    report["limitations"].append('<iframe src="https://example.invalid"></iframe>')
    rendered = report_html(report)
    assert "<script>" not in rendered
    assert "<svg" not in rendered
    assert "<iframe" not in rendered
    assert '<img src=x' not in rendered
    assert "&lt;script&gt;" in rendered
    assert "Content-Security-Policy" in rendered
    assert "default-src 'none'" in rendered


def test_http_report_url_normalization_does_not_change_saved_local_report():
    original = {"report_url": "report.html", "original_url": "original.svg", "preview_url": "preview.png"}
    result = http_report(original, "a"*32, True)
    assert result["report_url"] == "/api/preflights/" + "a"*32 + "/report.json"
    assert result["preview_url"].startswith("/api/preflights/")
    assert original["report_url"] == "report.html"
    assert "preview_url" not in http_report(original, "a"*32, False)


def test_local_http_token_host_static_boundary_and_attachment_contract(tmp_path):
    with local_server(tmp_path) as (client, authorized, data, port):
        assert client.get("/api/health").status_code == 401
        assert client.get("/api/health?token=not-an-authorization-header").status_code == 401
        assert client.get("/api/health", headers={"Authorization": "Bearer invalid"}).status_code == 401
        assert client.get("/api/health", headers={**authorized, "Host": "attacker.invalid"}).status_code == 403
        assert client.get("/api/health", headers=authorized).json()["mode"] == "bundled-local"
        assert client.get("/production/").status_code == 200
        assert client.get("/asset.txt").text == "bundled asset"
        assert client.get("/escape.txt").status_code == 404
        assert upload(client, {}).status_code == 401
        content = source()
        response = upload(client, authorized, content, filename='Card & "production".svg')
        assert response.status_code == 201, response.text
        report = response.json()
        stored = data / "preflights" / report["id"]
        assert stored.is_dir()
        assert (stored / "original.svg").read_bytes() == content
        assert report["source"]["sha256"] == hashlib.sha256(content).hexdigest()
        assert client.get(f"/api/preflights/{report['id']}", headers=authorized).json() == report
        downloaded = client.get(report["report_url"], headers=authorized)
        assert downloaded.json() == report
        assert "attachment" in downloaded.headers["content-disposition"]
        original = client.get(report["original_url"], headers=authorized)
        assert original.content == content
        assert original.headers["content-type"] == "application/octet-stream"
        assert "attachment" in original.headers["content-disposition"]
        assert original.headers["x-content-type-options"] == "nosniff"
        assert original.headers["referrer-policy"] == "no-referrer"
        assert client.get(report["preview_url"], headers=authorized).content.startswith(b"\x89PNG")
        assert client.get(report["original_url"]).status_code == 401
        local = json.loads((stored / "report.json").read_text())
        assert local["report_url"] == "report.html"
        assert (stored / "report.html").is_file()


def test_local_http_rejects_malformed_and_external_content_without_fetching(tmp_path):
    with local_server(tmp_path) as (client, authorized, data, port):
        malformed = client.post("/api/preflights", headers={**authorized, "Content-Type": "application/json"}, content=b"{}")
        assert malformed.status_code == 422
        assert upload(client, authorized, content=b"not an SVG").status_code == 422
        external = source(f'<image id="linked" width="100" height="100" href="http://127.0.0.1:{port}/must-never-fetch.svg"/>')
        response = upload(client, authorized, external)
        assert response.status_code == 201
        report = response.json()
        assert report["status"] == "fail"
        assert "preview_url" not in report
        assert any(f["rule_id"] == "svg.external_reference" and f["status"] == "fail" for f in report["findings"])
        assert client.get(report["original_url"], headers=authorized).content == external
        assert client.get(f"/api/preflights/{report['id']}/preview.png", headers=authorized).status_code == 404
        assert client.post("/api/structures", headers=authorized, content=b"not json").status_code == 422


def test_no_source_resource_is_ever_opened_by_the_checker(tmp_path, monkeypatch):
    import urllib.request
    def forbidden(*args, **kwargs):
        pytest.fail("The source attempted a network fetch")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    content = source('<image width="100" height="100" href="https://example.invalid/private.svg"/>')
    report = save_preflight(content, job(), tmp_path / "report")
    assert report["status"] == "fail"
    assert "preview_url" not in report
    assert (tmp_path / "report" / "original.svg").read_bytes() == content


def test_incomplete_upload_times_out_and_releases_worker_capacity(tmp_path):
    with local_server(tmp_path, read_timeout=.25) as (client, authorized, data, port):
        with socket.create_connection(("127.0.0.1", port), timeout=3) as connection:
            header = (f"POST /api/preflights HTTP/1.0\r\nHost: 127.0.0.1:{port}\r\n"
                      f"Authorization: {authorized['Authorization']}\r\nContent-Length: 500\r\n"
                      "Content-Type: multipart/form-data; boundary=x\r\n\r\nx")
            connection.sendall(header.encode())
            response = connection.recv(4096)
            assert b"408" in response.split(b"\r\n", 1)[0]
        assert upload(client, authorized).status_code == 201


def test_upload_size_headers_are_rejected_before_reading_a_body(tmp_path):
    with local_server(tmp_path) as (client, authorized, data, port):
        for declared, expected in ((str(50*1024*1024), 413), ("-1", 413), ("invalid", 400)):
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
            try:
                connection.putrequest("POST", "/api/preflights")
                connection.putheader("Authorization", authorized["Authorization"])
                connection.putheader("Content-Length", declared)
                connection.endheaders()
                assert connection.getresponse().status == expected
            finally:
                connection.close()
        assert not list(data.iterdir())


def test_actual_native_production_svg_cli(tmp_path):
    native_svg = os.environ.get("PRESSCHECK_NATIVE_SVG")
    native_job = os.environ.get("PRESSCHECK_NATIVE_JOB")
    if not native_svg or not native_job:
        pytest.skip("Set PRESSCHECK_NATIVE_SVG and PRESSCHECK_NATIVE_JOB to run native export integration")
    input_path, job_path = Path(native_svg), Path(native_job)
    source_hash = hashlib.sha256(input_path.read_bytes()).hexdigest()
    job_bytes = job_path.read_bytes()
    output = tmp_path / "native-report"
    result = cli(["preflight", "--input", str(input_path), "--job", str(job_path), "--output", str(output)])
    assert result.returncode == 0, result.stderr.decode()
    receipt = json.loads(result.stdout)
    assert Path(receipt["report_path"]) == (output / "report.json").resolve()
    report = json.loads(Path(receipt["report_path"]).read_text())
    assert report["id"] == receipt["id"]
    assert report["source"]["name"] == input_path.name
    assert report["source"]["sha256"] == source_hash
    assert report["source"]["vector_objects"] == 2199
    assert report["source"]["has_raster"] is False
    assert report["source"]["width_mm"] == pytest.approx(69.85)
    assert report["source"]["height_mm"] == pytest.approx(95.25)
    assert len(report["source"]["object_bounds_mm"]) == 2199
    assert all(math.isfinite(v) for box in report["source"]["object_bounds_mm"].values() for v in box)
    assert report["status"] == "incomplete"
    states = {f["rule_id"]: f["status"] for f in report["findings"]}
    assert states["canvas.trim_bleed"] == "pass"
    assert states["job.supplier"] == "requires_spec"
    assert states["svg.effects_bounds"] == "unsupported"
    assert report["report_url"] == "report.html"
    assert (output / "preview.png").read_bytes().startswith(b"\x89PNG")
    assert hashlib.sha256((output / "original.svg").read_bytes()).hexdigest() == source_hash
    assert hashlib.sha256(input_path.read_bytes()).hexdigest() == source_hash
    assert job_path.read_bytes() == job_bytes
    assert Path(receipt["report_path"]).stat().st_size < 4*1024*1024


def profile_store():
    return {"schema_version": 1, "profiles": [{"id": "printer-1", "name": "Verified fixture profile", "job": {**job(), "profile_name": "Local printer", "spec_source": "Manual test specification", "printer_notes": "Stock and grain need a physical proof"}}]}


def test_profiles_persist_across_new_server_ports_and_tokens(tmp_path):
    value = profile_store()
    value["profiles"][0]["job"]["supplier_confirmed"] = True
    with local_server(tmp_path) as (client, authorized, data, first_port):
        assert client.get("/api/profiles").status_code == 401
        assert client.put("/api/profiles", json=value).status_code == 401
        assert client.get("/api/profiles", headers=authorized).json() == {"schema_version": 1, "profiles": []}
        response = client.put("/api/profiles", headers=authorized, json=value)
        assert response.status_code == 200, response.text
        saved = response.json()
        assert saved["profiles"][0]["job"]["trim_width_mm"] == 63.5
        assert saved["profiles"][0]["job"]["placement"] == "fill_bleed"
        assert saved["profiles"][0]["job"]["supplier_confirmed"] is False
        stored = data / "printer-profiles.json"
        assert stored.stat().st_mode & 0o077 == 0
        before = stored.read_bytes()
        old_token = authorized.copy()
        assert client.get("/printer-profiles.json").status_code == 404
    with local_server(tmp_path) as (client, authorized, data, second_port):
        assert authorized != old_token
        assert client.get("/api/profiles", headers=old_token).status_code == 401
        assert client.get("/api/profiles", headers=authorized).json() == saved
        assert stored.read_bytes() == before
        assert client.put("/api/profiles", headers=authorized, json={"schema_version": 1, "profiles": []}).status_code == 200
        assert client.get("/api/profiles", headers=authorized).json()["profiles"] == []


def test_profile_validation_size_and_error_paths_preserve_saved_profiles(tmp_path):
    with local_server(tmp_path) as (client, authorized, data, port):
        value = profile_store()
        assert client.put("/api/profiles", headers=authorized, json=value).status_code == 200
        original = (data / "printer-profiles.json").read_bytes()
        invalid = [[], {"schema_version": True, "profiles": []}, {"schema_version": 2, "profiles": []},
                   {"schema_version": 1, "profiles": [{}]*101},
                   {"schema_version": 1, "profiles": value["profiles"]*2}]
        for change in ({"trim_width_mm": -1}, {"trim_width_mm": "63.5"}, {"bleed_mm": float('nan')},
                       {"expected_roles": ["unknown"]}, {"expected_roles": ["cut", "cut"]},
                       {"placement": "actual_size"}, {"api_key": "must-not-persist"}, {"printer_notes": "x"*10001}):
            invalid.append({"schema_version": 1, "profiles": [{**value["profiles"][0], "job": {**job(), **change}}]})
        for content in invalid:
            response = client.put("/api/profiles", headers={**authorized, "Content-Type": "application/json"}, content=json.dumps(content))
            assert response.status_code == 422, response.text
            assert (data / "printer-profiles.json").read_bytes() == original
        assert client.put("/api/profiles", headers={**authorized, "Content-Type": "application/json"}, content=b"not JSON").status_code == 422
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            connection.putrequest("PUT", "/api/profiles")
            connection.putheader("Authorization", authorized["Authorization"])
            connection.putheader("Content-Length", str(1024*1024+1))
            connection.endheaders()
            assert connection.getresponse().status == 413
        finally: connection.close()
        assert (data / "printer-profiles.json").read_bytes() == original
        (data / "printer-profiles.json").write_text("corrupt saved data")
        assert client.get("/api/profiles", headers=authorized).status_code == 422
        assert (data / "printer-profiles.json").read_text() == "corrupt saved data"


def test_atomic_profile_failure_does_not_replace_previous_file(tmp_path, monkeypatch):
    from presscheck.desktop_worker import write_profiles
    path = tmp_path / "printer-profiles.json"
    path.write_text("previous data")
    def unavailable(*args): raise OSError("Simulated disk error")
    monkeypatch.setattr(os, "replace", unavailable)
    with pytest.raises(OSError): write_profiles(path, profile_store())
    assert path.read_text() == "previous data"
    assert list(tmp_path.iterdir()) == [path]
