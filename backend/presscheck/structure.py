"""Parametric straight-tuck carton study, not supplier-approved CAD/tooling.

All geometry is physical millimetres. Nominal panel dimensions are fold-line
sizes, not certified internal dimensions. Only simple, locally generated geometry
is rasterized; uploaded SVGs are never handled by this module.
"""
import base64
import io
import math
import uuid
from xml.etree import ElementTree as ET

from PIL import Image, ImageDraw
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SVG = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG)


class StructureSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    kind: str = "tuck_carton"
    name: str = Field("Straight-tuck carton study", min_length=1, max_length=120)
    width_mm: float = Field(70, ge=20, le=400)
    depth_mm: float = Field(25, ge=8, le=200)
    height_mm: float = Field(100, ge=20, le=600)
    caliper_mm: float = Field(.4, ge=.1, le=2)
    glue_flap_mm: float = Field(12, ge=5, le=50)

    @field_validator("kind")
    @classmethod
    def known_kind(cls, value):
        if value != "tuck_carton":
            raise ValueError("Only the straight-tuck carton prototype is available")
        return value

    @field_validator("name")
    @classmethod
    def valid_name(cls, value):
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Use a name without control characters")
        return value.strip()

    @model_validator(mode="after")
    def coherent_geometry(self):
        if self.glue_flap_mm > min(self.width_mm * .6, self.depth_mm * .9):
            raise ValueError("Prototype glue flap must be smaller than 60% of width and 90% of depth")
        if self.caliper_mm > self.depth_mm / 8:
            raise ValueError("Caliper is too large for this prototype depth")
        return self


def number(value):
    return f"{value:.4f}".rstrip("0").rstrip(".")


def magdobe_project(root, geo, job, width, height, margin):
    """Version-two native schema: geometry stays editable and roles live on layers.

    `dimensions` is an SVG reference role; Magdobe's native enum intentionally
    uses `legend` for that layer. There is no guessed shapeID field: object node
    IDs are the actual VectorShape IDs in VectorHierarchy.swift.
    """
    shapes, hierarchy = [], []

    def color(hex_value, alpha=1):
        return {**dict(zip(("red", "green", "blue"), (int(hex_value[i:i+2], 16)/255 for i in (1, 3, 5)))), "alpha": alpha}

    def shape(name, points=None, closed=False, stroke=None, fill=None, stroke_width=.2, text=None, x=0., y=0.):
        points = points or []
        ident = str(uuid.uuid4())
        if points:
            x, y = min(p[0] for p in points), min(p[1] for p in points)
            w, h = max(p[0] for p in points)-x, max(p[1] for p in points)-y
        else:
            w, h = max(1., len(text or "")*1.8), 4.
        item = {"id": ident, "name": name, "kind": "text" if text is not None else "path",
                "x": x, "y": y, "width": w, "height": h,
                "anchors": [{"id": str(uuid.uuid4()), "point": {"x": px, "y": py}} for px, py in points],
                "closed": closed, "fill": fill, "stroke": stroke, "strokeWidth": stroke_width,
                "opacity": 1, "visible": True, "locked": False,
                "text": text or "", "fontSize": 3}
        shapes.append(item)
        return {"id": ident, "kind": "object", "name": "", "visible": True, "locked": False, "children": []}

    def layer(name, role, children):
        hierarchy.append({"id": str(uuid.uuid4()), "kind": "layer", "name": name,
                          "productionRole": role, "visible": True, "locked": False, "children": children})

    layer("CUT / prototype outline", "cut", [shape("Cut outline", [(x+margin, y+margin) for x, y in geo["outline"]], closed=True, stroke=color("#b73333"), stroke_width=.25)])
    layer("CREASE / fold studies", "crease", [shape(f"Crease {i+1}", [(a+margin, b+margin), (c+margin, d+margin)], stroke=color("#275aa0")) for i, (a,b,c,d) in enumerate(geo["creases"])])
    layer("GLUE / guide only", "glue", [shape("Glue guide", [(x+margin, y+margin) for x,y in geo["glue"]], closed=True, stroke=color("#45875a"), fill=color("#45875a", .15))])
    for ident, name in [("dimensions", "REFERENCE / dimensions, not tooling"), ("legend", "REFERENCE / unverified structure")]:
        children = []
        group = root.find(f"{{{SVG}}}g[@id='{ident}']")
        for i, element in enumerate(group):
            tag = element.tag.rsplit("}", 1)[-1]
            if tag == "line":
                children.append(shape(f"Dimension guide {i+1}", [(float(element.get("x1")), float(element.get("y1"))), (float(element.get("x2")), float(element.get("y2")))], stroke=color("#7b7367"), stroke_width=.15))
            elif tag == "text":
                text = element.text or ""
                x, y = float(element.get("x")), float(element.get("y"))-3
                if element.get("text-anchor") == "middle": x -= len(text)*.9
                children.append(shape(text, text=text, x=x, y=y, fill=color("#38291f"), stroke_width=0))
        layer(name, "legend", children)
    return {"version": 2, "width": width, "height": height, "shapes": shapes, "hierarchy": hierarchy, "production": job}


