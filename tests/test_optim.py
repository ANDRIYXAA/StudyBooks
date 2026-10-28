"""Offline test of the cost savings: models by role, the 70/80/90 % limit stops, text-only pages, several small uploads
in one call, repair instead of a re-run, the textbook maps, chapter summaries after a quiet time, duplicate files.
Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_optim.py   (never on the real data)"""
import asyncio
import datetime as dt
import io
import json
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

os.environ["SB_DIGEST_QUIET"] = "3600"
import sb  # noqa: E402
import web  # noqa: E402
from PIL import Image  # noqa: E402
from starlette.requests import Request  # noqa: E402

web.kick = lambda: None          # never wake a real worker from a test
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


TMP = Path(tempfile.mkdtemp())
CALLS = TMP / "calls.jsonl"
SEEN = TMP / "seen.txt"
os.environ["FAKE_CALLS"] = str(CALLS)
os.environ["FAKE_SEEN"] = str(SEEN)
ARGS = TMP / "args.jsonl"
os.environ["FAKE_ARGS"] = str(ARGS)


def calls():
    """The model calls since the last reset: [(task, model)]."""
    if not CALLS.exists():
        return []
    out = []
    for line in CALLS.read_text().splitlines():
        j = json.loads(line)
        t = j["task"]
        kind = ("transcribe" if "transcribing" in t else "refs" if "looking up the reference" in t else
                "clean" if "clean English textbook" in t else "map" if "map of part of a textbook" in t else
                "repair" if t.startswith("An earlier run") else "digest" if "end-of-chapter digest" in t else
                "fix" if "does not typeset" in t else t[:30])
        out.append((kind, j["model"]))
    return out


def reset_calls():
    CALLS.unlink(missing_ok=True)
    SEEN.unlink(missing_ok=True)


_n = [0]


def photo():
    _n[0] += 1
    b = io.BytesIO()
    Image.new("RGB", (400, 300), (255, (_n[0] * 37) % 256, (_n[0] * 91) % 256)).save(b, "JPEG")
    return b.getvalue()


def typed_pdf(text, plot=False):
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_textbox(pymupdf.Rect(50, 50, 550, 500), text, fontsize=11)
    for i in range(40 if plot else 0):         # a plot: many small vector lines
        page.draw_line((60 + 10 * i, 700), (65 + 10 * i, 700 - (i * 7) % 90))
    data = doc.tobytes()
    doc.close()
    return data


_clock = [0]


def add_sub(sp, files, subject="math1", date="2026-10-20", note="", kind="notes", sid=None):
    sid = sid or sb.new_id()
    d = sp.d["inbox"] / sid
    d.mkdir(parents=True)
    names = []
    for i, (name, data) in enumerate(files, 1):
        (d / name).write_bytes(data)
        names.append(name)
    _clock[0] += 1
    at = (sb.now() - dt.timedelta(hours=2) + dt.timedelta(seconds=_clock[0])).isoformat(timespec="seconds")
    sb.write_json(d / "submission.json", {"id": sid, "uploaded_at": at, "files": names, "note": note,
                                          "subject": subject, "kind": kind, "label": note or sid, "date": date,
                                          "original_names": names})
    return sid


WINDOW = time.time() + 3600


def usage(sp, util, typ="five_hour", resets=WINDOW):
    sb.write_json(sp.root / "claude_usage.json", {"limits": {typ: {"status": "allowed", "util": util,
                                                                   "resets": resets}}})


def treq(login="tester@example.com"):
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": [(b"tailscale-user-login", login.encode()), (b"x-lang", b"en")]})


sp = web.who(treq())

# --- models by role --------------------------------------------------------------------------------------------------
check("default (opus): transcription on sonnet", sp.role_model("transcribe") == ("sonnet", False))
check("default: textbook lookup on haiku", sp.role_model("refs") == ("haiku", False))
check("default: the clean text on the person's own model", sp.role_model("clean") == (sb.MODEL, True))
check("default: summaries on sonnet", sp.role_model("digest") == ("sonnet", False))
sp.update_profile(claude_model="haiku")
check("the person's model is the ceiling: haiku stays haiku", all(sp.role_model(r) == ("haiku", True) for r in ("transcribe", "clean", "digest")))
sp.update_profile(claude_model="sonnet")
check("sonnet person: lookup still haiku, transcription own", sp.role_model("refs") == ("haiku", False) and sp.role_model("transcribe") == ("sonnet", True))
os.environ["SB_MODEL_REFS"] = "sonnet"
check("SB_MODEL_<ROLE> overrides a role", sp.role_model("refs") == ("sonnet", True))
del os.environ["SB_MODEL_REFS"]
sp.update_profile(claude_model=None, claude_effort="high")

