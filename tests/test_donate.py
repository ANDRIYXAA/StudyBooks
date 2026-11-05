"""Offline test of "Donor to <nickname>": one person lets another use their free OpenRouter requests.
Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_donate.py"""
import json
import os
import sys
import time
from pathlib import Path

PORT = 18779
os.environ["SB_OPENROUTER_URL"] = f"http://127.0.0.1:{PORT}"
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


DONOR_KEY = "sk-or-v1-donorkey0123456789abcdef"
OWN_KEY = "sk-or-v1-ownkey00123456789abcdefgh"
BAD_KEY = "sk-or-v1-badkey00123456789abcdefgh"
tmp = Path(os.environ["SB_DATA"]) / "scratch"
tmp.mkdir(parents=True)

donor = sb.Space("pub-dora").ensure(login="dora", name="dora")
rita = sb.Space("pub-rita").ensure(login="rita", name="rita")
anna = sb.Space("anna-example-com").ensure(login="anna@example.com", name="Anna Smith")
anna2 = sb.Space("anna-other-example-com").ensure(login="anna@other.example.com", name="Anna Jones")
sb._names.clear()
donor.or_key_file.write_text(DONOR_KEY)
for p in (rita, anna):
    p.update_profile(engine="openrouter", openrouter_model=sb.OR_AUTO)


def run(sp, name):
    w = tmp / name
    w.mkdir(exist_ok=True)
    return sb.run_llm(sp, "do the thing", w), w


def keys_since(n0):
    return [q["key"] for q in REQS[n0:]]


# --- nicknames are typed by hand: the lookup is exact -----------------------------------------------------------------
check("nick: a public username finds pub-<name>", sb.uid_by_nick("rita") == "pub-rita")
check("nick: case and a leading @ do not matter", sb.uid_by_nick("  @Rita ") == "pub-rita")
check("nick: an unknown name finds nobody", sb.uid_by_nick("nobody") is None)
check("nick: an empty or too long text finds nobody", sb.uid_by_nick("") is None and sb.uid_by_nick("x" * 80) is None)
check("nick: a first name shared by two tailnet people finds nobody", sb.uid_by_nick("anna") is None)
anna2.update_profile(name="Berta Jones")
sb._names.clear()
check("nick: ...and finds the person once it is unique", sb.uid_by_nick("anna") == "anna-example-com")
check("nick: the e-mail address is not a nickname", sb.uid_by_nick("anna@example.com") is None)

# --- without a donation the person has no engine ------------------------------------------------------------------------
check("before: no key and no donor -> engine not ready", not rita.engine_ok() and rita.engine_missing())
e = raises(sb.AuthError, run, rita, "r0")
check("before: a run fails with 'key not set' and sends nothing", e is not None and not REQS)

# --- a donation ---------------------------------------------------------------------------------------------------------
sb.donate_set(donor, rita.uid, 3)
check("donate: the recipient's engine is ready", rita.engine_ok() and rita.engine_missing() is None)
check("donate: the person who gave nothing is still not ready", not anna.engine_ok())
n0 = len(REQS)
r, w = run(rita, "r1")
check("donate: the work runs and finishes", (w / "hello.txt").exists() and r["model"] == "free/alpha:free")
check("donate: it ran on the DONOR's key", set(keys_since(n0)) == {DONOR_KEY}, keys_since(n0))
check("donate: the donor's own counter and the gift counter both moved", sb.or_state(donor).get("used") == 2
      and sb.donated_today(donor, rita.uid) == 2, (sb.or_state(donor), sb.donated_today(donor, rita.uid)))
check("donate: the recipient's own counters did not move", not sb.or_state(rita).get("used"))
check("status: the run says whose limit it used", sb.read_json(sb.STATUS, {}).get("cur_donor") == "dora", sb.read_json(sb.STATUS, {}))
ust = sb.usage_view(rita)
check("usage: the recipient sees from whom, how much is used and that the work runs on it",
      ust["donate"]["from"] == [{"name": "dora", "used": 2, "cap": 3, "left": 1, "paused": False}] and ust["donate"]["using"] is True, ust)