def layout(spec):
    w, d, h, g, c = spec.width_mm, spec.depth_mm, spec.height_mm, spec.glue_flap_mm, spec.caliper_mm
    relief = max(.4, 2 * c)
    lip = min(18., max(6., d * .35))
    dust = min(w / 2 - relief, d * .75)
    bevel = min(3., dust / 3, g / 3)
    flap = d + lip
    xs = [g, g + w, g + w + d, g + 2*w + d, g + 2*w + 2*d]
    top, bottom = flap, flap + h
    points = [(g, top), (xs[1], top)]

    def dust_top(left, right):
        return [(left+relief, top), (left+relief, top-dust+bevel),
                (left+relief+bevel, top-dust), (right-relief-bevel, top-dust),
                (right-relief, top-dust+bevel), (right-relief, top), (right, top)]

    def dust_bottom(left, right):
        return [(right-relief, bottom), (right-relief, bottom+dust-bevel),
                (right-relief-bevel, bottom+dust), (left+relief+bevel, bottom+dust),
                (left+relief, bottom+dust-bevel), (left+relief, bottom), (left, bottom)]

    points += dust_top(xs[1], xs[2])
    points += [(xs[2]+relief, top), (xs[2]+relief, top-d),
               (xs[2]+relief+bevel, top-d-lip), (xs[3]-relief-bevel, top-d-lip),
               (xs[3]-relief, top-d), (xs[3]-relief, top), (xs[3], top)]
    points += dust_top(xs[3], xs[4])
    points.append((xs[4], bottom))
    points += dust_bottom(xs[3], xs[4])
    points += [(xs[3]-relief, bottom), (xs[3]-relief, bottom+d),
               (xs[3]-relief-bevel, bottom+d+lip), (xs[2]+relief+bevel, bottom+d+lip),
               (xs[2]+relief, bottom+d), (xs[2]+relief, bottom), (xs[2], bottom)]
    points += dust_bottom(xs[1], xs[2])
    points += [(g, bottom), (0., bottom-bevel), (0., top+bevel)]
    creases = [(x, top, x, bottom) for x in xs[:4]]
    for a, b in [(xs[1], xs[2]), (xs[2], xs[3]), (xs[3], xs[4])]:
        creases += [(a+relief, top, b-relief, top), (a+relief, bottom, b-relief, bottom)]
    creases += [(xs[2]+relief, top-d, xs[3]-relief, top-d),
                (xs[2]+relief, bottom+d, xs[3]-relief, bottom+d)]
    glue = [(g, top), (0., top+bevel), (0., bottom-bevel), (g, bottom)]
    return {"outline": points, "creases": creases, "glue": glue,
            "blank_width_mm": xs[4], "blank_height_mm": h+2*flap,
            "relief_mm": relief, "tuck_lip_mm": lip, "dust_flap_mm": dust,
            "xs": xs, "top": top, "bottom": bottom}


