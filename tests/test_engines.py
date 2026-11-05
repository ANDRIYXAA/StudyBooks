"""Offline test: the "switch between" box. Codex (fake CLI), Grok and Gemini (fake OpenAI-compatible server), the selection
of engines, the rest after a limit, the hand-off of a half-done task to the next engine, and the web endpoints.
Run from a COPY of the code, with a fresh data dir: SB_DATA=<tmp> python test_engines.py (never on the real data)"""
import asyncio
import json
import os
import re
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_DIGEST_QUIET"] = "0"
os.environ["SB_SV_PORTS"] = ""
FAKES = HERE if (HERE / "fake_claude.py").exists() else HERE / "tests"      # the fakes sit in tests/ of a test copy
os.environ["SB_CLAUDE"] = str(FAKES / "fake_claude.py")
os.environ["SB_CODEX"] = str(FAKES / "fake_codex.py")
os.environ["SB_GEMINI_CLI"] = str(FAKES / "fake_gemini.py")
os.environ["SB_GROK_CLI"] = str(FAKES / "fake_grok.py")
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(FAKES))

import engines as en  # noqa: E402
import sb  # noqa: E402
import web  # noqa: E402
from starlette.requests import Request  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# ---------------------------------------------------------------- the pure parts
check("classify: limits", all(en.classify(t) == "limit" for t in ("You've hit your usage limit. Try again in 3 hours.", "429 Too Many Requests",
      "You exceeded your current quota", "Claude is temporarily unavailable: session limit", "RESOURCE_EXHAUSTED")))
check("classify: login problems", all(en.classify(t) == "auth" for t in ("401 Unauthorized", "Please sign in again", "Invalid API key", "not logged in")))
check("classify: the rest", en.classify("the sandbox crashed") == "other" and en.classify("") == "other")
check("cool_seconds: from the text, clamped, with a default", en.cool_seconds("Try again in 3 hours") == 3 * 3600 and en.cool_seconds("retry after 120 s") == 120
      and en.cool_seconds("try again in 9 days") == en.COOL_MAX and en.cool_seconds("in 5 seconds") == en.COOL_MIN and en.cool_seconds("nothing") == en.COOL_DEFAULT
      and en.cool_seconds("(retry after 90 s)") == 90)
ev = "\n".join(json.dumps(x) for x in ({"type": "thread.started"}, {"type": "item.completed", "item": {"type": "agent_message", "text": "first"}},
                                         {"type": "item.completed", "item": {"type": "agent_message", "text": "last"}},
                                         {"type": "turn.completed", "usage": {"input_tokens": 3}}))
pc = en.parse_codex(ev + "\nnoise line\n")
check("parse_codex: the last message, the usage, no failure", pc["text"] == "last" and pc["messages"] == 2 and pc["usage"] == {"input_tokens": 3} and not pc["failed"])
pf = en.parse_codex(json.dumps({"type": "turn.failed", "error": {"message": "usage limit"}}))
check("parse_codex: a failure with its message", pf["failed"] and pf["error"] == "usage limit")
pr = en.parse_codex(json.dumps({"type": "error", "message": "Reconnecting... 2/5 (unexpected status 401 Unauthorized)"}) + "\n" + json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "ok"}}))
check("parse_codex: a 'Reconnecting' error event alone is not a failure, but its text is kept", not pr["failed"] and pr["text"] == "ok" and "Reconnecting" in pr["error"])
check("classify: the 401 of a Codex run without a sign-in is a login problem", en.classify("Reconnecting... 5/5 (unexpected status 401 Unauthorized: Missing bearer or basic authentication in header)") == "auth")
REAL = "Welcome to Codex [v\x1b[90m0.166.0\x1b[0m]\n1. Open this link in your browser and sign in to your account\n   \x1b[94mhttps://auth.openai.com/codex/device\x1b[0m\n2. Enter this one-time code \x1b[90m(expires in 15 minutes)\x1b[0m\n   \x1b[94m0ZNK-XBS17\x1b[0m\n"
check("parse_login: the real output of Codex 0.166 (colours, a 5-character second half)", en.parse_login(REAL)[:2] == ("https://auth.openai.com/codex/device", "0ZNK-XBS17"), en.parse_login(REAL)[:2])
check("parse_login: the link and the code from the output", en.parse_login("Open \x1b[1mhttps://auth.x.test/device\x1b[0m\nCode: AB12-CD34")[:2] == ("https://auth.x.test/device", "AB12-CD34"))
ho = en.handoff(["claude"], ["a.txt (10 bytes)"])
check("handoff: names who stopped, lists the files, forbids starting over", "Claude" in ho and "a.txt (10 bytes)" in ho and "do not start over" in ho)

