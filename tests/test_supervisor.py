"""Offline test: the supervisor bot (dead/hung worker, stale stops, failed items whose cause was temporary, the
OpenRouter cap after a payment) and OpenRouter's per-model 403 ("only available on agentic harnesses").

Run from a COPY of the code, with a fresh data dir:
  SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_supervisor.py   (never on the real data)"""
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_SV_PORTS"] = ""
os.environ["SB_OPENROUTER_URL"] = "http://127.0.0.1:18766"
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "tests" / "fake_claude.py"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))

import fake_openrouter  # noqa: E402
import sb  # noqa: E402

sb.OR_FREE_GAP = 0
srv, requests = fake_openrouter.start(18766)
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def events(code=None):
    return [e for e in sb.sv_state()["events"] if code is None or e["code"] == code]


sb.ensure_global()
sp = sb.Space("pub-orfan").ensure(login="orfan")

# --- 1. a worker that died without cleaning up ------------------------------------------------------------------------
sb.write_json(sb.STATUS, {"state": "busy", "pid": 999999, "current": "x", "current_uid": sp.uid, "cur_item": "a"})
sb.supervise(force=True)
st = sb.read_json(sb.STATUS)
check("a dead worker's state is reset", st["state"] == "idle" and not st.get("pid") and not st.get("cur_item"), st)
check("... and reported as fixed", events("worker_dead") and events("worker_dead")[-1]["level"] == "act")

# --- 2. a worker that hangs -------------------------------------------------------------------------------------------
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)", "sb.py", "run"], start_new_session=True)
old = (sb.now() - sb.dt.timedelta(hours=2)).isoformat(timespec="seconds")
sb.write_json(sb.STATUS, {"state": "busy", "pid": p.pid, "beat": old, "current_since": old})
sb.supervise(force=True)
try:
    p.wait(timeout=5)
    killed = True
except subprocess.TimeoutExpired:
    killed = False
    p.kill()
check("a worker with no heartbeat for an hour is stopped", killed)
check("... and reported", bool(events("worker_hung")))
sb.write_json(sb.STATUS, {"state": "idle"})
p2 = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", "sb.py", "run"], start_new_session=True)
sb.write_json(sb.STATUS, {"state": "busy", "pid": p2.pid, "beat": sb.now_iso()})
sb.supervise(force=True)
check("a worker with a fresh heartbeat is left alone", p2.poll() is None)
p2.kill()
sb.write_json(sb.STATUS, {"state": "idle"})

# --- 3. failed items: a temporary cause goes back into the queue (at most twice), a real error stays ---------------------
def failed_upload(name, err, retries=0):
    d = sp.d["failed"] / name
    d.mkdir(parents=True)
    sb.write_json(d / "submission.json", {"id": name, "uploaded_at": sb.now_iso(), "label": name, "files": [],
                                          "attempts": 3, "last_error": err, "sv_retries": retries})


failed_upload("t1", "Claude is temporarily unavailable: You've hit your session limit")
failed_upload("t2", "no file could be read: x.bin")
failed_upload("t3", "OpenRouter is temporarily unavailable (502): Bad gateway", retries=2)
sb.supervise(force=True)
check("a failure caused by a limit is retried", (sp.d["inbox"] / "t1").exists()
      and sb.read_json(sp.d["inbox"] / "t1" / "submission.json")["attempts"] == 0)
check("... counting the retry", sb.read_json(sp.d["inbox"] / "t1" / "submission.json")["sv_retries"] == 1)
check("a real error stays failed", (sp.d["failed"] / "t2").exists())
check("an item retried twice already stays failed", (sp.d["failed"] / "t3").exists())
sb.shutil.rmtree(sp.d["inbox"] / "t1")

# a failure keeps the finished chunks for "Retry"; finished chunks reset the count of attempts
d = sp.d["inbox"] / "big"
d.mkdir(parents=True)
sb.write_json(d / "submission.json", {"id": "big", "uploaded_at": sb.now_iso(), "label": "big", "files": [], "attempts": 2})
for i in (1, 2):
    (sp.d["work"] / "big" / f"c{i}").mkdir(parents=True)
    (sp.d["work"] / "big" / f"c{i}" / ".done").touch()
sb.record_failure(sp, "sub", d, sb.CallTimeout("the model did not finish within 40 min"))
check("a failure after new finished chunks counts as the first one", (d / "submission.json").exists()
      and sb.read_json(d / "submission.json")["attempts"] == 1)
