import hashlib, io, uuid, warnings
from pathlib import Path
import pymupdf as fitz
from PIL import Image, ImageOps, ImageCms

Image.MAX_IMAGE_PIXELS = 40_000_000
MAX_BYTES = 40 * 1024 * 1024
MAX_PAGES = 20
PREVIEW_EDGE = 1600

def ingest(content: bytes, name: str, root: Path) -> dict:
    if not content or len(content) > MAX_BYTES:
        raise ValueError('Artwork must be nonempty and at most 40 MiB')
    ident = uuid.uuid4().hex
    folder = root / ident
    folder.mkdir(parents=True)
    notes, pages = [], []
    try:
        if content.startswith(b'%PDF-'):
            doc = fitz.open(stream=content, filetype='pdf')
            if doc.needs_pass: raise ValueError('Encrypted PDFs are unsupported')
            if not 0 < len(doc) <= MAX_PAGES: raise ValueError('PDF limit is 20 pages')
            fmt = 'PDF'
            notes.append('PDF pages rendered into RGB by MuPDF; output intent/spot separations are not certified. Original PDF retained.')
            for page in doc:
                rect = page.rect
                if rect.width <= 0 or rect.height <= 0: raise ValueError('Invalid PDF page size')
                scale = min(2, PREVIEW_EDGE / max(rect.width, rect.height))
                pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
                img = Image.frombytes('RGB', [pix.width, pix.height], pix.samples)
                pages.append((img, {'points': [rect.width, rect.height], 'mode': 'PDF-RGB', 'dpi': [72*scale]*2, 'icc': None}))
            doc.close()
        else:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                src = Image.open(io.BytesIO(content))
                fmt = src.format
                if fmt not in ('PNG','JPEG','TIFF'): raise ValueError('Supported formats: PNG, JPEG, TIFF, PDF')
                if getattr(src, 'n_frames', 1) > MAX_PAGES: raise ValueError('Image limit is 20 frames')
                total_pixels=0
                for n in range(getattr(src, 'n_frames', 1)):
                    src.seek(n)
                    total_pixels+=src.width*src.height
                    if total_pixels>80_000_000: raise ValueError('Image frame total exceeds 80 million pixels')
                    src.load()
                    info = {'width': src.width, 'height': src.height, 'mode': src.mode, 'dpi': [float(v) for v in src.info.get('dpi', (0,0))], 'icc': None}
                    profile = src.info.get('icc_profile')
                    img = ImageOps.exif_transpose(src.copy())
                    if profile:
                        (folder / f'profile-{n}.icc').write_bytes(profile)
                        try:
                            icc = ImageCms.ImageCmsProfile(io.BytesIO(profile))
                            info['icc'] = {'name': ImageCms.getProfileName(icc).strip(), 'sha256': hashlib.sha256(profile).hexdigest()}
                            img = ImageCms.profileToProfile(img, icc, ImageCms.createProfile('sRGB'), outputMode='RGB')
                        except Exception as exc:
                            notes.append(f'Page {n+1}: ICC conversion failed ({type(exc).__name__}); uncalibrated RGB fallback')
                    else:
                        notes.append(f'Page {n+1}: no embedded ICC profile; RGB preview is assumed sRGB, CMYK uses an uncalibrated conversion')
                    if img.mode in ('RGBA','LA') or 'transparency' in src.info:
                        rgba = img.convert('RGBA'); bg = Image.new('RGBA', rgba.size, 'white'); bg.alpha_composite(rgba); img = bg.convert('RGB')
                        notes.append(f'Page {n+1}: transparency composited onto white for inspection')
                    img = img.convert('RGB')
                    img.thumbnail((PREVIEW_EDGE, PREVIEW_EDGE), Image.Resampling.LANCZOS)
                    pages.append((img, info))
                src.close()
        (folder / 'original').write_bytes(content)
        result = []
        for n, (img, info) in enumerate(pages):
            img.save(folder / f'page-{n}.png')
            result.append({**info, 'preview_width': img.width, 'preview_height': img.height, 'preview': f'/api/assets/{ident}/pages/{n}'})
        return {'id': ident, 'name': Path(name).name[:240], 'format': fmt, 'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest(), 'pages': result, 'warnings': notes}
    except Exception:
        import shutil
        shutil.rmtree(folder, ignore_errors=True)
        raise
