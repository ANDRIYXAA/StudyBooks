"""Offline test of suggestions + the admin merge queue + the abuse limits.
Run from a COPY of the code with SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py (never against the real data)."""
import json
import os
import shutil
import sys
from pathlib import Path

import sb

ADMIN, FRIEND = "owner-example-com", "friend"
sb.SUG_MIN_GAP = 0                    # the gap is tested separately
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def seed(sp, who):
    d = sp.d["library"] / "math1" / "complex-numbers" / "2026-10-16_2145_complex-numbers"
    d.mkdir(parents=True)
    sb.write_json(d / "meta.json", {"title": "Complex numbers", "title_original": "Komplexe Zahlen", "lang_original": "de",
                                    "kind": "lecture", "subject": "math1", "topic": "complex-numbers",
                                    "topic_title": "Complex numbers", "added": "2026-10-16T21:45:00+02:00", "order": 1,
                                    "note": "", "pages": [], "figures": []})
    (d / "clean.md").write_text(f"# Complex numbers ({who})\n\nLet $z = a + bi$.\n\n$$|z|^2 = a^2 + b^2.$$\n", encoding="utf-8")
    (d / "original.md").write_text("# Komplexe Zahlen\n", encoding="utf-8")
    i = sp.d["interactive"] / "math1"
    i.mkdir(parents=True)
    (i / "plane.html").write_text("<!DOCTYPE html><html><body><p>Plane explorer</p></body></html>", encoding="utf-8")
    sb.write_json(i / "plane.json", {"title": "Plane explorer", "topic": "complex-numbers", "topic_title": "Complex numbers",
                                     "added": "2026-10-16T22:00:00+02:00"})
    return d.name


admin = sb.Space(ADMIN).ensure(login="admin@example.com", name="Admin")
friend = sb.Space(FRIEND).ensure(login="friend@example.com", name="Friend")
ename_a, ename_f = seed(admin, "main"), seed(friend, "friend")
E = lambda name: {"type": "entry", "subject": "math1", "topic": "complex-numbers", "entry": name}
I = {"type": "interactive", "subject": "math1", "stem": "plane"}
TEXT = "Please add that |z| = 0 holds if and only if z = 0, with a short reason."


def fclean():
    return (friend.d["library"] / "math1" / "complex-numbers" / ename_f / "clean.md").read_text(encoding="utf-8")


def aclean():
    return (admin.d["library"] / "math1" / "complex-numbers" / ename_a / "clean.md").read_text(encoding="utf-8")


def sug(sp, **kw):
    try:
        return sb.create_suggestion(sp, kw), None
    except sb.SuggestError as e:
        return None, e.code


def last(sp):
    return sb.suggestions_of(sp)[-1]


# --- input validation --------------------------------------------------------------------------------------
check("too short text is refused", sug(friend, target=E(ename_f), text="short")[1] == 400)
check("spam text is refused", sug(friend, target=E(ename_f), text="a" * 100)[1] == 400)
check("too long text is refused", sug(friend, target=E(ename_f), text="word " * 400)[1] == 400)
check("path traversal in entry is refused", sug(friend, target=E("../../../etc"), text=TEXT)[1] in (400, 404))
check("unknown subject is refused", sug(friend, target={"type": "entry", "subject": "nope", "topic": "a", "entry": "b"}, text=TEXT)[1] == 404)
check("asis/missing page is refused", sug(friend, target=E("2000-01-01_0000_x"), text=TEXT)[1] == 404)

# --- the happy path: friend suggests, Claude accepts, it goes to the admin, the admin merges -----------------
before = fclean()
sid, err = sug(friend, target=E(ename_f), text=TEXT, share=True)
check("suggestion created", sid and not err, err)
check("duplicate open suggestion for the same page is refused", sug(friend, target=E(ename_f), text=TEXT + " again")[1] == 409)
print(json.dumps(sb.worker(), ensure_ascii=False))
r = last(friend)
check("accepted by Claude", r["status"] == "accepted", r)
check("friend's page changed", fclean() != before and "Note:" in fclean())
check("friend's undo data stored", r.get("before_sha") and r.get("after_sha"))
cs = sb.contributions()
check("one contribution queued for the admin", len(cs) == 1 and cs[0]["from_uid"] == FRIEND, cs)
c = cs[0]
check("analysis proposed a merge", c["status"] == "proposed", c)
check("main book NOT changed before the admin acts", "Merged note" not in aclean())
ov = sb.merge_overview(admin)
check("admin overview has a diff", ov["pending"] == 1 and any("+" in ln for ln in ov["items"][0]["diff"]), ov["pending"])
check("friend sees the contribution state", sb.share_status(friend, last(friend)) == "на розгляді в адміна")

res = sb.merge_action(admin, c["id"], "merge")
check("admin merges", res.get("ok") and "Merged note" in aclean(), res)
check("friend sees it merged", sb.share_status(friend, last(friend)) == "злито в основну книгу")
check("book rebuild requested", "math1" in (sb.read_json(admin.dirty, []) or []))
res = sb.merge_action(admin, c["id"], "undo")
check("admin undoes the merge", res.get("ok") and "Merged note" not in aclean(), res)
check("merge twice is refused", not sb.merge_action(admin, c["id"], "merge").get("ok"))

