"""Offline subset checks, plus opt-in integration with the pinned real converter.

The integration ICC is a fixture, never a printer-approved production default.
Set MAGDOBE_GHOSTSCRIPT and MAGDOBE_TEST_ICC to run the real conversion tests.
"""
import hashlib
import io
import json
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import zlib

import pikepdf
import pytest
from PIL import Image, ImageCms

from presscheck.pdfx_audit import audit_pdf
from presscheck.pdfx_audit import _decoded, _pdf_date
from presscheck.production_pdf import (
    _definition, _run, finalize_pdf, validate_icc, validate_pdf_settings,
)


def job(icc=None, target="pdfx4"):
    value = {"schema_version": 1, "workflow": "trading_card", "name": "Test <script> & vector job",
             "trim_width_mm": 63.5, "trim_height_mm": 88.9, "bleed_mm": 3.175,
             "safe_mm": 3.175, "bleed_intent": "full_bleed", "color_policy": "review_rgb",
             "supplier_confirmed": False, "placement": "fit_trim",
             "pdf": {"schema_version": 1, "target": target,
                     "output_condition_identifier": "TEST_ONLY",
                     "output_condition": "Test fixture; not supplier approved.",
                     "registry_name": "", "rendering_intent": "relative_colorimetric",
                     "black_point_compensation": False, "black_policy": "icc", "text_mode": "outlines"}}
    if icc is not None:
        value["pdf"]["icc_profile"] = {"sha256": hashlib.sha256(icc).hexdigest(), "name": "Fixture / CMYK v2"}
    return value


def make_pdf(path, count=1, *, alpha=False, color="rgb", mutation=None):
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(198, 270))
    page.obj["/BleedBox"] = pikepdf.Array([0, 0, 198, 270])
    page.obj["/TrimBox"] = pikepdf.Array([9, 9, 189, 261])
    page.obj["/Resources"] = pikepdf.Dictionary()
    content = []
    if alpha:
        page.obj["/Group"] = pikepdf.Dictionary(S=pikepdf.Name.Transparency, CS=pikepdf.Name.DeviceRGB)
        page.obj["/Resources"]["/ExtGState"] = pikepdf.Dictionary(Half=pikepdf.Dictionary(ca=.5, CA=.5))
        content.append("/Half gs")
    for i in range(count):
        x, y = 3 + i % 50 * 3.8, 3 + i // 50 * 5.9
        paint = f"{(i % 17 + 1)/18:.6f} {(i % 13 + 1)/14:.6f} {(i % 11 + 1)/12:.6f} rg" if color == "rgb" else ".2 .4 .6 .1 k"
        content.append(f"{paint} {x:.3f} {y:.3f} 1 1 re f")
    page.obj["/Contents"] = pdf.make_stream("\n".join(content).encode())
    if mutation:
        mutation(pdf, page)
    pdf.save(path, min_version="1.3")
    return path.read_bytes()


def failed(audit, text):
    assert audit["status"] == "fail"
    assert any(text.lower() in finding["message"].lower() for finding in audit["findings"] if finding["status"] == "fail"), audit


def test_profile_metadata_is_portable_and_partial_until_export():
    partial = validate_pdf_settings({})
    assert partial["target"] == "cmyk_pdf"
    assert "icc_profile" not in partial
    with pytest.raises(ValueError, match="identifier"):
        validate_pdf_settings({}, require_profile=True)
    settings = job(b"fixture")["pdf"]
    assert validate_pdf_settings(settings, True) == settings
    settings["icc_profile"]["sha256"] = settings["icc_profile"]["sha256"].upper()
    assert validate_pdf_settings(settings, True) == settings


@pytest.mark.parametrize("suffix", ["Z", "Z00'00'", "Z00'00", "+00'00'", "-00'00'"])
def test_pdf_utc_dates_accept_only_equivalent_zero_offsets(suffix):
    assert _pdf_date("D:20261116184248"+suffix) == datetime(2026, 11, 16, 18, 42, 48, tzinfo=timezone.utc)


