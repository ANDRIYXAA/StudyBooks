"""Offline test: a change to an interactive page on request (kind ipage_edit): who may ask, the checker, the one-step
undo, what the status shows. Run from a COPY of the code, with a fresh data dir:
SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_ipage_edit.py (never on the real data)"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_DIGEST_QUIET"] = "3600"
os.environ["SB_SV_PORTS"] = ""
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "fake_claude.py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.requests import Request  # noqa: E402

import sb  # noqa: E402
import web  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


web.kick = lambda: None
sb.set_lang("uk")
PAGE = ("<!DOCTYPE html><html><head><meta charset='utf-8'><title>Vector sum</title></head><body><input id='s' type='range'><canvas id='c'></canvas><script>"
        + "let a = 0;\n" + "document.getElementById('s').addEventListener('input', e => { a = +e.target.value; });\n" * 6 + "</script></body></html>")

main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)
guest = sb.Space("pub-guest")
guest.ensure(login="guest", name=None)
other = sb.Space("pub-other")
other.ensure(login="other", name=None)
d = main.d["interactive"] / "math1"
d.mkdir(parents=True, exist_ok=True)
for stem, by in (("owner-page", None), ("guest-page", "pub-guest")):
    (d / f"{stem}.html").write_text(PAGE, encoding="utf-8")
    sb.write_json(d / f"{stem}.json", {"title": "Vector sum", "topic": "t", "added": sb.now_iso()} | ({"by": by} if by else {}))


def call(path, sp, body):
    r = Request({"type": "http", "method": "POST", "path": path, "query_string": b"",
                 "headers": [(b"tailscale-user-login", sp.login.encode() if hasattr(sp, "login") else b"x"), (b"x-lang", b"uk")]})

    async def j():
        return body
    r.json = j
    r.state.sp = sp
    return r


web.who = lambda request: request.state.sp         # the sign-in itself is tested elsewhere


def ask(sp, stem, text="add a mass slider"):
    try:
        return asyncio.run(web.api_request(call("/api/request", sp, {"kind": "ipage_edit", "subject": "math1", "stem": stem, "text": text}))), None
    except web.HTTPException as e:
        return None, e.status_code


def queued(sp):
    return [x for x in (sb.read_json(p) for p in sp.d["requests"].glob("*.json")) if x]


j, err = ask(other, "owner-page")
check("a stranger may ask for a change of the owner's page", j and j["ok"] and queued(other)[0]["stem"] == "owner-page", (j, err))
rp0 = next(other.d["requests"].glob("*.json"))
sb.process_request(other, rp0)
cp = [sb.read_json(m) for m in d.glob("*.json") if sb.read_json(m).get("from") == "owner-page"]
check("... which becomes a page of their own, marked with who made it", len(cp) == 1 and cp[0]["by"] == "pub-other" and cp[0]["title"] == "Vector sum v2", cp)
check("... and the original stays as it was", (d / "owner-page.html").read_text(encoding="utf-8") == PAGE and not (d / "owner-page.prev.html").exists())
j, err = ask(guest, "nope")
check("an unknown page is 404", err == 404, (j, err))
j, err = ask(guest, "../x")
check("a path trick is 404", err == 404, (j, err))
j, err = ask(guest, "guest-page")
check("a person may ask to change their own page", j and j["ok"] and queued(guest)[0]["kind"] == "ipage_edit" and queued(guest)[0]["stem"] == "guest-page", (j, err))
j, err = ask(guest, "guest-page", "another change")
check("a second request for the same page waits", err == 409, (j, err))

# the status: the request is shown as a change, not as a new page
st = web.shared_view(guest, sb.status(guest))
math = next(x for x in st["subjects"] if x["id"] == "math1")
mk = math["making"]
check("the queued change is listed with edit=true", len(mk) == 1 and mk[0]["edit"] is True, mk)
byslug = {i["stem"]: i for i in math["interactive"]}
check("mine is true only for the person's own page", byslug["guest-page"]["mine"] is True and byslug["owner-page"]["mine"] is False, byslug)
check("no raw uid goes out", all("by" not in i for i in math["interactive"]))
st_o = web.shared_view(main, sb.status(main))
check("the owner may change every page (mine)", all(i["mine"] for i in next(x for x in st_o["subjects"] if x["id"] == "math1")["interactive"]))

# the worker makes the change
rp = next(guest.d["requests"].glob("*.json"))
sb.process_request(guest, rp)
new = (d / "guest-page.html").read_text(encoding="utf-8")
meta = sb.read_json(d / "guest-page.json")
check("the page is changed", "Mass slider added" in new, new[-200:])
check("the old version is kept for undo", (d / "guest-page.prev.html").read_text(encoding="utf-8") == PAGE)
check("meta: new sha, title, edit log, still the guest's", meta["sha"] == sb.hashlib.sha256(new.encode()).hexdigest() and meta["title"] == "Vector sum v2"
      and meta["edits"][-1]["by"] == "pub-guest" and meta["by"] == "pub-guest" and meta["edited"], meta)
check("the request is done", (guest.d["requests"] / "done" / rp.name).exists() and not rp.exists())
check("the other page is untouched", (d / "owner-page.html").read_text(encoding="utf-8") == PAGE)
check("the book is marked dirty", "math1" in (sb.read_json(main.dirty, []) or []))

# undo and redo
asyncio.run(web.api_ipage_undo(call("/api/ipage/undo", guest, {"subject": "math1", "stem": "guest-page"})))
check("undo brings the old version back", (d / "guest-page.html").read_text(encoding="utf-8") == PAGE)
asyncio.run(web.api_ipage_undo(call("/api/ipage/undo", guest, {"subject": "math1", "stem": "guest-page"})))
check("undo again is a redo", "Mass slider added" in (d / "guest-page.html").read_text(encoding="utf-8"))
try:
    asyncio.run(web.api_ipage_undo(call("/api/ipage/undo", other, {"subject": "math1", "stem": "guest-page"})))
    code = 200
except web.HTTPException as e:
    code = e.status_code
check("a stranger cannot undo", code == 403, code)

# the checker: a change that breaks the sandbox is refused, the page stays
for mode, name in (("evil", "a change that adds a network call"), ("same", "a change that changes nothing")):
    os.environ["FAKE_IEDIT"] = mode
    (d / "guest-page.html").write_text(PAGE, encoding="utf-8")
    ask(guest, "guest-page")
    rp = next(guest.d["requests"].glob("*.json"))
    try:
        sb.process_request(guest, rp)
        raised = False
    except sb.PermanentError:
        raised = True
    check(name + " is refused", raised and (d / "guest-page.html").read_text(encoding="utf-8") == PAGE)
    rp.unlink(missing_ok=True)
os.environ["FAKE_IEDIT"] = "ok"

# when the page is not the person's any more by the time the worker runs, the change becomes a copy
(d / "guest-page.html").write_text(PAGE.replace("let a = 0", "let a = 1"), encoding="utf-8")
ask(guest, "guest-page")
rp = next(guest.d["requests"].glob("*.json"))
sb.write_json(d / "guest-page.json", sb.read_json(d / "guest-page.json") | {"by": "pub-other"})
before_html = (d / "guest-page.html").read_text(encoding="utf-8")
n0 = len(list(d.glob("*.json")))
sb.process_request(guest, rp)
check("the worker re-checks who may change the page: a copy, not an edit",
      len(list(d.glob("*.json"))) == n0 + 1 and (d / "guest-page.html").read_text(encoding="utf-8") == before_html)

check("the writing standard is in every model call", "ASD-STE100" in sb.agent_brief("clean") and "ASD-STE100" in sb.agent_brief("ipage"))
check("... but not in the learning session", sb.agent_brief("learn") == "")
print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