# ---------------------------------------------------------------- a fake OpenAI-compatible server (Grok, Gemini)
SEEN = []


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, obj, extra=None):
        b = json.dumps(obj).encode()
        self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers(); self.wfile.write(b)

    def do_GET(self):
        key = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        if key.startswith("badkey"):
            return self._send(401, {"error": {"message": "Incorrect API key provided"}})
        if self.path == "/models":
            return self._send(200, {"data": [{"id": "models/gemini-2.5-pro"}, {"id": "models/gemini-2.5-flash"}, {"id": "grok-4.7"}, {"id": "grok-build-0.1"}]})
        self._send(404, {"error": {"message": "nope"}})

    def do_POST(self):
        key = (self.headers.get("Authorization") or "").replace("Bearer ", "")
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        SEEN.append({"key": key, "model": body.get("model"), "msgs": body.get("messages"), "tools": bool(body.get("tools"))})
        if key.startswith("limitkey"):
            return self._send(429, {"error": {"message": "Rate limit reached for requests"}}, {"Retry-After": "120"})
        if key.startswith("badkey"):
            return self._send(401, {"error": {"message": "Incorrect API key provided"}})
        tools = [m for m in body["messages"] if m["role"] == "tool"]
        if not tools:
            msg = {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "oa_out.txt", "content": "written by " + str(body.get("model"))})}}]}
            return self._send(200, {"choices": [{"message": msg, "finish_reason": "tool_calls"}], "usage": {"total_tokens": 50}})
        self._send(200, {"choices": [{"message": {"role": "assistant", "content": "done"}, "finish_reason": "stop"}], "usage": {"total_tokens": 20}})


srv = ThreadingHTTPServer(("127.0.0.1", 0), H)
threading.Thread(target=srv.serve_forever, daemon=True).start()
URL = f"http://127.0.0.1:{srv.server_address[1]}"
os.environ["SB_GROK_URL"] = URL
os.environ["SB_GEMINI_URL"] = URL
GOOD = "xai-goodkey-0123456789abcdef"
LIMIT = "limitkey-0123456789abcdef"
BAD = "badkey-0123456789abcdef"

# ---------------------------------------------------------------- spaces
sb.set_lang("en")
os.environ["SB_CODEX_OPEN"] = "0"           # the closed case first: only the owner and the allowed people
main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)
guest = sb.Space("pub-guest")
guest.ensure(login="guest", name=None)


def work(name):
    d = Path(os.environ["SB_DATA"]) / "w" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def login_codex(sp):
    (sp.root / "codex").mkdir(exist_ok=True)
    (sp.root / "codex" / "auth.json").write_text("{}")
    sp.forget_codex_status()


for k in ("FAKE_ERROR", "FAKE_CODEX"):
    os.environ.pop(k, None)

# ---------------------------------------------------------------- the selection
check("without a selection the engine is the one engine", guest.engines_on() == [guest.engine()] == ["claude"])
guest.update_profile(engines_on=["gemini", "codex", "claude", "nonsense"])
check("the selection is kept in the order of the box, unknown names are dropped", guest.engines_on() == ["claude", "codex", "gemini"])
check("an engine that is not connected is not in the chain", guest.engine_chain() == ["claude"])
check("engine_ok: at least one selected engine is connected", guest.engine_ok())
guest.update_profile(engines_on=["codex", "grok"])
check("engine_ok is false when no selected engine is connected", not guest.engine_ok() and "None of the selected" in (guest.engine_missing() or ""))
guest.update_profile(engines_on=None)

# ---------------------------------------------------------------- Grok / Gemini keys and models
try:
    sb.oa_set_key(guest, "grok", BAD)
    check("a refused key is not stored", False)
except sb.AuthError:
    check("a refused key is not stored", not guest.engine_key_file("grok").exists())
try:
    sb.oa_set_key(guest, "grok", "short")
    check("a key that does not look like one is refused", False)
except ValueError:
    check("a key that does not look like one is refused", True)
models = sb.oa_set_key(guest, "grok", GOOD)
check("the key is stored in a 0600 file and the model list is returned", guest.engine_key("grok") == GOOD and oct(guest.engine_key_file("grok").stat().st_mode & 0o777) == "0o600"
      and "grok-4.7" in models, (models,))
check("the default model is chosen from the list", guest.engine_model("grok") == "grok-4.7")
gm = sb.oa_set_key(guest, "gemini", "AIzaKey-0123456789abcdefghijklmnop")
check("the provider's 'models/' prefix is cut; a 'pro' model is the default for Gemini", "gemini-2.5-pro" in gm and guest.engine_model("gemini") == "gemini-2.5-pro")
v = sb.engines_view(guest)
check("the view shows a hint of the key, never the key", v["engines"]["grok"]["hasKey"] and v["engines"]["grok"]["hint"] == "…cdef" and GOOD not in json.dumps(v))

