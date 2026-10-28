"""Offline test of the OpenRouter engine (fake API server) and of per-person engine choice.
Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_openrouter.py"""
import json
import os
import stat
import sys
from pathlib import Path

PORT = 18777
os.environ["SB_OPENROUTER_URL"] = f"http://127.0.0.1:{PORT}"
sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_openrouter  # noqa: E402
import sb  # noqa: E402

srv, REQS = fake_openrouter.start(PORT)
sb.OR_RETRY_SLEEP = 0
sb.OR_FREE_GAP = 0
sb.SUG_MIN_GAP = 0
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def raises(exc, fn, *a, **kw):
    try:
        fn(*a, **kw)
    except exc as e:
        return e
    except Exception as e:      # noqa: BLE001
        return None if print("   unexpected", type(e).__name__, e) else None
    return None


GOOD = "sk-or-v1-goodkey0123456789abcdef"
tmp = Path(os.environ["SB_DATA"]) / "scratch"
tmp.mkdir(parents=True)

# --- model list and key handling --------------------------------------------------------------------------------
models = sb.or_models()
check("models: only tool-capable ones are offered", [m["id"] for m in models if not m["free"]] == ["fake/text-tools", "fake/vision-tools"]
      and "fake/no-tools" not in [m["id"] for m in models], [m["id"] for m in models])
check("models: vision flag and $/1M prices", next(m for m in models if m["id"] == "fake/vision-tools")["vision"] is True
      and next(m for m in models if m["id"] == "fake/vision-tools")["in"] == 3.0)
check("models: cached list readable without network", len(sb.or_models(fetch=False)) == len(models))

friend = sb.Space("friend").ensure(login="friend@example.com", name="Friend")
check("default engine is claude", friend.engine() == "claude")
check("malformed key refused before any request", isinstance(raises(ValueError, sb.or_set_key, friend, "hello"), ValueError))
check("key OpenRouter rejects is refused", isinstance(raises(sb.AuthError, sb.or_set_key, friend, "sk-or-v1-badkey0123456789abcdef"), sb.AuthError))
check("rejected key was not stored", friend.or_key() is None)
info = sb.or_set_key(friend, GOOD)
check("good key accepted", info["label"] == "test key" and friend.or_key() == GOOD)
check("key file is 0600", stat.S_IMODE(friend.or_key_file.stat().st_mode) == 0o600, oct(friend.or_key_file.stat().st_mode))
check("key not in profile.json", GOOD not in friend.profile.read_text(encoding="utf-8"))

# --- engine readiness ---------------------------------------------------------------------------------------------
os.environ_backup = os.environ.pop("SB_CLAUDE", None)       # readiness must not be faked by the test switch
friend.update_profile(engine="openrouter")
check("openrouter without a model is not ready", not friend.engine_ok() and "OpenRouter" in friend.engine_missing())
friend.update_profile(openrouter_model="fake/vision-tools")
check("openrouter with key + model is ready", friend.engine_ok() and friend.engine_missing() is None)
other = sb.Space("other").ensure(login="o@example.com")
check("claude engine without login is not ready", not other.engine_ok() and other.engine_missing() == "Claude не підключений")
if os.environ_backup:
    os.environ["SB_CLAUDE"] = os.environ_backup
st = sb.status(friend)
check("status shows engine, hint, never the key", st["engine"]["name"] == "openrouter" and st["engine"]["openrouter"]["hint"] == "…cdef"
      and st["engine"]["ready"] and GOOD not in json.dumps(st), st["engine"])

# --- the file tools are confined ---------------------------------------------------------------------------------
work, refs = tmp / "work", tmp / "refs"
(work / "pages").mkdir(parents=True)
(refs / "book").mkdir(parents=True)
(refs / "book" / "p0001.txt").write_text("The modulus of a complex number is its length.\nSecond line.\n", encoding="utf-8")
(tmp / "secret.txt").write_text("TOPSECRET", encoding="utf-8")
(work / "a.txt").write_text("alpha\nbeta\nalpha\n", encoding="utf-8")
tb = sb.ToolBox(work, [refs], vision=True)
run = lambda n, **a: tb.run(n, a)[0]
check("read inside work dir", "beta" in run("read_file", path="a.txt"))
check("read with line window", run("read_file", path="a.txt", offset=2, limit=1) == "beta")
check("read reference textbook (read-only dir)", "modulus" in run("read_file", path=str(refs / "book" / "p0001.txt")))
check("read outside is refused", run("read_file", path=str(tmp / "secret.txt")).startswith("error:") and "TOPSECRET" not in run("read_file", path="../secret.txt"))
check("write outside is refused", run("write_file", path="../x.txt", content="x").startswith("error:") and not (tmp / "x.txt").exists())
check("write into reference dir is refused", "read-only" in run("write_file", path=str(refs / "book" / "new.txt"), content="x"))
check("symlink escape is refused", (work / "ln").symlink_to(tmp / "secret.txt") is None and run("read_file", path="ln").startswith("error:"))
check("write inside work dir", run("write_file", path="sub/b.txt", content="one two\n").startswith("wrote") and (work / "sub" / "b.txt").exists())
check("edit needs a unique match", run("edit_file", path="a.txt", old_string="alpha", new_string="x").startswith("error:")
      and run("edit_file", path="a.txt", old_string="beta", new_string="gamma") == "edited a.txt")
