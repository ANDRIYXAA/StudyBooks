"""Offline test of the usage bar: reading Claude Code's stream output, remembering the plan limits it reports, and what
the page gets. Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_usage.py"""
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import sb

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


NOW = time.time()
RES = {"type": "result", "subtype": "success", "is_error": False, "result": "done", "num_turns": 2}
ev = lambda info: json.dumps({"type": "rate_limit_event", "rate_limit_info": info, "uuid": "u", "session_id": "s"})

# --- reading the stream -----------------------------------------------------------------------------------------------
stream = "\n".join([json.dumps({"type": "system", "subtype": "init", "tools": ["Read"]}),
                    ev({"status": "allowed", "resetsAt": NOW + 3600, "rateLimitType": "five_hour", "utilization": 0.42, "isUsingOverage": False}),
                    "not json at all", "", "[1, 2]",
                    json.dumps({"type": "assistant", "message": {"content": [{"type": "text", "text": "hi"}]},
                                "api_error_params": {"rate_limit_info": {"status": "rejected", "rateLimitType": "seven_day", "resetsAt": NOW + 7200}}}),
                    json.dumps(RES)])
out, infos = sb.parse_claude_output(stream)
check("the final result message is found", out == RES)
check("rate_limit_event and a refused request's info are both collected", [i.get("rateLimitType") for i in infos] == ["five_hour", "seven_day"], str(infos))
out, infos = sb.parse_claude_output(json.dumps(RES))
check("the old single-JSON output still parses", out == RES and infos == [])
out, infos = sb.parse_claude_output(json.dumps(RES, indent=2))
check("indented single JSON parses too", out == RES)
check("garbage gives nothing, not a crash", sb.parse_claude_output("boom\n") == (None, []) and sb.parse_claude_output("") == (None, []) and sb.parse_claude_output(None) == (None, []))
deep = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"rate_limit_info": {"x": 1}}}}}}}}}
check("a very deep nesting is not followed", sb._find_limit_infos(deep, o := []) is None and o == [])
check("reset times: seconds, milliseconds, ISO, junk", sb._epoch(1.8e9) == 1.8e9 and sb._epoch(1.8e12) == 1.8e9
      and abs(sb._epoch("2030-01-01T00:00:00Z") - 1893456000) < 1 and sb._epoch("x") is None and sb._epoch(True) is None and sb._epoch(-5) is None)
check("utilization: fraction, percent, 100%, junk", sb._fraction(0.42) == 0.42 and sb._fraction(42) == 0.42 and sb._fraction(1) == 1.0
      and sb._fraction(250) == 1.0 and sb._fraction("a") is None and sb._fraction(None) is None and sb._fraction(-1) is None)

# --- remembering it ---------------------------------------------------------------------------------------------------
sp = sb.Space("usage-test").ensure()
sb.set_lang("en")
check("a person who has not run anything has no limits yet", sb.usage_view(sp) == {"engine": "claude", "limits": []})
sb.note_claude_limits(sp, [])
check("nothing to note, no file", not (sp.root / "claude_usage.json").exists())
sb.note_claude_limits(sp, [{"status": "allowed", "resetsAt": NOW + 3600, "rateLimitType": "five_hour", "utilization": 0.42},
                           {"status": "allowed", "resetsAt": (NOW + 86400) * 1000, "rateLimitType": "seven_day", "utilization": 12},
                           {"status": "allowed", "resetsAt": NOW + 5, "rateLimitType": "weird type!", "utilization": 0.1},
                           {"status": "allowed", "rateLimitType": 7}, {"status": "allowed"}])
v = sb.usage_view(sp)
check("two valid limits kept, junk types dropped", [r["type"] for r in v["limits"]] == ["five_hour", "seven_day"], str(v))
check("numbers are normalised", v["limits"][0]["util"] == 0.42 and v["limits"][1]["util"] == 0.12 and abs(v["limits"][1]["resets"] - (NOW + 86400)) < 1)
sb.note_claude_limits(sp, [{"status": "allowed", "resetsAt": NOW + 3600, "rateLimitType": "five_hour"}])
check("same window, no new number: the last known one stays", sb.usage_view(sp)["limits"][0]["util"] == 0.42)
sb.note_claude_limits(sp, [{"status": "allowed", "resetsAt": NOW + 9000, "rateLimitType": "five_hour"}])
check("a new window forgets the old number", sb.usage_view(sp)["limits"][0]["util"] is None)
sb.note_claude_limits(sp, [{"status": "rejected", "resetsAt": NOW + 9000, "rateLimitType": "five_hour"}])
check("a refusal is recorded as such", sb.usage_view(sp)["limits"][0]["status"] == "rejected")
sb.note_claude_limits(sp, [{"status": "allowed_warning", "resetsAt": NOW + 100, "rateLimitType": "seven_day_opus", "utilization": 0.9},
                           {"status": "allowed", "resetsAt": NOW + 100, "rateLimitType": "overage", "utilization": 0.1},
                           {"status": "allowed", "resetsAt": NOW + 100, "rateLimitType": "zzz", "utilization": 0.1},
                           {"status": "allowed", "resetsAt": NOW + 100, "rateLimitType": "seven_day_sonnet", "utilization": 0.1}])