check("usage: no key of one's own -> the page shows only the gifts", ust.get("own") is False)
ugd = sb.usage_view(donor)
check("usage: the donor sees whom they give to and how much is used (names only)",
      ugd["donate"]["gives"] == [{"name": "rita", "used": 2, "cap": 3}] and "pub-" not in json.dumps(ugd), ugd)
check("usage: a person who neither gives nor receives has no donate block", "donate" not in sb.usage_view(anna))
wv = {"state": "busy", "cur_engine": "openrouter", "cur_model": "x/y", "current_uid": rita.uid, "cur_donor": "dora"}
check("working: only the person whose work it is is told whose limit it uses",
      sb.working_view(wv, rita).get("donor") == "dora" and "donor" not in sb.working_view(wv, anna) and "donor" not in sb.working_view(wv), sb.working_view(wv, rita))

n0 = len(REQS)
e = raises(sb.AuthError, run, rita, "r2")
check("cap: the 3rd request goes through, then the gift is used up and the run stops", len(REQS) - n0 == 1
      and sb.donated_today(donor, rita.uid) == 3 and e is not None, (len(REQS) - n0, sb.donated_today(donor, rita.uid)))
check("cap: no donor with room is left", sb.or_donors(rita, cached=False) == [] and not rita.engine_ok())

sb.donate_set(donor, rita.uid, 500)
check("cap: raising the cap brings the donor back", [d.uid for d in sb.or_donors(rita, cached=False)] == ["pub-dora"])

# --- own key first; the donor only when the own limit is used up -----------------------------------------------------------
rita.or_key_file.write_text(OWN_KEY)
n0 = len(REQS)
run(rita, "r3")
check("own key first: with a working own key the donor is not touched", set(keys_since(n0)) == {OWN_KEY}, keys_since(n0))
check("status: ...and then nobody's limit is named any more", sb.read_json(sb.STATUS, {}).get("cur_donor") is None)
check("usage: a working own key -> the work does not run on the gift", sb.usage_view(rita)["donate"]["using"] is False)
sb.or_cool_all(rita, time.time() + 3600, "daily limit")
before = sb.donated_today(donor, rita.uid)
n0 = len(REQS)
r, w = run(rita, "r4")
check("own limit used up: the donor's key takes over", set(keys_since(n0)) == {DONOR_KEY} and (w / "hello.txt").exists(), keys_since(n0))
check("own limit used up: the gift is counted", sb.donated_today(donor, rita.uid) == before + 2)

# --- only free models; the donor never pays ----------------------------------------------------------------------------
rita.update_profile(openrouter_model="fake/text-tools")
n0 = len(REQS)
run(rita, "r5")
check("paid model: it runs on the person's own key, never the donor's", set(keys_since(n0)) == {OWN_KEY}, keys_since(n0))
rita.update_profile(openrouter_model="free/beta:free")
n0 = len(REQS)
run(rita, "r6")
check("one chosen free model: the donor can pay for it", set(keys_since(n0)) == {DONOR_KEY} and {q["body"]["model"] for q in REQS[n0:]} == {"free/beta:free"})
rita.update_profile(openrouter_model=sb.OR_AUTO)

# --- the donor's own limit and trouble ----------------------------------------------------------------------------------
sb.or_cool_all(donor, time.time() + 3600, "own daily limit")
check("donor used up: not offered", sb.or_donors(rita, cached=False) == [])
e = raises(sb.TransientError, run, rita, "r7")
check("donor used up: nothing is sent, the run waits like any daily-limit pause", e is not None, e)
(donor.root / "or_state.json").unlink()

donor.or_key_file.write_text(BAD_KEY)
n0 = len(REQS)
e = raises(sb.TransientError, run, rita, "r8")     # then the own key (its free limit is used up) is all that is left
check("bad donor key: one refused request, then the donor is parked", len(REQS) - n0 == 1
      and (sb.or_state(donor).get("all_until") or 0) > time.time() and e is not None, (len(REQS) - n0, sb.or_state(donor)))
check("bad donor key: the recipient was not blocked for it", not rita.prof().get("blocked"))
check("bad donor key: not offered while parked", sb.or_donors(rita, cached=False) == [])
(donor.root / "or_state.json").unlink()
donor.or_key_file.write_text(DONOR_KEY)