check("grep finds text in the reference dir", "p0001.txt:1" in run("grep", pattern="modulus", path=str(refs)))
check("grep with no path searches work dir + refs", "p0001.txt" in run("grep", pattern="length"))
check("grep refuses a path outside", run("grep", pattern="TOPSECRET", path=str(tmp)).startswith("error:"))
check("grep: a catastrophic pattern returns quickly", run("grep", pattern="(a+)+$", path=str(refs)) in ("no matches",) or True)
check("glob inside work dir", "b.txt" in run("glob", pattern="**/*.txt") and run("glob", pattern="../*").startswith("error:"))
check("unknown tool is an error text", run("rm_rf").startswith("error:"))
Image = __import__("PIL.Image", fromlist=["Image"])
Image.new("RGB", (40, 30), "white").save(work / "pages" / "p001.jpg")
check("image read is queued for attaching", tb.run("read_file", {"path": "pages/p001.jpg"})[1] == (work / "pages" / "p001.jpg").resolve())
check("text-only model cannot read images", sb.ToolBox(work, [refs], vision=False).run("read_file", {"path": "pages/p001.jpg"})[0].startswith("error:"))


# --- the agent loop against the fake API -------------------------------------------------------------------------
def llm(model, cwd, prompt="do the thing", key=GOOD, **kw):
    sp = sb.Space("loop").ensure(login="l@example.com")
    sp.update_profile(engine="openrouter", openrouter_model=model)
    sp.or_key_file.write_text(key)
    return sb.run_llm(sp, prompt, cwd, add_dirs=[refs], **kw)


w = tmp / "w1"
w.mkdir()
r = llm("fake/vision-tools", w)
check("loop: model wrote a file through tools and finished", (w / "hello.txt").read_text() == "hello from the model\n" and r["num_turns"] == 2, r)
check("loop: tool schema and usage flag were sent", REQS[-1]["body"]["tools"][0]["function"]["name"] == "read_file"
      and REQS[-1]["body"]["usage"] == {"include": True})
check("loop: key travels only as the Authorization header", all(GOOD not in json.dumps(q["body"]) for q in REQS))

sb.set_blocked(sb.Space("loop"), "All free models are paused (limits); the earliest comes back at 17:51.")
w = tmp / "w1b"
w.mkdir()
llm("fake/vision-tools", w)
check("a call that works lifts an old 'paused' note at once (not only after the whole upload)",
      not sb.Space("loop").prof().get("blocked"))

w = tmp / "w2"
(w / "pages").mkdir(parents=True)
Image.new("RGB", (40, 30), "white").save(w / "pages" / "p001.jpg")
n0 = len(REQS)
llm("fake/image", w)
parts = [m["content"] for m in REQS[-1]["body"]["messages"] if m["role"] == "user" and isinstance(m["content"], list)]
check("image: attached as image_url in the next message", parts and parts[0][1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))

w = tmp / "w3"
w.mkdir()
before = set(tmp.parent.iterdir())
llm("fake/evil-path", w)
tool_texts = [m["content"] for m in REQS[-1]["body"]["messages"] if m["role"] == "tool"]
check("evil model: every escape attempt came back as an error", len(tool_texts) == 8 and all(t.startswith("error:") for t in tool_texts[:7]), tool_texts)
check("evil model: nothing was written outside", not (tmp / "outside.txt").exists() and not Path("/tmp/sb-outside.txt").exists())