def generate_structure(data: dict) -> dict:
    if not isinstance(data, dict):
        raise ValueError("Structure settings must be a JSON object")
    spec = StructureSpec.model_validate(data)
    geo = layout(spec)
    margin, legend = 12., 22.
    cw, ch = geo["blank_width_mm"] + 2*margin, geo["blank_height_mm"] + 2*margin + legend
    dimensions = {**spec.model_dump(exclude={"kind", "name"}),
                  "blank_width_mm": geo["blank_width_mm"], "blank_height_mm": geo["blank_height_mm"],
                  "canvas_width_mm": cw, "canvas_height_mm": ch,
                  "relief_mm": geo["relief_mm"], "tuck_lip_mm": geo["tuck_lip_mm"]}
    limitations = [
        "Unverified structural study. Obtain an approved dieline and a physical sample from your converter before production.",
        "Width, depth and height are nominal fold-line panel dimensions, not guaranteed internal capacity. Actual card-stack fit is unverified.",
        "Caliper changes an experimental flap-relief heuristic only; bend allowance, material grain, crease tooling and glue compatibility are not engineered.",
        "This straight-tuck study has no locking-bottom engineering, thumb notch, perforation, sheet nesting, gripper allowance or press-specific compensation.",
        "Cut, crease, glue and reference roles are SVG metadata and display colours, not certified PDF spot separations or machine-tool commands.",
        "Dimensions and legend are reference content on the annotated canvas. Do not send the complete reference drawing directly to a cutter.",
    ]
    root = ET.Element(f"{{{SVG}}}svg", {"version": "1.1", "width": number(cw)+"mm", "height": number(ch)+"mm",
        "viewBox": f"0 0 {number(cw)} {number(ch)}", "data-structure-status": "prototype_unverified"})
    ET.SubElement(root, f"{{{SVG}}}title").text = spec.name + " — unverified prototype"
    ET.SubElement(root, f"{{{SVG}}}desc").text = " ".join(limitations)
    def group(ident, role, attrs=None):
        return ET.SubElement(root, f"{{{SVG}}}g", {"id": ident, "data-production-role": role, **(attrs or {})})
    def point(p): return f"{number(p[0]+margin)} {number(p[1]+margin)}"
    cut = group("cut", "cut", {"fill": "none", "stroke": "#b73333", "stroke-width": ".25"})
    ET.SubElement(cut, f"{{{SVG}}}path", {"id": "cut-outline", "d": "M "+" L ".join(map(point, geo["outline"]))+" Z"})
    crease = group("crease", "crease", {"fill": "none", "stroke": "#275aa0", "stroke-width": ".2", "stroke-dasharray": "2 1"})
    for i, (x1, y1, x2, y2) in enumerate(geo["creases"]):
        ET.SubElement(crease, f"{{{SVG}}}line", {"id": f"crease-{i+1}", "x1": number(x1+margin), "y1": number(y1+margin), "x2": number(x2+margin), "y2": number(y2+margin)})
    glue = group("glue", "glue", {"fill": "#45875a", "fill-opacity": ".15", "stroke": "#45875a", "stroke-width": ".2"})
    ET.SubElement(glue, f"{{{SVG}}}polygon", {"id": "glue-guide", "points": " ".join(point(p) for p in geo["glue"])})
    refs = group("dimensions", "dimensions", {"fill": "#38291f", "font-size": "3", "font-family": "sans-serif"})
    for label, left, right in [("FRONT", geo["xs"][0], geo["xs"][1]), ("SIDE", geo["xs"][1], geo["xs"][2]), ("BACK", geo["xs"][2], geo["xs"][3]), ("SIDE", geo["xs"][3], geo["xs"][4])]:
        ET.SubElement(refs, f"{{{SVG}}}text", {"x": number((left+right)/2+margin), "y": number(geo["top"]+margin+spec.height_mm/2), "text-anchor": "middle"}).text = label
    for x1, y1, x2, y2 in [(margin, 6, cw-margin, 6), (6, margin, 6, margin+geo["blank_height_mm"])]:
        ET.SubElement(refs, f"{{{SVG}}}line", {"x1": number(x1), "y1": number(y1), "x2": number(x2), "y2": number(y2), "stroke": "#7b7367", "stroke-width": ".15"})
    ET.SubElement(refs, f"{{{SVG}}}text", {"x": number(cw/2), "y": "4.5", "text-anchor": "middle"}).text = f"Blank width {number(geo['blank_width_mm'])} mm"
    labels = group("legend", "legend", {"fill": "#38291f", "font-size": "3", "font-family": "sans-serif"})
    for i, text in enumerate(["PROTOTYPE / NO SUPPLIER APPROVAL", f"Nominal panels: {number(spec.width_mm)} W x {number(spec.depth_mm)} D x {number(spec.height_mm)} H mm", "RED: cut | BLUE: crease | GREEN: glue guide", "Reference dimensions/legend are not tooling operations."]):
        ET.SubElement(labels, f"{{{SVG}}}text", {"x": number(margin), "y": number(margin+geo["blank_height_mm"]+5+i*4)}).text = text
    svg = ET.tostring(root, encoding="unicode", xml_declaration=False)
    # Independent raster drawing from our own validated geometry; no SVG parser or URL resolver.
    scale = min(1400/cw, 1400/ch)
    image = Image.new("RGB", (min(1400, math.ceil(cw*scale)), min(1400, math.ceil(ch*scale))), "#fffdf5")
    draw = ImageDraw.Draw(image)
    px = lambda p: ((p[0]+margin)*scale, (p[1]+margin)*scale)
    draw.polygon([px(p) for p in geo["glue"]], fill="#d9eadb")
    outline = [px(p) for p in geo["outline"]]
    draw.line(outline+[outline[0]], fill="#b73333", width=max(1, round(.25*scale)))
    for x1, y1, x2, y2 in geo["creases"]:
        a, b = px((x1, y1)), px((x2, y2))
        draw.line([a, b], fill="#275aa0", width=max(1, round(.2*scale)))
    draw.text((margin*scale, (margin+geo["blank_height_mm"]+3)*scale), "PROTOTYPE / NO SUPPLIER APPROVAL", fill="#38291f")
    draw.text((margin*scale, (margin+geo["blank_height_mm"]+9)*scale), f"Nominal {number(spec.width_mm)} W x {number(spec.depth_mm)} D x {number(spec.height_mm)} H mm", fill="#38291f")
    buffer = io.BytesIO(); image.save(buffer, format="PNG")
    ident = uuid.uuid4().hex
    job = {"schema_version": 1, "workflow": "packaging", "name": spec.name,
           "trim_width_mm": cw, "trim_height_mm": ch, "bleed_mm": 0, "safe_mm": 0,
           "bleed_intent": "white_border", "color_policy": "review_rgb", "expected_roles": ["cut", "crease", "glue"],
           "supplier_confirmed": False, "placement": "fit_trim", "profile_name": "Generated / unverified carton study",
           "spec_source": "PressCheck parametric prototype; no supplier dieline",
           "printer_notes": "Annotated drawing canvas, not finished-carton trim. Confirm the structure, stock and tooling physically."}
    return {"id": ident, "schema_version": 1, "kind": spec.kind, "status": "prototype_unverified", "units": "mm",
            "specification": spec.model_dump(), "dimensions": dimensions, "svg": svg,
            "preview_png": "data:image/png;base64,"+base64.b64encode(buffer.getvalue()).decode(),
            "components": [{"id": "cut-outline", "role": "cut", "count": 1}, {"id": "crease", "role": "crease", "count": len(geo["creases"])}, {"id": "glue-guide", "role": "glue", "count": 1}, {"id": "dimensions", "role": "dimensions"}, {"id": "legend", "role": "legend"}],
            "limitations": limitations, "job": job,
            "project": magdobe_project(root, geo, job, cw, ch, margin)}
