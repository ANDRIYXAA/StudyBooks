"""Offline test: a change to one section of the clean book on request (kind entry_edit): who may ask, the checks
(links, figures, size, typesetting), the retry, the one-step undo, what the status shows. Run from a COPY of the code,
with a fresh data dir:
SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_entry_edit.py (never on the real data)"""
import asyncio
import inspect
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
TEXT = "# Complex numbers\n\n" + "".join(f"Line {i} of the lecture about $z = a + bi$.\n\n" for i in range(30)) + "{{fig:f1}}\n"

main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)
guest = sb.Space("pub-guest")
guest.ensure(login="guest", name=None)
other = sb.Space("pub-other")
other.ensure(login="other", name=None)
lib = main.d["library"] / "math1" / "numbers"


def make(name, by=None, asis=False, hour=10):
    e = lib / name
    e.mkdir(parents=True, exist_ok=True)
    sb.write_json(e / "meta.json", {"title": "Complex numbers " + name, "title_original": "x", "lang_original": "en", "kind": "lecture",
                                    "subject": "math1", "topic": "numbers", "topic_title": "Numbers", "date": "2026-10-05",
                                    "added": f"2026-10-05T{hour}:00:00+02:00", "order": 1, "note": "", "pages": [], "figures": []}
                  | ({"by": by} if by else {}) | ({"asis": True} if asis else {}))
    if not asis:
        (e / "clean.md").write_text(TEXT, encoding="utf-8")
        (e / "original.md").write_text(TEXT, encoding="utf-8")
    return e


own, gst, asis = make("e-owner"), make("e-guest", by="pub-guest", hour=11), make("e-asis", asis=True, hour=12)


def call(path, sp, body):
    r = Request({"type": "http", "method": "POST", "path": path, "query_string": b"",
                 "headers": [(b"x-lang", b"uk")]})

    async def j():
        return body
    r.json = j
    r.state.sp = sp
    return r


web.who = lambda request: request.state.sp         # the sign-in itself is tested elsewhere


def ask(sp, entry, text="explain the step about |z|", topic="numbers", subject="math1"):
    try:
        return asyncio.run(web.api_request(call("/api/request", sp, {"kind": "entry_edit", "subject": subject, "topic": topic,
                                                                     "entry": entry, "text": text}))), None
    except web.HTTPException as e:
        return None, e.status_code


def queued(sp):
    return [x for x in (sb.read_json(p) for p in sp.d["requests"].glob("*.json")) if x]


def run(sp):
    rp = next(sp.d["requests"].glob("*.json"))
    try:
        sb.process_request(sp, rp)
        return None, rp
    except sb.PermanentError as e:
        return e, rp


# who may ask
j, err = ask(other, "e-owner")
check("a stranger cannot change the owner's section", err == 403, (j, err))
j, err = ask(other, "e-guest")
check("... nor another guest's", err == 403, (j, err))
j, err = ask(guest, "e-owner")
check("a guest cannot change the owner's section", err == 403, (j, err))
j, err = ask(guest, "nope")
check("an unknown section is 404", err == 404, (j, err))
j, err = ask(guest, "../e-owner")
check("a path trick is 404", err == 404, (j, err))
j, err = ask(guest, "e-asis")
check("a PDF as is (no text) cannot be edited", err == 404, (j, err))
j, err = ask(guest, "e-guest", subject="../x")
check("a path trick in the subject is 404", err == 404, (j, err))
j, err = ask(guest, "e-guest")
check("a person may ask to change what they uploaded", j and j["ok"] and queued(guest)[0]["kind"] == "entry_edit" and queued(guest)[0]["entry"] == "e-guest", (j, err))
j, err = ask(guest, "e-guest", "another one")
check("a second request for the same section waits", err == 409, (j, err))

# the status
st = web.shared_view(guest, sb.status(guest))
math = next(x for x in st["subjects"] if x["id"] == "math1")
ents = {e["entry"]: e for d in math["days"] for e in d["entries"]}
mk = math["making"]
check("the queued change is listed as a section change, not as a page", len(mk) == 1 and mk[0]["entry_edit"] == {"topic": "numbers", "entry": "e-guest"} and not mk[0]["edit"], mk)
check("can_edit: text sections yes, PDF as is no", ents["e-guest"]["can_edit"] and ents["e-owner"]["can_edit"] and not ents["e-asis"]["can_edit"], ents)
check("mine only for the guest's own section", ents["e-guest"]["mine"] and not ents["e-owner"]["mine"], ents)
check("no raw uid goes out", all("by" not in e for e in ents.values()))
src = inspect.getsource(web.anon_status)
check("strangers get neither can_edit nor can_undo", '"can_edit"' in src and '"can_undo"' in src)
st_o = web.shared_view(main, sb.status(main))
check("the owner may change every section", all(e["mine"] for d in next(x for x in st_o["subjects"] if x["id"] == "math1")["days"] for e in d["entries"]))