sb.record_failure(sp, "sub", d, RuntimeError("x"))
sb.record_failure(sp, "sub", d, RuntimeError("x"))
check("three failures with no new chunk -> failed", (sp.d["failed"] / "big").exists())
check("... and the finished chunks are kept", (sp.d["work"] / "big" / "c2" / ".done").exists())
sb.retry_failed(sp, "big")
check("Retry goes on from them", (sp.d["inbox"] / "big").exists() and (sp.d["work"] / "big" / "c1" / ".done").exists())
sb.shutil.move(str(sp.d["inbox"] / "big"), str(sp.d["failed"] / "big"))
old = time.time() - sb.SV_KEEP_FAILED_WORK - 60
os.utime(sp.d["failed"] / "big", (old, old))
sb.supervise(force=True)
check("a month later nobody came back: the kept work is removed", not (sp.d["work"] / "big").exists()
      and (sp.d["failed"] / "big").exists())

# --- 4. OpenRouter: a person who pays after setting the key gets 1000 a day ----------------------------------------------
key = "sk-or-v1-freetier0123456789abcdef"
sb.or_set_key(sp, key)
sp.update_profile(engine="openrouter", openrouter_model=sb.OR_AUTO)
check("a key that never bought credits: 50 a day", sp.prof()["or_daily_cap"] == 50 and sp.prof()["or_paid"] is False)
day = sb.dt.datetime.now(sb.dt.timezone.utc).strftime("%Y-%m-%d")
sb.write_json(sp.root / "or_state.json", {"day": day, "used": 101, "all_until": time.time() + 3600, "all_why": "daily"})
srv.tier["paid"] = True
sb.supervise(force=True)
check("101 of 50 used -> asked again at once -> paid, 1000 a day", sp.prof()["or_paid"] is True and sp.prof()["or_daily_cap"] == 1000,
      sp.prof())
check("... the pause from the 50-a-day cap is lifted", not sb.or_state(sp).get("all_until"))
check("... and the supervisor says so", bool(events("or_paid")))
check("the usage bar shows the paid cap", sb.usage_view(sp)["cap"] == 1000 and sb.usage_view(sp)["paid"] is True)

# --- 5. a 403 from ONE model parks that model; the work goes on with the next one --------------------------------------
sb.set_blocked(sp, "OpenRouter did not accept the key or the request (403): thinkingmachines/inkling-small:free is only available on agentic harnesses.")
sb.supervise(force=True)
check("an old stop caused by one model's 403 is lifted", not sp.prof().get("blocked"))
w = Path(os.environ["SB_DATA"]) / "w403"
w.mkdir()
srv.ctrl["free/alpha:free"] = {"mode": "403"}
out = sb.run_openrouter(sp, "Write hello.txt", w)
check("the task finishes on the next model", out["model"] != "free/alpha:free" and (w / "hello.txt").exists(), out)
cool = sb.or_cooling(sp).get("free/alpha:free") or {}
check("the refusing model is parked for days", cool and cool["until"] - time.time() > 5 * 86400, cool)
check("... with a reason a person understands", "OpenRouter" in cool.get("why", ""), cool)
check("it is also gone from the free-model list", "free/alpha:free" not in {m["id"] for m in sb.or_free_ranked(sp, fetch=False)}, [m["id"] for m in sb.or_free_ranked(sp, fetch=False)])
check("... the other models are still listed", {"free/beta:free", "free/gamma:free"} <= {m["id"] for m in sb.or_free_ranked(sp, fetch=False)})
st_ = sb.or_state(sp); st_["hidden"]["free/alpha:free"] = time.time() - 1; sb.write_json(sp.root / "or_state.json", st_)
check("... and comes back after the hiding time (OpenRouter may lift the limit)", "free/alpha:free" in {m["id"] for m in sb.or_free_ranked(sp, fetch=False)})
(sp.root / "or_state.json").unlink()
for m in ("free/alpha:free", "free/beta:free", "free/gamma:free"):
    srv.ctrl[m] = {"mode": "403"}
try:
    sb.run_openrouter(sp, "Write hello.txt", w)
    check("three models refusing in one task -> the queue stops (it is the key)", False)
except sb.AuthError as e:
    check("three models refusing in one task -> the queue stops (it is the key)", "403" in str(e), e)
srv.ctrl.clear()

# --- 6. what the admin sees -------------------------------------------------------------------------------------------
v = sb.supervisor_view()
check("the admin view lists events, newest first", v["events"] and v["events"][0]["at"] >= v["events"][-1]["at"])
check("... in both languages", all(e["uk"] and e["en"] for e in v["events"]))
check("... with the checks of every person", any(p["who"] == "orfan" and p.get("or_paid") for p in v["checks"]["people"]), v["checks"])
check("telegram is off without its settings", v["telegram"] is False)
n = len(sb.sv_state()["events"])
sb.supervise(force=True)
check("the same finding is not repeated every run", len(sb.sv_state()["events"]) == n)
check("without force it waits its turn", sb.supervise().get("skipped"))
srv.shutdown()

print("\nALL OK" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
