"""Portable printer metadata is retained without accepting ICC bytes or locations."""
from copy import deepcopy

import pytest

from presscheck.desktop_worker import validate_profiles


def collection(pdf):
    return {"schema_version": 1, "profiles": [{"id": "printer-example", "name": "Unverified printer",
        "job": {"schema_version": 1, "name": "Card example", "supplier_confirmed": True, "pdf": pdf}}]}


def test_pdf_metadata_round_trips_with_identity_and_supplier_confirmation_reset():
    pdf = {"schema_version": 1, "target": "pdfx4",
           "icc_profile": {"sha256": "a" * 64, "name": "Printer condition / coated stock"},
           "output_condition_identifier": "Custom Press 4", "output_condition": "Printer supplied stock",
           "registry_name": "https://www.color.org", "rendering_intent": "perceptual",
           "black_point_compensation": True, "black_policy": "icc", "text_mode": "outlines"}
    original = collection(pdf)
    before = deepcopy(original)
    restored = validate_profiles(original)
    assert restored["profiles"][0]["job"]["pdf"] == pdf
    assert restored["profiles"][0]["job"]["supplier_confirmed"] is False
    assert original == before


def test_partial_pdf_setup_has_no_fabricated_icc_and_disabled_setup_is_removed():
    partial = validate_profiles(collection({"target": "pdfx4"}))["profiles"][0]["job"]["pdf"]
    assert "icc_profile" not in partial
    assert partial["output_condition_identifier"] == ""
    assert "pdf" not in validate_profiles(collection(None))["profiles"][0]["job"]


@pytest.mark.parametrize("pdf", [
    {"target": "pdfx1a"}, {"schema_version": True}, {"black_point_compensation": "yes"},
    {"icc_path": "/local/printer.icc"}, {"profile_data": "not allowed"},
    {"icc_profile": {"name": "Printer", "sha256": "a" * 64, "data": "bytes"}},
    {"registry_name": "https://user:secret@example.com"},
    {"registry_name": "https://example.com/?secret=value"},
    {"output_condition_identifier": "x" * 129}, {"output_condition": "x" * 501},
])
def test_invalid_pdf_metadata_cannot_be_saved_in_a_printer_profile(pdf):
    with pytest.raises(ValueError):
        validate_profiles(collection(pdf))