# --- the 70/80/90 % stops --------------------------------------------------------------------------------------------
usage(sp, 0.5)
check("50 %: no stop", sp.limit_gate() is None)
usage(sp, 0.72)
g = sp.limit_gate()
check("72 %: stop at the 70 % step", g and g["step"] == 0.7 and g["type"] == "five_hour", g)
try:
    sb.run_llm(sp, "anything", TMP)
    check("run_llm refuses while the stop is up", False)
except sb.LimitGateError as e:
    check("run_llm refuses while the stop is up (LimitGateError, a TransientError)", isinstance(e, sb.TransientError))
sp.ack_limits()
check("after Continue: no stop at 72 %", sp.limit_gate() is None)
usage(sp, 0.79)
check("79 %: still no stop (next step is 80)", sp.limit_gate() is None)
usage(sp, 0.83)
check("83 %: stop again at 80", (sp.limit_gate() or {}).get("step") == 0.8)
sp.ack_limits()
usage(sp, 0.93)
check("93 %: stop at 90", (sp.limit_gate() or {}).get("step") == 0.9)
sp.ack_limits()
check("after the 90 % Continue nothing stops any more in this window", sp.limit_gate() is None)
usage(sp, 0.74, resets=time.time() + 7200)
check("a new window starts the steps afresh", (sp.limit_gate() or {}).get("step") == 0.7)
usage(sp, 0.95, resets=time.time() - 10)
check("a window whose reset time passed does not stop anything", sp.limit_gate() is None)
usage(sp, 0.91, typ="seven_day")
check("the weekly window stops too", (sp.limit_gate() or {}).get("type") == "seven_day")
sp.update_profile(engine="openrouter")
check("OpenRouter has its own limits: no stop", sp.limit_gate() is None)
sp.update_profile(engine=None)

# worker under the stop: nothing processed, nothing counted as failed, the page shows it; a PDF as is still goes
reset_calls()
sid = add_sub(sp, [("a.jpg", photo())])
w = sb.worker()
sub = sb.read_json(sp.d["inbox"] / sid / "submission.json")
check("worker under the stop: upload untouched, no attempt counted", w["processed"] == 0 and not sub.get("attempts") and not calls(), w)
check("... and no 'blocked' note (the page shows the stop instead)", not sp.prof().get("blocked"))
st = sb.status(sp)
check("status carries the stop for the page", (st.get("limit_gate") or {}).get("step") == 0.9 and st["limit_gate"]["type"] == "seven_day")
check("kick does not start a worker for a stopped person", sb.kick().get("idle") is True)
r = web.api_limit_continue(treq())
check("the Continue endpoint lifts the stop", r["ok"] and sp.limit_gate() is None)
(sp.root / "claude_usage.json").unlink()

# --- one upload: three passes, the right models, the session-limit message --------------------------------------------
reset_calls()
w = sb.worker()
c = calls()
check("one photo upload: transcribe(sonnet) → clean(opus); no textbooks → no lookup call",
      c == [("transcribe", "sonnet"), ("clean", sb.MODEL)], c)
check("the upload is done with an entry", (sp.d["processed"] / sid).exists() and len(sb.entries_of(sp, "math1")) == 1)
args = [json.loads(l) for l in ARGS.read_text().splitlines()]
args = [next(a for a in args if "sonnet" in a), next(a for a in args if sb.MODEL in a)]
flag = lambda a, f: a[a.index(f) + 1] if f in a else None
check("transcription: sonnet, 60 turns, no effort flag", (flag(args[0], "--model"), flag(args[0], "--max-turns"), flag(args[0], "--effort")) == ("sonnet", "60", None), args[0])
check("clean: own model, the person's effort, 40 turns", (flag(args[1], "--model"), flag(args[1], "--max-turns"), flag(args[1], "--effort")) == (sb.MODEL, "40", "high"), args[1])

