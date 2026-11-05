"""Offline test of combined interactive pages: a person picks which pages of ONE subject go on one page with a menu.
Run from a COPY of the code, fresh data dir: SB_DATA=<tmp> python test_combine.py (never on the real data)"""
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parent))
import combine  # noqa: E402
import sb  # noqa: E402
import web  # noqa: E402
from starlette.requests import Request  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def hdrs(login):
    return [(b"tailscale-user-login", login.encode()), (b"x-lang", b"en")]


def req(login="owner@example.com", path="/"):
    return Request({"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": hdrs(login)})


def jreq(body, login="owner@example.com"):
    sent = [False]

    async def receive():
        if sent[0]:
            return {"type": "http.disconnect"}
        sent[0] = True
        return {"type": "http.request", "body": json.dumps(body).encode(), "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"", "headers": hdrs(login)}, receive)


def call(fn, body, login="owner@example.com"):
    try:
        return asyncio.run(fn(jreq(body, login))), None
    except web.HTTPException as e:
        return None, (e.status_code, e.detail)


main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)
subs = [s["id"] for s in main.load_syllabus()["subjects"]]
A, B = subs[0], subs[1]


def page(subject, stem, title, topic="t", added="2026-10-26T01:00:00+01:00", by=None):
    d = main.d["interactive"] / subject
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{stem}.html").write_text("<p>x</p>", encoding="utf-8")
    meta = {"title": title, "topic": topic, "topic_title": topic.title(), "added": added}
    if by:
        meta["by"] = by
    sb.write_json(d / f"{stem}.json", meta)


for i in range(1, 5):
    page(A, f"a{i}", f"Page A{i}", topic="alpha" if i < 3 else "beta")
page(A, "dup1", "Same title", added="2026-10-26T01:00:00+01:00")
page(A, "dup2", "Same title", added="2026-10-28T01:00:00+01:00")
page(A, "evil", "</script><script>alert(1)</script> & <b>x</b>", by="pub-secret-person")
page(B, "b1", "Page B1")
page(B, "b2", "Page B2")

# ---- the module
idir = main.d["interactive"] / A
check("nothing combined yet", combine.listing(idir) == [])
for bad, why in [(["a1"], "one page"), ([], "none"), (["a1", "a1"], "the same page twice"), (["a1", "nope"], "a missing page"),
                 (["a1", "../x"], "a path"), (["a1", "b1"], "a page of ANOTHER subject"), ("a1", "not a list")]:
    try:
        combine.create(idir, "T", bad, "u")
        check(f"rejected: {why}", False)
    except combine.CombineError:
        check(f"rejected: {why}", True)
try:
    combine.create(idir, "   ", ["a1", "a2"], "u")
    check("rejected: an empty title", False)
except combine.CombineError:
    check("rejected: an empty title", True)
check("clean_title removes control characters and cuts long text",
      combine.clean_title("a\x00b\x01 c" + "x" * 200).startswith("a b c") and len(combine.clean_title("x" * 200)) == combine.MAX_TITLE)

rec = combine.create(idir, "My set", ["a2", "a1", "a2"], "u1")
check("the chosen parts are stored once each", combine.get(idir, rec["id"])["parts"] == ["a2", "a1"])
check("the pages are not touched or copied", (idir / "a1.html").read_text() == "<p>x</p>")
check("a combined page does not show up as an ordinary page", all(p["stem"] != rec["id"] for p in sb.interactive_pages(main, A)))
v = combine.view(combine.get(idir, rec["id"]), sb.interactive_pages(main, A))
check("the view groups by topic and follows the study order, not the ticked order", [g["topic"] for g in v] == ["Alpha"] and [p["stem"] for p in v[0]["pages"]] == ["a1", "a2"], v)
sb.write_json(idir / "a1.json", {"title": "Page A1 edited", "topic": "alpha", "topic_title": "Alpha", "added": "x"})
v = combine.view(combine.get(idir, rec["id"]), sb.interactive_pages(main, A))
check("an edited page shows its new title (the choice is live)", "Page A1 edited" in [p["title"] for p in v[0]["pages"]])
(idir / "a2.json").unlink()
(idir / "a2.html").unlink()
v = combine.view(combine.get(idir, rec["id"]), sb.interactive_pages(main, A))
check("a deleted page is left out, the rest stays", [p["stem"] for p in v[0]["pages"]] == ["a1"])
page(A, "a2", "Page A2", topic="alpha")
check("a bad id cannot be deleted or read", not combine.delete(idir, "../x") and combine.get(idir, "../x") is None)
check("delete works", combine.delete(idir, rec["id"]) and combine.listing(idir) == [])
made = []
for i in range(combine.MAX_PER_SUBJECT):
    made.append(combine.create(idir, f"S{i}", ["a1", "a2"], "u"))
