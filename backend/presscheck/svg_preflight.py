"""Read-only SVG screening. Unknown production intent is never treated as a pass.

Geometry uses SVG user coordinates mapped to physical millimetres. Path bounds are
conservative Bézier control hulls; they are not used to claim ink continuity or to
fail copy safety. This module never resolves a URL or opens a linked file.
"""

import base64
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import math
import re
import warnings
import uuid
from typing import Literal
from xml.etree import ElementTree as ET

from defusedxml import ElementTree as SafeET
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field

MAX_BYTES = 40 * 1024 * 1024
MAX_ELEMENTS = 100_000
MAX_DEPTH = 100
MAX_PATH_TOKENS = 2_000_000
SVG_NS = "http://www.w3.org/2000/svg"
CSS_MM = 25.4 / 96
ROLES = {"artwork", "cut", "crease", "perforation", "glue", "white", "foil",
         "white_ink", "varnish", "emboss", "deboss", "dimensions", "registration",
         "barcode", "legend"}
GEOMETRY = {"path", "rect", "circle", "ellipse", "line", "polyline", "polygon"}
NUMBER = r"[-+]?(?:\d*\.\d+|\d+\.?\d*)(?:[eE][-+]?\d+)?"
NUM_RE = re.compile(NUMBER)
LENGTH_RE = re.compile(rf"^\s*({NUMBER})\s*(mm|cm|in|pt|pc|px|%)?\s*$", re.I)
IDENTITY = (1., 0., 0., 1., 0., 0.)
# MuPDF does not support parallel thread access. All preview operations use the
# same dedicated thread, including calls arriving through separate HTTP threads.
_PREVIEW_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="presscheck-preview")


class PreflightJob(BaseModel):
    # Preserve named profile/source/placement metadata supplied by the native app.
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)
    schema_version: Literal[1] = 1
    workflow: Literal["trading_card", "packaging"] = "trading_card"
    name: str = Field("Custom / unverified specification", max_length=160)
    trim_width_mm: float | None = Field(None, gt=0, le=5000)
    trim_height_mm: float | None = Field(None, gt=0, le=5000)
    bleed_mm: float | None = Field(None, ge=0, le=300)
    safe_mm: float | None = Field(None, ge=0, le=300)
    bleed_intent: Literal["full_bleed", "white_border"] = "full_bleed"
    color_policy: Literal["review_rgb", "rgb", "cmyk"] = "review_rgb"
    min_text_pt: float | None = Field(None, gt=0, le=200)
    min_stroke_mm: float | None = Field(None, gt=0, le=100)
    min_raster_ppi: float | None = Field(None, gt=0, le=5000)
    expected_roles: list[str] = Field(default_factory=list, max_length=30)
    supplier_confirmed: bool = False


def local_name(tag):
    return tag.rsplit("}", 1)[-1]


def multiply(a, b):
    """Composition a(b(point)), using SVG's six-value affine representation."""
    result = (a[0]*b[0]+a[2]*b[1], a[1]*b[0]+a[3]*b[1],
            a[0]*b[2]+a[2]*b[3], a[1]*b[2]+a[3]*b[3],
            a[0]*b[4]+a[2]*b[5]+a[4], a[1]*b[4]+a[3]*b[5]+a[5])
    if not all(math.isfinite(v) and abs(v) <= 1e18 for v in result):
        raise ValueError("Combined transform exceeds inspection limits")
    return result


def numbers(value):
    matches = list(NUM_RE.finditer(value))
    end = 0
    result = []
    for match in matches:
        if value[end:match.start()].strip(" ,\t\r\n"):
            raise ValueError("Invalid numeric sequence")
        number = float(match.group())
        if not math.isfinite(number) or abs(number) > 1e9:
            raise ValueError("Nonfinite or excessive coordinate")
        result.append(number)
        end = match.end()
    if value[end:].strip(" ,\t\r\n"):
        raise ValueError("Invalid numeric sequence")
    return result