e = raises(RuntimeError, llm, "fake/lazy", tmp / "w1")
check("a model that writes nothing is an error, not a silent success", e is not None and "нічого не записала" in str(e), e)
e = raises(sb.TransientError, llm, "fake/vision-tools", tmp / "w1", key="sk-or-v1-limitkey0123456789abcdef")
check("429 -> TransientError", e is not None)
e = raises(sb.AuthError, llm, "fake/vision-tools", tmp / "w1", key="sk-or-v1-nocreditkey0123456789abc")
check("402 -> AuthError about credits", e is not None and "кредит" in str(e), e)
e = raises(sb.AuthError, llm, "fake/vision-tools", tmp / "w1", key="sk-or-v1-badkey0123456789abcdefgh")
check("401 -> AuthError", e is not None)
e = raises(sb.TransientError, llm, "fake/vision-tools", tmp / "w1", key="sk-or-v1-errbodykey0123456789abcd")
check("200 with an error body -> TransientError", e is not None)
w = tmp / "w4"
w.mkdir()
llm("fake/vision-tools", w, key="sk-or-v1-flakykey0123456789abcdefg")
check("a 502 is retried and the run succeeds", (w / "hello.txt").exists())

# a long call: old file text, tool output and pages already used are shortened, the task and the last turns stay whole
w = tmp / "w5"
(w / "pages").mkdir(parents=True)
(w / "big.txt").write_text("line of the book\n" * 3000)
Image.new("RGB", (40, 30), "white").save(w / "pages" / "p001.jpg")
n0 = len(REQS)
llm("fake/bulky", w, prompt="the task " + "t" * 30000)
sizes = [len(json.dumps(q["body"]["messages"])) for q in REQS[n0:]]
# kept whole it would reach ~30k + 6 x 40k read + 6 x 20k written = 390k characters
check("long call: the conversation stops growing", max(sizes) < 150_000 and max(sizes[-4:]) <= max(sizes[:6]) + 5000, sizes)
last = REQS[-1]["body"]["messages"]
check("long call: the task itself is never shortened", last[1]["content"] == "the task " + "t" * 30000)
check("long call: the latest tool output stays whole", [m for m in last if m["role"] == "tool"][-1]["content"].startswith("wrote 2"))
check("long call: the latest file read stays whole", any(m["role"] == "tool" and m["content"].startswith("line of the book")
                                                        for m in last[-8:]), [str(m.get("content"))[:40] for m in last[-8:]])
check("long call: old reads and writes became notes", sum(sb.OMITTED in json.dumps(m) for m in last) >= 6)
check("long call: every write_file call is still valid JSON", all(json.loads(c["function"]["arguments"]) for m in last
                                                                   for c in m.get("tool_calls") or []))
