"""Offline test of HTML uploads: a saved page becomes text pages (formulas kept, scripts/styles/pictures dropped).
Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_html.py"""
import tempfile
from pathlib import Path

import sb
import web

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


tmp = Path(tempfile.mkdtemp())
sb.set_lang("uk")

PAGE = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Statics lecture 5</title>
<style>body{color:red}</style><script>var x = "<p>SCRIPTTEXT</p>";</script></head><body>
<h1>Equilibrium</h1>
<p>For a particle, \\(\\sum \\vec F = 0\\) and <span class="math inline">\\(M = F\\cdot d\\)</span>. Also $x_1$.</p>
<ul><li>First &amp; <b>bold</b></li><li>Second</li></ul>
<table><tr><th>Force</th><th>Unit</th></tr><tr><td>F</td><td>N</td></tr></table>
<img src="data:image/png;base64,QUFBQUFBQUE=" alt="free body diagram"><img src="fig.png">
<!-- HIDDENCOMMENT --><script>alert(1)</script></body></html>"""

f = tmp / "a.html"
f.write_text(PAGE, encoding="utf-8")
pages = sb.html_to_pages(f)
text = "\n\n".join(pages)
check("one short page", len(pages) == 1, pages)
check("title becomes a heading", text.startswith("# Statics lecture 5") and "# Equilibrium" in text, text[:80])
check("MathJax and dollar formulas become $…$", "$\\sum \\vec F = 0$" in text and "$M = F\\cdot d$" in text and "$x_1$" in text, text)
check("list and table survive", "- First & **bold**" in text and "Force" in text and "Unit" in text, text)
check("scripts, styles and comments are gone", not any(w in text for w in ("SCRIPTTEXT", "alert", "color:red", "HIDDENCOMMENT")), text)
check("pictures become a note, no base64", "(image: free body diagram)" in text and "(image)" in text and "QUFB" not in text and "base64" not in text, text)

# a long page is cut between paragraphs into pages of about HTML_PAGE characters
long = "<html><body>" + "".join(f"<p>Paragraph {i} " + "word " * 120 + "</p>" for i in range(40)) + "</body></html>"
f2 = tmp / "long.htm"
f2.write_text(long, encoding="utf-8")
lp = sb.html_to_pages(f2)
check("long page is split", len(lp) >= 4 and all(len(p) <= sb.HTML_PAGE for p in lp), [len(p) for p in lp])
check("nothing is lost in the split", all(f"Paragraph {i} " in "\n".join(lp) for i in range(40)))

# one paragraph longer than a page
f3 = tmp / "block.html"
f3.write_text("<html><body><pre>" + "\n".join(f"line {i} " + "x" * 60 for i in range(400)) + "</pre></body></html>", encoding="utf-8")
bp = sb.html_to_pages(f3)
check("a huge block is cut too", len(bp) >= 3 and all(len(p) <= sb.HTML_PAGE + 200 for p in bp), [len(p) for p in bp])

# a non-UTF-8 page (cp1251 meta charset)
f4 = tmp / "cp.html"
f4.write_bytes('<html><head><meta charset="windows-1251"></head><body><p>Рівновага сил</p></body></html>'.encode("cp1251"))
check("cp1251 page is read", "Рівновага сил" in "\n".join(sb.html_to_pages(f4)))

# prepare_pages numbers the parts and keeps them as text-only pages
work = tmp / "work"
pp, bad = sb.prepare_pages(tmp, ["a.html", "long.htm"], work)
check("prepare_pages: pages numbered on, no image", len(pp) == 1 + len(lp) and pp[0]["image"] is None and pp[0]["text"] == "p001.txt"
      and pp[-1]["n"] == len(pp) and not bad, (len(pp), bad))
check("prepare_pages: text stored", "Equilibrium" in (work / "text" / "p001.txt").read_text(encoding="utf-8"))
(tmp / "empty.html").write_text("<html><head><script>1</script></head><body>  </body></html>", encoding="utf-8")
pp2, bad2 = sb.prepare_pages(tmp, ["empty.html"], tmp / "work2")
check("an HTML with no text is reported, not crashed", not pp2 and len(bad2) == 1 and "empty.html" in bad2[0], bad2)
class _Sp:
    d = {"work": tmp / "nowork"}


check("HTML counts as more than one chunk for grouping", sb.est_pages(_Sp, tmp, {"id": "x", "files": ["a.html"]}) > sb.CHUNK)

# a stored HTML is never shown as a page of the site
check("uploaded HTML is sent as a download", ".html" not in web.SHOWN and ".htm" not in web.SHOWN)

# ---- is it an interactive page?
CODE = "let a = 0;\nfunction draw() { const c = document.getElementById('c').getContext('2d'); c.clearRect(0, 0, 300, 150);\n" \
       "  c.beginPath(); c.arc(150, 75, 10 + a, 0, 7); c.stroke(); }\n" \
       "document.getElementById('s').addEventListener('input', e => { a = +e.target.value; draw(); });\n" \
       "draw();  // the angle slider moves the vector and the plot is redrawn at once, with the numbers below it\n"
IA = ('<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
      '<title>Vector sum</title><style>body{margin:0}</style></head><body><input id="s" type="range"><canvas id="c"></canvas>'
      f'<script>{CODE}</script></body></html>')
check("slider + canvas + own script looks interactive", sb.looks_interactive(IA))
check("a plain lecture page does not", not sb.looks_interactive(PAGE))
check("only an external script (a saved site) does not",
      not sb.looks_interactive('<html><body><button>x</button><script src="https://cdn.example/app.js"></script></body></html>'))
check("only MathJax config does not",
      not sb.looks_interactive('<html><body><input><script>window.MathJax = {tex: {inlineMath: [["$","$"]]}}; ' + "// pad\n" * 80 + '</script></body></html>'))
check("a clean page has no problems", sb.page_problems(IA) == [], sb.page_problems(IA))
check("svg namespaces are not outside links", sb.page_problems(IA.replace("<canvas", '<svg xmlns="http://www.w3.org/2000/svg"></svg><canvas')) == [])
for what, bad in (("the network", IA.replace("draw();  //", "fetch('https://evil.example/x');  //", 1)),
                  ("an outside file", IA.replace("<canvas", '<img src="fig.png"><canvas')),
                  ("an outside script", IA.replace("<canvas", '<script src="https://cdn.example/lib.js"></script><canvas')),
                  ("storage", IA.replace("let a = 0;", "localStorage.setItem('k', 1);", 1))):
    pr = sb.page_problems(bad)
    check(f"{what} is reported, with the place", pr and "not allowed" in pr[0], pr)
check("a syntax error in the script is reported", any("syntax" in x for x in sb.page_problems(IA.replace("draw();  //", "draw(;  //", 1))),
      sb.page_problems(IA.replace("draw();  //", "draw(;  //", 1)))
check("a page that lost its script is reported", sb.page_problems("<html><body><p>hi</p></body></html>"))

# ---- the upload itself: interactive pages go to the model, the rest is read as text
import asyncio
import io
import os
from starlette.requests import Request

web.kick = lambda: None
SEEN = tmp / "seen.txt"
os.environ["FAKE_SEEN"] = str(SEEN)
BAD = IA.replace("draw();  //", "fetch('https://evil.example/x');  //", 1)


class Up:
    def __init__(self, name, data):
        self.filename, self._b = name, io.BytesIO(data if isinstance(data, bytes) else data.encode("utf-8"))

    async def read(self, n):
        return self._b.read(n)


def upload(files=(), subject="", code="", lang="uk", kind="notes"):
    r = Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                 "headers": [(b"tailscale-user-login", b"html@example.com"), (b"x-lang", lang.encode())]})
    try:
        return asyncio.run(web.api_upload(r, files=list(files), note="", subject=subject, kind=kind, topic="", topic_title="",
                                          date=sb.now().date().isoformat(), code=code)), None
    except web.HTTPException as e:
        return None, (e.status_code, e.detail)


me = sb.Space("html-example-com")
inbox = lambda: sorted(d.name for d in me.d["inbox"].iterdir() if d.is_dir() and not d.name.startswith("."))
subs = lambda: [sb.read_json(me.d["inbox"] / n / "submission.json") for n in inbox()]
pages_of = lambda subj: sorted((me.d["interactive"] / subj).glob("*.html")) if (me.d["interactive"] / subj).exists() else []

j, err = upload([Up("lecture5.html", PAGE)])
check("a text HTML is queued as notes", j and j["id"] and j["files"] == 1 and j["interactive"] == 0 and subs()[-1]["kind"] == "notes", (j, err))
j, err = upload([Up("sim.html", IA)], subject="nope")
check("an unknown subject is refused", err and err[0] == 400, (j, err))
j, err = upload([Up("sim.html", IA)])
mine = [x for x in subs() if x["label"] == "sim.html"]
check("an interactive HTML is queued for the model, no subject needed", j and j["interactive"] == 1 and j["files"] == 0
      and len(mine) == 1 and mine[0]["kind"] == "ipage" and mine[0]["subject"] == "" and not pages_of("math1"), (j, err, mine))
j, err = upload([Up("sim-again.html", IA)])
check("the same file again is a duplicate", err and err[0] == 409, (j, err))
j, err = upload([Up("sim2.html", IA.replace("Vector sum", "Vector sum 2")), Up("lecture6.html", PAGE.replace("Equilibrium", "Moments"))], subject="math1")
kind_of = {x["label"]: x["kind"] for x in subs()}
check("a mix: two submissions, one per kind", j and j["interactive"] == 1 and j["files"] == 1
      and kind_of.get("sim2.html") == "ipage" and kind_of.get("lecture6.html") == "notes", (j, err, kind_of))
j, err = upload([Up("evil.html", BAD)], subject="statics")
check("a page with a network call also goes to the model", j and j["interactive"] == 1, (j, err))
j, err = upload(code=IA.replace("Vector sum", "Pasted page"))
check("pasted HTML code is queued like a file", j and j["interactive"] == 1 and any(x["kind"] == "ipage" and any("pasted-page.html" in f for f in x["files"]) for x in subs()), (j, err))
j, err = upload(code="import React from 'react';\nexport default function App() { return <div/>; }")
check("pasted React code is queued like a file", j and j["interactive"] == 1 and any(x["kind"] == "ipage" and any("pasted-page.jsx" in f for f in x["files"]) for x in subs()), (j, err))
j, err = upload(code="just some words")
check("pasted text that is not code is refused", err and err[0] == 400, (j, err))
j, err = upload()
check("nothing at all is refused", err and err[0] == 400, (j, err))
j, err = upload([Up("a.png", b"x")], code="<canvas></canvas>", kind="asis")
check("page code only goes with notes", err and err[0] == 400, (j, err))

# ---- the worker: the model fixes and files each page
sb.worker()
check("all ipage submissions were processed", not any(s["kind"] == "ipage" for s in subs()), subs())
math = pages_of("math1")
stat = pages_of("statics")
metas = [sb.read_json(p.with_suffix(".json")) for p in math + stat]
check("a page was filed under the chosen subject", any(m["title"] == "Vector sum" for m in [sb.read_json(p.with_suffix(".json")) for p in math]), math)
check("a page without a chosen subject was filed where the model said", len(stat) >= 1, stat)
check("topic from the model is created", any(m["topic"] == "vectors" for m in metas) and
      any(t["id"] == "vectors" for sj in me.load_syllabus()["subjects"] for t in sj["topics"]), metas)
evil = [p for p in math + stat if "evil.example" in p.read_text(encoding="utf-8")]
check("the page with the network call was fixed before filing", not evil, evil)
fixed = [m for m in metas if m.get("changes")]
check("what the model changed is recorded", fixed and all(m["uploaded"] and m["orig_sha"] != m["sha"] for m in fixed), metas)
check("an untouched page keeps its sha as the original", any(m["orig_sha"] == m["sha"] for m in metas), metas)
check("every filed page passes the checks", all(sb.page_problems(p.read_text(encoding="utf-8")) == [] for p in math + stat))
check("the React source became a plain page", any("From React" in p.read_text(encoding="utf-8") for p in math + stat))
seen = SEEN.read_text(encoding="utf-8")
check("the model was told what the checker found", "not allowed (network" in seen and "evil.example" in seen)
check("the subject choice reached the prompt", "Subject the student chose: `math1`" in seen and "(none: choose it)" in seen)
done = [sb.read_json(me.d["processed"] / n / "submission.json") for n in os.listdir(me.d["processed"])
        if (me.d["processed"] / n / "submission.json").exists()]
check("processed submissions list the pages", any(d.get("interactive") for d in done), done)

# the original of a page that was fixed is a duplicate when it comes again
j, err = upload([Up("evil-again.html", BAD)], subject="statics")
check("the file as uploaded before is refused as a duplicate", err and err[0] == 409, (j, err))

# a model that cannot fix the page: nothing is filed when the original was broken, the original is kept when it was fine
os.environ["FAKE_IPAGE"] = "broken"
n_before = len(pages_of("math1"))
j, err = upload([Up("evil2.html", BAD.replace("Vector sum", "Evil 2"))], subject="math1")
sb.worker()
check("a page that stays broken is not filed", len(pages_of("math1")) == n_before)
check("and the upload failed", any((me.d["failed"] / n / "submission.json").exists() for n in os.listdir(me.d["failed"])), os.listdir(me.d["failed"]))
j, err = upload([Up("fine.html", IA.replace("Vector sum", "Fine page"))], subject="math1")
sb.worker()
check("a page that was fine is still filed when the model changes nothing", len(pages_of("math1")) == n_before + 1)

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
raise SystemExit(1 if fails else 0)
