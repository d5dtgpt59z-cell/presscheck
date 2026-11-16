import base64
import hashlib
import io
import json
from concurrent.futures import ThreadPoolExecutor
import threading
import time

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from presscheck.api import app
from presscheck import preflight_api
from presscheck import svg_preflight
from presscheck.svg_preflight import analyze_svg, parse_svg, render_safe_preview, path_points


def svg(body, attrs='width="69mm" height="94mm" viewBox="0 0 690 940"'):
    return f'<svg xmlns="http://www.w3.org/2000/svg" {attrs}>{body}</svg>'.encode()


def job(**kwargs):
    return {"schema_version": 1, "workflow": "trading_card", "name": "Test custom specification",
            "trim_width_mm": 63, "trim_height_mm": 88, "bleed_mm": 3, "safe_mm": 3,
            "bleed_intent": "full_bleed", "color_policy": "review_rgb", "min_text_pt": 6,
            "min_stroke_mm": .15, "min_raster_ppi": 300, "supplier_confirmed": False, **kwargs}


def finding(report, rule):
    return next(f for f in report["findings"] if f["rule_id"] == rule)


@pytest.mark.parametrize("count", [2199, 8783])
def test_native_geometry_count_and_conservative_bounds(count):
    body = ''.join(f'<path id="p{i}" d="M 10 20 C 20 5 40 10 50 20 L50 40Z" fill="#ec2330"/>' for i in range(count))
    original = svg(f'<g data-production-role="artwork" transform="translate(10 20)">{body}</g>')
    report = analyze_svg(original, job(profile_name="Custom", placement="fill_bleed"))
    assert report["source"]["vector_objects"] == count
    assert report["source"]["element_counts"]["path"] == count
    assert report["source"]["object_bounds_mm"]["p0"] == [2, 2.5, 6, 6]
    assert report["source"]["sha256"] == hashlib.sha256(original).hexdigest()
    assert report["source"]["has_raster"] is False
    assert report["status"] == "incomplete"
    assert report["job"]["placement"] == "fill_bleed"
    assert finding(report, "job.supplier")["status"] == "requires_spec"
    assert finding(report, "canvas.trim_bleed")["status"] == "pass"


def test_nested_viewport_letterbox_and_transform_order():
    original = svg('<g transform="translate(100 200)"><svg x="10" y="20" width="200" height="100" viewBox="0 0 10 10"><rect id="nested" width="10" height="10"/></svg></g>')
    result = analyze_svg(original, job())
    assert result["source"]["object_bounds_mm"]["nested"] == [16, 22, 26, 32]
    stretched = analyze_svg(svg('<svg x="10" y="20" width="200" height="100" viewBox="0 0 10 10" preserveAspectRatio="none"><rect id="stretch" width="10" height="10"/></svg>'), job())
    assert stretched["source"]["object_bounds_mm"]["stretch"] == [1, 2, 21, 12]


def test_printed_type_and_inherited_stroke_fail_real_minimum():
    result = analyze_svg(svg('<g stroke="black" stroke-width="1"><path id="hairline" d="M0 0L100 100"/><text id="tiny" x="30" y="40" font-size="10">Copy</text></g>'), job())
    assert finding(result, "stroke.minimum")["measured"] == pytest.approx(.1)
    assert finding(result, "stroke.minimum")["status"] == "fail"
    assert finding(result, "text.minimum")["status"] == "fail"
    assert finding(result, "text.minimum")["object_ids"] == ["tiny"]
    assert finding(result, "text.safe_bounds")["status"] == "unsupported"
    assert result["status"] == "fail"


def test_nonuniform_stroke_does_not_invent_a_measurement():
    result = analyze_svg(svg('<path transform="scale(1 3)" stroke="black" stroke-width="1" d="M0 0L100 100"/>'), job())
    assert finding(result, "stroke.measurement")["status"] == "unsupported"
    assert not any(f["rule_id"] == "stroke.minimum" for f in result["findings"])


