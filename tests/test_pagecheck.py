"""Offline test: a page is RUN before it is filed (pagecheck/ in a locked-down container). Needs docker, the node:22-alpine image and
`npm install` in pagecheck/ (point SB_PAGECHECK_DIR at it); without them the test says so and stops with a skip (exit 0)."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("SB_DATA", tempfile.mkdtemp(prefix="sbpc-"))
os.environ.setdefault("SB_CLAUDE", str(HERE / "tests" / "fake_claude.py"))
os.environ.setdefault("SB_PAGECHECK_DIR", str(HERE / "pagecheck"))
sys.path.insert(0, str(HERE))
from starlette.requests import Request  # noqa: E402

import sb  # noqa: E402
import web  # noqa: E402

if not sb.pagecheck_ready():
    print("SKIP: the page checker is not installed here (docker, node:22-alpine and pagecheck/node_modules are needed)")
    sys.exit(0)

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


web.kick = lambda: None
sb.set_lang("en")


def page(script, body="<input id='s' type='range' min='0' max='10' value='5'><button id='b'>go</button><canvas id='c'></canvas>"):
    pad = "// filler so that the script is long enough to count as the page's own\n" * 8
    return f"<!DOCTYPE html><html><head><meta charset='utf-8'><title>t</title></head><body>{body}<p>text of the page</p><script>{pad}{script}</script></body></html>"


GOOD = page("const g = document.getElementById('c').getContext('2d'); function draw(){ g.clearRect(0,0,300,100); g.beginPath(); g.arc(50,50,20,0,6.28); g.stroke(); g.measureText('hi'); } "
            "draw(); document.getElementById('s').addEventListener('input', draw); document.getElementById('b').onclick = draw; requestAnimationFrame(function f(){ draw(); });")
LOAD_BUG = page("function ticks(n){ const k = 1; if (n > 0) { k += 1; } return k; } ticks(1);")
HANDLER_BUG = page("document.getElementById('s').addEventListener('input', e => { nothingHere(e.target.value); });")
CLICK_BUG = page("document.getElementById('b').onclick = () => { const o = null; o.x = 1; };")
BLANK = "<!DOCTYPE html><html><head><title>t</title></head><body><script>" + "var x = 1;\n" * 60 + "</script></body></html>"
HANG = page("document.getElementById('b').onclick = () => { while (true) {} };")

p = sb.page_runtime_problems(GOOD)
check("a working page (canvas, slider, button, animation loop) has no problems", p == [], p)
p = sb.page_runtime_problems(LOAD_BUG)
check("a crash on load is found, with where and which line", len(p) == 1 and "Assignment to constant variable" in p[0] and "when it loaded" in p[0] and "k += 1" in p[0], p)
p = sb.page_runtime_problems(HANDLER_BUG)
check("an error in the handler of a slider is found (the slider is moved by the checker)", len(p) == 1 and "nothingHere is not defined" in p[0] and "#s" in p[0], p)
p = sb.page_runtime_problems(CLICK_BUG)
check("an error in the handler of a button is found (the button is pressed)", len(p) == 1 and "#b" in p[0], p)
p = sb.page_runtime_problems(BLANK)
check("a page that shows nothing is found", len(p) == 1 and "shows nothing" in p[0], p)
p = sb.page_runtime_problems(LOAD_BUG, old=LOAD_BUG)
check("an error the old page had already is not counted against the new one", p == [], p)
p = sb.page_runtime_problems(LOAD_BUG, old=GOOD)
check("an error the old page did not have is", len(p) == 1, p)
p = sb.page_runtime_problems(GOOD, old=LOAD_BUG)
check("a page that fixes the old one is fine", p == [], p)
p = sb.page_check("<p>no script</p>")
check("the static rules come first (and the run is not started for a page that fails them)", len(p) == 1 and "interactive" in p[0], p)
p = sb.page_runtime_problems(HANG)
check("a page with an endless loop in a button is stopped and reported, not waited for", len(p) == 1 and "hangs" in p[0], p)
check("the same page is not run twice (answer kept)", sb.page_runtime_problems(GOOD) == [] and len(sb._pagecheck_cache) >= 6)

# the sandbox: no network, no host files, read-only. A page that tries to look outside only fails inside the page.
SNOOP = page("window.__r = []; try { __r.push(typeof process, typeof require, typeof fetch); } catch (e) {} "
             "document.getElementById('b').onclick = () => { throw new Error('seen:' + window.__r.join(',')); };")
p = sb.page_runtime_problems(SNOOP)
check("the page cannot see node's process or require, and has no fetch", len(p) == 1 and "seen:undefined,undefined,undefined" in p[0], p)

# the whole path of an edit: the model breaks the page, the checker's list goes back, the model repairs it
main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)
d = main.d["interactive"] / "math1"
d.mkdir(parents=True, exist_ok=True)


def put(stem, html):
    (d / f"{stem}.html").write_text(html, encoding="utf-8")
    sb.write_json(d / f"{stem}.json", {"title": "Plot", "topic": "t", "added": sb.now_iso()})


def call(path, sp, body):
    r = Request({"type": "http", "method": "POST", "path": path, "query_string": b"", "headers": [(b"x-lang", b"en")]})

    async def j():
        return body
    r.json = j
    r.state.sp = sp
    return r


web.who = lambda request: request.state.sp
calls = Path(tempfile.mkdtemp()) / "calls.jsonl"
os.environ["FAKE_CALLS"] = str(calls)


def ask(kind, **kw):
    j = asyncio.run(web.api_request(call("/api/request", main, {"kind": kind, "subject": "math1", "text": "make it better"} | kw)))
    return main.d["requests"] / f"{j['id']}.json"


def n_calls():
    return len(calls.read_text().splitlines()) if calls.exists() else 0


put("p1", GOOD)
os.environ["FAKE_IEDIT"] = "rt"
n0 = n_calls()
sb.process_request(main, ask("ipage_edit", stem="p1"))
now_html = (d / "p1.html").read_text(encoding="utf-8")
check("an edit that crashes the page is sent back and repaired (two model calls)", n_calls() - n0 == 2 and "q = 2" not in now_html and "Mass slider added" in now_html, (n_calls() - n0, now_html[-200:]))
check("... and the filed page runs", sb.page_runtime_problems(now_html) == [])

put("p2", GOOD)
os.environ["FAKE_IEDIT"] = "rt_always"
try:
    sb.process_request(main, ask("ipage_edit", stem="p2"))
    raised = ""
except sb.PermanentError as e:
    raised = str(e)
check("an edit that stays broken fails, with the reason", "does not work" in raised or "could not change the page" in raised, raised)
check("... and the page is left as it was", (d / "p2.html").read_text(encoding="utf-8") == GOOD)

# a brand-new page was filed with no check at all
for f in list(d.glob("*")):
    f.unlink()
os.environ["FAKE_IEDIT"] = "fixbug"
os.environ["FAKE_NEWPAGE"] = "bug"
n0 = n_calls()
sb.process_request(main, ask("interactive"))
new_pages = list(d.glob("*.html"))
check("a new page that crashes is repaired before it is filed", len(new_pages) == 1 and "q = 2" not in new_pages[0].read_text(encoding="utf-8") and n_calls() - n0 == 2, (len(new_pages), n_calls() - n0))
check("... and its title and topic from the first call are kept", sb.read_json(new_pages[0].with_suffix(".json")).get("title") == "Complex plane explorer")
for f in list(d.glob("*")):
    f.unlink()
os.environ["FAKE_IEDIT"] = "rt_always"
try:
    sb.process_request(main, ask("interactive"))
    raised = ""
except sb.PermanentError as e:
    raised = str(e)
check("a new page that stays broken is not filed", "does not work" in raised and not list(d.glob("*.html")), raised)
os.environ.pop("FAKE_NEWPAGE")
os.environ["FAKE_IEDIT"] = "ok"

# a suggestion that breaks a page
msg = sb.validate_change("interactive", GOOD, GOOD.replace("draw(); document", "tick(); document"))
check("an addition that breaks the page is refused by validate_change", msg and "does not work" in msg, msg)

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
