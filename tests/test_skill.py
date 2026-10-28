"""Offline test: the skill every model call gets, and the learning session in which Opus rewrites it (budget = 20 % of
the measured 5-hour window, every version checked, kept and reversible).

Run from a COPY of the code, with a fresh data dir:
  SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_skill.py   (never on the real data)"""
import asyncio
import io
import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_DIGEST_QUIET"] = "3600"
os.environ["SB_SV_PORTS"] = ""
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "tests" / "fake_claude.py"))
ARGS = Path(os.environ["SB_DATA"]) / "args.jsonl"
os.environ["FAKE_ARGS"] = str(ARGS)

import sb  # noqa: E402
import web  # noqa: E402
from PIL import Image  # noqa: E402
from starlette.requests import Request  # noqa: E402

web.kick = lambda: None
fails = []
OWNER = "owner@example.com"


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def req(login):
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": [(b"tailscale-user-login", login.encode()), (b"x-lang", b"en")]})


class Up:
    def __init__(self, name, data):
        self.filename, self._b = name, io.BytesIO(data)

    async def read(self, n):
        return self._b.read(n)


def png(i):
    b = io.BytesIO()
    Image.new("RGB", (400, 300), (255, 200 - i, 200)).save(b, "PNG")
    return b.getvalue()


# --- the skill and what each call gets ---------------------------------------------------------------------------------
seed = sb.SKILL_SEED.read_text(encoding="utf-8")
check("the shipped skill passes its own checks", sb.skill_problems(seed) == [], sb.skill_problems(seed))
check("the skill is a real Claude Code skill (frontmatter)", seed.startswith("---\nname: studybooks-material\ndescription: "))
t = sb.agent_brief("transcribe")
check("a transcription call gets General + its section", "## General" not in t and "Pandoc Markdown" in t and "For this task (transcribe)" in t)
check("... and not the other sections", "For this task (clean)" not in t and "Correct silently" not in t)
check("a call without a role gets General only", "Pandoc Markdown" in sb.agent_brief(None) and "For this task" not in sb.agent_brief(None))
check("the learning call gets no brief (it reads the whole file)", sb.agent_brief("learn") == "")
check("the brief stays small", len(sb.agent_brief("transcribe")) < 9000, len(sb.agent_brief("transcribe")))
for name, bad in [("no frontmatter", seed.split("---", 2)[2]), ("a link", seed + "\nsee https://x.example\n"),
                  ("HTML", seed.replace("## refs", "<script>x</script>\n## refs")), ("a lost section", seed.replace("## fuse", "## fusion")),
                  ("too long", seed + "x" * sb.SKILL_MAX), ("most thrown away", seed[:900] + "\n## General\nx\n## transcribe\nx\n## refs\nx\n## clean\nx\n## fuse\nx\n")]:
    check(f"a skill with {name} is refused", sb.skill_problems(bad, seed) != [])

# --- the budget: 20 % of the 5-hour window, measured from Claude Code's own reports -------------------------------------
main = web.who(req(OWNER))
sb.learn_note(auto=False)
check("without a measurement the budget is the careful default", sb.learn_budget(main) == (sb.LEARN_USD_DEFAULT, "default"))
resets = time.time() + 3 * 3600
sb.write_json(main.root / "claude_usage.json", {"limits": {"five_hour": {"status": "allowed_warning", "resets": resets, "util": 0.5}}})
sb.note_spend(main, 4.0)
check("$4 at 50 % of the window -> a window of $8", sb.read_json(main.root / "claude_usage.json")["window_usd"] == 8.0)
check("-> a session budget of $1.60", sb.learn_budget(main) == (1.6, "measured"), sb.learn_budget(main))
sb.write_json(main.root / "claude_usage.json", {"window_usd": 10.0})

# --- material to learn from -------------------------------------------------------------------------------------------
for i in range(2):
    asyncio.run(web.api_upload(req(OWNER), files=[Up(f"p{i}.png", png(i))], note="", subject="math1", kind="notes",
                               topic="", topic_title="", code="", date=sb.now().date().isoformat()))
sb.worker()
check("two library entries to learn from", len(sb.entries_of(main, "math1")) == 2)
sb.log("[pub-x] /home/u/studybooks/data/users/pub-x/work/1/c1/e1_original.md still broken, raw fallback: pandoc could not convert some TeX math")
sb.log("    unexpected control sequence \\atop")
lessons, _ = sb.collect_lessons(0)
check("the log's problems are collected", "raw fallback" in lessons)
check("... without who it was or server paths", "pub-x" not in lessons and "/home/u" not in lessons, lessons[-300:])