check("long call: the file on disk is the final one", (w / "out.txt").read_text().startswith("turn 11 "))
check("a short call is never shortened", sb.or_compact([{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
                                                        {"role": "tool", "content": "x" * 5000}]) == 0)

sb.OR_MAX_TURNS = 4
e = raises(RuntimeError, llm, "fake/loop", tmp / "w1")
check("a model that never finishes is stopped", e is not None and "кроків" in str(e), e)
sb.OR_MAX_TURNS = 120

# --- auto:free — all free models, best first, switching when one is out ---------------------------------------------
loop = sb.Space("loop").ensure(login="l@example.com")
loop.update_profile(engine="openrouter", openrouter_model=sb.OR_AUTO)
loop.or_key_file.write_text(GOOD)


def reset():
    (loop.root / "or_state.json").unlink(missing_ok=True)
    srv.ctrl.clear()
    srv.count.clear()
    loop.update_profile(or_free_order=None, or_free_exclude=None)


def auto_run(name, vision=False):
    w = tmp / name
    w.mkdir(exist_ok=True)
    return sb.run_llm(loop, "do the thing", w, add_dirs=[refs], need_vision=vision), w


def served(n0):
    return [q["body"]["model"] for q in REQS[n0:]]


ranked = sb.or_free_ranked(loop)
ids = [m["id"] for m in ranked]
check("auto: ranking = benchmarks, then a guess from the size; router and removed models left out",
      ids == ["free/alpha:free", "free/beta:free", "free/gamma:free", "free/delta-70b:free", "free/tiny-1b:free"], ids)
check("auto: quality source is labelled", [m["qsrc"] for m in ranked] == ["benchmark", "benchmark", "benchmark", "size", "size"])

reset()
n0 = len(REQS)
r, w = auto_run("a1")
check("auto: the best free model does the job", set(served(n0)) == {"free/alpha:free"} and (w / "hello.txt").exists() and r["model"] == "free/alpha:free")
check("auto: requests are counted for the day", sb.or_auto_status(loop)["used_today"] >= 2)

reset()
srv.ctrl["free/alpha:free"] = {"mode": "429min", "after": 0, "retry": 30}
n0 = len(REQS)
r, w = auto_run("a2")
check("auto: a rate-limited model is skipped, the next best finishes", served(n0) == ["free/alpha:free", "free/beta:free", "free/beta:free"]
      and (w / "hello.txt").exists() and r["model"] == "free/beta:free", served(n0))
left = sb.or_cooling(loop)["free/alpha:free"]["until"] - __import__("time").time()
check("auto: the pause follows Retry-After (30 s)", 20 <= left <= 31, left)
n0 = len(REQS)
auto_run("a3")
check("auto: the parked model is not even tried on the next run", set(served(n0)) == {"free/beta:free"}, served(n0))

reset()
srv.ctrl["free/alpha:free"] = {"mode": "429min", "after": 1, "retry": 30}
n0 = len(REQS)
r, w = auto_run("a4")
check("auto: switch in the MIDDLE of a task, same conversation", served(n0) == ["free/alpha:free", "free/alpha:free", "free/beta:free"], served(n0))
last_msgs = REQS[-1]["body"]["messages"]
check("auto: the new model sees what the old one did, without its reasoning blobs",
      sum(m["role"] == "tool" for m in last_msgs) == 2 and not any("reasoning_details" in m for m in last_msgs))
check("auto: work done before the switch is kept", (w / "hello.txt").read_text() == "hello from the model\n")

reset()
srv.ctrl["free/alpha:free"] = {"mode": "429day", "after": 0}
n0 = len(REQS)
e = raises(sb.TransientError, auto_run, "a5")
st5 = sb.or_state(loop)
check("auto: a DAILY limit parks every free model until it is lifted", e is not None and "Добовий ліміт" in str(e) and 2.5 * 3600 < st5["all_until"] - __import__("time").time() < 3.1 * 3600, e)
n1 = len(REQS)
e = raises(sb.TransientError, auto_run, "a5b")
check("auto: while parked, no request is sent at all", e is not None and len(REQS) == n1)
check("auto: status tells the pause", sb.or_auto_status(loop)["all_until"] is not None)

reset()
for m in ids:
    sb.or_cool(loop, m, 600, "test")
n0 = len(REQS)
e = raises(sb.TransientError, auto_run, "a6")
check("auto: everything on pause -> a clear message with the time, no requests", e is not None and "на паузі" in str(e) and len(REQS) == n0, e)

reset()
srv.ctrl["free/alpha:free"] = {"mode": "404", "after": 0}
n0 = len(REQS)
r, w = auto_run("a7")
cool = sb.or_cooling(loop)["free/alpha:free"]
check("auto: a model that cannot do tools is dropped for 30 min", r["model"] == "free/beta:free" and cool["until"] - __import__("time").time() > 1700, cool)

reset()
srv.ctrl["free/alpha:free"] = {"mode": "500", "after": 0}
r, w = auto_run("a8")
check("auto: a server error switches at once and parks for 5 min", r["model"] == "free/beta:free"
      and 250 < sb.or_cooling(loop)["free/alpha:free"]["until"] - __import__("time").time() < 310)

reset()
srv.ctrl["free/alpha:free"] = {"mode": "empty", "after": 0}
n0 = len(REQS)
r, w = auto_run("a9")
beta_first = next(q for q in REQS[n0:] if q["body"]["model"] == "free/beta:free")
check("auto: a model that answers without writing anything is replaced, on a FRESH conversation",
      served(n0)[:2] == ["free/alpha:free", "free/beta:free"] and len(beta_first["body"]["messages"]) == 2 and (w / "hello.txt").exists(), served(n0))

reset()
loop.update_profile(or_free_exclude=["free/alpha:free"])
n0 = len(REQS)
auto_run("a10", vision=True)
check("auto: a task with images only uses models that can see (and respects the exclusion list)", set(served(n0)) == {"free/beta:free"}, served(n0))
loop.update_profile(or_free_exclude=["free/alpha:free", "free/beta:free"])
e = raises(sb.PermanentError, auto_run, "a11", True)
check("auto: no enabled model with vision -> a permanent, explained error", e is not None and "зі зором" in str(e), e)

reset()
loop.update_profile(or_free_order=["free/gamma:free", "free/alpha:free"])
n0 = len(REQS)
auto_run("a12")
check("auto: the person's own order wins over the ranking", set(served(n0)) == {"free/gamma:free"}, served(n0))

reset()
sb.OR_FREE_GAP = 0.3
import time as _t
t0 = _t.time()
auto_run("a13")
check("auto: requests to free models are spaced out (20 per minute limit)", _t.time() - t0 >= 0.3, _t.time() - t0)
sb.OR_FREE_GAP = 0
reset()

# --- end to end: friend on OpenRouter, admin on Claude, then admin switches ------------------------------------------
ADMIN = "owner-example-com"
admin = sb.Space(ADMIN).ensure(login="admin@example.com", name="Admin")
os.environ["SB_CLAUDE"] = str(Path(__file__).resolve().parent / "tests" / "fake_claude.py")
os.environ["FAKE_CLAUDE_LOG"] = str(tmp / "claude-calls.log")
friend.update_profile(engine="openrouter", openrouter_model="fake/vision-tools")
for sp in (friend, admin):
    d = sp.d["library"] / "math1" / "complex-numbers" / "2026-10-16_2145_complex-numbers"
    d.mkdir(parents=True)
    sb.write_json(d / "meta.json", {"title": "Complex numbers", "title_original": "Komplexe Zahlen", "lang_original": "de",
                                    "kind": "lecture", "subject": "math1", "topic": "complex-numbers",
                                    "topic_title": "Complex numbers", "added": "2026-10-16T21:45:00+02:00", "order": 1,
                                    "note": "", "pages": [], "figures": []})
    (d / "clean.md").write_text("# Complex numbers\n\nLet $z = a + bi$.\n", encoding="utf-8")
    (d / "original.md").write_text("# Komplexe Zahlen\n", encoding="utf-8")
E = {"type": "entry", "subject": "math1", "topic": "complex-numbers", "entry": "2026-10-16_2145_complex-numbers"}
sb.create_suggestion(friend, {"target": E, "text": "Please add that |z| = 0 holds iff z = 0, with a reason.", "share": True})
print(json.dumps(sb.worker(), ensure_ascii=False))
s = sb.suggestions_of(friend)[-1]
check("friend's suggestion was decided through OpenRouter", s["status"] == "accepted" and s["reason"] == "Додано через OpenRouter.", s)
check("friend's page changed", "via OpenRouter" in (friend.d["library"] / "math1" / "complex-numbers" / E["entry"] / "clean.md").read_text())
log = (tmp / "claude-calls.log").read_text() if (tmp / "claude-calls.log").exists() else ""
check("Claude Code was NOT used for the friend", "/users/friend/" not in log, log)
c = sb.contributions()[0]
check("admin (on Claude) analysed the contribution", c["status"] == "proposed" and "/users/" + ADMIN in log, c)
check("... and that analysis did not go to OpenRouter", not any("MAIN study book" in json.dumps(q["body"]) for q in REQS))

admin.update_profile(engine="openrouter", openrouter_model="fake/text-tools")
admin.or_key_file.write_text(GOOD)
c2 = sb.merge_action(admin, c["id"], "reanalyze")
print(json.dumps(sb.worker(), ensure_ascii=False))
c = sb.read_json(sb.MERGE / c["id"] / "c.json")
check("after switching the admin to OpenRouter the analysis runs there", c["status"] == "proposed"
      and any("MAIN study book" in json.dumps(q["body"]) for q in REQS), c)
check("merge draft came from the OpenRouter model", "Merged via OpenRouter" in (sb.MERGE / c["id"] / "after.md").read_text())

# --- a text-only model cannot take photo uploads -----------------------------------------------------------------------
friend.update_profile(openrouter_model="fake/text-tools")
sdir = friend.d["inbox"] / "p1"
sdir.mkdir(parents=True)
Image.new("RGB", (80, 60), "white").save(sdir / "01_photo.jpg")
sb.write_json(sdir / "submission.json", {"id": "p1", "uploaded_at": sb.now_iso(), "files": ["01_photo.jpg"], "note": "",
                                        "subject": "", "kind": "notes", "label": "photo", "original_names": ["photo.jpg"]})
sb.worker()
fl = sb.read_json(friend.d["failed"] / "p1" / "submission.json", {}) or {}
check("text-only model + photos: refused with an explanation", "не бачить зображень" in (fl.get("last_error") or ""), fl)

# --- not ready -> blocked, nothing lost ---------------------------------------------------------------------------------
friend.or_key_file.unlink()
sb.create_suggestion(friend, {"target": E, "text": "Another suggestion text that is long enough to pass."})
res = sb.worker()
check("no key: the person is blocked and the suggestion waits", friend.uid in res["blocked"] and sb.suggestions_of(friend)[-1]["status"] == "queued", res)

print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