# ---------------------------------------------------------------- the engines one by one
w = work("oa")
r = sb.run_oa(guest, "grok", "write the file", w)
check("Grok: the agent loop wrote the file with the chosen model", (w / "oa_out.txt").read_text() == "written by grok-4.7" and r["model"] == "grok-4.7")
check("... the key went in the header and tools were offered", SEEN[-1]["key"] == GOOD or SEEN[0]["key"] == GOOD and any(x["tools"] for x in SEEN))
guest.engine_key_file("grok").write_text(LIMIT)
try:
    sb.run_oa(guest, "grok", "write the file", work("oa2"))
    check("Grok: a 429 is a limit (TransientError) with the retry time", False)
except sb.TransientError as e:
    check("Grok: a 429 is a limit (TransientError) with the retry time", "retry after 120" in str(e) and en.cool_seconds(str(e)) == 120, str(e))
guest.engine_key_file("grok").write_text(BAD)
try:
    sb.run_oa(guest, "grok", "write the file", work("oa3"))
    check("Grok: a 401 is a login problem (AuthError)", False)
except sb.AuthError:
    check("Grok: a 401 is a login problem (AuthError)", True)
guest.engine_key_file("grok").write_text(GOOD)

login_codex(guest)
os.environ["FAKE_CODEX_LOG"] = str(Path(os.environ["SB_DATA"]) / "codex.log")
w = work("cx")
r = sb.run_codex(guest, "do the task", w, add_dirs=[HERE], role="clean")
log = [json.loads(l) for l in Path(os.environ["FAKE_CODEX_LOG"]).read_text().splitlines()]
check("Codex: runs `exec --json` in the work dir, sandboxed, no approvals, the prompt on stdin", (w / "codex_out.txt").exists() and r["result"] == "Done by codex."
      and "--json" in log[-1]["args"] and "workspace-write" in log[-1]["args"] and 'approval_policy="never"' in log[-1]["args"] and "do the task" in log[-1]["prompt"], log[-1]["args"])
check("... with the skill in front and the reference folder named", "Skill `studybooks-material`" in log[-1]["prompt"] and str(HERE) in log[-1]["prompt"])
check("... with the person's own CODEX_HOME", log[-1]["home"] == str(guest.root / "codex"))
os.environ["OPENAI_API_KEY"] = "sk-must-not-pass"
check("... and no OpenAI key from the server's environment", "OPENAI_API_KEY" not in guest.codex_env() and guest.codex_env()["CODEX_HOME"] == str(guest.root / "codex"))
guest.update_profile(codex_model="gpt-test", codex_effort="high")
sb.run_codex(guest, "again", work("cx2"))
a = json.loads(Path(os.environ["FAKE_CODEX_LOG"]).read_text().splitlines()[-1])["args"]
check("Codex: the model and the effort of the person go to the CLI", "-m" in a and "gpt-test" in a and 'model_reasoning_effort="high"' in a, a)
from PIL import Image  # noqa: E402
wv = work("cx3")
Image.new("RGB", (10, 10)).save(wv / "p1.png")
sb.run_codex(guest, "look", wv, need_vision=True)
a = json.loads(Path(os.environ["FAKE_CODEX_LOG"]).read_text().splitlines()[-1])["args"]
check("Codex: images of the work dir are attached when the task needs vision", "-i" in a and str(wv / "p1.png") in a[a.index("-i") + 1], a)
for mode, exc, why in (("limit", sb.TransientError, "a usage limit"), ("auth", sb.AuthError, "a login problem"), ("fail", RuntimeError, "another failure")):
    os.environ["FAKE_CODEX"] = mode
    try:
        sb.run_codex(guest, "x", work("cx-" + mode))
        check(f"Codex: {why} is told apart", False)
    except Exception as e:
        check(f"Codex: {why} is told apart", type(e) is exc, type(e).__name__)
os.environ.pop("FAKE_CODEX")
check("Codex: not logged in = not connected", not main.engine_connected("codex") and guest.engine_connected("codex"))