@pytest.mark.parametrize("suffix", ["Z01'00'", "Z00'01'", "Z24'00'", "Z00'60'", "+24'00'", "-00'60'", "Zjunk"])
def test_pdf_dates_reject_inconsistent_or_out_of_range_offsets(suffix):
    with pytest.raises(ValueError):
        _pdf_date("D:20261116184248"+suffix)


@pytest.mark.parametrize("changes", [
    {"icc_path": "/private/profile.icc"}, {"profile_bytes": "secret"},
    {"target": "pdfx1a"}, {"text_mode": "editable"}, {"black_policy": "rich_black"},
    {"rendering_intent": "auto"}, {"black_point_compensation": 1},
    {"registry_name": "https://user:secret@color.org/"},
    {"registry_name": "https://color.org/?secret=1"},
    {"registry_name": "file:///private/profile.icc"},
    {"icc_profile": {"name": "/private/profile.icc", "sha256": "0"*64}},
    {"icc_profile": {"name": "Profile", "sha256": "0"*64, "path": "/private/profile.icc"}},
])
def test_settings_reject_paths_secrets_and_unimplemented_options(changes):
    settings = job(b"fixture")["pdf"] | changes
    with pytest.raises(ValueError):
        validate_pdf_settings(settings, True)


def test_postscript_definition_encodes_user_text_instead_of_evaluating_it():
    value = job()
    value["name"] = "') (file) /run exec < & café"
    value["pdf"]["output_condition"] = ") deletefile ("
    definition = _definition(value)
    assert value["name"] not in definition
    assert ") deletefile (" not in definition
    assert "destination.icc" in definition
    definition.encode("ascii")


def test_source_2199_paths_physical_boxes_and_transparency_are_inspected(tmp_path):
    path = tmp_path/"complex.pdf"
    make_pdf(path, 2199, alpha=True)
    result = audit_pdf(path, job(), source=True)
    assert result["status"] == "pass_supported_checks", json.dumps(result["findings"], indent=2)
    assert result["metrics"]["vector_paths"] == 2199
    assert result["metrics"]["alpha_states"] > 0
    assert result["metrics"]["color_spaces"] == ["RGB"]


@pytest.mark.parametrize("change,reason", [
    (lambda pdf, page: page.obj.__setitem__("/TrimBox", pikepdf.Array([0, 0, 198, 270])), "TrimBox"),
    (lambda pdf, page: page.obj.__setitem__("/CropBox", pikepdf.Array([0, 0])), "CropBox"),
    (lambda pdf, page: page.obj["/Resources"].__setitem__("/Font", pikepdf.Dictionary(F=pikepdf.Dictionary(Type=pikepdf.Name.Font))), "Font"),
    (lambda pdf, page: pdf.Root.__setitem__("/Names", pikepdf.Dictionary(Anything=pikepdf.Dictionary(JS=pikepdf.String("danger")))), "Interactive"),
    (lambda pdf, page: page.obj.__setitem__("/Contents", pdf.make_stream(b"1 0 0 rg 0 0 10 10 re f 1 cm")), "operands"),
    (lambda pdf, page: page.obj.__setitem__("/Contents", pdf.make_stream(b"1 0 0 rg BT /F 10 Tf (text) Tj ET")), "text"),
])
def test_source_rejects_unsupported_or_misleading_content(tmp_path, change, reason):
    path = tmp_path/"bad.pdf"
    make_pdf(path, mutation=change)
    failed(audit_pdf(path, job(), source=True), reason)