# --- a batch of chunks: Claude reports no number below its warning level, so the work itself is counted ------------------
sb.BATCH_CHUNKS = 3
sp.ack_limits()
check("batch: nothing done yet, no stop", sp.limit_gate() is None)
sp.note_work(chunks=2)
check("batch: 2 of 3 chunks, no stop", sp.limit_gate() is None)
sp.note_work(chunks=1, usd=1.5)
g = sp.limit_gate()
check("batch: 3 chunks, stop of type batch with the count", g and g["type"] == "batch" and g["n"] == 3 and g["util"] is None, g)
check("batch: the text names the chunks, not a percent", "3" in sb.gate_text(g) and "%" not in sb.gate_text(g), sb.gate_text(g))
check("status carries the batch stop", (sb.status(sp).get("limit_gate") or {}).get("type") == "batch")
reset_calls()
sid = add_sub(sp, [("a.jpg", photo())], date="2026-10-17")
w = sb.worker()
sub = sb.read_json(sp.d["inbox"] / sid / "submission.json")
check("batch: the worker waits, no call, no attempt counted", w["processed"] == 0 and not calls() and not sub.get("attempts"), w)
check("batch: kick does not wake the worker", sb.kick().get("idle") is True)
web.api_limit_continue(treq())
check("batch: Continue starts a new batch", sp.limit_gate() is None and (sp.prof().get("batch") or {}).get("n") == 0)
sp.update_profile(claude_model="sonnet", claude_effort="high")
reset_calls(); ARGS.unlink(missing_ok=True)
w = sb.worker()
b = sp.prof().get("batch") or {}
check("batch: the chunk is counted, and the spent dollars (0.05 a call)", w["processed"] == 1 and b.get("n") == 1 and b.get("usd", 0) >= 0.09, (w, b))
args = [json.loads(l) for l in ARGS.read_text().splitlines()]
flag = lambda a, f: a[a.index(f) + 1] if f in a else None
tr = next(a for a in args if "--max-turns" in a and flag(a, "--max-turns") == "60")
cl = next(a for a in args if "--max-turns" in a and flag(a, "--max-turns") == "40")
check("sonnet person with effort: no effort on the transcription pass", flag(tr, "--effort") is None, tr)
check("... but the clean pass keeps it", flag(cl, "--effort") == "high", cl)
sp.update_profile(claude_model=None, claude_effort="high")
sb.BATCH_CHUNKS = 0
sp.update_profile(batch=None)

# --- soft stop: the chunk that runs is finished, the next one does not start --------------------------------------------
sp.update_profile(paused=None, soft_stop=None)
r = web.api_stop_soft(treq())
check("soft stop with nothing running pauses at once", r["ok"] and sp.paused() and not sp.soft_stopping(), r)
web.api_resume(treq())
check("resume clears the pause", not sp.paused())
sb.CHUNK = 1
reset_calls()
sid = add_sub(sp, [("s1.jpg", photo()), ("s2.jpg", photo())], date="2026-10-13")
_orig_chunk = sb.process_chunk


def _chunk_then_soft(*a, **k):
    _orig_chunk(*a, **k)
    sp.update_profile(soft_stop=True)          # the person presses "after this chunk" while chunk 1 runs


sb.process_chunk = _chunk_then_soft
w = sb.worker()
sb.process_chunk = _orig_chunk
sub = sb.read_json(sp.d["inbox"] / sid / "submission.json")
c = calls()
check("soft stop: chunk 1 was finished (2 calls), chunk 2 not started", c == [("transcribe", "sonnet"), ("clean", sb.MODEL)], c)
check("... the queue is paused, the flag is spent, the upload stays without a failed attempt",
      sp.paused() and not sp.soft_stopping() and (sp.d["inbox"] / sid).exists() and not sub.get("attempts"), sub)
check("... the finished chunk is kept", (sp.d["work"] / sid / "c1" / ".done").exists() and not (sp.d["work"] / sid / "c2" / ".done").exists())
check("status shows the pause", sb.status(sp)["paused"] and sb.status(sp)["soft_stop"] is False)
web.api_resume(treq())
reset_calls()
w = sb.worker()
check("after Continue only chunk 2 is paid for, and the upload is done",
      calls() == [("transcribe", "sonnet"), ("clean", sb.MODEL)] and (sp.d["processed"] / sid).exists(), calls())