def transform(value):
    result, end = IDENTITY, 0
    for match in re.finditer(r"([A-Za-z]+)\s*\(([^)]*)\)", value):
        if value[end:match.start()].strip(" ,\t\r\n"):
            raise ValueError("Invalid transform")
        name, args = match.group(1), numbers(match.group(2))
        if name == "matrix" and len(args) == 6:
            current = tuple(args)
        elif name in ("translate", "scale") and len(args) in (1, 2):
            x, y = args[0], args[1] if len(args) == 2 else (0 if name == "translate" else args[0])
            current = (1, 0, 0, 1, x, y) if name == "translate" else (x, 0, 0, y, 0, 0)
        elif name == "rotate" and len(args) in (1, 3):
            radians = math.radians(args[0])
            c, s = math.cos(radians), math.sin(radians)
            current = (c, s, -s, c, 0, 0)
            if len(args) == 3:
                x, y = args[1:]
                current = multiply((1, 0, 0, 1, x, y), multiply(current, (1, 0, 0, 1, -x, -y)))
        elif name in ("skewX", "skewY") and len(args) == 1:
            t = math.tan(math.radians(args[0]))
            current = (1, 0, t, 1, 0, 0) if name == "skewX" else (1, t, 0, 1, 0, 0)
        else:
            raise ValueError("Unsupported transform")
        result = multiply(result, current)
        end = match.end()
    if value[end:].strip(" ,\t\r\n"):
        raise ValueError("Invalid transform")
    if not all(math.isfinite(v) and abs(v) <= 1e12 for v in result):
        raise ValueError("Excessive transform")
    return result


def length(value, relative=None):
    """Return SVG user units; percentages require an explicit viewport axis."""
    match = LENGTH_RE.fullmatch(str(value))
    if not match:
        raise ValueError("Unsupported length or unit")
    n, unit = float(match[1]), (match[2] or "").lower()
    if not math.isfinite(n) or abs(n) > 1e9:
        raise ValueError("Nonfinite or excessive length")
    if unit == "%":
        if relative is None:
            raise ValueError("Percentage has no known viewport")
        return n * relative / 100
    return n * {"": 1, "px": 1, "mm": 1/CSS_MM, "cm": 10/CSS_MM,
                "in": 96, "pt": 96/72, "pc": 16}[unit]


def viewport_matrix(viewbox, width, height, aspect="xMidYMid meet"):
    x, y, vw, vh = viewbox
    if vw <= 0 or vh <= 0 or width <= 0 or height <= 0:
        raise ValueError("Viewport dimensions must be positive")
    parts = aspect.strip().split()
    if parts and parts[0] == "defer":
        parts.pop(0)
    align = parts[0] if parts else "xMidYMid"
    mode = parts[1] if len(parts) == 2 else "meet"
    if len(parts) > 2 or mode not in ("meet", "slice"):
        raise ValueError("Unsupported preserveAspectRatio")
    sx, sy = width/vw, height/vh
    if align == "none":
        return (sx, 0, 0, sy, -x*sx, -y*sy)
    if not re.fullmatch(r"x(Min|Mid|Max)Y(Min|Mid|Max)", align):
        raise ValueError("Unsupported preserveAspectRatio")
    scale = min(sx, sy) if mode == "meet" else max(sx, sy)
    ax = {"Min": 0, "Mid": .5, "Max": 1}[align[1:4]]
    ay = {"Min": 0, "Mid": .5, "Max": 1}[align[5:]]
    return (scale, 0, 0, scale, (width-vw*scale)*ax-x*scale,
            (height-vh*scale)*ay-y*scale)


def scales(matrix):
    a, b, c, d = matrix[:4]
    trace = a*a+b*b+c*c+d*d
    determinant = (a*d-b*c)**2
    discriminant = math.sqrt(max(0., trace*trace-4*determinant))
    return math.sqrt(max(0., (trace-discriminant)/2)), math.sqrt(max(0., (trace+discriminant)/2))


def mapped_bounds(points, matrix):
    if not points:
        return None
    xs, ys = [], []
    for x, y in points:
        xs.append(matrix[0]*x+matrix[2]*y+matrix[4])
        ys.append(matrix[1]*x+matrix[3]*y+matrix[5])
    return [min(xs), min(ys), max(xs), max(ys)]