# the worker makes the change
err, rp = run(guest)
new = (gst / "clean.md").read_text(encoding="utf-8")
meta = sb.read_json(gst / "meta.json")
check("no error", err is None, err)
check("the text is changed", "Remark added on request" in new and new.startswith(TEXT.rstrip()[:200]), new[-200:])
check("the old text is kept for undo", (gst / "clean.prev.md").read_text(encoding="utf-8") == TEXT)
check("meta: edit log, still the guest's, title untouched", meta["edits"][-1]["by"] == "pub-guest" and meta["by"] == "pub-guest" and meta["edited"]
      and meta["title"] == "Complex numbers e-guest" and meta["edit_changes"] == ["added a remark"], meta)
check("the original transcription is untouched", (gst / "original.md").read_text(encoding="utf-8") == TEXT)
check("the request is done", (guest.d["requests"] / "done" / rp.name).exists() and not rp.exists())
check("the other section is untouched", (own / "clean.md").read_text(encoding="utf-8") == TEXT)
check("the book is marked dirty", "math1" in (sb.read_json(main.dirty, []) or []))
check("the work folder is gone", not (guest.d["work"] / rp.stem).exists())

# undo and redo
r = asyncio.run(web.api_entry_undo(call("/api/entry/undo", guest, {"subject": "math1", "topic": "numbers", "entry": "e-guest"})))
check("undo brings the old text back", r["ok"] and (gst / "clean.md").read_text(encoding="utf-8") == TEXT)
asyncio.run(web.api_entry_undo(call("/api/entry/undo", guest, {"subject": "math1", "topic": "numbers", "entry": "e-guest"})))
check("undo again is a redo", "Remark added" in (gst / "clean.md").read_text(encoding="utf-8"))
for who_, name in ((other, "a stranger"), (guest, "a guest on the owner's section")):
    try:
        asyncio.run(web.api_entry_undo(call("/api/entry/undo", who_, {"subject": "math1", "topic": "numbers", "entry": "e-guest" if who_ is other else "e-owner"})))
        code = 200
    except web.HTTPException as e:
        code = e.status_code
    check(name + " cannot undo", code == 403, code)
try:
    asyncio.run(web.api_entry_undo(call("/api/entry/undo", main, {"subject": "math1", "topic": "numbers", "entry": "e-owner"})))
    code = 200
except web.HTTPException as e:
    code = e.status_code
check("undo with no earlier version is 404", code == 404, code)

# the checks: each bad change is refused and the text stays
for mode, name in (("evil", "a change that adds a link"), ("same", "a change that changes nothing"),
                   ("wipe", "a change that deletes the section"), ("badmath", "a change that does not typeset")):
    os.environ["FAKE_EEDIT"] = mode
    (gst / "clean.md").write_text(TEXT, encoding="utf-8")
    ask(guest, "e-guest")
    err, rp = run(guest)
    check(name + " is refused", isinstance(err, sb.PermanentError) and (gst / "clean.md").read_text(encoding="utf-8") == TEXT, (mode, err))
    rp.unlink(missing_ok=True)
check("a refused change leaves no work folder", not list(guest.d["work"].glob("*")))

# the second round fixes what the checker found
os.environ["FAKE_EEDIT"] = "retry"
(gst / "clean.md").write_text(TEXT, encoding="utf-8")
ask(guest, "e-guest")
err, rp = run(guest)
check("a first try that fails a check gets a second one", err is None and "Fixed on the second try" in (gst / "clean.md").read_text(encoding="utf-8")
      and "evil.example" not in (gst / "clean.md").read_text(encoding="utf-8"), err)
os.environ["FAKE_EEDIT"] = "ok"

# a figure marker may not be dropped or invented
check("dropping a figure marker is refused", sb.entry_edit_problem(TEXT, TEXT.replace("{{fig:f1}}", "")) is not None)
check("a new figure marker is refused", sb.entry_edit_problem(TEXT, TEXT + "\n{{fig:f9}}\n") is not None)
check("a plain addition passes", sb.entry_edit_problem(TEXT, TEXT + "\nOne more line.\n") is None)

# the worker re-checks who may change the section
(gst / "clean.md").write_text(TEXT, encoding="utf-8")
ask(guest, "e-guest")
rp = next(guest.d["requests"].glob("*.json"))
sb.write_json(gst / "meta.json", sb.read_json(gst / "meta.json") | {"by": "pub-other"})
err, _ = run(guest)
check("... by the time it runs the section is not the guest's any more: refused", isinstance(err, sb.PermanentError) and (gst / "clean.md").read_text(encoding="utf-8") == TEXT, err)
rp.unlink(missing_ok=True)

