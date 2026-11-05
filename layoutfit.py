"""Book layout helpers: how large a picture must be, and text that wraps around figures and tables.

Pure functions. This module must NOT import sb (sb.py runs as __main__; see CLAUDE.md).

Size rule. A picture shrinks only while nothing is lost:
  * a picture (photo, drawing with big areas of colour): shrinks while its detail survives a
    shrink-and-enlarge test at the reader's screen resolution;
  * a page of text, a table, a diagram: stays large enough that its smallest body-text line
    is at least MIN_LINE_CM tall after printing (about a 6 pt font);
  * when the measurement is not reliable, the picture keeps its full width.
The image file itself is never changed: only the size it is drawn at.
"""
import json
import re
from pathlib import Path

COL_CM = 16.6          # text width of the book page
FIG_MAX_CM = 12.5      # a figure inside the text never grows past this (the old fixed 75 %): the rule only shrinks
REF_PX = 1500          # analysis width
SCREEN_DPI = 100       # reader's screen at 100 % zoom
MIN_LINE_CM = 0.19     # ink height of the smallest body-text line after printing
MIN_DOC_CM = 10.0
MIN_PHOTO_CM = 8.0
MAX_TALL_CM = 15.0     # a picture in the middle of the text is never taller than this
PHOTO_FLAT = 0.8       # share of one flat colour below which a picture counts as a photo
FIT_CACHE = "_fit.json"
FIT_VERSION = 1


def _open_gray(path):
    from PIL import Image, ImageOps
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        pass
    return ImageOps.exif_transpose(Image.open(path)).convert("L")


def _mask(im, bg):
    from PIL import Image, ImageChops
    return ImageChops.difference(im, Image.new("L", im.size, bg)).point(lambda v: 255 if v > 40 else 0).convert("1")


def _area(m):
    from PIL import ImageStat
    return ImageStat.Stat(m.convert("L")).sum[0] / 255


def _line_height_px(mask):
    """Ink-weighted 25th percentile of the heights of text lines (runs of inked rows), px at REF_PX width."""
    from PIL import Image
    w, h = mask.size
    prof = mask.convert("L").resize((1, h), Image.BOX).tobytes()      # mean ink of every row, 0..255
    runs, cur, ink = [], 0, 0
    for v in list(prof) + [0]:
        if v > 6:
            cur += 1
            ink += v
        elif cur:
            runs.append((cur, ink))
            cur, ink = 0, 0
    runs = [(a, b) for a, b in runs if 5 <= a <= 70]
    if len(runs) < 4:
        return None
    runs.sort()
    total, acc = sum(b for _, b in runs), 0
    for a, b in runs:
        acc += b
        if acc >= total * 0.25:
            return a
    return None


def fit_width_cm(path, full_cm=COL_CM):
    """Width in cm at which the picture can be drawn without losing anything. Returns (cm, kind)."""
    from PIL import Image, ImageChops
    im = _open_gray(path)
    w0, h0 = im.size
    if w0 < 40 or h0 < 40:
        return full_cm, "small"
    ref = im.resize((REF_PX, max(1, round(h0 * REF_PX / w0))), Image.LANCZOS)
    hist = ref.histogram()
    bg = max(range(256), key=lambda i: hist[i])
    flat = sum(hist[max(0, bg - 12):bg + 13]) / (ref.size[0] * ref.size[1])
    m0 = _mask(ref, bg)
    if _area(m0) < 50:
        return full_cm, "blank"
    if flat < PHOTO_FLAT:
        def iou(w):
            px = max(8, round(w / 2.54 * SCREEN_DPI))
            back = im.resize((px, max(1, round(h0 * px / w0))), Image.BOX).resize(ref.size, Image.BILINEAR)
            m1 = _mask(back, bg)
            u = _area(ImageChops.logical_or(m0, m1))
            return _area(ImageChops.logical_and(m0, m1)) / u if u else 1.0
        base, w = iou(full_cm), full_cm
        for cand in (8, 9, 10, 11, 12, 13, 14, 15, 16):
            if cand <= full_cm and iou(cand) >= 0.9 * base:
                w = cand
                break
        return max(MIN_PHOTO_CM, w), "photo"
    lh = _line_height_px(m0)
    if lh is None:
        return full_cm, "unsure"
    w = MIN_LINE_CM * REF_PX / lh
    return min(full_cm, max(MIN_DOC_CM, round(w * 2) / 2)), "doc"


TRIM_DIR = "_trim"
TRIM_MIN_GAIN = 0.08   # crop only when it removes at least this share of the area
TRIM_PAD = 0.02        # keep this much of the size as a border around the content
TRIM_VERSION = 1


