"""Offline test of the per-person Claude model/effort and of "Stop" / "Continue".
Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_stop.py   (never against the real data)"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

import sb
import web

web.kick = lambda: None          # never wake a real worker from a test
fails = []
HOST = "t.example"


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


async def _asgi(method, path, headers, body):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "https",
             "path": path, "raw_path": path.encode(), "query_string": b"", "headers": headers,
             "client": ("127.0.0.1", 5555), "server": (HOST, 443)}
    sent, state = [], {"done": False}

    async def receive():
        if not state["done"]:
            state["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(m):
        sent.append(m)
    await web.app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], data


def call(method, path, body=None, login="stopper@example.com"):
    h = {"host": HOST, "x-lang": "en", "tailscale-user-login": login, "tailscale-user-name": "Stopper"}
    if method == "POST":
        h["content-type"] = "application/json"
        h["origin"] = f"https://{HOST}"
    raw = json.dumps(body).encode() if body is not None else b""
    st, data = asyncio.run(_asgi(method, path, [(k.encode(), v.encode()) for k, v in h.items()], raw))
    try:
        return st, json.loads(data)
    except ValueError:
        return st, None


ARGS = Path(tempfile.mkdtemp()) / "args.jsonl"
os.environ["FAKE_ARGS"] = str(ARGS)


def last_args():
    return json.loads(ARGS.read_text().splitlines()[-1])


def flag(args, name):
    return args[args.index(name) + 1] if name in args else None


# --- model and effort ---------------------------------------------------------------------------------------------------
st, j = call("GET", "/api/status")
check("status works for a tailnet person", st == 200 and j["ok"], str(j)[:300])
uid = j["user"]["uid"]
sp = sb.Space(uid)
C = j["engine"]["claude"]
check("status offers the models and efforts, nothing chosen yet",
      C["models"] == sb.CLAUDE_MODELS and C["efforts"] == sb.CLAUDE_EFFORTS and C["model"] == "" and C["effort"] == ""
      and C["default_model"] == sb.MODEL, str(C))

cwd = Path(tempfile.mkdtemp())
sb.run_llm(sp, "write explanation.md please", cwd)
a = last_args()
check("default: the server's model, no --effort", flag(a, "--model") == sb.MODEL and "--effort" not in a, str(a))

st, _ = call("POST", "/api/claude/settings", {"model": "sonnet", "effort": "high"})
check("settings are saved", st == 200 and sp.claude_model() == "sonnet" and sp.claude_effort() == "high")
sb.run_llm(sp, "write explanation.md please", cwd)
a = last_args()
check("the next call uses them", flag(a, "--model") == "sonnet" and flag(a, "--effort") == "high", str(a))
check("the tool limits are still there", flag(a, "--allowedTools") == "Read,Write,Edit,Grep,Glob" and "--disallowedTools" in a)
st, j = call("GET", "/api/status")
check("status shows the choice", j["engine"]["claude"]["model"] == "sonnet" and j["engine"]["claude"]["effort"] == "high")

for bad in ({"model": "gpt-4"}, {"effort": "ultra"}, {"model": "opus --dangerously-skip-permissions"}, {"effort": "--add-dir=/"}):
    st, _ = call("POST", "/api/claude/settings", bad)
    check(f"rejected: {bad}", st == 400 and sp.claude_model() == "sonnet" and sp.claude_effort() == "high")

sp.update_profile(claude_model="--bad", claude_effort="--add-dir=/")     # a hand-edited profile
check("junk in the profile falls back to the defaults", sp.claude_model() == sb.MODEL and sp.claude_effort() is None)
st, _ = call("POST", "/api/claude/settings", {"model": "", "effort": ""})
check("empty = back to the defaults", st == 200 and "claude_model" not in sp.prof() and "claude_effort" not in sp.prof())
sb.run_llm(sp, "write explanation.md please", cwd)
a = last_args()
check("and the call shows it", flag(a, "--model") == sb.MODEL and "--effort" not in a, str(a))

# --- Stop in the middle of a call ---------------------------------------------------------------------------------------
os.environ["FAKE_SLEEP"] = "60"
box = {}


def run():
    t0 = time.time()
    try:
        sb.run_llm(sp, "write explanation.md please", cwd)
        box["r"] = "finished"
    except sb.StoppedError:
        box["r"] = "stopped"
    except Exception as e:
        box["r"] = f"other: {e!r}"
    box["t"] = time.time() - t0


th = threading.Thread(target=run)
th.start()
time.sleep(1.5)
pid = last_args()[-1]
check("the slow fake Claude is running", Path(f"/proc/{pid}").exists())
st, _ = call("POST", "/api/stop")
th.join(30)
check("Stop ends the call with StoppedError", box.get("r") == "stopped", str(box))
check("within a few seconds, not after the 60 s run", box.get("t", 99) < 10, str(box))
time.sleep(0.3)
alive = Path(f"/proc/{pid}").exists() and "zombie" not in Path(f"/proc/{pid}/status").read_text().lower()
check("the Claude process is gone", not alive)
check("StoppedError is not an Exception (failure handlers let it through)", not issubclass(sb.StoppedError, Exception))
st, j = call("GET", "/api/status")
check("status says paused", j["paused"] is True)
os.environ.pop("FAKE_SLEEP")

# --- the worker leaves a paused person's queue alone ------------------------------------------------------------------------
sb.ensure_global()
other = sb.Space("other-person").ensure()
other.update_profile(login="other@example.com")
for s, label in ((sp, "mine"), (other, "theirs")):
    rid = sb.new_id()
    sb.write_json(s.d["requests"] / f"{rid}.json", {"id": rid, "kind": "explain", "subject": "math1", "topic": "",
                                                     "text": f"explain {label}", "created": sb.now_iso(), "label": label})
CWDS = ARGS.with_name("cwds.txt")
os.environ["FAKE_CLAUDE_LOG"] = str(CWDS)
sb.worker()
mine = list(sp.d["requests"].glob("*.json"))
check("the paused person's request is still queued", len(mine) == 1, str(mine))
check("and not marked as a failed attempt", sb.read_json(mine[0], {}).get("attempts", 0) == 0)
check("the other person's request was processed", not list(other.d["requests"].glob("*.json")))
cw = CWDS.read_text().splitlines() if CWDS.exists() else []
check("Claude ran for the other person only", cw and all(str(other.root) in l for l in cw), str(cw))
check("kick does not wake the worker only for a paused queue", sb.kick().get("started") is False)

# --- Stop of a running worker item: the item stays, attempts unchanged ---------------------------------------------------
call("POST", "/api/resume")
check("Continue lifts the pause", not sp.paused())
os.environ["FAKE_SLEEP"] = "60"
wt = threading.Thread(target=sb.worker)
wt.start()
time.sleep(2)
st_ = sb.read_json(sb.STATUS, {})
check("the worker is on this person's item", st_.get("current_uid") == uid, str(st_)[:200])
_, j = call("GET", "/api/status")
check("status says it runs for this person", j["running_here"] is True)
call("POST", "/api/stop")
wt.join(30)
check("the worker finished soon after Stop", not wt.is_alive())
mine = list(sp.d["requests"].glob("*.json"))
check("the stopped item is still queued", len(mine) == 1)
check("with no failed attempt", sb.read_json(mine[0], {}).get("attempts", 0) == 0 and not sb.read_json(mine[0], {}).get("last_error"))
check("nothing landed in failed/", not list(sp.d["failed"].iterdir()))
os.environ.pop("FAKE_SLEEP")
call("POST", "/api/resume")
sb.worker()
check("after Continue it is processed normally", not list(sp.d["requests"].glob("*.json")))

# --- OpenRouter: Stop is seen before the next request -------------------------------------------------------------------
orp = sb.Space("or-person").ensure()
orp.update_profile(engine="openrouter", openrouter_model="some/model", paused=True)
orp.or_key_file.write_text("sk-or-test", encoding="utf-8")
sent = []
real_chat, sb._or_chat = sb._or_chat, lambda *a, **k: sent.append(a) or {}
try:
    sb.run_llm(orp, "write explanation.md please", Path(tempfile.mkdtemp()))
    r = "finished"
except sb.StoppedError:
    r = "stopped"
sb._or_chat = real_chat
check("OpenRouter: a paused person's call stops before sending anything", r == "stopped" and not sent, f"{r} {len(sent)}")

# --- the page -----------------------------------------------------------------------------------------------------------------
html = (Path(sb.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
check("the page has the stop button and the model/effort selects",
      'id="stopBtn"' in html and 'id="clModel"' in html and 'id="clEffort"' in html and "/api/claude/settings" in html)

print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
