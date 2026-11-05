"""Example containers of the clean book: the text format the model writes, the checks the program runs on it, and the
checks of a redrawn diagram (SVG).

Pure functions; this module must NOT import sb (sb.py runs as __main__, see CLAUDE.md).

The model gets `clean.md` and writes `clean_ex.md`: the same text, in which every worked example is a container:

    === EXAMPLE | Title ===
    --- PROBLEM ---
    ...
    --- FIGURE ---           (optional: {{draw:d1}} = a redrawn diagram, or the old {{fig:f1}} marker)
    ...
    --- SOLUTION ---         (optional)
    ...
    --- EXPLANATION ---
    ...
    --- CHECK ---            (optional: small errors found in the example)
    ...
    === END ===

Rule of the owner: the text of the example and its formulas are NOT changed. The program therefore compares the text
outside EXPLANATION and CHECK with the old text: every formula must be there unchanged, the words must be the same."""
import collections
import re
import xml.etree.ElementTree as ET

START = re.compile(r"^=== EXAMPLE(?:[ \t]*\|[ \t]*(.*?))?[ \t]*===[ \t]*$")
END = re.compile(r"^=== END ===[ \t]*$")
SECTION = re.compile(r"^--- (PROBLEM|FIGURE|SOLUTION|EXPLANATION|CHECK) ---[ \t]*$")
ORDER = ("PROBLEM", "FIGURE", "SOLUTION", "EXPLANATION", "CHECK")
DRAW_RE = re.compile(r"\{\{draw:([A-Za-z0-9_-]+)\}\}")
FIG_RE = re.compile(r"\{\{fig:([A-Za-z0-9_-]+)\}\}")
MATH_RE = re.compile(r"(?<!\\)\$(.+?)(?<!\\)\$", re.S)
MD_BAD = re.compile(r"<[A-Za-z/!?]|!\[|\]\(|\{=|^\s*```\s*\{", re.M)
URL_RE = re.compile(r"https?://|\bwww\.|\bftp://|javascript:|vbscript:|\bdata:(?!image/)|\bfile:", re.I)

MAX_EXAMPLES = 40
MAX_TITLE = 100
MAX_EXPLANATION = 1800
MAX_CHECK = 900
SVG_MAX = 60_000
SVG_SIZE_MAX = 2000
SVG_TAGS = {"svg", "g", "defs", "line", "polyline", "polygon", "path", "circle", "ellipse", "rect", "text", "tspan",
            "marker", "title", "desc"}


class ExampleError(ValueError):
    """The text is not in the container format (English: the one who writes it is the model)."""


def parse(md):
    """-> [("text", markdown) | ("example", {"title": str, "sections": {NAME: markdown}})] in the order of the text."""
    out, text, ex, sec, buf = [], [], None, None, []

    def close_section():
        nonlocal sec, buf
        if sec is not None:
            ex["sections"][sec] = "\n".join(buf).strip("\n")
        sec, buf = None, []

    for n, line in enumerate(md.split("\n"), 1):
        m = START.match(line)
        if m:
            if ex is not None:
                raise ExampleError(f"line {n}: an example starts inside another example (close it with `=== END ===`)")
            if text:
                out.append(("text", "\n".join(text)))
                text = []
            ex = {"title": (m.group(1) or "").strip(), "sections": {}}
            continue
        if END.match(line):
            if ex is None:
                raise ExampleError(f"line {n}: `=== END ===` without an open example")
            close_section()
            out.append(("example", ex))
            ex = None
            continue
        if ex is None:
            text.append(line)
            continue
        s = SECTION.match(line)
        if s:
            name = s.group(1)
            if name in ex["sections"] or name == sec:
                raise ExampleError(f"line {n}: the part {name} is written twice in one example")
            if sec is not None and ORDER.index(name) < ORDER.index(sec):
                raise ExampleError(f"line {n}: the part {name} comes after {sec}; the order is {', '.join(ORDER)}")
            if any(ORDER.index(name) < ORDER.index(k) for k in ex["sections"]):
                raise ExampleError(f"line {n}: the part {name} is out of order; the order is {', '.join(ORDER)}")
            close_section()
            sec = name
            continue
        if sec is None:
            if line.strip():
                raise ExampleError(f"line {n}: text inside an example must be under `--- PROBLEM ---` or another part marker")
            continue
        buf.append(line)
    if ex is not None:
        raise ExampleError("the last example has no `=== END ===`")
    if text:
        out.append(("text", "\n".join(text)))
    return out


