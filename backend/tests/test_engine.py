from pathlib import Path
import io
import cv2
import numpy as np
import pytest
from PIL import Image, ImageCms
from presscheck.ingestion import ingest
from presscheck.engine import inspect
from presscheck.registration import register
from presscheck.color import compare_color
from presscheck.models import Config
from presscheck.text import compare_text
D=Path(__file__).resolve().parents[2]/'dataset'

def test_identical_pass(tmp_path):
    r=inspect(D/'cards-master.png',D/'cards-clean.png',tmp_path,Config())
    assert r['status']=='PASS'
    assert r['metrics']['changed_area_percent']==0
    assert not r['text']['changes']
    assert r['registration']['confidence']>.9

def test_color_difference(tmp_path):
    r=inspect(D/'label-master.png',D/'label-color.png',tmp_path,Config(color_regions=[(240,520,290,570)]))
    assert r['status']=='FAIL'
    assert r['color_samples'][0]['mean_delta_e']>10
    assert r['metrics']['changed_area_percent']>8

def test_text_difference(tmp_path):
    r=inspect(D/'label-master.png',D/'label-copy.png',tmp_path,Config())
    assert any('SOLAR' in c['reference'] and 'LUNAR' in c['candidate'] for c in r['text']['changes'])
    assert r['status'] in ('REVIEW REQUIRED','FAIL')

def test_barcode_payload(tmp_path):
    r=inspect(D/'label-master.png',D/'label-barcode.png',tmp_path,Config())
    assert r['barcode']['status']=='review'
    assert any(f['kind']=='barcode' for f in r['findings'])

def test_shift_registration(tmp_path):
    r=inspect(D/'cards-master.png',D/'cards-offset.png',tmp_path,Config())
    assert r['registration']['confidence']>.55
    assert any(f['kind']=='geometry' for f in r['findings'])

def test_rotation():
    a=np.array(Image.open(D/'cards-master.png')); b=np.array(Image.open(D/'rotated.png'))
    _,_,r=register(a,b)
    assert r['confidence']>.8 and r['rotation_degrees'] in (90,270)

def test_featureless_inconclusive(tmp_path):
    blank=tmp_path/'blank.png'; Image.new('RGB',(100,100),'white').save(blank)
    r=inspect(blank,blank,tmp_path/'out',Config())
    assert r['status']=='INCONCLUSIVE'

def test_geometry_unavailable(tmp_path):
    r=inspect(D/'toy-master.png',D/'toy-clean.png',tmp_path,Config())
    assert next(g for g in r['geometry'] if g['check']=='safe zone')['status']=='unavailable'

def test_safe_zone(tmp_path):
    r=inspect(D/'toy-master.png',D/'toy-defective.png',tmp_path,Config(trim=(30,30,770,1030),safe_px=20))
    assert next(g for g in r['geometry'] if g['check']=='safe zone')['status']=='review'

def test_ingest_source_and_frames(tmp_path):
    data=(D/'multipage.pdf').read_bytes(); a=ingest(data,'sample.pdf',tmp_path)
    assert len(a['pages'])==2 and (tmp_path/a['id']/'original').read_bytes()==data
    c=ingest((D/'cmyk.tiff').read_bytes(),'sample.tiff',tmp_path)
    assert c['pages'][0]['mode']=='CMYK' and c['warnings']

def test_icc(tmp_path):
    buf=io.BytesIO(); profile=ImageCms.ImageCmsProfile(ImageCms.createProfile('sRGB')).tobytes()
    Image.new('RGB',(20,20),'red').save(buf,format='PNG',icc_profile=profile)
    a=ingest(buf.getvalue(),'color.png',tmp_path)
    assert a['pages'][0]['icc']['sha256']
    assert (tmp_path/a['id']/'profile-0.icc').exists()

def test_reject_invalid(tmp_path):
    with pytest.raises(ValueError): ingest(b'','empty.png',tmp_path)
    with pytest.raises(Exception): ingest(b'garbage','bad.png',tmp_path)
    assert not list(tmp_path.iterdir())

def test_bad_rectangle():
    with pytest.raises(ValueError): Config(trim=(4,4,2,8))

def test_zero_delta():
    a=np.zeros((10,10,3),np.uint8)
    delta,_=compare_color(a,a,[])
    assert np.max(delta)==0
