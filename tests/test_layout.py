"""Offline test of the book layout: picture sizes, trimming of pictures WITHOUT text, text wrapped around figures,
source pages in the original book. Needs Pillow and the Typst binary (bin/typst) of the code copy.
Run from a COPY of the code: SB_DATA=<tmp> python test_layout.py"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import layoutfit as lf
from PIL import Image, ImageDraw

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


tmp = Path(tempfile.mkdtemp(prefix="sblayout-"))


def text_page(path, size=(1650, 1275), line_h=14, gap=40, lines=40, bg="white"):
    """A page with many lines of small 'text' (black bars of the height of a small font)."""
    im = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(im)
    y = 60
    for _ in range(lines):
        x = 80
        while x < size[0] - 200:
            wlen = 24 + (x * 7) % 40
            d.rectangle([x, y, x + wlen, y + line_h], fill=(60, 60, 60))
            x += wlen + 48                      # word gaps, so most of the page stays white like real text
        y += line_h + gap
    im.save(path)


def photo(path, size=(1650, 1275)):
    """A smooth, colourful picture (no text, no big flat colour) on a white margin."""
    im = Image.new("RGB", size, "white")
    px = im.load()
    for y in range(120, size[1] - 120):
        for x in range(150, size[0] - 150):
            px[x, y] = ((x * 3) % 200 + 30, (y * 2) % 200 + 30, ((x + y) % 150) + 60)
    im.save(path)


def blob_on_white(path, size=(1650, 1275)):
    im = Image.new("RGB", size, "white")
    ImageDraw.Draw(im).ellipse([600, 400, 1000, 800], fill=(200, 30, 30))   # no text, big white margins
    im.save(path)


# ---- size rule
p_text = tmp / "text.jpg"
text_page(p_text)
p_photo = tmp / "photo.jpg"
photo(p_photo)
cm_t, kind_t = lf.fit_width_cm(p_text)
cm_p, kind_p = lf.fit_width_cm(p_photo)
check("a page of small text keeps a large width", kind_t == "doc" and cm_t >= 13, f"{cm_t} {kind_t}")
check("a photo may shrink to its floor", kind_p == "photo" and cm_p <= lf.MIN_PHOTO_CM + 4, f"{cm_p} {kind_p}")
big = tmp / "big.jpg"
text_page(big, line_h=34, gap=70, lines=14)
cm_b, kind_b = lf.fit_width_cm(big)
check("big text may be smaller than small text", kind_b == "doc" and cm_b < cm_t, f"{cm_b} vs {cm_t}")
check("the floor for documents is kept", cm_b >= lf.MIN_DOC_CM)

# cache: second call is read from _fit.json
a = lf.fit_for(p_text)
check("fit cache file written", (tmp / lf.FIT_CACHE).exists())
check("fit cache gives the same answer", lf.fit_for(p_text) == a)

# ---- trimming: only pictures without text
p_blob = tmp / "blob.jpg"
blob_on_white(p_blob)
t_blob = lf.trim_copy(p_blob)
check("a picture without text is trimmed", t_blob != p_blob and Image.open(t_blob).size[0] < 1650 * 0.7, str(t_blob))
check("the original file is untouched", Image.open(p_blob).size == (1650, 1275))
check("a page with text is never cropped", lf.trim_copy(p_text) == p_text)
check("has_text sees the text page", lf.has_text(p_text) and not lf.has_text(p_blob))
check("trimming twice reuses the copy", lf.trim_copy(p_blob) == t_blob)

# ---- source-page plan (no rotation, ever)
port = tmp / "port.jpg"
text_page(port, size=(1275, 1650))
mode, w = lf.page_plan(port)
check("an upright page stays in the flow at full width", mode == "flow" and w <= lf.COL_CM and w >= 12, f"{mode} {w}")
mode, w = lf.page_plan(p_photo)
check("a photo slide shares the page with two more", mode == "flow" and w < 11, f"{mode} {w}")
dense = tmp / "dense.jpg"
text_page(dense, size=(1754, 1241), line_h=9, gap=16, lines=44)
mode, w = lf.page_plan(dense)
check("dense small print goes on its own landscape page", mode == "land" and w > lf.COL_CM, f"{mode} {w}")

# ---- grouping figures with text
TYP = """=== 1.1 Electric Current
==== 1.1.1 Definition
#figure(image("/figs/f1.jpg", width: 75.0%),
  caption: [
    Caption f1
  ]
)

