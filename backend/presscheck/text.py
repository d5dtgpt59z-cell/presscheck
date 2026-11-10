import shutil
import numpy as np
from difflib import SequenceMatcher
import pytesseract
from pytesseract import Output

def read_text(img):
    if not shutil.which('tesseract'): return {'available':False,'reason':'Tesseract executable not installed','words':[]}
    try:
        # Barcode stripes are not typography; decoded payloads are compared separately.
        ocr_image = img.copy()
        excluded=[]
        try:
            import zxingcpp
            for code in zxingcpp.read_barcodes(img):
                pos=code.position
                points=[pos.top_left,pos.top_right,pos.bottom_left,pos.bottom_right]
                x0=max(0,min(p.x for p in points)-8);x1=min(img.shape[1],max(p.x for p in points)+8)
                y0=max(0,min(p.y for p in points)-8);y1=min(img.shape[0],max(p.y for p in points)+50)
                ocr_image[y0:y1,x0:x1]=255;excluded.append([x0,y0,x1,y1])
        except ImportError: pass
        data = pytesseract.image_to_data(ocr_image,config='--psm 11',output_type=Output.DICT,timeout=25)
        words = []
        for i, word in enumerate(data['text']):
            conf = float(data['conf'][i])
            if word.strip() and conf>=25:
                words.append({'text':word.strip(),'confidence':conf/100,'bbox':[data['left'][i],data['top'][i],data['left'][i]+data['width'][i],data['top'][i]+data['height'][i]]})
        return {'available':True,'words':words,'excluded_barcode_regions':excluded}
    except Exception as e: return {'available':False,'reason':str(e)[:200],'words':[]}

def compare_text(a,b):
    ra,rb = read_text(a),read_text(b)
    changes=[]
    if ra['available'] and rb['available']:
        wa,wb=ra['words'],rb['words']
        seq=SequenceMatcher(None,[x['text'] for x in wa],[x['text'] for x in wb],autojunk=False)
        for op,i,j,k,l in seq.get_opcodes():
            if op=='equal': continue
            words=wa[i:j]+wb[k:l]
            changes.append({'operation':op,'reference':' '.join(x['text'] for x in wa[i:j]),'candidate':' '.join(x['text'] for x in wb[k:l]),'confidence':min(x['confidence'] for x in words),'reference_boxes':[x['bbox'] for x in wa[i:j]],'candidate_boxes':[x['bbox'] for x in wb[k:l]]})
    return {'available':ra['available'] and rb['available'],'reference':ra,'candidate':rb,'changes':changes,'note':'OCR suggestions require visual confirmation; confidence is engine confidence, not probability of a defect.'}