# ---------------------------------------------------------------- switching
CALLS = Path(os.environ["SB_DATA"]) / "claude_calls.txt"
os.environ["FAKE_CALLS"] = str(CALLS)
Path(os.environ["FAKE_CODEX_LOG"]).unlink()
sp = guest
sp.update_profile(engines_on=["claude", "codex", "grok"], engine="claude")
check("the chain is the selected engines in order", sp.engine_chain() == ["claude", "codex", "grok"])
w = work("sw1")
(w / "half.md").write_text("half of the work\n")
os.environ["FAKE_ERROR"] = "You've hit your session limit · resets 11:40pm"
r = sb.run_llm(sp, "Do the whole task.", w, role="clean")
os.environ.pop("FAKE_ERROR")
cool = sp.engine_cool_get()
check("Claude hit its limit: it rests and the SAME call went on with Codex", "claude" in cool and (w / "codex_out.txt").exists() and r["result"] == "Done by codex.", (cool, r))
cl = [json.loads(l) for l in Path(os.environ["FAKE_CODEX_LOG"]).read_text().splitlines()]
check("... Codex was told what was already written (the hand-off) and by whom", "HANDOFF" in cl[-1]["prompt"] and "half.md" in cl[-1]["prompt"] and "Claude" in cl[-1]["prompt"], cl[-1]["prompt"][-500:])
check("... the first prompt of Codex is the task itself", cl[-1]["prompt"].count("Do the whole task.") == 1)
check("... the rest is about 20 minutes (the message has no time) and is stored with the reason", 1000 < cool["claude"]["until"] - time.time() < 1300 and "session limit" in cool["claude"]["why"], cool)
check("the chain now skips the resting engine", sp.engine_chain() == ["codex", "grok"])
n_calls = len(CALLS.read_text().splitlines()) if CALLS.exists() else 0
sb.run_llm(sp, "Second task.", work("sw2"), role="clean")
check("a resting engine is not asked again (no new Claude call)", (len(CALLS.read_text().splitlines()) if CALLS.exists() else 0) == n_calls)
sp.engine_cool_set("claude", time.time() - 5, "over")
check("when the rest is over the first engine is back in the chain", sp.engine_chain() == ["claude", "codex", "grok"])
sb.run_llm(sp, "Third task.", work("sw3"), role="clean")
check("... and it is used again first", len(CALLS.read_text().splitlines()) == n_calls + 1)
# two engines at their limit: the third continues
os.environ["FAKE_ERROR"] = "429 rate limit"
os.environ["FAKE_CODEX"] = "limit"
w = work("sw4")
r = sb.run_llm(sp, "Four.", w, role="clean")
os.environ.pop("FAKE_ERROR"); os.environ.pop("FAKE_CODEX")
check("Claude and Codex at their limit: Grok finishes the call", (w / "oa_out.txt").exists() and set(sp.engine_cool_get()) == {"claude", "codex"}, sp.engine_cool_get())
gp = next(m["msgs"][1]["content"] for m in reversed(SEEN) if m["msgs"] and isinstance(m["msgs"][1]["content"], str) and "Four." in m["msgs"][1]["content"])
check("... and Grok got the hand-off with both names", "HANDOFF" in gp and "Claude" in gp and "Codex" in gp, gp[-400:])
cool_codex = sp.engine_cool_get()["codex"]["until"] - time.time()
check("... Codex rests for the 3 hours it asked for", 3 * 3600 - 60 < cool_codex <= 3 * 3600, cool_codex)
# everything at its limit
sp.engine_key_file("grok").write_text(LIMIT)
w = work("sw5")
try:
    sb.run_llm(sp, "Five.", w, role="clean")
    check("every selected engine at its limit: the call is a TransientError (the queue waits)", False)
except sb.TransientError as e:
    check("every selected engine at its limit: the call is a TransientError (the queue waits)", "at their limit" in str(e) and set(sp.engine_cool_get()) == {"claude", "codex", "grok"}, str(e))
try:
    sb.run_llm(sp, "Six.", w, role="clean")
    check("... and the next call says when the first is back", False)
except sb.TransientError as e:
    check("... and the next call says when the first is back", "at their limit or not connected" in str(e), str(e))
sp.engine_cool_clear()
sp.engine_key_file("grok").write_text(GOOD)
# a login problem also moves on
os.environ["FAKE_ERROR"] = "Not logged in: please log in"
w = work("sw6")
sb.run_llm(sp, "Seven.", w, role="clean")
os.environ.pop("FAKE_ERROR")
check("a login problem of the first engine also moves on to the next", "claude" in sp.engine_cool_get() and (w / "codex_out.txt").exists())
sp.engine_cool_clear()
# one engine only: no switching, no rest, errors as before
sp.update_profile(engines_on=["claude"])
os.environ["FAKE_ERROR"] = "429 rate limit"
try:
    sb.run_llm(sp, "Eight.", work("sw7"), role="clean")
    check("one engine: its limit is raised as before", False)
except sb.TransientError:
    check("one engine: its limit is raised as before", sp.engine_cool_get() == {})