def path_points(data):
    """Conservative bounds for Béziers, including smooth reflected controls.

    Arcs are explicitly unavailable, rather than bounded by their endpoints.
    """
    tokens = re.findall(rf"[A-Za-z]|{NUMBER}|[^\s,]", data)
    if len(tokens) > MAX_PATH_TOKENS:
        raise ValueError("Path exceeds coordinate limit")
    points, index, command, x, y, start = [], 0, None, 0., 0., (0., 0.)
    previous, control = None, None
    widths = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}
    while index < len(tokens):
        if tokens[index].isalpha():
            command = tokens[index]
            index += 1
            if command.upper() == "Z":
                x, y = start
                points.append(start)
                previous, control, command = "Z", None, None
                continue
        if command is None or command.upper() not in widths:
            raise ValueError("Invalid SVG path command")
        kind, relative = command.upper(), command.islower()
        if not points and kind != "M":
            raise ValueError("SVG paths must begin with moveto")
        count = widths[kind]
        if index + count > len(tokens) or any(re.fullmatch(NUMBER, t) is None for t in tokens[index:index+count]):
            raise ValueError("Invalid SVG path coordinates")
        values = [float(t) for t in tokens[index:index+count]]
        if any(not math.isfinite(v) or abs(v) > 1e9 for v in values):
            raise ValueError("Nonfinite or excessive path coordinates")
        index += count
        if kind == "A":
            raise NotImplementedError("Elliptical arc bounds are not measured")
        if kind in ("H", "V"):
            x = values[0] + (x if relative else 0) if kind == "H" else x
            y = values[0] + (y if relative else 0) if kind == "V" else y
            control = None
        else:
            pairs = [(values[i] + (x if relative else 0), values[i+1] + (y if relative else 0)) for i in range(0, count, 2)]
            reflected = (x, y)
            if kind in ("S", "T") and previous in (("C", "S") if kind == "S" else ("Q", "T")) and control:
                reflected = (2*x-control[0], 2*y-control[1])
                points.append(reflected)
            points.extend(pairs)
            x, y = pairs[-1]
            control = pairs[-2] if kind in ("C", "S", "Q") else reflected if kind == "T" else None
            if kind == "M":
                start = (x, y)
                command = "l" if relative else "L"
        points.append((x, y))
        previous = kind
    return points


def geometry_points(element, viewport):
    tag, wv, hv = local_name(element.tag), *viewport
    def v(name, default="0", axis=None):
        return length(element.get(name, default), axis)
    if tag == "path":
        return path_points(element.get("d", ""))
    if tag in ("polyline", "polygon"):
        p = numbers(element.get("points", ""))
        if len(p) % 2:
            raise ValueError("Odd polygon coordinate count")
        return list(zip(p[::2], p[1::2]))
    if tag == "line":
        return [(v("x1", axis=wv), v("y1", axis=hv)), (v("x2", axis=wv), v("y2", axis=hv))]
    if tag in ("rect", "image"):
        x, y, w, h = v("x", axis=wv), v("y", axis=hv), v("width", axis=wv), v("height", axis=hv)
        if w < 0 or h < 0:
            raise ValueError("Negative geometry dimension")
        return [(x, y), (x+w, y), (x+w, y+h), (x, y+h)]
    x, y = v("cx", axis=wv), v("cy", axis=hv)
    normalized_diagonal = math.hypot(wv, hv)/math.sqrt(2)
    rx = v("r", axis=normalized_diagonal) if tag == "circle" else v("rx", axis=wv)
    ry = rx if tag == "circle" else v("ry", axis=hv)
    if rx < 0 or ry < 0:
        raise ValueError("Negative geometry radius")
    return [(x-rx, y-ry), (x+rx, y-ry), (x+rx, y+ry), (x-rx, y+ry)]