def test_effective_raster_resolution_uses_placed_physical_size():
    buffer = io.BytesIO()
    Image.new("RGB", (300, 150), "blue").save(buffer, format="PNG")
    data = base64.b64encode(buffer.getvalue()).decode()
    # 254 user units at .1mm/unit = one printed inch, with a 2:1 raster.
    result = analyze_svg(svg(f'<image id="photo" width="254" height="127" href="data:image/png;base64,{data}"/>'), job())
    assert result["source"]["has_raster"] is True
    assert finding(result, "raster.effective_ppi")["measured"] == pytest.approx(300)
    assert finding(result, "raster.effective_ppi")["status"] == "pass"
    assert render_safe_preview(svg(f'<image width="254" height="127" href="data:image/png;base64,{data}"/>'), result) is None


@pytest.mark.parametrize("body", [
    '<script>alert(1)</script>', '<rect width="20" height="20" onload="alert(1)"/>',
    '<image width="10" height="10" href="file:///etc/passwd"/>',
    '<path d="M0 0L20 20" fill="url(https://example.invalid/paint.svg#x)"/>',
    '<use href="https://example.invalid/a.svg#x"/>',
])
def test_active_and_external_content_fail_without_a_preview(body):
    content = svg(body)
    result = analyze_svg(content, job())
    assert result["status"] == "fail"
    assert render_safe_preview(content, result) is None


@pytest.mark.parametrize("content", [
    b'<!DOCTYPE svg [<!ENTITY e SYSTEM "file:///etc/passwd">]><svg>&e;</svg>',
    b'<html/>', b'<svg><path></svg>',
    svg('<g>'*101 + '</g>'*101),
])
def test_invalid_or_unsafe_xml_is_rejected(content):
    with pytest.raises(ValueError):
        parse_svg(content)


def test_duplicate_ids_missing_references_and_unknown_effects_are_not_clear():
    result = analyze_svg(svg('<defs><mask id="mask"/></defs><path id="same" d="M0 0L20 20"/><path id="same" d="M0 0L20 20" fill="url(#missing)" mask="url(#mask)"/>'), job())
    assert result["status"] == "fail"
    assert finding(result, "svg.ids")["status"] == "fail"
    assert finding(result, "svg.references")["status"] == "fail"
    assert finding(result, "svg.effects_bounds")["status"] == "unsupported"


def test_roles_cmyk_and_unverified_parameters_are_explicit():
    result = analyze_svg(svg('<g data-production-role="white_ink"><rect width="30" height="40"/></g>'), job(expected_roles=["white_ink", "foil"], color_policy="cmyk"))
    assert "white_ink" in result["source"]["production_roles"]
    assert finding(result, "color.production")["status"] == "unsupported"
    role_findings = [f for f in result["findings"] if f["rule_id"] == "roles.expected"]
    assert [f["status"] for f in role_findings] == ["pass", "fail"]
    empty = analyze_svg(svg('<g data-production-role="foil"/>'), job(expected_roles=["foil"]))
    assert finding(empty, "roles.expected")["status"] == "fail"
    alias = analyze_svg(svg('<g data-production-role="white"><rect width="10" height="10"/></g>'), job(expected_roles=["white_ink"]))
    assert finding(alias, "roles.expected")["status"] == "pass"


def test_unsafe_and_unresolved_sizes_never_pass():
    bad_size = analyze_svg(svg('<rect width="20" height="20"/>', 'width="1024" height="1536" viewBox="0 0 1024 1536"'), job())
    assert finding(bad_size, "job.physical_intent")["status"] == "requires_spec"
    assert finding(bad_size, "canvas.trim_bleed")["status"] == "fail"
    invalid = analyze_svg(svg('<rect width="20" height="20"/>', 'width="100%" height="100%"'), job())
    assert finding(invalid, "svg.viewport")["status"] == "fail"
    missing = analyze_svg(svg('<rect width="20" height="20"/>'), {})
    assert finding(missing, "job.geometry")["status"] == "requires_spec"


def test_safe_preview_is_png_and_original_is_not_mutated():
    content = svg('<defs><linearGradient id="paint"><stop offset="0" stop-color="#f00"/><stop offset="1" stop-color="#00f"/></linearGradient></defs><rect id="rect" width="690" height="940" fill="url(#paint)"/>')
    original = bytes(content)
    report = analyze_svg(content, job())
    preview = render_safe_preview(content, report)
    assert preview.startswith(b"\x89PNG\r\n\x1a\n")
    assert content == original
    with Image.open(io.BytesIO(preview)) as image:
        assert max(image.size) == 1600


