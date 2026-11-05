"""Offline test of the shared blocks and the speed-ups of the code review: the syllabus cache, the entries memo of one status
request, unique_stem, finish_request, watch_process, json_body, the shared glass script and the phone rule.
Run from a COPY of the code, with a fresh data dir: SB_DATA=<tmp> python test_shared_blocks.py (never on the real data)"""
import asyncio
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_SV_PORTS"] = ""
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import sb  # noqa: E402
import web  # noqa: E402
from starlette.requests import Request  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


sb.set_lang("en")
main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)

# ---------------------------------------------------------------- the syllabus cache
a = main.load_syllabus()
a["subjects"][0]["title"] = "CHANGED BY A CALLER"
b = main.load_syllabus()
check("the syllabus cache gives every caller its own copy", b["subjects"][0]["title"] != "CHANGED BY A CALLER")
n = {"parse": 0}
real_load = sb.yaml.load


def counting(*args, **kw):
    n["parse"] += 1
    return real_load(*args, **kw)


sb.yaml.load = counting
main.load_syllabus(); main.load_syllabus(); main.load_syllabus()
check("the file is parsed once while it does not change", n["parse"] == 0, n)
syl = main.load_syllabus()
syl["subjects"][0]["title"] = "A new title"
main.save_syllabus(syl)
check("a saved syllabus is read again, with the change", main.load_syllabus()["subjects"][0]["title"] == "A new title" and n["parse"] == 1, n)
sb.yaml.load = real_load
check("the C parser of libyaml is used when it is there", sb.YAML_LOADER is getattr(sb.yaml, "CSafeLoader", sb.yaml.SafeLoader))

# ---------------------------------------------------------------- the entries memo of one status request
SUBJ = main.load_syllabus()["subjects"][0]["id"]
d = main.d["library"] / SUBJ / "t1" / "2026-11-10_a"
d.mkdir(parents=True, exist_ok=True)
sb.write_json(d / "meta.json", {"title": "A", "topic": "t1", "date": "2026-11-10", "added": "2026-11-10T09:00:00+01:00", "pages": []})
(d / "clean.md").write_text("x" * 400)
reads = {"n": 0}
real_read = sb.read_json


def counting_read(path, default=None):
    if str(path).endswith("meta.json"):
        reads["n"] += 1
    return real_read(path, default)


sb.read_json = counting_read
sb.entries_of(main, SUBJ); sb.entries_of(main, SUBJ)
outside = reads["n"]
reads["n"] = 0
token = sb._ENTRIES_MEMO.set({})
sb.entries_of(main, SUBJ); sb.entries_of(main, SUBJ); sb.entries_of(main, SUBJ)
sb._ENTRIES_MEMO.reset(token)
check("outside a status request every call reads the disk; inside, once", outside == 2 and reads["n"] == 1, (outside, reads["n"]))
reads["n"] = 0
st = sb.status(main)
per_status = reads["n"]
sb.read_json = real_read
check("a status request reads the meta of an entry only once", per_status <= 2, per_status)
sb.write_json(d / "meta.json", {"title": "B", "topic": "t1", "date": "2026-11-10", "added": "2026-11-10T09:00:00+01:00", "pages": []})
check("the next request sees a change (the memo lives for one request)", any(e["title"] == "B" for e in sb.entries_of(main, SUBJ)))
st2 = sb.status(main)
check("... also in the status", any(e["title"] == "B" for dd in next(s for s in st2["subjects"] if s["id"] == SUBJ)["days"] for e in dd["entries"]))

# ---------------------------------------------------------------- small shared helpers
dd = Path(os.environ["SB_DATA"]) / "stems"
dd.mkdir(exist_ok=True)
s1 = sb.unique_stem("Same Title", lambda n_: (dd / f"{n_}.html").exists())
(dd / f"{s1}.html").write_text("1")
s2 = sb.unique_stem("Same Title", lambda n_: (dd / f"{n_}.html").exists())
(dd / f"{s2}.html").write_text("2")
s3 = sb.unique_stem("Same Title", lambda n_: (dd / f"{n_}.html").exists())
check("unique_stem: the same title in the same minute gets -2, -3", s2 == s1 + "-2" and s3 == s1 + "-3" and s1.endswith("_same-title"), (s1, s2, s3))