os.environ.pop("FAKE_ERROR")
# stop and other errors do not switch
sp.update_profile(engines_on=["claude", "codex"], paused=True)
os.environ["FAKE_SLEEP"] = "4"          # long enough for the 2-second look at the Stop flag
try:
    sb.run_llm(sp, "Nine.", work("sw8"), role="clean")
    check("Stop is not a limit: it goes through the chain untouched", False)
except sb.StoppedError:
    check("Stop is not a limit: it goes through the chain untouched", sp.engine_cool_get() == {})
os.environ.pop("FAKE_SLEEP", None)
sp.update_profile(paused=None)
os.environ["FAKE_CODEX"] = "fail"
os.environ["FAKE_ERROR"] = "429 rate limit"
try:
    sb.run_llm(sp, "Ten.", work("sw9"), role="clean")
    check("an ordinary failure of the engine that took over is raised, not hidden", False)
except RuntimeError as e:
    check("an ordinary failure of the engine that took over is raised, not hidden", "Codex error" in str(e), str(e))
os.environ.pop("FAKE_CODEX"); os.environ.pop("FAKE_ERROR")
sp.engine_cool_clear()

# ---------------------------------------------------------------- the web endpoints
web.kick = lambda: None
web.who = lambda request: request.state.sp


def call(fn, who_, body=None, *a):
    r = Request({"type": "http", "method": "POST", "path": "/x", "query_string": b"", "headers": [(b"x-lang", b"en")]})

    async def j():
        return body or {}
    r.json = j
    r.state.sp = who_
    try:
        res = fn(r, *a)
        return (asyncio.run(res) if asyncio.iscoroutine(res) else res), None
    except web.HTTPException as e:
        return None, (e.status_code, e.detail)


g2 = sb.Space("pub-two")
g2.ensure(login="two", name=None)
res, err = call(web.api_engines, g2, {"on": ["claude", "codex"]})
check("select: Codex is for the owner only", err and err[0] == 403, (res, err))
res, err = call(web.api_engines, g2, {"on": ["claude", "grok"]})
check("select: an engine without a key is refused", err and err[0] == 400 and "Grok" in err[1], (res, err))
res, err = call(web.api_engines, g2, {"on": []})
check("select: at least one", err and err[0] == 400, (res, err))
res, err = call(web.api_engines, g2, {"on": ["claude", "bogus"]})
check("select: an unknown engine is refused", err and err[0] == 400, (res, err))
res, err = call(web.api_engine_key, g2, {"engine": "grok", "key": BAD})
check("key: a refused key gives 400 and is not stored", err and err[0] == 400 and not g2.engine_key_file("grok").exists(), (res, err))
res, err = call(web.api_engine_key, g2, {"engine": "grok", "key": GOOD})
check("key: a good key is stored and the models come back, without the key", err is None and "grok-4.7" in res["models"] and GOOD not in json.dumps(res), (res, err))
res, err = call(web.api_engines, g2, {"on": ["grok", "claude"]})
check("select: with the key Grok can be selected; the main engine is the first of the box", err is None and g2.engines_on() == ["claude", "grok"] and g2.engine() == "claude", (res, err))
res, err = call(web.api_engines, g2, {"on": ["grok"]})
check("select: only Grok", err is None and g2.engines_on() == ["grok"] and g2.engine() == "grok" and g2.engine_ok())
res, err = call(web.api_engine_model, g2, {"engine": "grok", "model": "grok-build-0.1"})
check("model: set for Grok", err is None and g2.engine_model("grok") == "grok-build-0.1")
res, err = call(web.api_engine_model, g2, {"engine": "grok", "model": "bad model; rm -rf"})
check("model: a name with odd characters is refused", err and err[0] == 400)
res, err = call(web.api_engine_model, g2, {"engine": "codex", "model": "", "effort": "nonsense"})
check("model: an unknown Codex effort is refused", err and err[0] == 400)
res, err = call(web.api_engine_models, g2, None, "grok")
check("models: the list of the provider", err is None and "grok-4.7" in res["models"], (res, err))
res, err = call(web.api_engine_key_remove, g2, {"engine": "grok"})
check("key remove: the file is gone and the engine leaves the selection", err is None and not g2.engine_key_file("grok").exists() and "grok" not in g2.engines_on())