def test_source_no_rasterization_and_recursive_form_color_rules(tmp_path):
    path = tmp_path/"form.pdf"
    def change(pdf, page):
        form = pdf.make_stream(b".1 .2 .3 .4 k 0 0 10 10 re f")
        form["/Type"] = pikepdf.Name.XObject
        form["/Subtype"] = pikepdf.Name.Form
        form["/BBox"] = pikepdf.Array([0, 0, 10, 10])
        page.obj["/Resources"]["/XObject"] = pikepdf.Dictionary(Form=form)
        page.obj["/Contents"] = pdf.make_stream(b"/Form Do")
    make_pdf(path, mutation=change)
    failed(audit_pdf(path, job(), source=True), "preconverted")
    def image(pdf, page):
        raster = pdf.make_stream(b"\0\0\0")
        raster["/Subtype"] = pikepdf.Name.Image
        raster["/Width"], raster["/Height"] = 1, 1
        page.obj["/Resources"]["/XObject"] = pikepdf.Dictionary(Image=raster)
        page.obj["/Contents"] = pdf.make_stream(b"/Image Do")
    make_pdf(path, mutation=image)
    failed(audit_pdf(path, job(), source=True), "Raster")


def test_contents_array_can_share_graphics_stack(tmp_path):
    path = tmp_path/"array.pdf"
    def array(pdf, page):
        page.obj["/Contents"] = pikepdf.Array([pdf.make_stream(b"q 1 0 0 rg"), pdf.make_stream(b"0 0 10 10 re f Q")])
    make_pdf(path, mutation=array)
    assert audit_pdf(path, job(), source=True)["status"] == "pass_supported_checks"


def test_stream_decompression_is_bounded_before_qpdf_content_parsing():
    pdf = pikepdf.Pdf.new()
    stream = pdf.make_stream(zlib.compress(b"0 "*100_000))
    stream["/Filter"] = pikepdf.Name.FlateDecode
    with pytest.raises(ValueError, match="Decoded PDF stream"):
        _decoded(stream, 1024)


def test_aggregate_decoded_budget_and_token_budget_are_enforced(tmp_path, monkeypatch):
    import presscheck.pdfx_audit as auditor
    path = tmp_path/"source.pdf"
    make_pdf(path, 50)
    monkeypatch.setattr(auditor, "MAX_TOTAL_DECODED", 128)
    failed(audit_pdf(path, job(), source=True), "aggregate")
    monkeypatch.setattr(auditor, "MAX_TOTAL_DECODED", 128*1024*1024)
    monkeypatch.setattr(auditor, "MAX_CONTENT_TOKENS", 20)
    def must_not_parse(*args, **kwargs):
        raise AssertionError("qpdf instruction list was allocated before the token gate")
    monkeypatch.setattr(pikepdf, "parse_content_stream", must_not_parse)
    failed(audit_pdf(path, job(), source=True), "content-token")


def test_compressed_object_stream_input_is_explicitly_outside_native_draft_subset(tmp_path):
    source, packed = tmp_path/"source.pdf", tmp_path/"packed.pdf"
    make_pdf(source)
    with pikepdf.open(source) as pdf:
        pdf.save(packed, object_stream_mode=pikepdf.ObjectStreamMode.generate, min_version="1.5")
    failed(audit_pdf(packed, job(), source=True), "object/xref")


def test_existing_outputs_never_overwritten_even_for_invalid_inputs(tmp_path):
    output = tmp_path/"output"
    output.mkdir()
    original = output/"production.pdf"
    original.write_bytes(b"keep original")
    with pytest.raises(ValueError, match="overwritten"):
        finalize_pdf(tmp_path/"missing.pdf", tmp_path/"missing.icc", {}, output)
    assert original.read_bytes() == b"keep original"
    assert list(output.iterdir()) == [original]


def test_failed_validation_retains_safe_report_without_pdf(tmp_path):
    path = tmp_path/"source.pdf"
    source = make_pdf(path)
    icc = tmp_path/"profile.icc"
    icc.write_bytes(b"not an ICC")
    output = tmp_path/"out"
    result = finalize_pdf(path, icc, job(icc.read_bytes()), output)
    assert result["status"] == "fail" and "output" not in result
    assert result["source"]["sha256"] == hashlib.sha256(source).hexdigest()
    assert result["job"]["pdf"] == job(icc.read_bytes())["pdf"]
    assert not (output/"production.pdf").exists()
    assert not list(output.glob(".production-stage-*"))
    assert sorted(p.name for p in output.iterdir()) == ["report.html", "report.json"]
    markup = (output/"report.html").read_text()
    assert "<script>" not in markup and "&lt;script&gt;" in markup
    assert "Export failed" in markup
    assert path.read_bytes() == source and icc.read_bytes() == b"not an ICC"
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output/"report.json").stat().st_mode & 0o777 == 0o600