rq = main.d["requests"] / "r1.json"
(main.d["requests"] / "done").mkdir(parents=True, exist_ok=True)
sb.write_json(rq, {"id": "r1", "kind": "x"})
wk = main.d["work"] / "r1"
wk.mkdir(parents=True, exist_ok=True)
(wk / "f").write_text("x")
sb.finish_request(main, rq, sb.read_json(rq), work=wk, note_done="hello")
done = sb.read_json(main.d["requests"] / "done" / "r1.json")
check("finish_request: the record moves to done with done_at and extras, the file and the work folder go", not rq.exists() and not wk.exists()
      and done["done_at"] and done["note_done"] == "hello", done)

p = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdout.write(sys.stdin.read().upper())"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True)
out, err = sb.watch_process(main, p, 30, "Test", input="hello")
check("watch_process: sends the input and returns stdout and stderr", out == "HELLO" and err == "", (out, err))
main.update_profile(paused=True)
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
t0 = time.time()
try:
    sb.watch_process(main, p, 30, "Test")
    check("watch_process: Stop kills the process", False)
except sb.StoppedError:
    check("watch_process: Stop kills the process within seconds", time.time() - t0 < 15 and p.poll() is not None, time.time() - t0)
main.update_profile(paused=None)
p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
try:
    sb.watch_process(main, p, 1, "Test")
    check("watch_process: the time limit is a CallTimeout", False)
except sb.CallTimeout as e:
    check("watch_process: the time limit is a CallTimeout that names the engine", "Test" in str(e) and p.poll() is not None)

tb = sb.ToolBox(dd, [], vision=False)
msgs = []
sp_ = sb.agent_system_prompt(tb, [dd], "BRIEF")
check("agent_system_prompt: the work dir, the reference dirs and the skill", str(tb.cwd) in sp_ and str(dd) in sp_ and sp_.endswith("BRIEF"))
(dd / "w.txt").write_text("hello")
more = sb.run_tool_calls(tb, [{"id": "c1", "function": {"name": "read_file", "arguments": '{"path": "w.txt"}'}},
                              {"id": "c2", "function": {"name": "read_file", "arguments": "not json"}}], msgs)
check("run_tool_calls: one tool message for each call, a bad argument is an error text, no pictures", more is False and len(msgs) == 2
      and "hello" in msgs[0]["content"] and msgs[0]["tool_call_id"] == "c1" and "not valid JSON" in msgs[1]["content"], msgs)

# ---------------------------------------------------------------- json_body and the shared glass script
def jreq(raw):
    r = Request({"type": "http", "method": "POST", "path": "/x", "query_string": b"", "headers": []})

    async def j():
        if isinstance(raw, Exception):
            raise raw
        return raw
    r.json = j
    return r


check("json_body: a dict stays, a list or a broken body is an empty dict", asyncio.run(web.json_body(jreq({"a": 1}))) == {"a": 1}
      and asyncio.run(web.json_body(jreq([1, 2]))) == {} and asyncio.run(web.json_body(jreq(ValueError("bad json")))) == {})
for page in (web.INDEX, web.HUB):
    txt = web.page_text(page)
    check(f"the shared glass script is put into {page.name}", "function glassIn" in txt and "/*__GLASS_JS__*/" not in txt)
glass = (HERE.parent / "web" / "glass.js").read_text(encoding="utf-8") if (HERE.parent / "web" / "glass.js").exists() else (HERE / "web" / "glass.js").read_text(encoding="utf-8")
check("the glass does not move on a phone, a touch screen or with reduced motion", "(hover: none)" in glass and "(pointer: coarse)" in glass
      and "(max-width: 700px)" in glass and "prefers-reduced-motion" in glass and "if (glassStill() || !el.animate) return;" in glass)
check("the page files no longer carry their own copy of the glass script", "function glassIn" not in web.INDEX.read_text(encoding="utf-8") and "function glassIn" not in web.HUB.read_text(encoding="utf-8"))

print("\n%d failed" % len(fails) if fails else "\nall shared-block checks passed")
sys.exit(1 if fails else 0)
