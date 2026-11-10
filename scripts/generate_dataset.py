"""Original fictional artwork. Deterministic vector shapes, text and EAN-13 payloads."""

import json, io, random
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from barcode import EAN13
from barcode.writer import ImageWriter

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "dataset"
OUT.mkdir(exist_ok=True)
FONT = next(
    (
        p
        for p in [
            Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
            Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ]
        if p.exists()
    ),
    None,
)
if not FONT:
    raise RuntimeError("Install DejaVu Sans or Arial")


def font(size):
    return ImageFont.truetype(str(FONT), size)


W, H = 800, 1060
CREAM = "#F4E7C8"
INK = "#38291F"


def make(kind, defects=()):
    bg = {"cards": "#59B5AE", "label": "#E8B83D", "toy": "#F47B36"}[kind]
    im = Image.new("RGB", (W, H), CREAM)
    d = ImageDraw.Draw(im)
    d.rectangle((30, 30, 770, 1030), outline=INK, width=3)
    d.rectangle((50, 50, 750, 850), fill=bg)
    # Printed ticks and repeat marks provide distributed registration features.
    rng = random.Random(78)
    for i in range(85):
        x = rng.randrange(70, 730)
        y = rng.randrange(70, 840)
        d.line((x, y, x + 8, y + 3), fill=INK, width=2)
    d.text((75, 72), "MAGNUS / ORIGINAL GOODS", fill=INK, font=font(20))
    title = {"cards": "ORBIT CLUB", "label": "SOLAR SODA", "toy": "MOON ROVER"}[kind]
    if "copy" in defects:
        title = "LUNAR SODA"
    d.text((74, 150), title, fill=INK, font=font(64))
    d.text(
        (78, 232),
        {
            "cards": "COLLECTIBLE EXPLORER CARDS",
            "label": "BLOOD ORANGE / BOTANICAL FIZZ",
            "toy": "BUILD YOUR NEXT ADVENTURE",
        }[kind],
        fill=INK,
        font=font(22),
    )
    if "logo" not in defects:
        x = 590 + (35 if "shift" in defects else 0)
        d.ellipse((x, 290, x + 100, 390), fill=CREAM, outline=INK, width=5)
        d.text((x + 16, 320), "M78", fill=INK, font=font(24))
    # Original planetary illustration.
    fill = "#D85D4B" if "color" not in defects else "#778B4A"
    d.ellipse((145, 345, 645, 785), fill=fill, outline=INK, width=7)
    d.ellipse((200, 400, 590, 730), outline=CREAM, width=6)
    d.arc((85, 480, 715, 630), 0, 350, fill=INK, width=18)
    for x, y, r in [(300, 460, 28), (480, 550, 50), (360, 670, 21)]:
        d.ellipse((x - r, y - r, x + r, y + r), outline=CREAM, width=5)
    if "art" not in defects:
        d.polygon(
            [(400, 365), (435, 470), (400, 450), (365, 470)],
            fill=CREAM,
            outline=INK,
            width=4,
        )
    if "legal" not in defects:
        d.text((70, 862), "© 2026 MAGNUS FICTIONAL GOODS", fill=INK, font=font(18))
        d.text(
            (70, 890), "CONCEPT SAMPLE. NOT FOR RETAIL SALE.", fill=INK, font=font(17)
        )
    d.text(
        (70, 957),
        {
            "cards": "8 CARDS / SERIES 01",
            "label": "330 mL / NO CAFFEINE",
            "toy": "AGES 8+ / 48 PARTS",
        }[kind],
        fill=INK,
        font=font(20),
    )
    payload = "590123412345" if "barcode" not in defects else "400638133393"
    buffer = io.BytesIO()
    EAN13(payload, writer=ImageWriter()).write(
        buffer,
        options={
            "module_width": 0.254,
            "module_height": 9,
            "font_size": 7,
            "quiet_zone": 3,
            "dpi": 200,
        },
    )
    code = Image.open(buffer).convert("RGB")
    im.paste(code, (490, 900))
    if "safe" in defects:
        d.text((3, 35), "WARNING: SMALL PARTS", font=font(23), fill=INK)
    if "clip" in defects:
        im.paste(CREAM, (0, 480, 115, H))
    if "offset" in defects:
        shifted = Image.new("RGB", im.size, CREAM)
        shifted.paste(im, (15, 8))
        im = shifted
    if "scale" in defects:
        scaled = im.resize((752, 996))
        im = Image.new("RGB", (W, H), CREAM)
        im.paste(scaled, (24, 32))
    return im


def main():
    manifest = []
    cases = []
    definitions = [
        ("cards", "Collectible card pack", ["legal", "shift", "art"]),
        ("label", "Consumer product label", ["copy", "color", "barcode"]),
        ("toy", "Toy package", ["clip", "safe", "art"]),
    ]
    for kind, name, defects in definitions:
        ref = make(kind)
        ref.save(OUT / f"{kind}-master.png", dpi=(300, 300))
        ref.save(OUT / f"{kind}-clean.png", dpi=(300, 300))
        make(kind, defects).save(OUT / f"{kind}-defective.png")
        config = {
            "trim": [30, 30, 770, 1030],
            "bleed_px": 12,
            "safe_px": 20,
            "color_regions": [[240, 520, 290, 570]],
        }
        manifest.append(
            {
                "id": kind,
                "name": name,
                "reference": f"{kind}-master.png",
                "clean": f"{kind}-clean.png",
                "candidate": f"{kind}-defective.png",
                "config": config,
                "injected": defects,
            }
        )
        cases.append(
            {
                "id": kind + "-clean",
                "reference": f"{kind}-master.png",
                "candidate": f"{kind}-clean.png",
                "positive": False,
                "defects": [],
            }
        )
        for defect in [
            "logo",
            "shift",
            "copy" if kind == "label" else "legal",
            "color",
            "clip",
            "barcode",
            "offset",
            "scale",
            "art",
        ]:
            path = f"{kind}-{defect}.png"
            make(kind, [defect]).save(OUT / path)
            cases.append(
                {
                    "id": kind + "-" + defect,
                    "reference": f"{kind}-master.png",
                    "candidate": path,
                    "positive": True,
                    "defects": [defect],
                }
            )
        # Benign JPEG re-encoding explicitly tests false positives.
        ref.save(OUT / f"{kind}-jpeg.jpg", quality=92)
        cases.append(
            {
                "id": kind + "-jpeg",
                "reference": f"{kind}-master.png",
                "candidate": f"{kind}-jpeg.jpg",
                "positive": False,
                "defects": [],
            }
        )
    # Ingestion fixtures: multipage PDF, untagged CMYK TIFF, rotated image.
    make("cards").save(
        OUT / "multipage.pdf",
        save_all=True,
        append_images=[make("label")],
        resolution=144,
    )
    make("label").convert("CMYK").save(OUT / "cmyk.tiff", dpi=(300, 300))
    make("cards").rotate(90, expand=True).save(OUT / "rotated.png")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2))
    (OUT / "cases.json").write_text(json.dumps(cases, indent=2))
    print(f"Generated {len(cases)} benchmark cases + 3 demos and ingestion fixtures")


if __name__ == "__main__":
    main()