check("automatic mode off -> no session", not sb.learn_due(main))
sb.learn_note(auto=True)
check("automatic mode: enough new evidence -> a session is due", sb.learn_due(main), sb.learn_evidence(main))
sb.learn_note(auto=False, request=True)
check("a by-hand request is due", sb.learn_due(main))

# --- a good session ---------------------------------------------------------------------------------------------------
ARGS.write_text("")
os.environ["FAKE_LEARN"] = "ok"
r = sb.learn_skill(main)
live = sb.SKILL_FILE.read_text(encoding="utf-8")
check("a good rewrite goes live", "Learned rule" in live and r["versions"], r)
check("... it is kept as a version with notes", sb.learn_state()["history"][-1]["notes"].startswith("- added a rule"))
check("... the seed is kept too, to roll back to", "seed" in sb.skill_versions())
calls = [json.loads(l) for l in ARGS.read_text().splitlines()]
check("the session runs on Opus", calls and all(a[a.index("--model") + 1] == "opus" for a in calls), calls[:1])
check("... with the remaining budget as Claude Code's own cap", all("--max-budget-usd" in a and float(a[a.index("--max-budget-usd") + 1]) <= 2.0 for a in calls))
check("... and without the brief in its system prompt", all("--append-system-prompt" not in a for a in calls))
check("the reviewed entries are remembered", len(sb.learn_state()["reviewed"]) >= 2)
check("the request is used up", not sb.learn_state().get("request") and not sb.learn_due(main))
check("the cheap passes now get the learned rule", "Learned rule" in sb.agent_brief("transcribe"))

# --- bad rewrites never go live ---------------------------------------------------------------------------------------
for mode in ("bad", "url"):
    before = sb.SKILL_FILE.read_text(encoding="utf-8")
    os.environ["FAKE_LEARN"] = mode
    sb.learn_note(request=True, reviewed=[])
    sb.learn_skill(main)
    check(f"a {mode} rewrite is refused, the live skill stays", sb.SKILL_FILE.read_text(encoding="utf-8") == before)
    check(f"... and the reason is kept", bool(sb.learn_state().get("last_error")), sb.learn_state().get("last_error"))

# --- Claude Code stops at --max-budget-usd: not a sign-in problem; what it wrote is still checked and used ------------
os.environ["FAKE_LEARN"], os.environ["FAKE_BUDGET"] = "ok", "1"
n = len(sb.learn_state()["history"])
sb.learn_note(request=True, reviewed=[])
try:
    sb.learn_skill(main)
    check("a call that hit its spending cap is not taken for a sign-in error", True)
except sb.AuthError as e:
    check("a call that hit its spending cap is not taken for a sign-in error", False, e)
check("... and the skill it wrote before the cap still goes live", len(sb.learn_state()["history"]) == n + 1)
os.environ.pop("FAKE_BUDGET")

# --- the budget and the limit stop it -----------------------------------------------------------------------------------
sb.write_json(main.root / "claude_usage.json", {"window_usd": 0.5})
sb.learn_note(request=True)
ARGS.write_text("")
os.environ["FAKE_LEARN"] = "ok"
r = sb.learn_skill(main)
check("a budget too small for one call -> no call at all", r["rounds"] == 0 and ARGS.read_text() == "", r)
sb.write_json(main.root / "claude_usage.json", {"limits": {"five_hour": {"status": "allowed_warning", "resets": resets, "util": 0.92}}})
sb.learn_note(request=True)
check("a 70/80/90 % stop waiting for Continue -> no session", not sb.learn_due(main))
sb.write_json(main.root / "claude_usage.json", {"window_usd": 10.0})

# --- the worker runs a requested session; the admin rolls back ---------------------------------------------------------
n = len(sb.learn_state()["history"])
sb.learn_note(request=True, reviewed=[])
sb.worker()
check("the worker runs a requested session", len(sb.learn_state()["history"]) == n + 1, sb.learn_state().get("last_error"))
v = sb.rollback_skill("seed")
check("rolling back to the seed restores it", sb.SKILL_FILE.read_text(encoding="utf-8") == seed and v)
try:
    sb.rollback_skill("../../etc/passwd")
    check("a bad version name is refused", False)
except LookupError:
    check("a bad version name is refused", True)
info = sb.skill_info()
check("the admin view has the text, history and budget", info["text"] == seed and info["history"] and info["budget"][1] == "measured")
os.environ.pop("FAKE_LEARN")

print("\nALL OK" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