# Codex sign-in (the owner, and the people the owner allows)
res, err = call(web.codex_login, g2)
check("Codex sign-in: not for a guest", err and err[0] == 403, (res, err))
check("a guest is not allowed Codex, the owner is", not sb.codex_allowed(g2) and sb.codex_allowed(main))
web.need_admin = lambda request: request.state.sp
res, err = call(web.api_account_action, main, None, "two", "codex_on")
check("the admin allows Codex for an account", err is None and sb.codex_allowed(g2) and sb.engines_view(g2)["engines"]["codex"]["allowed"] is True, (res, err))
res, err = call(web.api_account_action, main, None, "nobody-here", "codex_on")
check("... an account that does not exist is a 404", err and err[0] == 404, (res, err))
res, err = call(web.codex_login_status, g2)
check("an allowed person may use the Codex sign-in endpoints", err is None and res["loggedIn"] is False, (res, err))
res, err = call(web.api_account_action, main, None, "two", "codex_off")
check("the admin takes the permission back", err is None and not sb.codex_allowed(g2))
res, err = call(web.codex_login_status, g2)
check("... and the endpoints are closed again", err and err[0] == 403, (res, err))
os.environ["FAKE_CODEX_LOGIN_SECONDS"] = "1.5"
res, err = call(web.codex_login, main)
check("Codex sign-in: the link and the one-time code come back", err is None and res["url"] == "https://auth.example.test/codex/device" and res["code"] == "AB12-CD34", (res, err))
res, err = call(web.codex_login_status, main)
check("... the sign-in is pending while the process runs", err is None and res["pending"] is True and res["loggedIn"] is False, (res, err))
time.sleep(2.5)
res, err = call(web.codex_login_status, main)
check("... and when it ends Codex is signed in", err is None and res["pending"] is False and res["loggedIn"] is True, (res, err))
res, err = call(web.api_engines, main, {"on": ["claude", "codex"]})
check("the owner can select Codex now", err is None and main.engines_on() == ["claude", "codex"], (res, err))
res, err = call(web.codex_logout, main)
check("Codex sign-out leaves the selection and the credentials", err is None and not main.engine_connected("codex") and main.engines_on() == ["claude"])


# ---------------------------------------------------------------- Gemini with a Google sign-in (the official CLI in a pseudo-terminal)
for k in ("FAKE_GEMINI", "FAKE_ERROR", "FAKE_CODEX"):
    os.environ.pop(k, None)
os.environ["FAKE_GEMINI_LOG"] = str(Path(os.environ["SB_DATA"]) / "gemini.log")
g3 = sb.Space("pub-three")
g3.ensure(login="three", name=None)
check("without a sign-in or a key Gemini is not connected", not g3.engine_connected("gemini") and not sb.engines_view(g3)["engines"]["gemini"]["cli"])
res, err = call(web.gemini_login, g3)
check("Gemini sign-in: the link comes back (the CLI runs in a terminal)", err is None and res["url"].startswith("https://accounts.google.com/"), (res, err))
res, err = call(web.gemini_code, g3, {"code": "WRONG"})
check("Gemini sign-in: a wrong code is not a sign-in", err is None and res["ok"] is False and not g3.engine_connected("gemini"), (res, err))
res, err = call(web.gemini_login, g3)
res, err = call(web.gemini_code, g3, {"code": "GOODCODE"})
check("Gemini sign-in: the right code stores the credentials and the engine is connected", err is None and res["ok"] is True and g3.gemini_cli_ok() and g3.engine_connected("gemini"), (res, err))
check("... the sign-in process is ended by the app", "gemini:" + g3.uid not in web._logins)
check("... the view says it is a Google sign-in", sb.engines_view(g3)["engines"]["gemini"]["cli"] is True)
res, err = call(web.gemini_code, g3, {"code": "GOODCODE"})
check("a code without a started sign-in is a 409", err and err[0] == 409, (res, err))
res, err = call(web.gemini_code, g3, {"code": ""})
check("an empty code is a 400", err and err[0] == 400, (res, err))
w = work("gm1")
r = sb.run_gemini_cli(g3, "do the gemini task", w, add_dirs=[HERE], role="clean")
gl = [json.loads(l) for l in Path(os.environ["FAKE_GEMINI_LOG"]).read_text().splitlines()]
check("Gemini CLI: the prompt goes on stdin with the skill in front, the answer is read from the JSON", r["result"] == "Done by gemini." and (w / "gemini_out.txt").exists()
      and "do the gemini task" in gl[-1]["prompt"] and "Skill `studybooks-material`" in gl[-1]["prompt"], gl[-1])
check("... auto_edit (no shell), JSON output, the reference folder added, trust skipped", "auto_edit" in gl[-1]["args"] and "json" in gl[-1]["args"] and "--include-directories" in gl[-1]["args"]
      and str(HERE) in gl[-1]["args"][gl[-1]["args"].index("--include-directories") + 1] and "--skip-trust" in gl[-1]["args"], gl[-1]["args"])