def examples_of(segments):
    return [s for kind, s in segments if kind == "example"]


def _norm_math(s):
    return re.sub(r"\s+", "", s)


def _words(s):
    s = MATH_RE.sub(" ", s)
    s = FIG_RE.sub(" ", DRAW_RE.sub(" ", s))
    return collections.Counter(re.findall(r"\w+", s.lower()))


def core_text(segments):
    """The text that must equal the old text: everything except the titles, EXPLANATION and CHECK, and the draw markers."""
    parts = []
    for kind, s in segments:
        if kind == "text":
            parts.append(s)
        else:
            for name in ("PROBLEM", "FIGURE", "SOLUTION"):
                if s["sections"].get(name):
                    parts.append(s["sections"][name])
    return "\n".join(parts)


def structure_problems(segments):
    out, exs = [], examples_of(segments)
    if len(exs) > MAX_EXAMPLES:
        out.append(f"{len(exs)} examples; at most {MAX_EXAMPLES} in one section")
    for i, ex in enumerate(exs, 1):
        sec = ex["sections"]
        if not sec.get("PROBLEM", "").strip():
            out.append(f"example {i}: the PROBLEM part is missing or empty")
        if not sec.get("EXPLANATION", "").strip():
            out.append(f"example {i}: the EXPLANATION part is missing or empty")
        if len(sec.get("EXPLANATION", "")) > MAX_EXPLANATION:
            out.append(f"example {i}: the EXPLANATION has {len(sec['EXPLANATION'])} characters; at most {MAX_EXPLANATION}")
        if len(sec.get("CHECK", "")) > MAX_CHECK:
            out.append(f"example {i}: the CHECK has {len(sec['CHECK'])} characters; at most {MAX_CHECK}")
        if not ex["title"] or len(ex["title"]) > MAX_TITLE:
            out.append(f"example {i}: the title must be 1 to {MAX_TITLE} characters")
        added = "\n".join([ex["title"], sec.get("EXPLANATION", ""), sec.get("CHECK", "")])
        if MD_BAD.search(added) or URL_RE.search(added):
            out.append(f"example {i}: the title, EXPLANATION or CHECK has a link, HTML or an image: this is not allowed")
        if DRAW_RE.search(sec.get("EXPLANATION", "") + sec.get("CHECK", "")) or FIG_RE.search(sec.get("EXPLANATION", "") + sec.get("CHECK", "")):
            out.append(f"example {i}: figure markers belong in PROBLEM, FIGURE or SOLUTION, not in EXPLANATION or CHECK")
    return out


def preservation_problems(old, core):
    """The text of the examples is not rewritten: formulas unchanged (the rule of the owner), words the same."""
    out = []
    fo = collections.Counter(_norm_math(m) for m in MATH_RE.findall(old))
    fn = collections.Counter(_norm_math(m) for m in MATH_RE.findall(core))
    missing = fo - fn
    if missing:
        sample = "; ".join(f"`{k[:60]}`" for k in list(missing)[:3])
        out.append(f"{sum(missing.values())} formulas of the text were changed or lost (copy every formula exactly as it is): {sample}")
    extra = fn - fo
    if sum(extra.values()) > max(2, int(0.03 * max(1, sum(fo.values())))):
        out.append(f"{sum(extra.values())} formulas appear in the text that were not there: new formulas belong only in EXPLANATION or CHECK")
    wo, wn = _words(old), _words(core)
    n = sum(wo.values()) or 1
    lost, added = sum((wo - wn).values()), sum((wn - wo).values())
    if lost > max(3, int(0.03 * n)):
        out.append(f"{lost} words of the text are missing: do not delete or rewrite the text of an example")
    if added > max(6, int(0.03 * n)):
        out.append(f"{added} words were added to the text outside EXPLANATION and CHECK: do not rewrite the text of an example")
    return out