try:
    combine.create(idir, "one too many", ["a1", "a2"], "u")
    check("the per-subject cap holds", False)
except combine.CombineError:
    check("the per-subject cap holds", True)
for c in combine.listing(idir):
    combine.delete(idir, c["id"])

# ---- the web layer
r, e = call(web.api_combine, {"subject": A, "title": "Owner set", "stems": ["a1", "a3", "evil", "dup1", "dup2"]})
check("the owner creates a combined page", r and r["ok"] and r["url"] == f"/c/{A}/{r['id']}", (r, e))
cid = r["id"]
r2, e2 = call(web.api_combine, {"subject": A, "title": "x", "stems": ["a1", "b1"]})
check("pages of another subject cannot be mixed in", e2 and e2[0] == 400, (r2, e2))
r3, e3 = call(web.api_combine, {"subject": "no-such-subject", "title": "x", "stems": ["a1", "a2"]})
check("an unknown subject is refused", e3 and e3[0] == 404, e3)
g1, ge = call(web.api_combine, {"subject": A, "title": "Guest set", "stems": ["a1", "a2"]}, login="guest@example.com")
check("a guest may combine too, the set lands in the main book", g1 and combine.get(idir, g1["id"]) is not None, (g1, ge))

st = web.shared_view(main, sb.status(main))
rows = {s["id"]: s["combined"] for s in st["subjects"]}
check("the status lists the combined pages per subject", len(rows[A]) == 2 and rows[B] == [], rows)
check("the owner may delete every one", all(c["can_delete"] for c in rows[A]))
check("no raw uid in the rows", "pub-secret-person" not in json.dumps(rows) and all("by" not in c for c in rows[A]))
guest = sb.Space(sb.slugify("guest@example.com", 60))
guest.ensure(login="guest@example.com", name=None)
gst = web.shared_view(guest, sb.status(guest))
grows = {c["title"]: c for s in gst["subjects"] if s["id"] == A for c in s["combined"]}
check("a guest sees both sets, may delete only their own", grows["Guest set"]["can_delete"] and not grows["Owner set"]["can_delete"], grows)

d1, de1 = call(web.api_combine_delete, {"subject": A, "id": cid}, login="guest@example.com")
check("a guest cannot delete the owner's set", de1 and de1[0] == 403, (d1, de1))
d2, de2 = call(web.api_combine_delete, {"subject": A, "id": g1["id"]}, login="guest@example.com")
check("a guest deletes their own set", d2 and d2["ok"] and combine.get(idir, g1["id"]) is None, (d2, de2))
d3, de3 = call(web.api_combine_delete, {"subject": A, "id": "c0000000000"})
check("a missing set gives 404", de3 and de3[0] == 404, de3)