# the owner changes their own section directly
ask(main, "e-owner")
err, rp = run(main)
check("the owner changes their own section", err is None and "Remark added" in (own / "clean.md").read_text(encoding="utf-8"), err)
# ---- the Edit button of the whole book (kind book_edit)
def reset():
    for e, by in ((own, None), (gst, "pub-guest")):
        (e / "clean.md").write_text(TEXT, encoding="utf-8")
        (e / "clean.prev.md").unlink(missing_ok=True)
        m = sb.read_json(e / "meta.json")
        m.pop("edits", None)
        m.pop("edited", None)
        m.pop("by", None)
        sb.write_json(e / "meta.json", m | ({"by": by} if by else {}))
    for sp_ in (main, guest, other):
        for p in sp_.d["requests"].glob("*.json"):
            p.unlink()


def book_ask(sp, subject="math1", text="in the lecture replace the example"):
    try:
        return asyncio.run(web.api_request(call("/api/request", sp, {"kind": "book_edit", "subject": subject, "text": text}))), None
    except web.HTTPException as e:
        return None, e.status_code


def txt(e):
    return (e / "clean.md").read_text(encoding="utf-8")


reset()
j, err = book_ask(other)
check("book edit: a person with no sections of their own gets 404", err == 404, (j, err))
j, err = book_ask(guest, "../x")
check("... a path trick in the subject is 404", err == 404, (j, err))
j, err = book_ask(guest, "nope")
check("... an unknown subject is 404", err == 404, (j, err))
j, err = book_ask(guest)
check("a guest may ask for a change of the book", j and j["ok"] and queued(guest)[0]["kind"] == "book_edit", (j, err))
j, err = book_ask(guest)
check("... a second one for the same book waits", err == 409, (j, err))
st = web.shared_view(guest, sb.status(guest))
mk = next(x for x in st["subjects"] if x["id"] == "math1")["making"]
check("the status lists it as a book edit", len(mk) == 1 and mk[0]["book_edit"] is True and mk[0]["entry_edit"] is None and not mk[0]["edit"], mk)
check("the guest gets only the sections they may change", [m["title"] for _, m in sb.book_edit_sections(guest, sb.shared_target(guest), "math1")] == ["Complex numbers e-guest"])
check("the owner gets every text section, in book order", [d.name for d, _ in sb.book_edit_sections(main, main, "math1")] == ["e-owner", "e-guest"])

os.environ["FAKE_BEDIT"] = "ok"
err, rp = run(guest)
done = sb.read_json(guest.d["requests"] / "done" / rp.name)
check("book edit by a guest: their section is changed, the owner's is not", err is None and "Book-wide remark" in txt(gst) and txt(own) == TEXT, err)
check("... the old text is kept, the edit is logged with the batch", (gst / "clean.prev.md").read_text(encoding="utf-8") == TEXT
      and sb.read_json(gst / "meta.json")["edits"][-1]["batch"] == rp.stem, sb.read_json(gst / "meta.json"))
check("... the request is done and says what changed", done["changed"] == ["Complex numbers e-guest"] and done["skipped"] == [] and not rp.exists(), done)
check("... no work folder is left", not list(guest.d["work"].glob("*")))

reset()
os.environ["FAKE_BEDIT"] = "two"
book_ask(main)
err, rp = run(main)
check("book edit by the owner can change several sections", err is None and "Book-wide remark" in txt(own) and "Book-wide remark" in txt(gst), err)
check("... each has its own undo step", (own / "clean.prev.md").exists() and (gst / "clean.prev.md").exists())

reset()
os.environ["FAKE_BEDIT"] = "partial"
book_ask(main)
err, rp = run(main)
done = sb.read_json(main.d["requests"] / "done" / rp.name)
check("a section that fails the checks is left as it was, the good ones are applied",
      err is None and "Book-wide remark" in txt(own) and txt(gst) == TEXT and done["skipped"] == ["Complex numbers e-guest"] and done["changed"] == ["Complex numbers e-owner"], (err, done))

reset()
os.environ["FAKE_BEDIT"] = "retry"
book_ask(main)
err, rp = run(main)
check("a failed section gets a second try", err is None and "Fixed on the second try" in txt(gst) and "evil.example" not in txt(gst), err)

for mode, name in (("none", "a change that changes nothing"), ("all_bad", "a change that only breaks the checks")):
    reset()
    os.environ["FAKE_BEDIT"] = mode
    book_ask(main)
    err, rp = run(main)
    check("book edit: " + name + " is refused, the text stays", isinstance(err, sb.PermanentError) and txt(own) == TEXT and txt(gst) == TEXT, (mode, err))
    check("... and no work folder is left", not list(main.d["work"].glob("*")))
os.environ["FAKE_BEDIT"] = "ok"

reset()
book_ask(guest)
rp = next(guest.d["requests"].glob("*.json"))
sb.write_json(gst / "meta.json", sb.read_json(gst / "meta.json") | {"by": "pub-other"})
err, _ = run(guest)
check("book edit: the worker re-checks who may change what", isinstance(err, sb.PermanentError) and txt(gst) == TEXT, err)
reset()

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