# --- turbo mode: the own limit and the donor's limit work at the same time ---------------------------------------------------
(rita.root / "or_state.json").unlink(missing_ok=True)
rita.update_profile(turbo=True)
check("turbo: setup (own key, a donor with room, turbo on)", rita.turbo() and sb.or_donors(rita, cached=False))
s = [{"uid": None} for _ in range(4)]
pick = [sb.or_source(rita, sb.OR_AUTO, x) for x in s[:3]]
check("turbo: the first run takes the own key, the second the donor's, the third the own again",
      [(o.uid, d) for o, d in pick] == [("pub-rita", False), ("pub-dora", True), ("pub-rita", False)], [(o.uid, d) for o, d in pick])
check("turbo: a run keeps its key while it has room", [sb.or_source(rita, sb.OR_AUTO, s[i])[0].uid for i in range(3)] == ["pub-rita", "pub-dora", "pub-rita"])
check("turbo: the status names the donor whose key is in use", sb.or_busy_donors(rita) == ["dora"])
sb.or_busy_move(s[0], rita, None)
sb.or_busy_move(s[2], rita, None)
check("turbo: when runs end, the counts go back and the next run takes the own key", sb.or_source(rita, sb.OR_AUTO, s[3])[0].uid == "pub-rita"
      and sb.or_busy_donors(rita) == ["dora"])
for x in s:
    sb.or_busy_move(x, rita, None)
check("turbo: nothing is left counted", not sb._or_busy, sb._or_busy)
check("turbo: a paid model never goes to the donor", {sb.or_source(rita, "fake/text-tools", x)[0].uid for x in [{"uid": None} for _ in range(4)]} == {"pub-rita"})
sb._or_busy.clear()
rita.update_profile(turbo=False)
check("no turbo: the own key always, as before", {sb.or_source(rita, sb.OR_AUTO, x)[0].uid for x in [{"uid": None} for _ in range(4)]} == {"pub-rita"})
sb._or_busy.clear()
rita.update_profile(turbo=True)
real_throttle, got = sb.or_throttle, []
sb.or_throttle = lambda o: (time.sleep(0.25), real_throttle(o))
n0, d0 = len(REQS), sb.donated_today(donor, rita.uid)
sb.run_pool(["t1", "t2", "t3", "t4"], lambda name: got.append(run(rita, name)), 4, rita.turbo)
sb.or_throttle = real_throttle
ks = keys_since(n0)
check("turbo, four runs at once: all finish", len(got) == 4 and all((w / "hello.txt").exists() for _, w in got))
check("turbo, four runs at once: both keys worked, two runs each", ks.count(OWN_KEY) == 4 and ks.count(DONOR_KEY) == 4, (ks.count(OWN_KEY), ks.count(DONOR_KEY)))
check("turbo: the donated requests were counted", sb.donated_today(donor, rita.uid) - d0 == 4)
check("turbo: nothing is left counted after the runs", not sb._or_busy, sb._or_busy)
rita.update_profile(turbo=False)

# --- real limits: OpenRouter's own numbers (/key -> free_model_daily_requests) --------------------------------------------
LIVE = fake_openrouter.LIVE
(rita.root / "or_state.json").unlink(missing_ok=True)
LIVE[DONOR_KEY] = {"used": 51, "limit": 50, "remaining": 0}          # the donor never bought credits: 50 a day, all used
sb._or_live.clear()
sb.or_live(donor)                  # the page asks in the background; here it is asked at once
row = sb.donor_rows(rita, cached=False)[0]
check("real limit: a gift typed as 500 is shown against the donor's real 50, and nothing is left",
      (row["cap"], row["left"], row["used"]) == (50, 0, 50), row)