def has_text(path):
    """True when the picture has anything that looks like a line of text (or thin line art). When in doubt: True."""
    from PIL import Image
    im = _open_gray(path)
    w0, h0 = im.size
    ref = im.resize((REF_PX, max(1, round(h0 * REF_PX / max(w0, 1)))), Image.LANCZOS)
    hist = ref.histogram()
    bg = max(range(256), key=lambda i: hist[i])
    prof = _mask(ref, bg).convert("L").resize((1, ref.size[1]), Image.BOX).tobytes()
    cur = 0
    for v in list(prof) + [0]:
        if v > 6:
            cur += 1
        else:
            if 5 <= cur <= 70:
                return True
            cur = 0
    return False


def trim_copy(path):
    """For a picture WITHOUT text: a copy with the empty margins cut off (original untouched). A picture with
    text is never cropped. Returns the path to use (the copy, or the original when nothing is cut)."""
    from PIL import Image, ImageChops, ImageOps
    path = Path(path)
    out_dir = path.parent / TRIM_DIR
    out = out_dir / f"{path.stem}.v{TRIM_VERSION}.jpg"
    marker = out_dir / f"{path.stem}.v{TRIM_VERSION}.none"
    st = path.stat()
    for f in (out, marker):
        if f.exists() and f.stat().st_mtime >= st.st_mtime:
            return out if f is out else path
    if has_text(path):
        keep = None
    else:
        im = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
        g = im.convert("L")
        hist = g.histogram()
        bg = max(range(256), key=lambda i: hist[i])
        box = ImageChops.difference(g, Image.new("L", g.size, bg)).point(lambda v: 255 if v > 25 else 0).getbbox()
        keep = None
        if box:
            w, h = im.size
            px, py = round(w * TRIM_PAD), round(h * TRIM_PAD)
            box = (max(0, box[0] - px), max(0, box[1] - py), min(w, box[2] + px), min(h, box[3] + py))
            if (box[2] - box[0]) * (box[3] - box[1]) <= (1 - TRIM_MIN_GAIN) * w * h:
                keep = im.crop(box)
    try:
        out_dir.mkdir(exist_ok=True)
        if keep is None:
            marker.write_text("", encoding="utf-8")
            return path
        keep.save(out, "JPEG", quality=92)
        return out
    except OSError:
        return path


PAGE_H_CM = 24.9       # text height of the book page
LABEL_CM = 1.6         # room for the caption line under a picture
LAND_W_CM, LAND_H_CM = 25.0, 15.8    # picture area of a landscape A4 page (1.5 cm margins, header, caption line)


def page_plan(path):
    """How a source page (photo or slide) goes into the original book: ("flow", cm) = in the text flow, so
    two or three share one page; ("land", cm) = on its own landscape page (small print stays readable,
    nothing is rotated). Returns (mode, width_cm)."""
    from PIL import Image
    path = Path(path)
    with Image.open(path) as im:
        w, h = im.size
    asp = w / max(h, 1)
    if asp < 0.95:                                   # upright page: shrinking only adds margin
        return "flow", round(min(COL_CM, (PAGE_H_CM - LABEL_CM) * asp), 1)
    need, kind = fit_for(path)
    if kind in ("unsure", "blank", "small"):
        need = 10.0 if kind != "small" else COL_CM
    n3 = (PAGE_H_CM - 3 * LABEL_CM) / 3 * asp        # widest picture that still lets three share a page
    n2 = (PAGE_H_CM - 2 * LABEL_CM) / 2 * asp        # same for two
    if need <= n3:
        return "flow", round(n3, 1)
    if need <= n2 * 1.08:                            # up to 8 % under the ideal size is still readable
        return "flow", round(n2, 1)
    return "land", round(min(LAND_W_CM, LAND_H_CM * asp), 1)


