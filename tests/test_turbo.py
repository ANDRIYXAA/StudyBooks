"""Offline test of turbo mode (the header checkbox): several chunks of one upload at the same time, on threads of the one
worker. Thread pool rules, the locks around the shared state files, the spacing of requests, errors that cross threads,
the toggle API and a whole upload through the worker. Run from a COPY of the code, with a fresh data dir:
SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_turbo.py   (never on the real data)"""
import asyncio
import datetime as dt
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_DIGEST_QUIET"] = "3600"
os.environ["SB_SV_PORTS"] = ""
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "fake_claude.py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sb  # noqa: E402
import web  # noqa: E402
from PIL import Image  # noqa: E402
from starlette.requests import Request  # noqa: E402

web.kick = lambda: None
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


sb.set_lang("uk")


class Meter:
    """How many jobs run at the same time."""

    def __init__(self):
        self.now = self.max = 0
        self.lock = threading.Lock()

    def __enter__(self):
        with self.lock:
            self.now += 1
            self.max = max(self.max, self.now)

    def __exit__(self, *a):
        with self.lock:
            self.now -= 1


# --- the thread pool -------------------------------------------------------------------------------------------------
m = Meter()
done = []


def job(i):
    with m:
        time.sleep(0.25)
    done.append(i)


sb.run_pool(list(range(6)), job, 3, lambda: True)
check("pool: 6 jobs, 3 threads: three run at once, all are done", m.max == 3 and sorted(done) == list(range(6)), (m.max, done))
m, done = Meter(), []
sb.run_pool(list(range(4)), job, 3, lambda: False)
check("pool: turbo off: one job at a time, in order", m.max == 1 and done == [0, 1, 2, 3], (m.max, done))
m, done = Meter(), []
flag = [False]
threading.Timer(0.35, lambda: flag.__setitem__(0, True)).start()
sb.run_pool(list(range(8)), job, 3, lambda: flag[0])
check("pool: the checkbox is read live: switched on while running, more jobs overlap", m.max >= 2 and sorted(done) == list(range(8)), (m.max, done))
m, done = Meter(), []
sb.run_pool([0], job, 3, lambda: True)
check("pool: a single job just runs", done == [0])

# an error stops new jobs, the running ones finish, the right error is raised
started = []


def bad(i):
    started.append(i)
    time.sleep(0.2)
    if i == 1:
        raise sb.TransientError("limit")
    done.append(i)


done = []
try:
    sb.run_pool(list(range(10)), bad, 3, lambda: True)
    err = None
except sb.TransientError as e:
    err = e
check("pool: a failing job raises its error after the running ones finished", isinstance(err, sb.TransientError) and 1 in started, (err, started))
check("pool: after a failure no new job starts (not all 10 were started)", len(started) < 10, started)


def two_errors(i):
    time.sleep(0.3 if i == 0 else 0.1)          # both jobs are running when they fail
    raise (sb.TransientError("limit") if i == 0 else sb.CancelledItem())


try:
    sb.run_pool([0, 1], two_errors, 2, lambda: True)
    err = None
except BaseException as e:      # noqa: BLE001
    err = e
check("pool: Cancel outranks a limit error (BaseException crosses the thread)", isinstance(err, sb.CancelledItem), err)


def stopped(i):
    raise sb.StoppedError()


try:
    sb.run_pool([0, 1, 2], stopped, 3, lambda: True)
    err = None
except BaseException as e:      # noqa: BLE001
    err = e
check("pool: Stop crosses the thread", isinstance(err, sb.StoppedError), err)

# the language of the caller reaches the threads (messages written there are L(uk, en))
sb.set_lang("en")
seen = []
sb.run_pool([0, 1, 2], lambda i: (time.sleep(0.05), seen.append(sb.L("uk", "en"))), 3, lambda: True)
check("pool: threads keep the language of the request", seen == ["en"] * 3, seen)
sb.set_lang("uk")