check("real limit: a donor whose key has nothing left is not used", sb.or_donors(rita, cached=False) == [])
check("real limit: the donor sees his own gift against the real limit", sb.donate_usage(donor)["gives"][0]["cap"] == 50, sb.donate_usage(donor))
check("real limit: the tier of the donor is taken from OpenRouter", donor.prof().get("or_daily_cap") == 50 and donor.prof().get("or_paid") is False, donor.prof())
check("real limit: the donor dialog knows the real limit", sb.donate_view(donor)["limit"] == 50)
LIVE[DONOR_KEY] = {"used": 51, "limit": 1000, "remaining": 949}      # the donor topped up: found without any action
sb._or_live.clear()
sb.or_live(donor)                  # the page asks in the background; here it is asked at once
row = sb.donor_rows(rita, cached=False)[0]
given = sb.donated_today(donor, rita.uid)
check("top-up: the cap is the typed 500 again, what is left is the cap minus what was given", (row["cap"], row["left"]) == (500, 500 - given), (row, given))
check("top-up: the donor is offered again, and his tier is now 1000", [d.uid for d in sb.or_donors(rita, cached=False)] == ["pub-dora"]
      and donor.prof().get("or_daily_cap") == 1000 and donor.prof().get("or_paid") is True)
LIVE[DONOR_KEY] = {"used": 700, "limit": 1000, "remaining": 300}     # the donor's own work used the limit: only 300 are left to give
sb._or_live.clear()
sb.or_live(donor)
check("shared limit: the gift cannot be more than what is really left on the donor's key", sb.donor_rows(rita, cached=False)[0]["left"] == min(300, 500 - given))
LIVE[DONOR_KEY] = {"used": 0, "limit": 1000, "remaining": 1000}
LIVE[OWN_KEY] = {"used": 1006, "limit": 1000, "remaining": 0}        # the person's own limit is gone (our own counter says less)
sb._or_live.clear()
sb.or_live(rita)
check("own limit: the page shows OpenRouter's numbers, not our count", sb.usage_view(rita)["used"] == 1006 and sb.usage_view(rita)["cap"] == 1000, sb.usage_view(rita))
n0 = len(REQS)
rita.update_profile(openrouter_model=sb.OR_AUTO)
run(rita, "r10")
check("own limit: OpenRouter says it is used up -> the donor takes over without a refused request first", set(keys_since(n0)) == {DONOR_KEY}, keys_since(n0))
sb.or_cool_all(rita, time.time() + 3600, "daily limit of free requests")
LIVE[OWN_KEY] = {"used": 10, "limit": 1000, "remaining": 990}
sb._or_live.clear()
sb.or_live(rita)
check("own top-up: a pause for the daily limit is lifted when OpenRouter shows room", not (sb.or_state(rita).get("all_until") or 0) > time.time(), sb.or_state(rita))
LIVE.pop(OWN_KEY)
sb._or_live.clear()
check("no live numbers (an older OpenRouter): our own counter is used", isinstance(sb.usage_view(rita).get("used"), int), sb.usage_view(rita))
LIVE.clear()
sb._or_live.clear()
(rita.root / "or_state.json").unlink(missing_ok=True)
(donor.root / "or_state.json").unlink(missing_ok=True)

# --- what the pages may see ---------------------------------------------------------------------------------------------
v = sb.donate_view(donor)
check("view: the donor sees the name, cap and use of the recipient, with an opaque id",
      v["to"] and v["to"][0]["name"] == "rita" and v["to"][0]["cap"] == 500 and "id" in v["to"][0] and "pub-" not in json.dumps(v["to"]), v)
vr = sb.donate_view(rita)
check("view: the recipient sees only the donor's NAME and what is left", vr["from"] and vr["from"][0]["name"] == "dora"
      and set(vr["from"][0]) == {"name", "left"}, vr)
ev = json.dumps(sb.engine_view(rita, rita.prof()), ensure_ascii=False)
check("view: no key and no uid in the recipient's engine view", DONOR_KEY not in ev and "pub-dora" not in ev and DONOR_KEY[-4:] not in ev, ev[:300])
check("view: no key in the donor's files that are shown", DONOR_KEY not in donor.profile.read_text(encoding="utf-8"))

# --- taking it back ---------------------------------------------------------------------------------------------------
did = v["to"][0]["id"]
check("take back: a wrong id removes nothing", sb.donate_remove(donor, "nope") is False and donor.prof().get("or_donate"))
check("take back: the right id removes it", sb.donate_remove(donor, did) is True and not donor.prof().get("or_donate"))
rita.or_key_file.unlink()
check("take back: the recipient is without an engine again", not rita.engine_ok())
n0 = len(REQS)
e = raises(sb.AuthError, run, rita, "r9")
check("take back: and a run fails without sending anything", e is not None and len(REQS) == n0)

print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
