"""Offline test of linked accounts (SB_LINKED): two accounts of one person share every sign-in, and donations to either
account work for both. Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_linked.py"""
import json
import os
import sys
import time
from pathlib import Path

PORT = 18781
os.environ["SB_OPENROUTER_URL"] = f"http://127.0.0.1:{PORT}"
os.environ["SB_LINKED"] = "boss-example-com, pub-boss ; pub-x,pub-x"    # spaces and a one-member group are ignored
sys.path.insert(0, str(Path(__file__).resolve().parent / "tests"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import fake_openrouter  # noqa: E402
import sb  # noqa: E402

srv, REQS = fake_openrouter.start(PORT)
sb.OR_RETRY_SLEEP = 0
sb.OR_FREE_GAP = 0
sb.DONATE_CACHE_S = 0
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
        print("   unexpected", type(e).__name__, e)
        return None
    return None


OWN_KEY = "sk-or-v1-ownkey00123456789abcdefgh"
DONOR_KEY = "sk-or-v1-donorkey0123456789abcdef"
tmp = Path(os.environ["SB_DATA"]) / "scratch"
tmp.mkdir(parents=True)

boss = sb.Space("boss-example-com").ensure(login="boss@example.com", name="Boss Person")
pub = sb.Space("pub-boss").ensure(login="boss", name="boss")
dora = sb.Space("pub-dora").ensure(login="dora", name="dora")
rita = sb.Space("pub-rita").ensure(login="rita", name="rita")
sb._names.clear()


def run(sp, name):
    w = tmp / name
    w.mkdir(exist_ok=True)
    return sb.run_llm(sp, "do the thing", w), w


def keys_since(n0):
    return [q["key"] for q in REQS[n0:]]


# --- the groups --------------------------------------------------------------------------------------------------------
check("groups: one real group, the home first", sb.link_groups() == [["boss-example-com", "pub-boss"]], sb.link_groups())
check("groups: both members see the same group", sb.linked_uids("pub-boss") == sb.linked_uids("boss-example-com"))
check("groups: a person in no group is alone", sb.linked_uids("pub-rita") == ["pub-rita"])
check("linked(): the other accounts only", [s.uid for s in pub.linked()] == ["boss-example-com"] and [s.uid for s in boss.linked()] == ["pub-boss"])

# --- every sign-in lives in the home folder ------------------------------------------------------------------------------
check("home: the login folder of both accounts is the home's", pub.login_root == boss.login_root == boss.root)
check("home: an account in no group keeps its own", rita.login_root == rita.root)
check("claude: the same CLAUDE_CONFIG_DIR", pub.claude_env()["CLAUDE_CONFIG_DIR"] == boss.claude_env()["CLAUDE_CONFIG_DIR"] == str(boss.root / "claude"))
check("codex: the same CODEX_HOME", pub.codex_env()["CODEX_HOME"] == boss.codex_env()["CODEX_HOME"])
check("grok and gemini: the same HOME", pub.grok_home() == boss.grok_home() and pub.gemini_home() == boss.gemini_home())
check("keys: the same files", pub.or_key_file == boss.or_key_file and pub.engine_key_file("grok") == boss.engine_key_file("grok"))
check("rita: her own files", rita.claude_env()["CLAUDE_CONFIG_DIR"] == str(rita.root / "claude") and rita.or_key_file.parent == rita.root)

boss.engine_key_file("grok").write_text("xai-key-of-the-boss-1234")
check("a key set in one account is there in the other", pub.engine_key("grok") == "xai-key-of-the-boss-1234")
pub.engine_key_file("grok").unlink()
check("...and removed in one account it is gone in both", boss.engine_key("grok") is None)
(boss.grok_home() / ".grok").mkdir(parents=True, exist_ok=True)
(boss.grok_home() / ".grok" / "auth.json").write_text("{}")
check("grok sign-in of one account counts for the other", sb.grok_bin().exists() is False or pub.grok_cli_ok())
(boss.grok_home() / ".grok" / "auth.json").unlink()
pub.engine_cool_set("grok", time.time() + 600, "limit")
check("rests after a limit are shared (the same sign-in has the same limit)", "grok" in boss.engine_cool_get())
boss.engine_cool_clear()
sb.note_claude_limits(pub, [{"rateLimitType": "five_hour", "status": "allowed", "utilization": 0.4, "resetsAt": time.time() + 3600}])
check("Claude's reported limits are shared", ((sb.read_json(boss.login_root / "claude_usage.json", {}) or {}).get("limits") or {}).get("five_hour", {}).get("util") == 0.4)

# --- settings and books stay per account -------------------------------------------------------------------------------------
pub.update_profile(engine="openrouter", openrouter_model=sb.OR_AUTO)
boss.update_profile(engine="openrouter", openrouter_model=sb.OR_AUTO, claude_model="opus")
pub.update_profile(claude_model="sonnet")
check("settings: per account", boss.claude_model() == "opus" and pub.claude_model() == "sonnet")
check("books: per account", pub.d["library"] != boss.d["library"])

# --- OpenRouter: one key and one counter for both -----------------------------------------------------------------------------
boss.or_key_file.write_text(OWN_KEY)
n0 = len(REQS)
r, w = run(pub, "p1")
check("the public account works on the shared key", set(keys_since(n0)) == {OWN_KEY} and (w / "hello.txt").exists(), keys_since(n0))
check("one counter for the shared key", sb.or_state(boss).get("used") == 2 and sb.or_state(pub) == sb.or_state(boss), sb.or_state(boss))

# --- a donation to the PUBLIC account works for the admin account -------------------------------------------------------------
dora.or_key_file.write_text(DONOR_KEY)
sb.donate_set(dora, "pub-boss", 3)
check("donor: offered to the account that was named", [d.uid for d in sb.or_donors(pub, cached=False)] == ["pub-dora"])
check("donor: offered to the linked admin account as well", [d.uid for d in sb.or_donors(boss, cached=False)] == ["pub-dora"])
check("donor: not offered to a person outside the group", sb.or_donors(rita, cached=False) == [])
check("donor_named: the named account and its cap", sb.donor_named(dora, boss) == ("pub-boss", 3))
sb.or_cool_all(boss, time.time() + 3600, "daily limit")
check("the daily-limit pause of the shared key is seen by both", (sb.or_state(pub).get("all_until") or 0) > time.time())
n0 = len(REQS)
r, w = run(boss, "b1")
check("admin: with the own limit used up the donation to the public account takes over",
      set(keys_since(n0)) == {DONOR_KEY} and (w / "hello.txt").exists(), keys_since(n0))
check("admin: the gift is counted on the account that was named", sb.donated_today(dora, "pub-boss") == 2
      and sb.donated_today(dora, "boss-example-com") == 0, sb.read_json(dora.root / "donated.json"))
uv = sb.usage_view(boss)
check("admin: the usage bar shows the gift and that the work runs on it",
      uv.get("donate", {}).get("from") == [{"name": "dora", "used": 2, "cap": 3, "left": 1, "paused": False}]
      and uv["donate"]["using"] is True, uv)
check("admin: the engine dialog lists the donor", [x["name"] for x in sb.donate_view(boss)["from"]] == ["dora"])
n0 = len(REQS)
e = raises(sb.TransientError, run, pub, "p2")      # the shared own key is parked for the day: the run waits
check("the cap is for the group: the public account gets only what is left (1), then it waits",
      len(REQS) - n0 == 1 and sb.donated_today(dora, "pub-boss") == 3 and e is not None, (len(REQS) - n0, sb.donated_today(dora, "pub-boss")))
check("cap used up: no donor for either account", sb.or_donors(pub, cached=False) == [] and sb.or_donors(boss, cached=False) == [])

# --- a direct donation to the admin account is counted on its own name ---------------------------------------------------------
sb.donate_set(dora, "boss-example-com", 2)
check("donor_named: the own name comes first", sb.donor_named(dora, boss) == ("boss-example-com", 2)
      and sb.donor_named(dora, pub) == ("pub-boss", 3))
sb.donate_remove(dora, sb.donate_id("boss-example-com"))

# --- an account of the group never "donates" to the group ------------------------------------------------------------------------
sb.donate_set(pub, "boss-example-com", 50)
check("a donation inside the group is ignored (same person, same key)", all(d.uid != "pub-boss" for d in sb.or_donors(boss, cached=False)))
sb.donate_remove(pub, sb.donate_id("boss-example-com"))

# --- the page: who shares the sign-ins (names only, never a uid) ----------------------------------------------------------------
ev = sb.engines_view(boss)
check("engines view: the admin sees the public account's name", ev["linked"] == ["boss"], ev.get("linked"))
check("engines view: the public account sees the admin's first name, no e-mail", sb.engines_view(pub)["linked"] == ["Boss"]
      and "example" not in json.dumps(sb.engines_view(pub)["linked"]))
check("engines view: nobody linked -> empty", sb.engines_view(rita)["linked"] == [])

# --- without SB_LINKED everything is separate again -------------------------------------------------------------------------------
os.environ["SB_LINKED"] = ""
check("off: own folders again", pub.login_root == pub.root and pub.or_key() is None and pub.linked() == [])
check("off: a donation to the public account is not offered to the admin", sb.or_donors(boss, cached=False) == [])
os.environ["SB_LINKED"] = "nobody-here,pub-boss"
check("a home without a profile: the account keeps its own folder", pub.login_root == pub.root)

srv.shutdown()
print("\n" + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