def fit_for(path, cache_dir=None, full_cm=COL_CM):
    """fit_width_cm with a small cache next to the pictures (keyed by size and mtime)."""
    path = Path(path)
    cache_file = (Path(cache_dir) if cache_dir else path.parent) / FIT_CACHE
    try:
        cache = json.loads(cache_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cache = {}
    st = path.stat()
    key = f"{FIT_VERSION}:{st.st_size}:{int(st.st_mtime)}:{full_cm}"
    hit = cache.get(path.name)
    if hit and hit[0] == key:
        return hit[1], hit[2]
    cm, kind = fit_width_cm(path, full_cm)
    cache[path.name] = [key, cm, kind]
    try:
        cache_file.write_text(json.dumps(cache), encoding="utf-8")
    except OSError:
        pass
    return cm, kind


# ----------------------------------------------------------------------------- Typst post-processing

IMG_RE = re.compile(r'image\("([^"]+)",\s*width:\s*[\d.]+%\)')


def size_images(typ, root, rel_ok=lambda p: True):
    """Replace `image("p", width: 75%)` by a width in cm chosen with fit_for. Unknown files stay unchanged."""
    root = Path(root)

    def rep(m):
        p = root / m.group(1).lstrip("/")
        if not p.is_file() or not rel_ok(m.group(1)):
            return m.group(0)
        try:
            from PIL import Image
            p = trim_copy(p)                       # only a picture without text is cut
            cm, _ = fit_for(p, full_cm=FIG_MAX_CM)
            with Image.open(p) as im:
                w, h = im.size
            cm = min(cm, MAX_TALL_CM * w / max(h, 1))
            rel = "/" + p.relative_to(root).as_posix()
        except Exception:
            return m.group(0)
        return f'image("{rel}", width: {cm:.1f}cm)'
    return IMG_RE.sub(rep, typ)


def _blocks(typ):
    """Split Typst source at blank lines, but never inside brackets/parentheses."""
    out, cur, depth = [], [], 0
    for line in typ.split("\n"):
        if not line.strip() and depth <= 0 and cur:
            out.append("\n".join(cur))
            cur = []
            continue
        if not line.strip() and not cur:
            continue
        # pandoc puts a figure straight after its heading, with no blank line: split there
        if depth <= 0 and cur and (line.startswith("#figure(") or line.startswith("=")
                                   or cur[0].startswith("=") and len(cur) == 1):
            out.append("\n".join(cur))
            cur = []
        cur.append(line)
        depth += line.count("[") + line.count("(") - line.count("]") - line.count(")")
    if cur:
        out.append("\n".join(cur))
    return out


def _kind(block):
    if block.startswith("#figure("):
        return "fig"
    if block.startswith(("=", "$", "```", "#table", "#block", "#grid", "#heading", "#pagebreak", "#v(", "#align",
                         "#set", "#show", "#let", "#import", "#entrymeta", "#summarybox", "#explainbox", "#text(size")):
        return "other"
    if block.startswith(("-", "+", "/ ")) or block[:1].isdigit():
        return "list"
    if block.startswith("#strong[") and "\n" not in block and len(block) < 60:
        return "label"
    return "text"


WRAP_MAX_CHARS = 1100
WRAP_MAX_BLOCKS = 4


def wrap_figures(typ):
    """Group every figure/table with the plain text that follows it: `#wrapfig(figure, [text])`.
    Typst (see WRAP_PRELUDE) decides by measuring whether the text really wraps."""
    blocks = _blocks(typ)
    out, i = [], 0
    while i < len(blocks):
        b = blocks[i]
        if _kind(b) != "fig":
            out.append(b)
            i += 1
            continue
        body, chars, j = [], 0, i + 1
        while j < len(blocks) and len(body) < WRAP_MAX_BLOCKS:
            k = _kind(blocks[j])
            if k not in ("text", "list"):
                break
            if chars + len(blocks[j]) > WRAP_MAX_CHARS and body:
                break
            body.append(blocks[j])
            chars += len(blocks[j])
            j += 1
        if body:
            out.append("#wrapfig(\n" + b[1:] + ",\n[\n" + "\n\n".join(body) + "\n])")
        else:
            out.append(b)
        i = j if body else i + 1
    return "\n\n".join(out) + "\n"


WRAP_PRELUDE = r"""
#import "@preview/wrap-it:0.1.1": wrap-content
#let FIG-GAP = 1.1em
#show math.equation.where(block: true): set block(above: 0.8em, below: 0.8em)
// figure and table: a thin frame and clear space around it; the caption stays inside the frame
#show figure.where(kind: image): it => block(above: 1.1em, below: 1.1em, breakable: false,
  stroke: 0.5pt + luma(185), inset: (x: 7pt, y: 6pt), radius: 2pt, width: auto, align(center, it))
#show figure.where(kind: table): it => block(above: 1.1em, below: 1.1em, it)
#show figure.caption: it => text(size: 8.5pt, fill: luma(80), it)
// wrap the text around a figure when the figure is narrow and the text is long enough; else a block, then the text
#let wrapfig(fig, body) = layout(size => {
  let colw = size.width
  let m = measure(fig)
  let bw = colw - m.width - FIG-GAP.to-absolute()
  if m.width > colw * 0.62 or bw < 5cm { fig; body } else {
    let bh = measure(body, width: bw).height
    if bh < m.height * 0.7 { fig; body } else {
      wrap-content(fig, body, align: top + right, column-gutter: FIG-GAP)
    }
  }
})
"""
