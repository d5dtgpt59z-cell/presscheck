import base64
import io
import math
from xml.etree import ElementTree as ET

from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
import pytest

from presscheck.structure import generate_structure, StructureSpec, layout
from presscheck import structure_api


def test_closed_vector_net_physical_canvas_and_typed_operations():
    result = generate_structure({})
    root = ET.fromstring(result['svg'])
    ns = {'s': 'http://www.w3.org/2000/svg'}
    cut = root.find(".//s:path[@id='cut-outline']", ns)
    assert cut is not None and cut.get('d').endswith(' Z')
    assert 'image' not in {e.tag.rsplit('}', 1)[-1] for e in root.iter()}
    assert root.get('width') == f"{result['dimensions']['canvas_width_mm']:g}mm"
    assert float(root.get('height')[:-2]) == result['dimensions']['canvas_height_mm']
    assert {e.get('data-production-role') for e in root if e.get('data-production-role')} == {'cut', 'crease', 'glue', 'dimensions', 'legend'}
    assert result['job']['trim_width_mm'] == result['dimensions']['canvas_width_mm']
    assert result['job']['supplier_confirmed'] is False
    assert result['status'] == 'prototype_unverified'
    assert any('not guaranteed internal' in text for text in result['limitations'])
    raw = base64.b64decode(result['preview_png'].split(',', 1)[1])
    with Image.open(io.BytesIO(raw)) as image:
        assert image.format == 'PNG' and max(image.size) <= 1400


def test_parameters_really_change_geometry_and_caliper_relief():
    original = generate_structure({})
    modified = generate_structure({'width_mm': 90, 'depth_mm': 32, 'height_mm': 120, 'caliper_mm': .8, 'glue_flap_mm': 15})
    assert modified['dimensions']['blank_width_mm'] == 2*90 + 2*32 + 15
    assert modified['dimensions']['relief_mm'] == 1.6
    assert original['svg'] != modified['svg']
    assert modified['dimensions']['canvas_height_mm'] > original['dimensions']['canvas_height_mm']


def test_native_project_has_editable_geometry_and_exact_hierarchy_order():
    result = generate_structure({})
    project = result['project']
    assert project['version'] == 2
    assert project['width'] == result['dimensions']['canvas_width_mm']
    assert project['height'] == result['dimensions']['canvas_height_mm']
    assert project['production'] == result['job']
    assert project['production']['supplier_confirmed'] is False
    layers = project['hierarchy']
    assert [node['productionRole'] for node in layers] == ['cut', 'crease', 'glue', 'legend', 'legend']
    assert all(node['kind'] == 'layer' for node in layers)
    assert [child['id'] for node in layers for child in node['children']] == [shape['id'] for shape in project['shapes']]
    cut = project['shapes'][0]
    expected = layout(StructureSpec())['outline']
    assert cut['kind'] == 'path' and cut['closed'] is True and cut['fill'] is None
    assert [(anchor['point']['x']-12, anchor['point']['y']-12) for anchor in cut['anchors']] == expected
    creases = project['shapes'][1:13]
    assert all(shape['kind'] == 'path' and len(shape['anchors']) == 2 and not shape['closed'] for shape in creases)
    assert project['shapes'][13]['name'] == 'Glue guide' and project['shapes'][13]['closed']
    assert any(shape['kind'] == 'text' and 'NO SUPPLIER APPROVAL' in shape['text'] for shape in project['shapes'])
    import uuid
    identifiers = [shape['id'] for shape in project['shapes']] + [node['id'] for node in layers]
    assert len(set(identifiers)) == len(identifiers)
    assert all(str(uuid.UUID(ident)) == ident for ident in identifiers)


@pytest.mark.parametrize('settings', [
    {'width_mm': 0}, {'width_mm': float('nan')}, {'depth_mm': float('inf')},
    {'height_mm': 9999}, {'caliper_mm': 2, 'depth_mm': 8}, {'glue_flap_mm': 50},
    {'name': '<bad>\x00'}, {'kind': 'machine_ready'}, {'unknown': 'ignored'},
])
def test_practical_bounds_and_invalid_values_are_rejected(settings):
    with pytest.raises(ValueError):
        generate_structure(settings)


@pytest.mark.parametrize('settings', [{}, {'width_mm': 20, 'depth_mm': 8, 'height_mm': 20, 'caliper_mm': .1, 'glue_flap_mm': 5}, {'width_mm': 400, 'depth_mm': 200, 'height_mm': 600, 'caliper_mm': 2, 'glue_flap_mm': 50}])
def test_cut_outline_has_no_self_intersections(settings):
    spec = StructureSpec.model_validate(settings); geo = layout(spec)
    p = geo['outline']; edges = list(zip(p, p[1:]+p[:1]))
    def cross(a,b,c):return (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
    for i,(a,b) in enumerate(edges):
        assert a != b and all(math.isfinite(v) for point in (a,b) for v in point)
        for j,(c,d) in enumerate(edges):
            if abs(i-j)<=1 or {i,j}=={0,len(edges)-1}:continue
            assert not (cross(a,b,c)*cross(a,b,d)<0 and cross(c,d,a)*cross(c,d,b)<0)


def test_names_are_escaped_and_http_download_is_attachment(tmp_path, monkeypatch):
    monkeypatch.setattr(structure_api, 'ROOT', tmp_path)
    app = FastAPI();app.include_router(structure_api.router)
    with TestClient(app) as client:
        response = client.post('/api/structures', json={'name': '<script>not code</script>'})
        assert response.status_code == 201
        value = response.json()
        assert '<script>' not in value['svg'] and '&lt;script&gt;' in value['svg']
        attachment = client.get(value['svg_url'])
        assert attachment.status_code == 200
        assert attachment.headers['content-type'] == 'application/octet-stream'
        assert 'attachment' in attachment.headers['content-disposition']
        assert client.get('/api/structures/not-a-real-id/structure.svg').status_code == 404
        assert client.post('/api/structures', json={'height_mm': -1}).status_code == 422