The electrical current is the charge transport per time unit.

Second paragraph of plain text.

$ I = Q / t $

Text after the equation.

#figure(image("/figs/f2.jpg", width: 75.0%),
  caption: [
    Caption f2
  ]
)

$ s = I / A $

#figure(
  align(center)[#table(
    columns: 2,
    [a], [b],
  )]
  , kind: table
  )

- one
- two
"""
out = lf.wrap_figures(TYP)
check("a figure with text after it is wrapped", out.count("#wrapfig(") == 2, str(out.count("#wrapfig(")))
check("the two paragraphs follow the first figure", "Second paragraph of plain text." in out.split("#wrapfig(")[1].split("\n$ I")[0])
check("the equation is not pulled into the wrapped text", "$ I = Q / t $" in out and "$ I = Q / t $" not in out.split("#wrapfig(")[1].split("])")[0])
check("a figure followed by an equation is left alone", "#figure(image(\"/figs/f2.jpg\"" in out)
check("a table followed by a list is wrapped", "kind: table" in out.split("#wrapfig(")[-1] and "- one" in out.split("#wrapfig(")[-1])
check("headings stay headings", out.count("\n=== ") + out.startswith("=== ") == 1 and "==== 1.1.1 Definition" in out)
import re
w_in = sorted(re.findall(r"\w+", TYP))
w_out = sorted(w for w in re.findall(r"\w+", out) if w != "wrapfig")
check("grouping keeps every word of the source", w_in == w_out, f"{len(w_in)} vs {len(w_out)}")

# ---- size_images rewrites widths and trims
root = tmp / "root"
figs = root / "figs"
figs.mkdir(parents=True)
shutil.copy(p_blob, figs / "f1.jpg")
shutil.copy(p_text, figs / "f2.jpg")
r = lf.size_images(TYP, root)
check("the trimmed copy is used for a picture without text", '/figs/_trim/f1.v1.jpg' in r, r[:200])
check("a page with text keeps its own file", 'image("/figs/f2.jpg", width:' in r)
check("percent widths are replaced by cm", "width: 75.0%" not in r and "cm)" in r)
check("an unknown file is left unchanged", lf.size_images('image("/nope.jpg", width: 75.0%)', root) == 'image("/nope.jpg", width: 75.0%)')

# ---- Typst really compiles the wrapped text, from the vendored package, without network
typst = Path(__file__).resolve().parent / "bin" / "typst"
pkgs = Path(__file__).resolve().parent / "typst-packages"
if typst.exists() and pkgs.exists():
    shutil.copy(p_photo, figs / "f3.jpg")
    body = lf.size_images(TYP.replace("/figs/f1.jpg", "/figs/f3.jpg"), root)
    body = lf.wrap_figures(body.replace("Second paragraph of plain text.", "Long text. " * 120))
    (root / "t.typ").write_text('#set page(paper: "a4")\n' + lf.WRAP_PRELUDE + "\n" + body, encoding="utf-8")
    pr = subprocess.run([str(typst), "compile", "--package-cache-path", str(pkgs), "--root", str(root),
                         str(root / "t.typ"), str(root / "t.pdf")], capture_output=True, text=True,
                        env={**os.environ, "HTTPS_PROXY": "http://127.0.0.1:9", "HTTP_PROXY": "http://127.0.0.1:9"})
    check("typst compiles wrapped text with the vendored package", pr.returncode == 0 and (root / "t.pdf").exists(),
          pr.stderr[-400:])
else:
    print("skip typst compile (no bin/typst or typst-packages in this copy)")

shutil.rmtree(tmp, ignore_errors=True)
print("\n%d failed" % len(fails) if fails else "\nall layout checks passed")
sys.exit(1 if fails else 0)