# --- friend undoes his own change ----------------------------------------------------------------------------
res = sb.undo_suggestion(friend, r["id"])
check("friend undoes own change", res.get("ok") and fclean() == before, res)

# --- abuse: hostile drafts are stopped by the code checks, whatever Claude says ---------------------------------
os.environ["FAKE_SUG"] = "evil"
for name, tg in (("markdown link+html", E(ename_f)), ("html script/fetch", I)):
    sid, err = sug(friend, target=tg, text=TEXT + f" ({name})", share=True)
    sb.worker()
    r = last(friend)
    check(f"evil draft blocked: {name}", r["status"] == "rejected" and r.get("blocked_by_checks"), r)
check("friend's page untouched after evil drafts", fclean() == before)
check("interactive page untouched", "fetch" not in (friend.d["interactive"] / "math1" / "plane.html").read_text())
check("no contribution created from blocked drafts", len(sb.contributions()) == 1)

os.environ["FAKE_SUG"] = "big"
sug(friend, target=E(ename_f), text=TEXT + " (big)")
sb.worker()
check("oversized draft blocked", last(friend)["status"] == "rejected" and last(friend).get("blocked_by_checks"))

# --- abuse: rate limits and cool-down -------------------------------------------------------------------------
os.environ["FAKE_SUG"] = "reject"
n_before = len(sb.suggestions_of(friend))
for i in range(6):
    sug(friend, target=E(ename_f), text=TEXT + f" variant number {i}")
    sb.worker()
lim = sb.suggest_limits(friend)
print("limits:", lim)
check("per-day cap or cool-down kicks in", lim["wait"] > 0, lim)
sid, err = sug(friend, target=E(ename_f), text=TEXT + " one more different sentence here")
check("refused with 429 while limited", err == 429, err)
check("failed ones do not count, pending cap holds", sb.suggest_limits(friend)["open"] == 0)

# --- admin-only switches ---------------------------------------------------------------------------------------
os.environ["FAKE_SUG"] = "accept"
other = sb.Space("other").ensure(login="o@example.com", name="Other")
seed(other, "other")
oname = sorted((other.d["library"] / "math1" / "complex-numbers").iterdir())[0].name
st = sb.merge_state()
st["paused"] = True
sb.save_merge_state(st)
sid, err = sug(other, target=E(oname), text=TEXT + " for paused intake", share=True)
sb.worker()
s = last(other)
check("paused intake: suggestion applies locally but is not queued", s["status"] == "accepted" and not s.get("contribution")
      and "не приймає" in (s.get("share_note") or ""), s)
st["paused"] = False
st["blocked"] = ["other"]
sb.save_merge_state(st)
sug(other, target=I, text=TEXT + " blocked person interactive", share=True)
sb.worker()
s = last(other)
check("blocked person is not queued", not s.get("contribution") and "не приймає" in (s.get("share_note") or ""), s)

# --- the admin's own suggestion goes straight to the main book, no queue ---------------------------------------
n = len(sb.contributions())
sug(admin, target=E(ename_a), text=TEXT + " admin edit", share=True)
sb.worker()
check("admin's own suggestion applies to the main book without a queue", "Note:" in aclean() and len(sb.contributions()) == n)

# --- merge analysis verdicts ------------------------------------------------------------------------------------
st["blocked"] = []
sb.save_merge_state(st)
os.environ["FAKE_MRG"] = "duplicate"
third = sb.Space("third").ensure(login="t@example.com", name="Third")
seed(third, "third")
tname = sorted((third.d["library"] / "math1" / "complex-numbers").iterdir())[0].name
sug(third, target=E(tname), text=TEXT + " duplicate scenario", share=True)
sb.worker()
c3 = [c for c in sb.contributions() if c["from_uid"] == "third"][0]
check("duplicate verdict closes the contribution", c3["status"] == "skipped" and c3["verdict"] == "duplicate", c3)
check("merge of a skipped contribution is refused", not sb.merge_action(admin, c3["id"], "merge").get("ok"))
check("re-analysis is possible", sb.merge_action(admin, c3["id"], "reanalyze").get("ok"))
os.environ["FAKE_MRG"] = "merge"
sb.worker()
c3 = sb.read_json(sb.MERGE / c3["id"] / "c.json")
check("re-analysis proposes a merge", c3["status"] == "proposed", c3)
page = admin.root / c3["match_path"]
page.write_text(page.read_text(encoding="utf-8") + "\nEdited meanwhile.\n", encoding="utf-8")
res = sb.merge_action(admin, c3["id"], "merge")
check("page changed after the analysis: merge refused", not res.get("ok") and "змінилась" in res.get("error", ""), res)
check("decline works", sb.merge_action(admin, c3["id"], "decline", "not now").get("ok")
      and sb.read_json(sb.MERGE / c3["id"] / "c.json")["status"] == "declined")
check("contributor sees the admin declined", sb.share_status(third, last(third)) == "адмін відхилив")

for sp in (admin, friend, other, third):
    errs = (sb.read_json(sp.profile, {}) or {}).get("build_errors")
    check(f"books of {sp.uid} built without errors", not errs, errs)
    check(f"clean.pdf of {sp.uid} exists", (sp.d["books"] / "math1" / "clean.pdf").exists())

print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