# ---- the page
resp = web.combined_page(req(), A, cid)
html = resp.body.decode()
m = re.search(r"const DATA = (\{.*?\});\nconst T", html, re.S)
check("the data is embedded as one JSON object", bool(m))
data = json.loads(m.group(1))["subject"] if m else {"groups": []}
check("the page knows its title and subject", data.get("page_title") == "Owner set" and data.get("id") == A)
labels = [p["label"] for g in data["groups"] for p in g["pages"]]
check("repeated titles get the day and time they were added", "Same title · 2026-10-26 01:00" in labels and "Same title · 2026-10-28 01:00" in labels, labels)
check("only the chosen pages, all of this subject", len(labels) == 5 and all(p["url"].startswith(f"/i/{A}/") for g in data["groups"] for p in g["pages"]))
check("nobody's uid goes out", "pub-secret-person" not in html)
check("a hostile title cannot leave the script block", "</script><script>alert(1)" not in html and html.count("<script>") == 1 and html.count("</script>") == 1)
csp = resp.headers.get("content-security-policy", "")
check("the page may frame only its own site and loads nothing outside", "frame-src 'self'" in csp and "default-src 'none'" in csp and "frame-ancestors 'none'" in csp and "http://" not in html and "https://" not in html, csp)
check("the frame is sandboxed, titles never go through innerHTML", 'sandbox="allow-scripts"' in html and "allow-same-origin" not in html and "innerHTML" not in html)
check("never cached", resp.headers.get("cache-control") == "no-store")
try:
    web.combined_page(req(), B, cid)
    check("a set is not reachable under another subject", False)
except web.HTTPException as e:
    check("a set is not reachable under another subject", e.status_code == 404)
try:
    web.combined_page(req(), A, "../etc")
    check("a bad id gives 404", False)
except web.HTTPException as e:
    check("a bad id gives 404", e.status_code == 404)
i = web.interactive(req(path=f"/i/{A}/a1.html"), A, "a1")
icsp = i.headers.get("content-security-policy", "")
check("an interactive page can be framed only by the same site", "frame-ancestors 'self'" in icsp and "sandbox allow-scripts" in icsp, icsp)
body = i.body.decode()
check("the language bridge is added once (at the end when the page has no </body>)", body.count("addEventListener('message'") == 1 and body.endswith("</script>"), body[-200:])
(idir / "full.html").write_text("<html><body><p>x</p></body></html>", encoding="utf-8")
sb.write_json(idir / "full.json", {"title": "Full", "topic": "t", "added": sb.now_iso()})
fb = web.interactive(req(path=f"/i/{A}/full.html"), A, "full").body.decode()
check("with a </body> the bridge goes right before it", fb.count("addEventListener('message'") == 1 and fb.index("addEventListener('message'") < fb.rindex("</body>") and fb.endswith("</body></html>"), fb[-120:])
(idir / "full.html").unlink()
(idir / "full.json").unlink()
check("the stored file is not changed by serving", (idir / "a1.html").read_text() == "<p>x</p>")
check("outside a combined page the page is not widened", "sbWiden" not in body)
ie = web.interactive(Request({"type": "http", "method": "GET", "path": "/i", "query_string": b"embed=1", "headers": hdrs("owner@example.com")}), A, "a1")
check("inside a combined page (?embed=1) it is widened to the whole window", "sbWiden" in ie.body.decode() and "max-width" in ie.body.decode())
check("the bridge only accepts messages from the parent and only uk / en", "e.source!==parent" in body and "d.sbLang!=='uk'&&d.sbLang!=='en'" in body)
check("the bridge presses the page's own EN / УКР button", "'УКР':'EN'" in body and "data-lang" in body)
nobody = web.page_bridge(False)
check("no network and no storage in the bridge", "fetch" not in nobody and "XMLHttpRequest" not in nobody and "localStorage" not in nobody)
nb = web.HUB.read_text(encoding="utf-8")
check("the combined page has its own EN / УКР switch and sends the language to the frame", 'data-l="uk"' in nb and 'data-l="en"' in nb and "postMessage({sbLang" in nb)
check("the combined page opens its pages with ?embed=1", "embed=1" in nb)
rows_now = {c["id"]: c for s in web.shared_view(main, sb.status(main))["subjects"] if s["id"] == A for c in s["combined"]}
check("a status row lists the parts of the set", rows_now[cid]["parts"] == ["a1", "a3", "evil", "dup1", "dup2"], rows_now.get(cid))

# ---- the site page
index = web.INDEX.read_text(encoding="utf-8")
check("the book page has the combine button and dialog", "comboOpen(" in index and 'id="comboDlg"' in index and "/api/combine" in index)
check("the existing page buttons are untouched", "ipageRow(s.id, i)" in index and 'class="glass"' in index)
check("a combined page is ONE glass button and its pages leave the list", "function comboRow" in index and "mergedStems(s).has(i.stem)" in index
      and index[index.index("function comboRow"):index.index("function comboOpen")].count('class="glass"') == 1)