def test_concurrent_preview_callers_share_one_renderer_thread(monkeypatch):
    active, largest = 0, 0
    threads = set()
    lock = threading.Lock()
    def backend(content, report):
        nonlocal active, largest
        with lock:
            active += 1
            largest = max(largest, active)
            threads.add(threading.get_ident())
        time.sleep(.015)
        with lock:
            active -= 1
        return content
    monkeypatch.setattr(svg_preflight, "_render_safe_preview", backend)
    payloads = [str(index).encode() for index in range(12)]
    with ThreadPoolExecutor(max_workers=6) as callers:
        results = list(callers.map(lambda data: render_safe_preview(data, {}), payloads))
    assert results == payloads
    assert largest == 1
    assert len(threads) == 1
    assert threading.get_ident() not in threads


def test_bezier_smooth_controls_remain_conservative():
    points = path_points("M0 0 Q10 10 20 0 T40 0 T60 0")
    assert (30, -10) in points
    assert (50, 10) in points
    with pytest.raises(ValueError):
        path_points("L10 10")


def test_excessive_composed_transform_is_incomplete_not_nonfinite():
    body = '<g transform="scale(1000000)">'*10 + '<rect id="huge" width="10" height="10"/>' + '</g>'*10
    report = analyze_svg(svg(body), job())
    assert finding(report, "svg.transform")["status"] == "unsupported"
    json.dumps(report, allow_nan=False)
    tiny = analyze_svg(svg('<rect width="10" height="10"/>', 'width="69mm" height="94mm" viewBox="0 0 1e-100 1e-100"'), job())
    assert finding(tiny, "svg.viewport")["status"] == "fail"
    json.dumps(tiny, allow_nan=False)


def test_external_stylesheet_and_foreign_geometry_are_not_accepted():
    content = b'<?xml-stylesheet href="file:///private/secret.css"?>' + svg('<rect width="10" height="10"/>')
    report = analyze_svg(content, job())
    assert finding(report, "svg.external_reference")["status"] == "fail"
    assert render_safe_preview(content, report) is None
    content = svg('<x:rect xmlns:x="http://www.w3.org/1999/xhtml" id="foreign" width="10" height="10"/>')
    report = analyze_svg(content, job())
    assert finding(report, "svg.foreign_namespace")["status"] == "unsupported"
    assert "foreign" not in report["source"]["object_bounds_mm"]
    assert render_safe_preview(content, report) is None


def test_preflight_api_preserves_original_and_serves_attachments(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight_api, "ROOT", tmp_path)
    content = svg('<rect id="box" width="690" height="940" fill="#fcc"/>')
    with TestClient(app) as client:
        response = client.post("/api/preflights", files={"file": ("../../artwork.svg", content, "image/svg+xml")}, data={"job": json.dumps(job())})
        assert response.status_code == 201, response.text
        report = response.json()
        assert report["source"]["name"] == "artwork.svg"
        assert client.get(f"/api/preflights/{report['id']}").json() == report
        original = client.get(report["original_url"])
        assert original.content == content
        assert "attachment" in original.headers["content-disposition"]
        assert "nosniff" == original.headers["x-content-type-options"]
        assert client.get(report["report_url"]).json() == report
        assert client.get(report["preview_url"]).content.startswith(b"\x89PNG")
        assert client.get("/api/preflights/not-an-id").status_code == 404
        invalid = client.post("/api/preflights", files={"file": ("bad.svg", b"not svg")}, data={"job": "{}"})
        assert invalid.status_code == 422


def test_invalid_jobs_fail_before_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(preflight_api, "ROOT", tmp_path)
    with TestClient(app) as client:
        for config in ('[]', '{"trim_width_mm": -2}', '{"color_policy":"certified_topps"}', '{"unknown":NaN}'):
            response = client.post("/api/preflights", files={"file": ("a.svg", svg(""))}, data={"job": config})
            assert response.status_code == 422
    assert not list(tmp_path.iterdir())
