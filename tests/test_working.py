"""Offline test: the activity labels name the engine and model that really work (Claude or OpenRouter), not a fixed "Claude".
Run from a COPY of the code, fresh data dir: SB_DATA=<tmp> python test_working.py (never on the real data)"""
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
sys.path.insert(0, str(Path(__file__).resolve().parent))
import sb  # noqa: E402
import web  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


# ---- the status
check("idle: nothing is working", sb.working_view({"state": "idle", "cur_engine": "claude", "cur_model": "opus"}) is None)
check("busy but no call made yet: nothing to name", sb.working_view({"state": "busy"}) is None)
check("an old worker (no engine recorded): an id with a slash is OpenRouter", sb.working_view({"state": "busy", "cur_model": "nvidia/x:free"}) == {"engine": "openrouter", "model": "nvidia/x:free"})
check("an old worker: a plain name is Claude", sb.working_view({"state": "busy", "cur_model": "opus"}) == {"engine": "claude", "model": "opus"})
check("busy with OpenRouter", sb.working_view({"state": "busy", "cur_engine": "openrouter", "cur_model": "a/b:free"}) == {"engine": "openrouter", "model": "a/b:free"})
check("busy with Claude", sb.working_view({"state": "busy", "cur_engine": "claude", "cur_model": "sonnet"}) == {"engine": "claude", "model": "sonnet"})

src = Path(sb.__file__).read_text(encoding="utf-8")
check("the Claude call records its engine", 'set_status(cur_model=model, cur_engine="claude", cur_donor=None)' in src)
check("both OpenRouter places record theirs", src.count('cur_engine="openrouter"') == 2, src.count('cur_engine="openrouter"'))
check("the engine is cleared together with the progress of an item", '"cur_engine"' in src[src.index("PROGRESS_KEYS"):src.index("def working_view")])

me = sb.Space(sb.admin_uids()[0])
me.ensure(login="owner@example.com", name=None)
sb.set_status(state="busy", cur_engine="openrouter", cur_model="nvidia/nemotron-3-ultra-550b-a55b:free")
st = sb.status(me)
check("the status of a person carries `working`", st.get("working") == {"engine": "openrouter", "model": "nvidia/nemotron-3-ultra-550b-a55b:free"}, st.get("working"))
sb.set_status(state="idle")
check("... and it is empty when the worker is idle", sb.status(me).get("working") is None)
check("a stranger's status carries it too", '"working": st.get("working")' in Path(web.__file__).read_text(encoding="utf-8"))

# ---- the label on the page (the real function, run with node)
index = web.INDEX.read_text(encoding="utf-8")
fn = index[index.index("function engineWho()"):index.index("function act(phase")]
act_line = re.search(r'text = \(T\.uk\[key\] \? t\(key\) : phase\)\.replace\(/\^Claude\(\?=\\s\)/, engineWho\(\)\);', index)
check("act() puts the engine into the label", bool(act_line))
script = fn + r'''
const cases = [
  [{engine: "openrouter", model: "nvidia/nemotron-3-ultra-550b-a55b:free"}, "OpenRouter · nemotron-3-ultra-550b-a55b"],
  [{engine: "openrouter", model: "openai/gpt-4o"}, "OpenRouter · gpt-4o"],
  [{engine: "claude", model: "opus"}, "Claude · opus"],
  [{engine: "claude", model: "claude-sonnet-4-5-20250929"}, "Claude · claude-sonnet-4-5"],
  [{engine: "claude", model: null}, "Claude"],
  [null, "Claude"],
];
const bad = [];
for (const [w, want] of cases) { globalThis.S = {working: w}; const got = engineWho(); if (got !== want) bad.push([w, got, want]); }
globalThis.S = null; if (engineWho() !== "Claude") bad.push(["S null", engineWho()]);
const label = "Claude is reading the upload…".replace(/^Claude(?=\s)/, (globalThis.S = {working: {engine: "openrouter", model: "x/y:free"}}, engineWho()));
if (label !== "OpenRouter · y is reading the upload…") bad.push(["label", label]);
const uk = "Claude розбирає завантаження…".replace(/^Claude(?=\s)/, engineWho());
if (uk !== "OpenRouter · y розбирає завантаження…") bad.push(["label uk", uk]);
const other = "The book is being built".replace(/^Claude(?=\s)/, engineWho());
if (other !== "The book is being built") bad.push(["other label", other]);
console.log(JSON.stringify(bad));
'''
tmp = Path(tempfile.mkdtemp()) / "t.js"
tmp.write_text(script, encoding="utf-8")
try:
    r = subprocess.run(["node", str(tmp)], capture_output=True, text=True, timeout=30)
    check("the label names OpenRouter or Claude with a short model name", r.returncode == 0 and r.stdout.strip() == "[]", r.stdout + r.stderr)
except FileNotFoundError:
    print("skip: node is not installed")

print("\n%d failed" % len(fails) if fails else "\nall working-label checks passed")
sys.exit(1 if fails else 0)