check("the counter counts the glass buttons on show", "glassCount(s)" in index and "const glassCount" in index)
check("the old plain row with 'N pages' is gone", 'class="muted small">${esc(t("combo.n"' not in index)
check("drawings keep their natural size inside a combined page", "sbCap" in web.page_bridge(True) and "viewBox" in web.page_bridge(True) and "sbCap" not in web.page_bridge(False))
check("the page's own switch is hidden inside a combined page", "sbHideSwitch" in web.page_bridge(True) and "sbHideSwitch" not in web.page_bridge(False))
check("the pages of a set stay reachable, folded under it", 'class="members"' in index and "ipageRow(subj, i)" in index)
skill = Path(__file__).resolve().parent / "skills" / "studybooks-interactive" / "SKILL.md"
if skill.exists():
    sk = skill.read_text(encoding="utf-8")
    check("the skill asks for the whole window width", "Use the whole width of the window" in sk and "max-width" in sk)
    check("the skill keeps drawings at their natural size on a wide screen", "Drawings keep their size" in sk and "viewBox" in sk)
    check("the skill makes the menu buttons glass, with the CSS", "Menu buttons are glass" in sk and ".gbtn::before" in sk and 'class="gbtn"' in sk)
    check("the skill no longer asks for a 980 px column", "at most 980 px" not in sk)
    check("the skill explains the study order of the pages", "## Order of the pages" in sk and "decides where the page stands" in sk)
    check("the skill makes two languages mandatory with exact button texts", "Two languages are mandatory" in sk and "exactly `EN` and" in sk)

check("the dialog offers only pages of the one subject", "s.interactive.filter(i =>" in index and "S.subjects.find(x => x.id === dlg.dataset.subject)" in index)


# ---- the order of the study material
OC = subs[2] if len(subs) > 2 else B
od = main.d["interactive"] / OC
for f in list(od.glob("*")) if od.exists() else []:
    if f.is_file():
        f.unlink()
syl = main.load_syllabus()
sb.ensure_topic(syl, OC, "t-late", "Late")
sb.ensure_topic(syl, OC, "t-early", "Early")
sb.ensure_topic(syl, OC, "t-none", "None")
main.save_syllabus(syl)
for d_, tp in (("2026-10-20", "t-early"), ("2026-10-27", "t-late")):
    ed = main.d["library"] / OC / tp / f"{d_}_e"
    ed.mkdir(parents=True, exist_ok=True)
    sb.write_json(ed / "meta.json", {"title": "E", "topic": tp, "date": d_, "added": d_ + "T09:00:00+01:00", "order": 0})
page(OC, "p-late", "Late page", topic="t-late", added="2026-10-21T10:00:00+01:00")      # made first, but its topic is taught later
page(OC, "p-early", "Early page", topic="t-early", added="2026-10-28T10:00:00+01:00")   # made last, but its topic is taught first
page(OC, "p-none", "No entries", topic="t-none", added="2026-10-24T10:00:00+01:00")     # no entries: the day it was made
pg = sb.interactive_pages(main, OC)
check("pages follow the order of the study material, not the order they were made",
      [p["stem"] for p in pg] == ["p-early", "p-none", "p-late"], [p["stem"] for p in pg])
check("every page has its place (rank) in that order", [p["rank"] for p in pg] == [0, 1, 2])
r1 = combine.create(od, "All", ["p-late", "p-none", "p-early"], "u")
v = combine.view(combine.get(od, r1["id"]), pg)
check("a combined page lists the topics in the study order", [g["topic"] for g in v] == ["T-Early", "T-None", "T-Late"], [g["topic"] for g in v])
r2 = combine.create(od, "Late set", ["p-late", "p-none"], "u")
rows = web.combined_rows(main, OC, "u")
check("the combined pages are listed in the study order", [r["title"] for r in rows] == ["All", "Late set"], [r["title"] for r in rows])
st_ = sb.status(main)
check("the status lists the pages in the same order", [i["stem"] for s in st_["subjects"] if s["id"] == OC for i in s["interactive"]] == ["p-early", "p-none", "p-late"])