# --- the locks ---------------------------------------------------------------------------------------------------------
sp = sb.Space("turbo-tester").ensure(login="tester@example.com", name="T")
sp.update_profile(batch={"n": 0, "usd": 0.0})
ts = [threading.Thread(target=lambda: [sp.note_work(chunks=1) for _ in range(25)]) for _ in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
check("locks: 8 threads x 25 chunk counts: none lost", (sp.prof().get("batch") or {}).get("n") == 200, sp.prof().get("batch"))
ts = [threading.Thread(target=lambda k=k: [sb.set_status(**{f"k{k}": i}) for i in range(25)]) for k in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
st = sb.read_json(sb.STATUS)
check("locks: status written by 8 threads keeps every key's last value", all(st.get(f"k{k}") == 24 for k in range(8)), st)
ts = [threading.Thread(target=lambda: [sb.or_note_request(sp) for _ in range(25)]) for _ in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
check("locks: the daily request counter counts every request", sb.or_state(sp).get("used") == 200, sb.or_state(sp))
ts = [threading.Thread(target=lambda k=k: sb.or_cool(sp, f"m{k}", 60, "429")) for k in range(8)]
[t.start() for t in ts]
[t.join() for t in ts]
check("locks: 8 models parked at once, none lost", len(sb.or_state(sp).get("cool") or {}) == 8, sb.or_state(sp).get("cool"))

# --- requests stay spaced, also from several threads -------------------------------------------------------------------
sb.OR_FREE_GAP = 0.2
sb._or_last.clear()
stamps = []
ts = [threading.Thread(target=lambda: [(sb.or_throttle(sp), stamps.append(time.time())) for _ in range(3)]) for _ in range(3)]
t0 = time.time()
[t.start() for t in ts]
[t.join() for t in ts]
gaps = [b - a for a, b in zip(sorted(stamps), sorted(stamps)[1:])]
check("throttle: 9 requests from 3 threads are at least the gap apart", len(stamps) == 9 and min(gaps) >= 0.17, (min(gaps), len(stamps)))
check("throttle: and they are not slower than one after another", time.time() - t0 < 9 * 0.2 + 0.6)
sb.OR_FREE_GAP = 0

# --- the checkbox -------------------------------------------------------------------------------------------------------


def treq(login="tester@example.com"):
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": [(b"tailscale-user-login", login.encode()), (b"x-lang", b"en")]})


def post(body):
    r = treq()

    async def j():
        return body
    r.json = j
    return asyncio.run(web.api_turbo(r))


me = web.who(treq())
st = sb.status(me)
check("status: turbo is off and not available with Claude", st["turbo"] == {"on": False, "available": False, "n": sb.TURBO_N}, st["turbo"])
try:
    post({"on": True})
    code = 200
except web.HTTPException as e:
    code = e.status_code
check("API: turbo on with the Claude engine is refused (409)", code == 409 and not me.prof().get("turbo"), code)
me.update_profile(engine="openrouter")
check("status: available with OpenRouter", sb.status(me)["turbo"]["available"] is True)
r = post({"on": True})
check("API: turbo on with OpenRouter", r["on"] and me.prof().get("turbo") is True and me.turbo() and sb.status(me)["turbo"]["on"])
r = post({"on": False})
check("API: turbo off removes the flag", not r["on"] and "turbo" not in me.prof() and not me.turbo())
me.update_profile(turbo=True, engine="claude")
check("a stale flag does nothing with the Claude engine", not me.turbo())
check("the page has the checkbox and its call", 'id="turboBox"' in (Path(sb.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
      and "/api/turbo" in (Path(sb.__file__).parent / "web" / "index.html").read_text(encoding="utf-8"))

# --- a whole upload through the worker -------------------------------------------------------------------------------
me.update_profile(engine="openrouter", turbo=None)
sb.Space.engine_ok = lambda self: True          # no key in a test: the model calls go to the fake Claude below


def fake_llm(sp_, prompt, cwd, add_dirs=(), timeout=2400, need_vision=False, role=None, max_usd=None):
    time.sleep(0.15)
    r = sb.run_claude_cli(sp_, prompt, cwd, add_dirs, timeout, role, max_usd)
    if sp_.paused():                            # "Stop" is seen when the model call returns (the real engines also see it in between)
        raise sb.StoppedError()
    return r


sb.run_llm = fake_llm
sb.CHUNK = 1
_n = [0]


def photo():
    _n[0] += 1
    b = io.BytesIO()
    Image.new("RGB", (400, 300), (255, (_n[0] * 37) % 256, (_n[0] * 91) % 256)).save(b, "JPEG")
    return b.getvalue()


def add_sub(files, subject, date):
    sid = sb.new_id()
    d = me.d["inbox"] / sid
    d.mkdir(parents=True)
    for name, data in files:
        (d / name).write_bytes(data)
    at = (sb.now() - dt.timedelta(hours=2)).isoformat(timespec="seconds")
    sb.write_json(d / "submission.json", {"id": sid, "uploaded_at": at, "files": [n for n, _ in files], "note": "", "subject": subject,
                                          "kind": "notes", "label": sid, "date": date, "original_names": [n for n, _ in files]})
    return sid


meter, snaps = Meter(), []
_orig_chunk = sb.process_chunk


def spy(*a, **k):
    with meter:
        time.sleep(0.4)
        snaps.append(sb.read_json(sb.STATUS, {}).get("cur_active"))
        return _orig_chunk(*a, **k)


sb.process_chunk = spy


def run_upload(subject, date, turbo, n=4):
    me.update_profile(turbo=True if turbo else None)
    meter.now = meter.max = 0
    snaps.clear()
    sid = add_sub([(f"p{i}.jpg", photo()) for i in range(n)], subject, date)
    sb.worker()
    return sid


sid = run_upload("math1", "2026-10-20", turbo=False)
seq_entries = len(sb.entries_of(sb.shared_target(me), "math1"))
check("turbo off: one chunk at a time, the upload is processed", meter.max == 1 and (me.d["processed"] / sid).exists(), (meter.max, list(me.d["failed"].glob("*"))))
check("turbo off: the status never lists parallel chunks", not any(s for s in snaps), snaps)

sid = run_upload("statics", "2026-10-21", turbo=True)
tur_entries = len(sb.entries_of(sb.shared_target(me), "statics"))
check("turbo on: chunks overlap (2 or 3 at a time, never more than TURBO_N)", 2 <= meter.max <= sb.TURBO_N, meter.max)
check("turbo on: the upload is processed and gives the same number of entries as without turbo", (me.d["processed"] / sid).exists() and tur_entries == seq_entries and tur_entries > 0,
      (tur_entries, seq_entries, list(me.d["failed"].glob("*"))))
check("turbo on: the status listed the chunks in progress", any(s and len(s) > 1 for s in snaps), snaps)
status = sb.read_json(sb.STATUS, {})
check("after the run the progress fields are cleared", not status.get("cur_active"), status.get("cur_active"))

# a chunk that fails: the others finish and keep their results, the upload is retried later without redoing them
me.update_profile(turbo=True)
sid = add_sub([(f"q{i}.jpg", photo()) for i in range(4)], "math1", "2026-10-22")
state = {"n": 0}


def flaky(sp_, subs, work, cw, chunk, ci, nchunks):
    if ci == 2 and not state["n"]:
        state["n"] = 1
        time.sleep(0.2)
        raise sb.TransientError("limit for this one")
    return _orig_chunk(sp_, subs, work, cw, chunk, ci, nchunks)


sb.process_chunk = flaky
sb.worker()
work = me.d["work"] / sid
kept = sorted(p.parent.name for p in work.glob("c*/.done"))
check("failure: the upload stays in the queue (a transient error is not a failed attempt)", (me.d["inbox"] / sid).exists() and not (me.d["processed"] / sid).exists())
check("failure: chunks that were running or done kept their results, the failed one did not", "c2" not in kept and len(kept) >= 1, kept)
sb.process_chunk = _orig_chunk
sb.worker()
check("failure: the next run finishes the upload", (me.d["processed"] / sid).exists(), list(me.d["failed"].glob("*")))

# Stop is seen by every running chunk: the queue pauses and nothing is lost
me.update_profile(turbo=True, paused=None)
sid = add_sub([(f"r{i}.jpg", photo()) for i in range(4)], "math1", "2026-10-23")


pressed = []


def stop_soon(*a, **k):
    if not pressed:                             # "Stop" is pressed once, shortly after the first chunk starts
        pressed.append(1)
        threading.Timer(0.1, lambda: me.update_profile(paused=True, paused_at=sb.now_iso())).start()
    return _orig_chunk(*a, **k)


sb.process_chunk = stop_soon
sb.worker()
sb.process_chunk = _orig_chunk
check("stop: with turbo the upload stays queued, not failed", (me.d["inbox"] / sid).exists() and not list(me.d["failed"].glob(f"{sid}*")), list(me.d["failed"].glob("*")))
me.update_profile(paused=None)
sb.worker()
check("stop: after Continue it is done", (me.d["processed"] / sid).exists())

print("\nFAILED: " + ", ".join(fails) if fails else "\nall ok")
sys.exit(1 if fails else 0)