sb.CHUNK = 12
_html = (Path(sb.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
check("the page has both stop buttons", 'id="softBtn"' in _html and "/api/stop/soft" in _html and 'id="stopBtn"' in _html)

# --- the book shows where an interactive page is being made -------------------------------------------------------------
sp.update_profile(paused=None)
rq = sp.d["requests"] / "rq-inter.json"
sb.write_json(rq, {"id": "rq-inter", "kind": "interactive", "subject": "math1", "topic": "", "text": "Phase portrait of a pendulum", "created": sb.now_iso()})
m1 = next(x for x in sb.status(sp)["subjects"] if x["id"] == "math1")
check("a queued interactive page shows on its book", [(q["id"], q["text"], q["running"]) for q in m1["making"]] == [("rq-inter", "Phase portrait of a pendulum", False)] and m1["activity"] == "queued", m1["making"])
sb.set_status(state="busy", current_uid=sp.uid, pid=os.getpid(), current_subject="math1", current_phase="interactive", current_rid="rq-inter")
m1 = next(x for x in sb.status(sp)["subjects"] if x["id"] == "math1")
check("... and the running one is marked, with its own activity text key", m1["making"][0]["running"] is True and m1["activity"] == "interactive", (m1["making"], m1["activity"]))
sb.set_status(state="idle", current_uid=None, pid=None, current_subject=None, current_phase=None, current_rid=None)
rq.unlink()
# --- "Generate" on one waiting page works although the queue is paused ----------------------------------------------------
sp.update_profile(paused=True)
for rid_ in ("rq-a", "rq-b"):
    sb.write_json(sp.d["requests"] / f"{rid_}.json", {"id": rid_, "kind": "interactive", "subject": "math1", "topic": "", "text": f"page {rid_}", "created": sb.now_iso()})
m1 = next(x for x in sb.status(sp)["subjects"] if x["id"] == "math1")
check("paused: both waiting pages are listed with ids, none started by hand", [(q["id"], q["queued_by_hand"]) for q in m1["making"]] == [("rq-a", False), ("rq-b", False)], m1["making"])
reset_calls()
w = sb.worker()
check("paused: the worker does nothing", w["processed"] == 0 and not calls(), (w, calls()))
try:
    web.api_request_run(treq(), "nope")
    check("an unknown request is refused", False)
except web.HTTPException as e:
    check("an unknown request is refused (404)", e.status_code == 404)
r = web.api_request_run(treq(), "rq-a")
check("Generate puts only that request on the by-hand list", r["ok"] and sp.run_now_ids() == {"rq-a"}, sp.run_now_ids())
m1 = next(x for x in sb.status(sp)["subjects"] if x["id"] == "math1")
check("... and the page sees it as queued by hand", [q["queued_by_hand"] for q in m1["making"]] == [True, False])
_started = []
_popen = sb.subprocess.Popen
sb.subprocess.Popen = lambda *a, **k: _started.append(a) or None
kr = sb.kick()
sb.subprocess.Popen = _popen
check("kick wakes a worker for the by-hand page although the person is paused", kr.get("started") is not False and len(_started) == 1, (kr, _started))
reset_calls()
w = sb.worker()
pages_ = list((sp.d["interactive"] / "math1").glob("*.html"))
check("Generate: that page is made (one model call), the other one waits", w["processed"] == 1 and len(pages_) == 1
      and not (sp.d["requests"] / "rq-a.json").exists() and (sp.d["requests"] / "rq-b.json").exists(), (w, pages_))
check("... the pause still holds and the by-hand mark is used up", sp.paused() and not sp.run_now_ids(), sp.prof())
(sp.d["requests"] / "rq-b.json").unlink()
sp.update_profile(paused=None)

# --- paused uploads are sent one by one ------------------------------------------------------------------------------
sp.update_profile(paused=True, paused_at=sb.now_iso())
up1 = add_sub(sp, [("p1.jpg", photo())], date="2026-10-26", sid="20261026-100000-aaaa")
up2 = add_sub(sp, [("p2.jpg", photo())], date="2026-10-26", sid="20261026-100001-bbbb")
q = {x["id"]: x for x in sb.status(sp)["queue"]}
check("paused uploads: listed, none started by hand", q[up1]["queued_by_hand"] is False and q[up2]["queued_by_hand"] is False, q)
reset_calls()
w = sb.worker()
check("paused uploads: the worker leaves both", w["processed"] == 0 and not calls(), (w, calls()))
r = web.api_request_run(treq(), up2)
check("'Process now' accepts an upload and marks only it", r["ok"] and sp.run_now_ids() == {up2}, sp.run_now_ids())
q = {x["id"]: x for x in sb.status(sp)["queue"]}
check("... the page sees which one is queued by hand", q[up2]["queued_by_hand"] and not q[up1]["queued_by_hand"], q)
reset_calls()
w = sb.worker()
check("only the chosen upload is processed (same subject and day are NOT pulled in)", w["processed"] == 1
      and (sp.d["processed"] / up2).exists() and (sp.d["inbox"] / up1).exists(), (w, sorted(p.name for p in sp.d["inbox"].iterdir())))
check("... the pause holds and the mark is used up", sp.paused() and not sp.run_now_ids(), sp.prof())
shutil.rmtree(sp.d["inbox"] / up1)
sp.update_profile(paused=None, paused_at=None)

# --- a call that runs out of its 40 minutes does not lose what it has written ------------------------------------------------
_real_llm = sb.run_llm
_n_pages = lambda: len(list((sp.d["interactive"] / "math1").glob("*.html")))


def _cut_with_page(sp_, prompt, cwd, *a, **k):
    (Path(cwd) / "page.html").write_text("<!DOCTYPE html><html><body>" + "x" * 600 + "</body></html>", encoding="utf-8")
    raise sb.CallTimeout("Claude did not finish within 40 min")


sb.write_json(sp.d["requests"] / "rq-c.json", {"id": "rq-c", "kind": "interactive", "subject": "math1", "topic": "", "text": "page c", "created": sb.now_iso()})
before = _n_pages()
sb.run_llm = _cut_with_page
w = sb.worker()
check("cut off at the time limit but the page is complete: it is taken", w["processed"] == 1 and _n_pages() == before + 1
      and not (sp.d["requests"] / "rq-c.json").exists(), (w, _n_pages(), before))


def _cut_empty(sp_, prompt, cwd, *a, **k):
    (Path(cwd) / "notes.txt").write_text("half-way", encoding="utf-8")
    raise sb.CallTimeout("Claude did not finish within 40 min")


sb.write_json(sp.d["requests"] / "rq-d.json", {"id": "rq-d", "kind": "interactive", "subject": "math1", "topic": "", "text": "page d", "created": sb.now_iso()})
sb.run_llm = _cut_empty
w = sb.worker()
rq = sb.read_json(sp.d["requests"] / "rq-d.json", {})
check("cut off with nothing usable: an attempt is counted, the work stays", w["processed"] == 0 and rq.get("attempts") == 1
      and (sp.d["work"] / "rq-d" / ".cut").exists() and (sp.d["work"] / "rq-d" / "notes.txt").exists()
      and any((sp.d["work"] / "rq-d" / "context").rglob("*.md")), (w, rq))
_prompts = []


def _spy(sp_, prompt, cwd, *a, **k):
    _prompts.append((prompt, (Path(cwd) / "notes.txt").exists()))
    return _real_llm(sp_, prompt, cwd, *a, **k)


sb.run_llm = _spy
w = sb.worker()
check("the retry goes on from the work directory and tells the model so",
      w["processed"] == 1 and _prompts and "RAN OUT OF TIME" in _prompts[0][0] and _prompts[0][1], (w, [x[1] for x in _prompts]))
check("... and the page is made", _n_pages() == before + 2 and not (sp.d["requests"] / "rq-d.json").exists(), (_n_pages(), before, sorted(x.name for x in (sp.d["interactive"] / "math1").glob("*.html"))))
sb.run_llm = _real_llm
sb.write_json(sp.d["requests"] / "rq-e.json", {"id": "rq-e", "kind": "interactive", "subject": "math1", "topic": "", "text": "page e", "created": sb.now_iso()})
_prompts.clear()
sb.run_llm = _spy
sb.worker()
sb.run_llm = _real_llm
check("a normal first try has no 'ran out of time' note", _prompts and "RAN OUT OF TIME" not in _prompts[0][0])
check("the prompts ask for an early complete file", all("Save early" in (Path(sb.__file__).parent / "prompts" / f).read_text(encoding="utf-8") for f in ("interactive.md", "explain.md")))

_html = (Path(sb.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
check("the page has a text for it", '"act.interactive"' in _html and "s.making" in _html)

os.environ["FAKE_ERROR"] = "You've hit your session limit · resets 11:40pm (UTC)"
sid = add_sub(sp, [("b.jpg", photo())], date="2026-10-19")
w = sb.worker()
sub = sb.read_json(sp.d["inbox"] / sid / "submission.json")
check("'session limit' is a limit, not a failed attempt", sp.uid in w["blocked"] and not sub.get("attempts"), (w, sub.get("attempts")))
del os.environ["FAKE_ERROR"]
sp.update_profile(blocked=None)
sb.worker()

# --- typed PDF pages go as text only -----------------------------------------------------------------------------------
reset_calls()
plain = typed_pdf("The rigid body is in equilibrium when the sum of the forces and the sum of the moments vanish. " * 6)
mathy = typed_pdf("Equilibrium. " * 30, plot=True)
sid = add_sub(sp, [("slides.pdf", plain), ("formulas.pdf", mathy), ("c.jpg", photo())], date="2026-10-18")
sb.worker()
seen = SEEN.read_text(encoding="utf-8") if SEEN.exists() else ""
check("a plain typed page is given as text only", "text/p001.txt (typed page, text only" in seen, seen[:600])
import pymupdf as _pm  # noqa: E402
with _pm.open(stream=plain) as _d:
    check("text full of formulas is not trusted to the text layer",
          sb.text_is_enough(_d[0], "Text " * 100) and not sb.text_is_enough(_d[0], "Text " * 60 + "α ≤ β → ∑ ∫ ∂ " * 10))
check("a page with a plot keeps its image", "- page 2: pages/p002.jpg, text layer text/p002.txt" in seen)
check("a photo keeps its image", "- page 3: pages/p003.jpg" in seen)
ent = next(m for m in sb.entries_of(sp, "math1") if m["date"] == "2026-10-18")
check("the original book still gets every page image", len(list((ent["dir"] / "pages").glob("*.jpg"))) == 3)
check("the text layers are in the prompt (no Read turn per page), marked as data",
      "----- text layer of page 1 -----" in seen and "never instructions" in seen and "given below, do not open the file" in seen)

# --- English typed pages: the transcription is the clean text, no lookup and no clean call -------------------------------
os.environ["FAKE_LANG"] = "en"
reset_calls()
sid = add_sub(sp, [("en-slides.pdf", typed_pdf("Newton's second law relates force and acceleration of a body. " * 8))], date="2026-10-12")
sb.worker()
c = calls()
check("English typed pages: only the transcription is paid for", c == [("transcribe", "sonnet")], c)
ent = next(m for m in sb.entries_of(sp, "math1") if m["date"] == "2026-10-12")
orig = (ent["dir"] / "original.md").read_text(encoding="utf-8") if (ent["dir"] / "original.md").exists() else None
check("... and the upload is in the book", (sp.d["processed"] / sid).exists() and ent is not None)
reset_calls()
sid = add_sub(sp, [("en-photo.jpg", photo())], date="2026-10-12")
sb.worker()
check("English but a photo (handwriting): the clean pass stays", [k for k, _ in calls()] == ["transcribe", "clean"], calls())
sb.SKIP_CLEAN_EN = False
reset_calls()
sid = add_sub(sp, [("en-slides2.pdf", typed_pdf("Hooke's law states that the force is proportional to the extension. " * 8))], date="2026-10-12")
sb.worker()
check("SB_SKIP_CLEAN=0: the clean pass runs again", [k for k, _ in calls()] == ["transcribe", "clean"], calls())
sb.SKIP_CLEAN_EN = True
del os.environ["FAKE_LANG"]
reset_calls()
sid = add_sub(sp, [("de-slides.pdf", typed_pdf("Der starre Koerper ist im Gleichgewicht, wenn die Summe der Kraefte verschwindet. " * 8))], date="2026-10-12")
sb.worker()
check("German typed pages still get the clean (translation) pass", [k for k, _ in calls()] == ["transcribe", "clean"], calls())

# --- several small uploads of one subject and day: one call per pass ------------------------------------------------
reset_calls()
ids = [add_sub(sp, [(f"g{i}.jpg", photo())], date="2026-10-15", note=f"part {i}") for i in range(3)]
other = add_sub(sp, [("h.jpg", photo())], date="2026-10-16")
w = sb.worker()
c = calls()
check("3 small uploads of one day + 1 of another day: 2 transcriptions, 2 clean passes",
      [k for k, _ in c].count("transcribe") == 2 and [k for k, _ in c].count("clean") == 2, c)
check("all four are done", w["processed"] == 4 and all((sp.d["processed"] / i).exists() for i in ids + [other]), w)
subs = [sb.read_json(sp.d["processed"] / i / "submission.json") for i in ids]
check("each upload keeps its own entry and knows its partners",
      all(len(s["entries"]) == 1 for s in subs) and sorted(subs[0]["processed_with"]) == sorted(ids[1:]), [s.get("entries") for s in subs])
check("the shared prompt names the uploads", "These pages come from 3 separate uploads" in SEEN.read_text(encoding="utf-8"))
day = [m for m in sb.entries_of(sp, "math1") if m["date"] == "2026-10-15"]
check("entries keep their upload's note and order", [m["note"] for m in day] == ["part 0", "part 1", "part 2"], [m["note"] for m in day])

# the same subject goes first while it has more waiting
reset_calls()
a = add_sub(sp, [("s1.jpg", photo())], subject="math1", date="2026-10-10")
b = add_sub(sp, [("s2.jpg", photo())], subject="statics", date="2026-10-10")
cc = add_sub(sp, [("s3.jpg", photo())], subject="math1", date="2026-10-11")
sb.worker()
order = [json.loads(l)["cwd"].split("/work/")[1].split("/")[0] for l in CALLS.read_text().splitlines()
         if "transcribing" in json.loads(l)["task"]]
check("same subject first: math1, math1, then statics", order == [a, cc, b], order)

# --- repair instead of a re-run ---------------------------------------------------------------------------------------
reset_calls()
os.environ["FAKE_NO_ORIGINAL"] = "1"
sid = add_sub(sp, [("r.jpg", photo())], date="2026-10-09")
sb.worker()
del os.environ["FAKE_NO_ORIGINAL"]
c = [k for k, _ in calls()]
check("a missing file: one repair call, the transcription is not paid twice", c == ["transcribe", "repair", "clean"], c)
check("... and the upload is done", (sp.d["processed"] / sid).exists())

reset_calls()
os.environ["FAKE_NO_CLEAN"] = os.environ["FAKE_NO_REPAIR"] = "1"
sid = add_sub(sp, [("r2.jpg", photo())], date="2026-10-08")
sb.worker()
sub = sb.read_json(sp.d["inbox"] / sid / "submission.json")
check("the clean pass failed even after the repair: attempt 1", sub.get("attempts") == 1, sub)
del os.environ["FAKE_NO_CLEAN"], os.environ["FAKE_NO_REPAIR"]
reset_calls()
sb.worker()
c = [k for k, _ in calls()]
check("the retry pays only for the missing part (a repair), not the transcription again", c == ["repair"], c)
check("... and finishes the upload", (sp.d["processed"] / sid).exists())

# a chunk left by the older code (no chunk.json, old pages.json) is started afresh and works
reset_calls()
sid = add_sub(sp, [("o.jpg", photo())], date="2026-10-07")
work = sp.d["work"] / sid
pages, un = sb.prepare_pages(sp.d["inbox"] / sid, ["o.jpg"], work)
sb.write_json(work / "pages.json", {"pages": pages, "unsupported": un})
(work / "c1").mkdir()
(work / "c1" / "junk.md").write_text("half of an old run")
sb.worker()
check("an old half-done chunk is redone with the new passes", (sp.d["processed"] / sid).exists()
      and [k for k, _ in calls()] == ["transcribe", "clean"], calls())

# --- textbook maps -------------------------------------------------------------------------------------------------
import pymupdf  # noqa: E402

base = sb.REFS_OPEN / "math1"
base.mkdir(parents=True, exist_ok=True)
# a book with an outline and bold terms: the map comes from the PDF, no model
doc = pymupdf.open()
toc = []
for i in range(1, 13):
    pg = doc.new_page()
    pg.insert_text((50, 80), f"Section {i}", fontsize=14, fontname="hebo")
    pg.insert_text((50, 120), f"term number {i} matters", fontsize=11, fontname="hebo")
    pg.insert_text((50, 150), "plain text " * 20, fontsize=9)
    toc.append([1 if i % 4 == 1 else 2, f"{i}. Topic {i}", i])
doc.set_toc(toc)
doc.save(base / "outlined.pdf")
doc.close()
sb.index_reference_pdf(base / "outlined.pdf", sb.REFS_OPEN, "math1")
# a book without an outline: the cheapest model maps it, 80 pages per call
doc = pymupdf.open()
for i in range(1, 91):
    doc.new_page().insert_text((50, 80), f"page {i} about derivatives " * 8, fontsize=9)
doc.save(base / "plain.pdf")
doc.close()
sb.index_reference_pdf(base / "plain.pdf", sb.REFS_OPEN, "math1")

reset_calls()
sid = add_sub(sp, [("m.jpg", photo())], date="2026-10-06")
sb.worker()
c = calls()
check("first lookup: maps are made (2 haiku calls for the 90-page book), then lookup(haiku), clean",
      c == [("transcribe", "sonnet"), ("map", "haiku"), ("map", "haiku"), ("refs", "haiku"), ("clean", sb.MODEL)], c)
om = (base / "outlined.map.md").read_text(encoding="utf-8")
check("outline map: sections with pages and the bold terms", "p.1–1 · 1. Topic 1" in om and "term number 1 matters" in om, om[:400])
pm = (base / "plain.map.md").read_text(encoding="utf-8")
check("model map: both parts joined", "p.1–80" in pm and "p.81–90" in pm, pm)
check("no half-made map parts are left", not list(base.glob(".*map-parts")))
seen = SEEN.read_text(encoding="utf-8")
check("the lookup prompt points at both maps", "outlined.map.md`" in seen and "plain.map.md`" in seen)
reset_calls()
sid = add_sub(sp, [("m2.jpg", photo())], date="2026-10-05")
sb.worker()
c = calls()
check("next upload: the maps are reused, no map call", c == [("transcribe", "sonnet"), ("refs", "haiku"), ("clean", sb.MODEL)], c)
sb.index_reference_pdf(base / "plain.pdf", sb.REFS_OPEN, "math1")
reset_calls()
add_sub(sp, [("m3.jpg", photo())], date="2026-10-04")
sb.worker()
check("a re-indexed book gets a new map", [k for k, _ in calls()].count("map") == 2, calls())

# --- chapter summaries wait for a quiet time --------------------------------------------------------------------------
check("summaries are not made right after uploads", bool(sb.stale_digests(sp)) and not sb.digests_due(sp))
check("kick does not wake the worker for summaries yet", sb.kick().get("idle") is True)
sb.DIGEST_QUIET = 0
reset_calls()
sb.worker()
c = calls()
check("after the quiet time: summaries, on sonnet", c and all(x == ("digest", "sonnet") for x in c) and not sb.stale_digests(sp), c)

# --- the same file twice --------------------------------------------------------------------------------------------
class Up:
    def __init__(self, name, data):
        self.filename, self._b = name, io.BytesIO(data)

    async def read(self, n):
        return self._b.read(n)


def req():
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": [(b"tailscale-user-login", b"dup@example.com"), (b"x-lang", b"en")]})


def upload(files, kind="notes"):
    return asyncio.run(web.api_upload(req(), files=files, note="", subject="math1" if kind != "reference" else "math1",
                                      kind=kind, topic="", topic_title="", code="", date="2026-10-20"))


X, Y = photo(), photo()
r1 = upload([Up("x.jpg", X)])
check("a new file is accepted", r1["ok"] and r1["skipped"] == [])
try:
    upload([Up("x-again.jpg", X)])
    check("the same file again is refused", False)
except web.HTTPException as e:
    check("the same file again is refused (409, in English)", e.status_code == 409 and "uploaded before" in e.detail, e.detail)
r2 = upload([Up("x2.jpg", X), Up("y.jpg", Y), Up("y-copy.jpg", Y)])
dsp = web.who(req())
s2 = sb.read_json(dsp.d["inbox"] / r2["id"] / "submission.json")
check("a mix: only the new file is kept, the others are named", s2["original_names"] == ["y.jpg"]
      and r2["skipped"] == ["x2.jpg", "y-copy.jpg"] and s2["files"] == [s2["files"][0]] and len(list((dsp.d["inbox"] / r2["id"]).glob("*.jpg"))) == 1, (r2, s2))
r3 = upload([Up("x-as-book.pdf", X)], kind="reference")
check("a textbook is a different kind: the same bytes as a textbook are accepted", r3["ok"])
shutil.move(str(dsp.d["inbox"] / r1["id"]), str(dsp.d["failed"] / r1["id"]))
r4 = upload([Up("x-retry.jpg", X)])
check("after the first upload failed, the same file may be sent again", r4["ok"] and r4["skipped"] == [])

print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