# ---- adding pages to an existing combined page
page(OC, "p-new", "New page", topic="t-late", added="2026-10-29T10:00:00+01:00")
page(OC, "p-new2", "New page 2", topic="t-early", added="2026-10-29T11:00:00+01:00")
rs = combine.add(od, r2["id"], ["p-late", "p-new", "p-new", "p-new2"])
check("add: new pages go to the end, repeats and pages already in the set are skipped", rs["parts"] == ["p-late", "p-none", "p-new", "p-new2"], rs["parts"])
check("add: the set is stored", combine.get(od, r2["id"])["parts"] == ["p-late", "p-none", "p-new", "p-new2"])
check("add: the title and the author stay", combine.get(od, r2["id"])["title"] == "Late set" and combine.get(od, r2["id"])["by"] == "u")
for why, args in (("nothing new", (r2["id"], ["p-late", "p-new"])), ("a missing page", (r2["id"], ["nope"])),
                  ("a bad id", ("../x", ["p-new"])), ("a bad page name", (r2["id"], ["../a1"])), ("not a list", (r2["id"], "p-new"))):
    try:
        combine.add(od, *args)
        check(f"add rejected: {why}", False)
    except combine.CombineError:
        check(f"add rejected: {why}", True)
check("a rejected add changes nothing", combine.get(od, r2["id"])["parts"] == ["p-late", "p-none", "p-new", "p-new2"])
pg2 = sb.interactive_pages(main, OC)
v2 = combine.view(combine.get(od, r2["id"]), pg2)
check("after adding, the menu follows the study order", [g["topic"] for g in v2] == ["T-Early", "T-None", "T-Late"]
      and [p["stem"] for g in v2 for p in g["pages"]] == ["p-new2", "p-none", "p-late", "p-new"], [(g["topic"], [p["stem"] for p in g["pages"]]) for g in v2])
rows = web.combined_rows(main, OC, "u")
check("a status row shows the new parts", next(r for r in rows if r["id"] == r2["id"])["parts"] == ["p-late", "p-none", "p-new", "p-new2"])
res, err = call(web.api_combine_add, {"subject": OC, "id": r2["id"], "stems": ["p-early"]})
check("api: the owner adds pages to a set", err is None and res["ok"] and res["n"] == 5 and res["url"] == f"/c/{OC}/{r2['id']}", (res, err))
res, err = call(web.api_combine_add, {"subject": OC, "id": r2["id"], "stems": ["p-early"]})
check("api: adding only pages that are in already gives 400", err and err[0] == 400, (res, err))
res, err = call(web.api_combine_add, {"subject": OC, "id": "cffffffffff", "stems": ["p-early"]})
check("api: an unknown set gives 404", err and err[0] == 404, (res, err))
res, err = call(web.api_combine_add, {"subject": "../x", "id": r2["id"], "stems": ["p-early"]})
check("api: a bad subject gives 404", err and err[0] == 404, (res, err))
res, err = call(web.api_combine_add, {"subject": OC, "id": r2["id"], "stems": ["p-none"]}, "guest@example.com")
check("api: a signed-in guest is not refused for rights (a page already in the set is a 400 about that)", err is not None and err[0] == 400, (res, err))
page(OC, "p-g", "Guest page", topic="t-late", added="2026-10-30T10:00:00+01:00")
res, err = call(web.api_combine_add, {"subject": OC, "id": r2["id"], "stems": ["p-g"]}, "guest@example.com")
check("api: a guest adds a page to a set made by somebody else", err is None and res["ok"], (res, err))
rows_g = web.combined_rows(main, OC, "somebody")
check("rows say who may add: a signed-in person yes, a stranger no",
      all(r["can_add"] for r in rows_g) and not any(r["can_add"] for r in web.combined_rows(main, OC)))
check("the dialog has the add mode", 'id="comboMode"' in index and "/api/combine/add" in index and "function comboFill" in index and "combo.pickAdd" in index)
check("the list in the dialog hides pages that are already in the chosen set", "have.has(i.stem)" in index)

print("\n%d failed" % len(fails) if fails else "\nall combine checks passed")
sys.exit(1 if fails else 0)