def figure_problems(old, new, draw_of):
    """Every figure of the old text is still shown: by its own marker or by the drawing that replaces it."""
    out = []
    old_figs, new_figs, draws = set(FIG_RE.findall(old)), set(FIG_RE.findall(new)), set(DRAW_RE.findall(new))
    draw_of = draw_of if isinstance(draw_of, dict) else {}
    replaced = set()
    for d in draws:
        f = draw_of.get(d)
        if f not in old_figs:
            out.append(f"`{{{{draw:{d}}}}}` must say in result.json `draw_of` which `{{{{fig:...}}}}` of the text it redraws")
        else:
            replaced.add(f)
    if new_figs - old_figs:
        out.append("new `{{fig:...}}` markers are not allowed")
    if old_figs - new_figs - replaced:
        out.append("a `{{fig:...}}` marker of the text was lost: keep every figure (or redraw it and use its `{{draw:...}}` marker)")
    if replaced & new_figs:
        out.append("a figure is shown twice: its `{{fig:...}}` marker and its redraw are both in the text")
    return out


def draw_markers_outside(segments):
    """Draw markers in the plain text (not in an example): not allowed."""
    return sorted({d for kind, s in segments if kind == "text" for d in DRAW_RE.findall(s)})


def svg_problems(text):
    """Why an SVG drawing may not be used (English), else []. Only plain shapes and text: no script, no outside file."""
    out = []
    if len(text.encode("utf-8")) > SVG_MAX:
        out.append(f"the SVG is larger than {SVG_MAX} bytes")
    if re.search(r"<!DOCTYPE|<!ENTITY|<\?xml-stylesheet", text, re.I):
        return out + ["no DOCTYPE, ENTITY or stylesheet instruction in the SVG"]
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        return out + [f"the SVG is not valid XML: {e}"]
    if root.tag.split("}")[-1] != "svg":
        return out + ["the root element must be `<svg>`"]
    if "http://www.w3.org/2000/svg" not in root.tag and "xmlns" not in text[:400]:
        out.append('the `<svg>` needs `xmlns="http://www.w3.org/2000/svg"`')
    vb = (root.get("viewBox") or "").replace(",", " ").split()
    try:
        w, h = float(vb[2]), float(vb[3])
        if not (0 < w <= SVG_SIZE_MAX and 0 < h <= SVG_SIZE_MAX):
            out.append(f"the viewBox must be at most {SVG_SIZE_MAX} wide and high")
    except (IndexError, ValueError):
        out.append('the `<svg>` needs a `viewBox="0 0 width height"`')
    n = 0
    for el in root.iter():
        tag = el.tag.split("}")[-1] if isinstance(el.tag, str) else ""
        n += 1
        if tag not in SVG_TAGS:
            out.append(f"the element `<{tag}>` is not allowed (allowed: {', '.join(sorted(SVG_TAGS))})")
            continue
        for attr, val in el.attrib.items():
            name = attr.split("}")[-1].lower()
            v = str(val).strip()
            if name.startswith("on"):
                out.append(f"the attribute `{name}` is not allowed")
            elif name == "href" and not v.startswith("#"):
                out.append("an `href` may only point to an element of the same drawing (`#id`)")
            elif "javascript" in v.lower() or ("url(" in v.lower() and not re.fullmatch(r"url\(\s*#[\w-]+\s*\)", v)):
                out.append(f"the value of `{name}` is not allowed")
            elif name == "style" and re.search(r"url\(|@import|expression", v, re.I):
                out.append("the `style` attribute may not load anything")
    if n < 3:
        out.append("the drawing is empty")
    return sorted(set(out))