def test_converter_subprocess_does_not_inherit_provider_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-leak")
    monkeypatch.setenv("GS_OPTIONS", "must-not-alter-conversion")
    out = _run([sys.executable, "-c", "import os;print(os.getenv('OPENAI_API_KEY'));print(os.getenv('GS_OPTIONS'));print(os.getenv('PATH'))"], tmp_path, 5)
    assert out.splitlines() == ["None", "None", "/usr/bin:/bin"]


def test_converter_timeout_reaps_child(tmp_path):
    pid = tmp_path/"pid.txt"
    code = "import os,time,pathlib;pathlib.Path('pid.txt').write_text(str(os.getpid()));time.sleep(20)"
    with pytest.raises(ValueError, match="time limit"):
        _run([sys.executable, "-c", code], tmp_path, .2)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pid.read_text()), 0)


def test_worker_sigterm_reaps_converter_process_group(tmp_path):
    # This exercises actual OS signals rather than mocking subprocess lifecycle.
    code = "import os,pathlib,time;pathlib.Path('child.pid').write_text(str(os.getpid()));time.sleep(20)"
    entry = "from pathlib import Path;import sys;from presscheck.production_pdf import _run;_run([sys.executable,'-c',sys.argv[1]],Path(sys.argv[2]),30)"
    env = {"PATH": "/usr/bin:/bin", "PYTHONPATH": str(Path(__file__).resolve().parents[1])}
    worker = subprocess.Popen([sys.executable, "-c", entry, code, str(tmp_path)], env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    child = None
    try:
        deadline = time.monotonic()+5
        while not (tmp_path/"child.pid").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        assert (tmp_path/"child.pid").exists(), "Converter did not start"
        child = int((tmp_path/"child.pid").read_text())
        started = time.monotonic()
        worker.send_signal(signal.SIGTERM)
        worker.communicate(timeout=1)
        assert time.monotonic()-started < 1
        with pytest.raises(ProcessLookupError): os.kill(child, 0)
    finally:
        if worker.poll() is None: worker.kill(); worker.communicate(timeout=2)
        if child:
            try: os.kill(child, signal.SIGKILL)
            except ProcessLookupError: pass


@pytest.fixture
def real_tools():
    converter, profile = os.environ.get("MAGDOBE_GHOSTSCRIPT"), os.environ.get("MAGDOBE_TEST_ICC")
    if not converter or not profile:
        pytest.skip("Pinned Ghostscript and test-only ICC fixture paths were not supplied")
    return Path(converter), Path(profile).read_bytes()


@pytest.mark.parametrize("target", ["cmyk_pdf", "pdfx4"])
def test_real_converter_2199_paths_cmyk_alpha_outline_and_immutable_source(tmp_path, real_tools, target):
    converter, icc = real_tools
    source_path, icc_path = tmp_path/"complex.pdf", tmp_path/"test-profile.icc"
    source = make_pdf(source_path, 2199, alpha=True)
    icc_path.write_bytes(icc)
    config = job(icc, target)
    result = finalize_pdf(source_path, icc_path, config, tmp_path/"result", converter)
    assert result["status"] == "pass_supported_checks", json.dumps(result["findings"], indent=2)
    assert result["output"]["source_vector_paths"] == 2199
    assert result["output"]["vector_paths"] > 0
    assert result["output"]["text_objects"] == result["output"]["raster_objects"] == 0
    assert result["output"]["alpha_states"] > 0
    assert result["output"]["color_spaces"] == ["CMYK"]
    assert source_path.read_bytes() == source and icc_path.read_bytes() == icc
    assert result["job"]["pdf"] == config["pdf"]
    output_path = tmp_path/"result/production.pdf"
    assert hashlib.sha256(output_path.read_bytes()).hexdigest() == result["output"]["sha256"]
    assert audit_pdf(output_path, config, icc)["status"] == "pass_supported_checks"
    if target == "pdfx4": assert result["output"]["pdfx_claim"] == "PDF/X-4"
    else: assert result["output"]["pdfx_claim"] is None


def test_real_profile_validation_rejects_class_channels_hash_and_tag_bounds(real_tools):
    _, icc = real_tools
    descriptor = job(icc)["pdf"]["icc_profile"]
    assert validate_icc(icc, descriptor) == hashlib.sha256(icc).hexdigest()
    for offset, replacement in ((12, b"mntr"), (16, b"RGB "), (8, b"\x05"), (136, b"\xff\xff\xff\xff")):
        changed = icc[:offset] + replacement + icc[offset+len(replacement):]
        with pytest.raises(ValueError):
            validate_icc(changed, job(changed)["pdf"]["icc_profile"])
    with pytest.raises(ValueError, match="SHA"):
        validate_icc(icc, descriptor | {"sha256": "0"*64})


def test_requested_intent_and_bpc_match_independent_littlecms_color_values(tmp_path, real_tools):
    """Compare painted ink values, not metadata or a converter command string.

    Both engines receive identical embedded source/destination ICC bytes. The
    tolerance allows 8-bit LittleCMS output rounding versus pdfwrite's floating
    CMYK operands; it is much smaller than this fixture's BPC/intent changes.
    """
    converter, destination_icc = real_tools
    source_profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
    source_icc = source_profile.tobytes()
    source_profile = ImageCms.ImageCmsProfile(io.BytesIO(source_icc))
    colors = [(12, 15, 20), (35, 68, 210), (250, 187, 73)]
    source_path, icc_path = tmp_path/"three-colors.pdf", tmp_path/"printer.icc"
    def embed_source_profile(pdf, page):
        profile = pdf.make_stream(source_icc)
        profile["/N"] = 3
        page.obj["/Resources"]["/ColorSpace"] = pikepdf.Dictionary(
            Source=pikepdf.Array([pikepdf.Name.ICCBased, profile]))
        body = ["/Source cs"]
        for index, channels in enumerate(colors):
            body.append(" ".join(f"{value/255:.12f}" for value in channels) + f" scn {10+index*30} 10 20 20 re f")
        page.obj["/Contents"] = pdf.make_stream("\n".join(body).encode())
    source = make_pdf(source_path, mutation=embed_source_profile)
    icc_path.write_bytes(destination_icc)
    rgb = Image.new("RGB", (3, 1))
    rgb.putdata(colors)
    destination_profile = ImageCms.ImageCmsProfile(io.BytesIO(destination_icc))
    painted_by_mode = {}
    for intent, intent_number in (("perceptual", 0), ("relative_colorimetric", 1), ("absolute_colorimetric", 3)):
        for bpc in (False, True):
            config = job(destination_icc)
            config["pdf"].update(rendering_intent=intent, black_point_compensation=bpc)
            output = tmp_path/f"{intent}-{int(bpc)}"
            report = finalize_pdf(source_path, icc_path, config, output, converter)
            assert report["status"] == "pass_supported_checks", json.dumps(report["findings"], indent=2)
            assert report["output"]["vector_paths"] == 3
            assert report["output"]["raster_objects"] == report["output"]["text_objects"] == 0
            assert report["output"]["color_spaces"] == ["CMYK"]
            with pikepdf.open(output/"production.pdf") as pdf:
                current, stack, paints = None, [], []
                for instruction in pikepdf.parse_content_stream(pdf.pages[0]):
                    op = str(instruction.operator)
                    if op == "k": current = tuple(float(value) for value in instruction.operands)
                    elif op == "q": stack.append(current)
                    elif op == "Q": current = stack.pop()
                    elif op in ("f", "f*", "F"):
                        assert current is not None, "Expected explicit CMYK fill paint"
                        paints.append(current)
            assert len(paints) == len(colors)
            transform = ImageCms.buildTransform(source_profile, destination_profile, "RGB", "CMYK",
                renderingIntent=intent_number, flags=ImageCms.Flags.BLACKPOINTCOMPENSATION if bpc else 0)
            expected_image = ImageCms.applyTransform(rgb, transform)
            expected = [tuple(channel/255 for channel in expected_image.getpixel((index, 0))) for index in range(3)]
            for actual, reference in zip(paints, expected):
                assert actual == pytest.approx(reference, abs=1.5/255), (intent, bpc, actual, reference)
            painted_by_mode[intent, bpc] = paints
    # The test fixture must actually distinguish the settings; otherwise a
    # converter silently ignoring them could pass the individual comparisons.
    def difference(left, right):
        return max(abs(a-b) for color_a, color_b in zip(left, right) for a, b in zip(color_a, color_b))
    assert difference(painted_by_mode["relative_colorimetric", False], painted_by_mode["relative_colorimetric", True]) > 5/255
    assert difference(painted_by_mode["relative_colorimetric", False], painted_by_mode["absolute_colorimetric", False]) > 5/255
    assert source_path.read_bytes() == source and icc_path.read_bytes() == destination_icc


def test_real_pdfx4_on_utc_host_preserves_date_and_xmp_equivalence(tmp_path, real_tools, monkeypatch):
    import presscheck.production_pdf as production
    converter, icc = real_tools
    environment = production._environment
    monkeypatch.setattr(production, "_environment", lambda directory: environment(directory) | {"TZ": "UTC"})
    source_path, icc_path = tmp_path/"source.pdf", tmp_path/"printer.icc"
    make_pdf(source_path)
    icc_path.write_bytes(icc)
    config = job(icc)
    output = tmp_path/"output"
    report = finalize_pdf(source_path, icc_path, config, output, converter)
    assert report["status"] == "pass_supported_checks", json.dumps(report["findings"], indent=2)
    with pikepdf.open(output/"production.pdf") as pdf:
        # This is the exact emitted form that caused the Ubuntu CI failure.
        assert str(pdf.docinfo["/CreationDate"]).endswith("Z00'00'")
        assert _pdf_date(pdf.docinfo["/CreationDate"]).utcoffset().total_seconds() == 0
        pdf.docinfo["/CreationDate"] = "D:20000101000000Z00'00'"
        inconsistent = tmp_path/"inconsistent-date.pdf"
        pdf.save(inconsistent, min_version="1.6")
    failed(audit_pdf(inconsistent, config, icc), "dates are inconsistent")


@pytest.mark.parametrize("tamper", ["rgb", "icc", "box", "xmp", "action", "font"])
def test_generated_output_tampering_is_detected(tmp_path, real_tools, tamper):
    converter, icc = real_tools
    source_path, icc_path = tmp_path/"source.pdf", tmp_path/"printer.icc"
    make_pdf(source_path)
    icc_path.write_bytes(icc)
    config = job(icc)
    output = tmp_path/"result"
    baseline = finalize_pdf(source_path, icc_path, config, output, converter)
    assert baseline["status"] == "pass_supported_checks", json.dumps(baseline["findings"], indent=2)
    changed = tmp_path/"tampered.pdf"
    with pikepdf.open(output/"production.pdf") as pdf:
        page = pdf.pages[0]
        if tamper == "rgb": page.obj["/Contents"] = pdf.make_stream(b"1 0 0 rg 0 0 10 10 re f")
        if tamper == "icc": pdf.Root["/OutputIntents"][0]["/DestOutputProfile"].write(b"other profile")
        if tamper == "box": page.obj["/TrimBox"] = pikepdf.Array([0,0,198,270])
        if tamper == "xmp": del pdf.Root["/Metadata"]
        if tamper == "action": pdf.Root["/Names"] = pikepdf.Dictionary(Custom=pikepdf.Dictionary(JS=pikepdf.String("evil")))
        if tamper == "font": page.obj["/Resources"]["/Font"] = pikepdf.Dictionary(F=pikepdf.Dictionary(Type=pikepdf.Name.Font))
        pdf.save(changed, min_version="1.6")
    assert audit_pdf(changed, config, icc)["status"] == "fail"