check("at most 4 rows, in the usual order", [r["type"] for r in sb.usage_view(sp)["limits"]] == ["five_hour", "seven_day", "seven_day_opus", "seven_day_sonnet"],
      str([r["type"] for r in sb.usage_view(sp)["limits"]]))
f = sp.root / "claude_usage.json"
st = json.loads(f.read_text())
st["limits"]["five_hour"]["resets"] = NOW - 10
f.write_text(json.dumps(st))
check("a window that has reset is dropped", "five_hour" not in [r["type"] for r in sb.usage_view(sp)["limits"]])
check("the file keeps only the plan numbers (no tokens, no text)", all(set(e) <= {"status", "resets", "util", "overage", "seen"} for e in st["limits"].values()) and set(st) == {"limits", "updated"})
check("the file is private to the person's folder", f.parent == sp.root)

# --- from a real call of the CLI (the fake one) into the page's status -----------------------------------------------
sp2 = sb.Space("usage-run").ensure()
os.environ["FAKE_LIMITS"] = json.dumps([{"status": "allowed", "resetsAt": NOW + 600, "rateLimitType": "five_hour", "utilization": 0.55},
                                         {"status": "allowed_warning", "resetsAt": NOW + 5 * 86400, "rateLimitType": "seven_day", "utilization": 0.8}])
cwd = Path(tempfile.mkdtemp())
res = sb.run_llm(sp2, "write explanation.md please", cwd)
check("the call still works and returns the result", res.get("subtype") == "success" and (cwd / "explanation.md").exists(), str(res))
rows = sb.status(sp2)["usage"]["limits"]
check("status carries what the call reported", [(r["type"], r["util"]) for r in rows] == [("five_hour", 0.55), ("seven_day", 0.8)], str(rows))
check("status says which engine", sb.status(sp2)["usage"]["engine"] == "claude")
check("80 % of the week stops the queue until the person confirms", (sb.status(sp2)["limit_gate"] or {}).get("step") == 0.8)
sp2.ack_limits()     # "Continue" (the stops themselves: test_optim.py)
os.environ["FAKE_LIMITS"] = "[]"
sb.run_llm(sp2, "write explanation.md please", cwd)
check("a call that reports nothing leaves the numbers as they were", [r["util"] for r in sb.status(sp2)["usage"]["limits"]] == [0.55, 0.8])

# --- OpenRouter -----------------------------------------------------------------------------------------------------------
sp3 = sb.Space("usage-or").ensure()
sp3.update_profile(engine="openrouter", openrouter_model="some/model")
check("OpenRouter with a chosen model has no bar", sb.usage_view(sp3) == {"engine": "openrouter"})
sp3.update_profile(openrouter_model=sb.OR_AUTO, or_daily_cap=50)
sb.write_json(sp3.root / "or_state.json", {"day": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).strftime("%Y-%m-%d"), "used": 12})
v = sb.usage_view(sp3)
check("auto-free shows requests used today of the cap", v.get("auto") and v["used"] == 12 and v["cap"] == 50 and v["until"] is None, str(v))
sp3.update_profile(or_daily_cap=None)
check("an unknown cap is None, not a guess", sb.usage_view(sp3)["cap"] is None)

# --- the page ---------------------------------------------------------------------------------------------------------------
html = (Path(sb.__file__).parent / "web" / "index.html").read_text(encoding="utf-8") if (Path(sb.__file__).parent / "web" / "index.html").exists() else ""
check("the page has the usage block under the engine button", 'id="usage"' in html and 'class="engwrap"' in html and "renderUsage()" in html)

print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