check("... its own GEMINI_CLI_HOME, run in the work dir, and no key from the server's environment", gl[-1]["home"] == str(g3.gemini_home()) and gl[-1]["cwd"] == str(w) and gl[-1]["env_key"] is None)
for mode, exc, why in (("limit", sb.TransientError, "a quota limit"), ("auth", sb.AuthError, "a sign-in problem"), ("fail", RuntimeError, "another failure")):
    os.environ["FAKE_GEMINI"] = mode
    try:
        sb.run_gemini_cli(g3, "x", work("gm-" + mode))
        check(f"Gemini CLI: {why} is told apart", False)
    except Exception as e:
        check(f"Gemini CLI: {why} is told apart", type(e) is exc, type(e).__name__)
    if mode == "auth":
        check("... a refused sign-in is forgotten", not g3.gemini_cli_ok())
        (g3.gemini_home() / ".gemini" / "oauth_creds.json").write_text("{}")
os.environ.pop("FAKE_GEMINI")
check("the quota text gives the rest time", en.cool_seconds("[API Error: 429 Quota exceeded. Please retry in 3 hours.]") == 3 * 3600)
# run_llm uses the CLI when signed in, the key when not
g3.update_profile(engines_on=["gemini"], engine="gemini")
Path(os.environ["FAKE_GEMINI_LOG"]).unlink()
sb.run_llm(g3, "via the cli", work("gm2"), role="clean")
check("run_llm: Gemini with a Google sign-in uses the CLI", Path(os.environ["FAKE_GEMINI_LOG"]).exists())
g3.gemini_forget()
sb.oa_set_key(g3, "gemini", "AIzaKey-0123456789abcdefghijklmnop")
Path(os.environ["FAKE_GEMINI_LOG"]).unlink()
sb.run_llm(g3, "via the key", work("gm3"), role="clean")
check("... and with only an API key it uses the key loop", not Path(os.environ["FAKE_GEMINI_LOG"]).exists() and (work("gm3") / "oa_out.txt").exists())
# switching: Claude at its limit, Gemini (CLI) takes over
(g3.gemini_home() / ".gemini" / "oauth_creds.json").write_text("{}")
g3.update_profile(engines_on=["claude", "gemini"], engine="claude")
os.environ["FAKE_ERROR"] = "429 rate limit"
w = work("gm4")
sb.run_llm(g3, "switch to gemini", w, role="clean")
os.environ.pop("FAKE_ERROR")
check("a Claude limit hands the call to Gemini with a Google sign-in", (w / "gemini_out.txt").exists() and "claude" in g3.engine_cool_get())
res, err = call(web.gemini_logout, g3)
check("Gemini sign-out removes the credentials and keeps the engine only if a key is there", err is None and not g3.gemini_cli_ok() and g3.engine_connected("gemini"), (res, err))


# ---------------------------------------------------------------- Grok with an xAI sign-in (the official Grok Build CLI, device code)
for k in ("FAKE_GEMINI", "FAKE_ERROR", "FAKE_CODEX", "FAKE_GROK"):
    os.environ.pop(k, None)
os.environ["FAKE_GROK_LOG"] = str(Path(os.environ["SB_DATA"]) / "grok.log")
g4 = sb.Space("pub-four")
g4.ensure(login="four", name=None)
check("without a sign-in or a key Grok is not connected", not g4.engine_connected("grok") and not sb.engines_view(g4)["engines"]["grok"]["cli"])
res, err = call(web.grok_login, g4)
check("Grok sign-in: the link and the one-time code come back", err is None and res["url"].startswith("https://accounts.x.ai/oauth2/device") and res["code"] == "AB12-CD34", (res, err))
res, err = call(web.grok_login_status, g4)
check("... the sign-in is pending while the process runs", err is None and res["pending"] is True and res["loggedIn"] is False, (res, err))
time.sleep(2.5)
res, err = call(web.grok_login_status, g4)
check("... and when it ends Grok is signed in, with the credentials in this person's own HOME", err is None and res["loggedIn"] is True and res["pending"] is False
      and (g4.grok_home() / ".grok" / "auth.json").exists() and g4.engine_connected("grok"), (res, err))
check("... the view says it is an account sign-in", sb.engines_view(g4)["engines"]["grok"]["cli"] is True)
os.environ["XAI_API_KEY"] = "xai-server-key-must-not-pass"
check("the CLI never gets a key from the server's environment, and HOME is the person's own", "XAI_API_KEY" not in g4.grok_env() and g4.grok_env()["HOME"] == str(g4.grok_home()))
w = work("gk1")
r = sb.run_grok_cli(g4, "do the grok task", w, add_dirs=[HERE], role="clean")
gl = [json.loads(l) for l in Path(os.environ["FAKE_GROK_LOG"]).read_text().splitlines()]
check("Grok CLI: the prompt goes in a file with the skill in front, the answer is read from the JSON", r["result"] == "Done by grok." and (w / "grok_out.txt").exists()
      and "do the grok task" in gl[-1]["prompt"] and "Skill `studybooks-material`" in gl[-1]["prompt"], gl[-1])