def parse_svg(content):
    if not content or len(content) > MAX_BYTES:
        raise ValueError("SVG must be nonempty and at most 40 MiB")
    try:
        root = SafeET.fromstring(content, forbid_dtd=True)
    except Exception as exc:
        raise ValueError("Invalid or unsafe SVG XML") from exc
    if root.tag not in (f"{{{SVG_NS}}}svg", "svg"):
        raise ValueError("The document root must be SVG")
    count, stack = 0, [(root, 1)]
    while stack:
        element, depth = stack.pop()
        count += 1
        if count > MAX_ELEMENTS or depth > MAX_DEPTH:
            raise ValueError("SVG exceeds element or nesting limit")
        stack.extend((child, depth+1) for child in element)
    return root


def analyze_svg(content: bytes, job: dict) -> dict:
    config = PreflightJob.model_validate(job)
    try:
        json.dumps(config.model_dump(), allow_nan=False)
    except (ValueError, TypeError, RecursionError) as exc:
        raise ValueError("Job specification must contain finite JSON values") from exc
    root = parse_svg(content)
    findings, limited, counts, ids, duplicate_ids = [], set(), Counter(), {}, set()
    def add(rule, status, message, objects=(), **details):
        findings.append({"id": f"F{len(findings)+1:04}", "rule_id": rule,
                         "status": status, "message": message,
                         "object_ids": list(objects), **details})
    def once(rule, status, message):
        if rule not in limited:
            limited.add(rule)
            add(rule, status, message)
    if re.search(br"<\?xml-stylesheet\b", content, re.I):
        add("svg.external_reference", "fail", "External XML stylesheets are blocked; nothing was fetched")
    for element in root.iter():
        tag = local_name(element.tag)
        counts[tag] += 1
        ident = element.get("id")
        if ident:
            if ident in ids:
                duplicate_ids.add(ident)
            ids[ident] = element
    if duplicate_ids:
        add("svg.ids", "fail", "Duplicate IDs make references ambiguous", sorted(duplicate_ids))
    else:
        add("svg.ids", "pass", "Element IDs contain no duplicates")
    width_mm = height_mm = None
    root_matrix, root_viewport, viewbox = None, None, None
    try:
        width, height = length(root.get("width", "")), length(root.get("height", ""))
        width_mm, height_mm = width*CSS_MM, height*CSS_MM
        if min(width_mm, height_mm) <= 0 or max(width_mm, height_mm) > 10000:
            raise ValueError("Physical canvas dimensions are outside supported limits")
        viewbox = numbers(root.get("viewBox", f"0 0 {width} {height}"))
        if len(viewbox) != 4:
            raise ValueError("viewBox requires four coordinates")
        root_matrix = viewport_matrix(viewbox, width_mm, height_mm, root.get("preserveAspectRatio", "xMidYMid meet"))
        root_matrix = multiply(root_matrix, transform(root.get("transform", "")))
        root_viewport = (viewbox[2], viewbox[3])
        add("svg.viewport", "pass", "Physical canvas and viewBox are valid", measured=[width_mm, height_mm], unit="mm")
        if any(not re.search(r"(?:mm|cm|in|pt|pc)\s*$", root.get(key, ""), re.I) for key in ("width", "height")):
            add("job.physical_intent", "requires_spec", "SVG pixels use the standard 96px/in conversion; confirm intended print size or export physical units")
    except ValueError as exc:
        root_matrix, root_viewport = None, None
        add("svg.viewport", "fail", str(exc))
    if not config.supplier_confirmed:
        add("job.supplier", "requires_spec", "Custom specification is unverified; printer, stock, finishing and tolerances need confirmation")
    else:
        add("job.supplier", "review", "Supplier confirmation is user supplied; this screening is not printer certification")
    if None in (config.trim_width_mm, config.trim_height_mm, config.bleed_mm, config.safe_mm):
        add("job.geometry", "requires_spec", "Set trim width/height, bleed and safety in millimetres")
    elif width_mm and height_mm:
        expected = [config.trim_width_mm+2*config.bleed_mm, config.trim_height_mm+2*config.bleed_mm]
        matches = all(abs(a-b) <= .05 for a, b in zip((width_mm, height_mm), expected))
        add("canvas.trim_bleed", "pass" if matches else "fail", "Canvas matches trim plus bleed" if matches else "Canvas does not match trim plus bleed; do not stretch artwork silently", measured=[width_mm, height_mm], expected=expected, unit="mm")
        if 2*config.safe_mm >= min(config.trim_width_mm, config.trim_height_mm):
            add("job.safe_area", "fail", "Safety inset leaves no printable safe area")
    if config.bleed_intent == "full_bleed":
        add("bleed.ink_continuity", "review", "Canvas extent does not prove background ink reaches every cut edge; inspect bleed coverage against the intended design")
    else:
        add("bleed.white_border", "review", "White border is intentional; verify border widths under the supplier's cutting tolerance")
    if config.color_policy == "cmyk":
        add("color.production", "unsupported", "SVG RGB paint cannot establish CMYK separations, ink limits, spot plates or PDF output intent; inspect the actual production file")
    elif config.color_policy == "review_rgb":
        add("color.production", "review", "SVG color is screened as RGB; printer conversion, gamut and output profile remain unverified")
    else:
        add("color.production", "pass", "RGB source matches the selected RGB input policy; this does not verify press color")
    if config.min_stroke_mm is None:
        add("job.minimum_stroke", "requires_spec", "No minimum printed stroke width supplied")
    if config.min_text_pt is None and counts["text"]:
        add("job.minimum_text", "requires_spec", "No minimum printed type size supplied")
    if config.workflow == "packaging":
        add("packaging.structure", "unsupported", "SVG screening alone does not verify folds, glue clearances, structural fit or manufacturability")
    rendered_vectors, text_objects, has_raster, roles = 0, 0, False, set()
    min_strokes, min_types, bounds, raster_results = [], [], {}, []
    css_unknown = bool(counts["style"])
    if css_unknown:
        once("svg.css", "unsupported", "Stylesheet CSS is not evaluated; computed visibility, paint and typography are unavailable")
    inherited_keys = {"stroke", "stroke-width", "font-size", "font-family", "visibility", "display", "fill", "vector-effect"}
    stack = [(root, root_matrix, root_viewport, {}, "artwork", False, False)]
    while stack:
        element, matrix, viewport, inherited, role, definition, hidden_parent = stack.pop()
        tag = local_name(element.tag)
        ident = element.get("id")
        object_ids = [ident] if ident else []
        if element.tag.startswith("{") and not element.tag.startswith("{" + SVG_NS + "}"):
            once("svg.foreign_namespace", "unsupported", "Foreign-namespace elements are not interpreted as SVG geometry")
            matrix = None
        style = inherited.copy()
        for key in inherited_keys:
            if element.get(key) is not None:
                style[key] = element.get(key)
        inline = element.get("style", "")
        for declaration in inline.split(";"):
            if ":" in declaration:
                key, value = declaration.split(":", 1)
                style[key.strip()] = value.strip()
        if any(key in style for key in ("transform", "font", "writing-mode", "x", "y", "width", "height", "cx", "cy", "r", "rx", "ry", "d")):
            once("svg.css_layout", "unsupported", "CSS transforms/font shorthand/writing modes are not measured")
            matrix = None
        local_role = element.get("data-production-role", role)
        if local_role == "white":
            local_role = "white_ink"
        if local_role not in ROLES:
            once("svg.roles_unknown", "unsupported", "An unrecognized production role is present")
        definition = definition or tag in ("defs", "clipPath", "mask", "symbol", "marker", "pattern")
        display_hidden = hidden_parent or style.get("display") == "none"
        hidden = display_hidden or style.get("visibility") in ("hidden", "collapse")
        if local_role in ROLES and tag in GEOMETRY | {"image", "text", "use"} and not definition and not hidden:
            roles.add(local_role)
        if element is not root:
            try:
                if matrix is not None:
                    matrix = multiply(matrix, transform(element.get("transform", "")))
                if tag == "svg" and matrix is not None:
                    x, y = length(element.get("x", 0), viewport[0]), length(element.get("y", 0), viewport[1])
                    w, h = length(element.get("width", "100%"), viewport[0]), length(element.get("height", "100%"), viewport[1])
                    nested = numbers(element.get("viewBox", f"0 0 {w} {h}"))
                    if len(nested) != 4:
                        raise ValueError("Invalid nested viewBox")
                    matrix = multiply(matrix, multiply((1, 0, 0, 1, x, y), viewport_matrix(nested, w, h, element.get("preserveAspectRatio", "xMidYMid meet"))))
                    viewport = (nested[2], nested[3])
            except ValueError as exc:
                add("svg.transform", "unsupported", str(exc), object_ids)
                matrix = None
        for key, value in element.attrib.items():
            name = local_name(key)
            if name.lower().startswith("on"):
                add("svg.active_content", "fail", "Event handlers are not permitted", object_ids)
            if name == "href":
                if value.startswith("#"):
                    if value[1:] not in ids:
                        add("svg.references", "fail", "Internal reference target is missing", object_ids)
                elif not (tag == "image" and value.startswith("data:")):
                    add("svg.external_reference", "fail", "External or file references are blocked; nothing was fetched", object_ids)
            for target in re.findall(r"url\(\s*['\"]?([^)'\"\s]+)", value, re.I):
                if not target.startswith("#"):
                    add("svg.external_reference", "fail", "External paint/resource references are blocked; nothing was fetched", object_ids)
                elif target[1:] not in ids:
                    add("svg.references", "fail", "Internal paint/resource target is missing", object_ids)
        if tag in ("script", "foreignObject", "iframe", "audio", "video"):
            add("svg.active_content", "fail", "Active or foreign content is not permitted", object_ids)
        if tag not in GEOMETRY | {"svg", "g", "defs", "title", "desc", "metadata", "linearGradient", "radialGradient", "stop", "clipPath", "text", "tspan", "image"}:
            once("svg.unsupported_elements", "unsupported", "Some SVG elements need separate review: " + ", ".join(sorted(set(counts)-GEOMETRY-{"svg", "g", "defs", "title", "desc", "metadata", "linearGradient", "radialGradient", "stop", "clipPath", "text", "tspan", "image"})))
        if any(element.get(key) not in (None, "none") or style.get(key) not in (None, "none") for key in ("filter", "mask", "clip-path")):
            once("svg.effects_bounds", "unsupported", "Clipped, masked or filtered visible bounds are not measured")
        if tag in GEOMETRY | {"use"} and not definition:
            rendered_vectors += 1
        if tag == "text" and not definition:
            text_objects += 1
        if tag in GEOMETRY | {"image"} and matrix is not None and not definition and not hidden and not css_unknown:
            try:
                box = mapped_bounds(geometry_points(element, viewport), matrix)
                if box and ident:
                    bounds[ident] = [round(v, 6) for v in box]
            except NotImplementedError as exc:
                once("svg.arc_bounds", "unsupported", str(exc))
            except ValueError as exc:
                add("svg.geometry", "fail", str(exc), object_ids)
        if not definition and not hidden and matrix is not None and not css_unknown:
            low, high = scales(matrix)
            uniform = math.isclose(low, high, rel_tol=1e-6, abs_tol=1e-10)
            if tag in GEOMETRY and style.get("stroke", "none") != "none":
                try:
                    if style.get("vector-effect", "none") != "none":
                        raise ValueError("Non-scaling stroke measurement is unavailable")
                    stroke = length(style.get("stroke-width", "1"))
                    if stroke < 0:
                        raise ValueError("Negative stroke width")
                    if not uniform:
                        raise ValueError("Nonuniform stroke transform needs review")
                    min_strokes.append((stroke*low, ident))
                except ValueError as exc:
                    once("stroke.measurement", "unsupported", str(exc))
            if tag in ("text", "tspan") and (element.text or "").strip():
                try:
                    if "font-size" not in style or not uniform:
                        raise ValueError("Text size requires explicit font-size and uniform scale")
                    size = length(style["font-size"])*low*72/25.4
                    if size <= 0:
                        raise ValueError("Nonpositive text size")
                    min_types.append((size, ident))
                except ValueError as exc:
                    once("text.measurement", "unsupported", str(exc))
        if tag == "image":
            has_raster = True
        if tag == "image" and not definition and not hidden:
            href = element.get("href", element.get("{http://www.w3.org/1999/xlink}href", ""))
            try:
                match = re.fullmatch(r"data:image/(png|jpeg);base64,([A-Za-z0-9+/=\s]+)", href)
                if not match or len(match[2]) > 12*1024*1024:
                    raise ValueError("Only bounded embedded PNG/JPEG raster dimensions are inspected")
                data = base64.b64decode(re.sub(r"\s", "", match[2]), validate=True)
                with warnings.catch_warnings():
                    warnings.simplefilter("error", Image.DecompressionBombWarning)
                    with Image.open(io.BytesIO(data)) as image:
                        pixels = image.size
                        if image.format not in ("PNG", "JPEG") or max(pixels) > 40000 or pixels[0]*pixels[1] > 40_000_000:
                            raise ValueError("Raster format or dimensions outside inspection limits")
                if matrix is None or viewport is None or css_unknown:
                    raise ValueError("Raster placement cannot be resolved")
                w, h = length(element.get("width", 0), viewport[0]), length(element.get("height", 0), viewport[1])
                if w <= 0 or h <= 0:
                    raise ValueError("Raster placement has no positive dimensions")
                placement = viewport_matrix((0, 0, *pixels), w, h, element.get("preserveAspectRatio", "xMidYMid meet"))
                _, largest = scales(multiply(matrix, placement))
                if largest <= 0:
                    raise ValueError("Degenerate raster placement")
                raster_results.append((25.4/largest, ident))
            except (ValueError, OSError, Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
                add("raster.measurement", "unsupported", str(exc), object_ids)
        stack.extend((child, matrix, viewport, style, local_role, definition, display_hidden) for child in reversed(list(element)))
    if counts["use"]:
        once("svg.instances", "unsupported", "Reusable use instances are counted but referenced geometry is not expanded for measurement")
    if text_objects:
        add("text.fonts", "review", "Editable text depends on fonts; this server does not prove font availability, licensing or production embedding")
        add("text.safe_bounds", "unsupported", "Glyph bounds and text safety cannot be proven without the actual font layout; inspect or outline a production copy")
    else:
        add("text.objects", "pass", "No editable text elements; outlined lettering cannot be identified or measured as font sizes")
    def threshold(rule, entries, minimum, unit):
        if not entries or minimum is None:
            return
        failures = [(v, ident) for v, ident in entries if v + 1e-8 < minimum]
        if failures:
            add(rule, "fail", "Measured printed size is below the configured minimum", [ident for _, ident in failures if ident][:100], measured=round(min(v for v, _ in failures), 6), expected=minimum, unit=unit)
        else:
            add(rule, "pass", "Measured printed sizes meet the configured minimum", measured=round(min(v for v, _ in entries), 6), expected=minimum, unit=unit)
    threshold("stroke.minimum", min_strokes, config.min_stroke_mm, "mm")
    threshold("text.minimum", min_types, config.min_text_pt, "pt")
    threshold("raster.effective_ppi", raster_results, config.min_raster_ppi, "ppi")
    if has_raster and config.min_raster_ppi is None:
        add("job.minimum_raster_ppi", "requires_spec", "No minimum effective raster resolution supplied")
    for role in config.expected_roles:
        role = "white_ink" if role == "white" else role
        if role not in ROLES:
            add("roles.expected", "requires_spec", "Requested production role is not recognized: " + role)
        elif role not in roles:
            add("roles.expected", "fail", "Required production role is missing: " + role)
        else:
            add("roles.expected", "pass", "Required production role is present: " + role)
    if roles & {"white", "white_ink", "foil", "varnish", "emboss", "deboss"}:
        add("finishing.production", "review", "Finishing roles are labels, not verified press separations; confirm mask values, feature gaps, overlap and registration tolerances with the supplier")
    states = {f["status"] for f in findings}
    status = "fail" if "fail" in states else "incomplete" if states & {"requires_spec", "unsupported"} else "review" if "review" in states else "pass_supported_checks"
    ident = uuid.uuid4().hex
    return {"id": ident, "schema_version": 1, "status": status,
            "source": {"name": "artwork.svg", "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content), "format": "SVG", "width_mm": width_mm, "height_mm": height_mm, "view_box": viewbox, "element_counts": dict(counts), "vector_objects": rendered_vectors, "text_objects": text_objects, "has_raster": has_raster, "object_bounds_mm": bounds, "production_roles": sorted(roles)},
            "job": config.model_dump(), "findings": findings,
            "limitations": ["Automated source screening is not production approval or Topps certification.", "SVG RGB appearance does not verify CMYK, ICC output intent, total ink, trapping, overprint or physical finishing.", "Bézier geometry bounds are conservative control hulls; they do not establish ink continuity or identify outlined lettering.", "Stroke checks do not measure minimum filled-shape feature sizes, gaps or outlined text sizes.", "Preview rendering and source analysis are separate; fonts, effects and unsupported geometry require manual review."],
            "report_url": f"/api/preflights/{ident}/report.json", "original_url": f"/api/preflights/{ident}/original"}


def render_safe_preview(content: bytes, report: dict) -> bytes | None:
    """Render through one consistent MuPDF thread across API and CLI callers."""
    return _PREVIEW_EXECUTOR.submit(_render_safe_preview, content, report).result()


def _render_safe_preview(content: bytes, report: dict) -> bytes | None:
    """Rasterize a passive allowlisted subset; the original SVG is never embedded."""
    allowed_tags = GEOMETRY | {"svg", "g", "defs", "linearGradient", "radialGradient", "stop", "clipPath", "title", "desc", "metadata"}
    allowed_attrs = {"id", "version", "overflow", "width", "height", "viewBox", "preserveAspectRatio", "x", "y", "x1", "x2", "y1", "y2", "cx", "cy", "r", "rx", "ry", "d", "points", "fill", "fill-rule", "fill-opacity", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-miterlimit", "stroke-opacity", "stroke-dasharray", "stroke-dashoffset", "opacity", "transform", "clip-path", "clip-rule", "clipPathUnits", "gradientUnits", "gradientTransform", "spreadMethod", "offset", "stop-color", "stop-opacity", "fx", "fy", "href", "display", "visibility", "style"}
    if any(f["rule_id"] in {"svg.active_content", "svg.external_reference", "svg.references", "svg.css", "svg.css_layout", "svg.viewport", "svg.transform", "svg.geometry"} and f["status"] != "pass" for f in report["findings"]):
        return None
    root = parse_svg(content)
    for element in root.iter():
        if element.tag.startswith("{") and not element.tag.startswith("{" + SVG_NS + "}"):
            return None
        if local_name(element.tag) not in allowed_tags:
            return None
        for key, value in list(element.attrib.items()):
            name = local_name(key)
            if name.startswith("data-"):
                del element.attrib[key]
                continue
            if name not in allowed_attrs or (name == "href" and not value.startswith("#")):
                return None
            if any(token in value for token in ("\\", "/*", "@")):
                return None
            if name == "style":
                # Only passive presentation declarations; CSS escapes/imports are excluded.
                if any(token in value for token in ("\\", "@", "/*", "<", ">")):
                    return None
                for declaration in value.split(";"):
                    if declaration.strip() and (":" not in declaration or declaration.split(":", 1)[0].strip() not in allowed_attrs-{"style", "href", "d", "points", "id"}):
                        return None
            for target in re.findall(r"url\(\s*['\"]?([^)'\"\s]+)", value, re.I):
                if not target.startswith("#"):
                    return None
    try:
        import pymupdf as fitz
        with fitz.open(stream=ET.tostring(root), filetype="svg") as document:
            page = document[0]
            edge = max(page.rect.width, page.rect.height)
            if edge <= 0:
                return None
            return page.get_pixmap(matrix=fitz.Matrix(1600/edge, 1600/edge), alpha=False).tobytes("png")
    except Exception:
        return None