check("... acceptEdits (no shell), JSON, no web search, the work dir and the reference folder", "acceptEdits" in gl[-1]["args"] and "json" in gl[-1]["args"] and "--disable-web-search" in gl[-1]["args"]
      and str(w) in gl[-1]["args"] and str(HERE) in gl[-1]["args"], gl[-1]["args"])
check("... the person's own HOME, no key from the server, and the prompt file is removed", gl[-1]["home"] == str(g4.grok_home()) and gl[-1]["env_key"] is None
      and not list((g4.root / "tmp").glob("grok-*.txt")))
for mode, exc, why in (("limit", sb.TransientError, "a rate limit"), ("auth", sb.AuthError, "a sign-in problem"), ("fail", RuntimeError, "another failure")):
    os.environ["FAKE_GROK"] = mode
    try:
        sb.run_grok_cli(g4, "x", work("gk-" + mode))
        check(f"Grok CLI: {why} is told apart", False)
    except Exception as e:
        check(f"Grok CLI: {why} is told apart", type(e) is exc, type(e).__name__)
    if mode == "auth":
        check("... a refused sign-in is forgotten", not g4.grok_cli_ok())
        (g4.grok_home() / ".grok").mkdir(parents=True, exist_ok=True)
        (g4.grok_home() / ".grok" / "auth.json").write_text("{}")
os.environ.pop("FAKE_GROK")
g4.update_profile(engines_on=["grok"], engine="grok")
Path(os.environ["FAKE_GROK_LOG"]).unlink()
sb.run_llm(g4, "via the cli", work("gk2"), role="clean")
check("run_llm: Grok with an account sign-in uses the CLI", Path(os.environ["FAKE_GROK_LOG"]).exists())
g4.grok_forget()
sb.oa_set_key(g4, "grok", GOOD)
Path(os.environ["FAKE_GROK_LOG"]).unlink()
sb.run_llm(g4, "via the key", work("gk3"), role="clean")
check("... and with only an API key it uses the key loop", not Path(os.environ["FAKE_GROK_LOG"]).exists() and (work("gk3") / "oa_out.txt").exists())
(g4.grok_home() / ".grok" / "auth.json").write_text("{}")
g4.update_profile(engines_on=["claude", "grok"], engine="claude")
os.environ["FAKE_ERROR"] = "429 rate limit"
w = work("gk4")
sb.run_llm(g4, "switch to grok", w, role="clean")
os.environ.pop("FAKE_ERROR")
check("a Claude limit hands the call to Grok with an account sign-in", (w / "grok_out.txt").exists() and "claude" in g4.engine_cool_get())
res, err = call(web.grok_logout, g4)
check("Grok sign-out removes the credentials and keeps the engine only if a key is there", err is None and not g4.grok_cli_ok() and g4.engine_connected("grok"), (res, err))

# ---------------------------------------------------------------- the status
st = sb.status(sp)["engine"]
check("the status carries the box: order, per-engine state, the chain", st["switch"]["order"] == list(en.ORDER) and set(st["switch"]["engines"]) == set(en.ORDER)
      and "chain" in st["switch"] and st["switch"]["engines"]["codex"]["allowed"] is False, st["switch"]["order"])
page = web.INDEX.read_text(encoding="utf-8")
check("the page has the account sign-in for Gemini and Grok, one shared device-code flow for Codex and Grok", all(x in page for x in (
    "function showGemini", "function showGrok", "function deviceSignIn", "/api/gemini/code", "/api/grok/login", "/api/grok/status"))
      and page.count("function deviceSignIn") == 1 and 'deviceSignIn("codex"' in page and 'deviceSignIn("grok"' in page)
check("the owner may sign in to Codex; the guest may not (closed)", sb.engines_view(main)["engines"]["codex"]["allowed"] is True)
os.environ.pop("SB_CODEX_OPEN")
check("by default every person may sign in to Codex", sb.codex_allowed(g2) and sb.codex_allowed(guest))
res, err = call(web.api_engines, g2, {"on": ["claude", "codex"]})
check("... and the endpoint no longer says 403 (it says: not connected yet)", err and err[0] == 400 and "Codex" in err[1], (res, err))
res, err = call(web.codex_login, g2)
check("... a guest can start the Codex sign-in", err is None and res["code"] == "AB12-CD34", (res, err))
check("the claude/openrouter engine choice still works as before", sb.status(sp)["engine"]["name"] in en.ORDER)

print("\n%d failed" % len(fails) if fails else "\nall engine checks passed")
sys.exit(1 if fails else 0)
