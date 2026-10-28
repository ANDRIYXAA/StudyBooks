"""Offline test: everybody's uploads go into the shared (main) book; the same lecture from two people is recognised
(`same_as` from the transcription pass) and merged into one entry; who uploaded what is shown; a person may change the
date of what they uploaded, the owner of everything; the same file from two people is processed once; the queue shows
what exactly is being processed; every model call gets the skill.

Run from a COPY of the code, with a fresh data dir:
  SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_shared_upload.py   (never on the real data)"""
import asyncio
import datetime as dt
import io
import json
import os
import sys
import tempfile
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
sb.learn_note(auto=False)          # no learning session inside this test
fails = []
OWNER, FRIEND, OTHER = "owner@example.com", "friend@example.com", "other@example.com"
TODAY = sb.now().date()
D1 = (TODAY - dt.timedelta(days=6)).isoformat()
D2 = (TODAY - dt.timedelta(days=2)).isoformat()


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def req(login, lang="en", body=None):
    raw = json.dumps(body).encode() if body is not None else b""

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": [(b"tailscale-user-login", login.encode()), (b"x-lang", lang.encode()),
                                (b"content-type", b"application/json")]}, receive)


class Up:
    def __init__(self, name, data):
        self.filename, self._b = name, io.BytesIO(data)

    async def read(self, n):
        return self._b.read(n)


_shade = [0]


def png():
    _shade[0] += 1
    b = io.BytesIO()
    Image.new("RGB", (400, 300), (255, 255 - _shade[0], 200)).save(b, "PNG")
    return b.getvalue()


def upload(login, date, files=None, note=""):
    return asyncio.run(web.api_upload(req(login), files=files or [Up("p1.png", png())], note=note, subject="math1",
                                      kind="notes", topic="", topic_title="", code="", date=date))


def call(fn, login, body):
    try:
        return 200, asyncio.run(fn(req(login, body=body)))
    except web.HTTPException as e:
        return e.status_code, e.detail


main = web.who(req(OWNER))
main.update_profile(name="Alex Owner")
friend = web.who(req(FRIEND))
other = web.who(req(OTHER))
check("the main book is the owner's library", sb.main_space().uid == main.uid)

# --- 1. the owner's lecture -------------------------------------------------------------------------------------------
upload(OWNER, D1, note="lecture 2")
sb.worker()
ents = sb.entries_of(main, "math1")
check("the owner's upload is one entry of the main book", len(ents) == 1, len(ents))
first = ents[0]
check("... marked as the owner's", sb.entry_by(first, main.uid) == main.uid)
target = f"math1/{first['topic']}/{first['dir'].name}"
old_clean = (first["dir"] / "clean.md").read_text(encoding="utf-8")
check("the transcription prompt lists that entry as a possible same lecture", target in sb.lecture_candidates(main, {"subject": "math1", "date": D2, "uploaded_at": sb.now_iso()}))

calls = [json.loads(l) for l in ARGS.read_text().splitlines()]
briefs = [a[a.index("--append-system-prompt") + 1] for a in calls if "--append-system-prompt" in a]
check("every Claude call gets the skill in its system prompt", len(briefs) == len(calls) and all("studybooks-material" in b for b in briefs),
      f"{len(briefs)}/{len(calls)}")
check("... the transcription pass gets its own section", any("For this task (transcribe)" in b and "Indices and exponents" in b for b in briefs))
check("... the clean pass gets its own section", any("For this task (clean)" in b and "Correct silently" in b for b in briefs))

# --- 2. a friend uploads the SAME lecture: merged into that entry -----------------------------------------------------
os.environ["FAKE_SAME_AS"] = target
ARGS.write_text("")
r = upload(FRIEND, D2, files=[Up("board.png", png())])
sb.worker()
os.environ.pop("FAKE_SAME_AS")
ents = sb.entries_of(main, "math1")
check("the same lecture is merged: still one entry", len(ents) == 1, [e["title"] for e in ents])
m = ents[0]
new_clean = (m["dir"] / "clean.md").read_text(encoding="utf-8")
check("the merged text keeps the old text", new_clean.startswith(old_clean.rstrip()[:200]))
check("... and adds what the second upload brought", "The second upload adds" in new_clean)
check("the original book gets the second transcription as a supplement", "Supplement, added" in (m["dir"] / "original.md").read_text(encoding="utf-8"))
check("the friend's page photo is added to the entry", len([p for p in m["pages"] if p.get("by") == friend.uid]) == 1)
check("the friend's figure is added under a new id", any(f["id"].startswith("x") and f["id"].endswith("f1") for f in m["figures"]), m["figures"])
check("the merged figure placeholder points at the renamed figure", any("{{fig:" + f["id"] + "}}" in new_clean for f in m["figures"] if f["id"] != "f1"))
check("contributors name the friend", [c["by"] for c in m.get("contributors") or []] == [friend.uid])
check("the entry keeps its class date (the lecture's, not the friend's)", m["date"] == D1)
check("nothing lands in the friend's own library", not list((friend.d["library"]).glob("*/*/*/meta.json")))
sub = sb.read_json(friend.d["processed"] / r["id"] / "submission.json")
check("the friend's processed upload says it was merged into the main book", sub.get("fused") and sub.get("library") == main.uid, sub)
calls = [json.loads(l) for l in ARGS.read_text().splitlines()]
check("the merge ran as its own pass with the fuse section of the skill",
      any("--append-system-prompt" in a and "For this task (fuse)" in a[a.index("--append-system-prompt") + 1] for a in calls))
check("the merge pass runs on the cheap model", any("For this task (fuse)" in (a[a.index("--append-system-prompt") + 1] if "--append-system-prompt" in a else "")
                                                   and a[a.index("--model") + 1] == "sonnet" for a in calls))

# --- 3. a merge that throws the old text away is refused: filed next to the lecture, on its date -----------------------
os.environ["FAKE_SAME_AS"] = target
os.environ["FAKE_FUSE"] = "short"
upload(OTHER, D2, files=[Up("notes.png", png())])
sb.worker()
os.environ.pop("FAKE_FUSE")
ents = sb.entries_of(main, "math1")
check("a refused merge keeps the material as its own entry", len(ents) == 2, len(ents))
sep = next(e for e in ents if e.get("by") == other.uid)
check("... under the recognised lecture's date", sep["date"] == D1 and sep.get("date_given") == D2, (sep["date"], sep.get("date_given")))
check("... and the lecture's text is untouched", (m["dir"] / "clean.md").read_text(encoding="utf-8") == new_clean)

# --- 4. same_as pointing at nothing real is ignored --------------------------------------------------------------------
os.environ["FAKE_SAME_AS"] = "math1/no-such-topic/2026-01-01_0000_x"
upload(FRIEND, D2, files=[Up("other.png", png())])
sb.worker()
os.environ.pop("FAKE_SAME_AS")
ents = sb.entries_of(main, "math1")
own = [e for e in ents if e.get("by") == friend.uid]
check("an unknown same_as -> an ordinary new entry on the given date", len(ents) == 3 and own and own[0]["date"] == D2,
      [(e.get("by"), e["date"]) for e in ents])

# --- 5. who uploaded what, and who may change a date -------------------------------------------------------------------
st_owner = web.shared_view(main, sb.status(main))
day1 = next(d for s in st_owner["subjects"] if s["id"] == "math1" for d in s["days"] if d["date"] == D1)
lec = next(e for e in day1["entries"] if e["entry"] == m["dir"].name)
check("the owner's view names who added an entry", lec["by_name"] == "Alex", lec)
check("... and who supplemented it", lec["with"] == ["friend"], lec)
check("the owner may move every entry; no raw ids leave the server", all(e["mine"] and "by" not in e for d in
      next(s for s in st_owner["subjects"] if s["id"] == "math1")["days"] for e in d["entries"]))
st_friend = web.shared_view(friend, sb.status(friend))
fd = {e["entry"]: e for s in st_friend["subjects"] if s["id"] == "math1" for d in s["days"] for e in d["entries"]}
check("the friend may move only what the friend uploaded", fd[own[0]["dir"].name]["mine"] and not fd[m["dir"].name]["mine"])
check("... and sees names, not ids", all("by" not in e for e in fd.values()) and fd[own[0]["dir"].name]["by_name"] == "friend")

body = {"subject": "math1", "topic": own[0]["topic"], "entry": own[0]["dir"].name, "date": D1}
check("the friend changes the date of their own entry", call(web.api_entry_date, FRIEND, body)[0] == 200)
check("... it really moved", sb.read_json(own[0]["dir"] / "meta.json")["date"] == D1)
code, _ = call(web.api_entry_date, FRIEND, {"subject": "math1", "topic": m["topic"], "entry": m["dir"].name, "date": D2})
check("the friend cannot move the owner's entry (403)", code == 403, code)
code, _ = call(web.api_entry_date, OWNER, {"subject": "math1", "topic": sep["topic"], "entry": sep["dir"].name, "date": D2})
check("the owner moves somebody else's entry", code == 200 and sb.read_json(sep["dir"] / "meta.json")["date"] == D2, code)
code, _ = call(web.api_entry_date, FRIEND, {"subject": "math1", "topic": "../x", "entry": "y", "date": D2})
check("a bad path is refused", code == 404, code)

an = web.anon_status(req(FRIEND))
check("the page for strangers drops who uploaded what", not any(set(e) & {"by", "by_name", "with", "mine"}
                                                                 for s in an["subjects"] for d in s["days"] for e in d["entries"]))

# --- 6. the same file from two people is processed once ---------------------------------------------------------------
same = png()
upload(OWNER, D2, files=[Up("slides.png", same)])
try:
    upload(FRIEND, D2, files=[Up("my-copy.png", same)])
    check("the friend's copy of the owner's file is refused", False)
except web.HTTPException as e:
    check("the friend's copy of the owner's file is refused, naming who has it", e.status_code == 409 and "Alex" in e.detail, e.detail)
for d in main.d["inbox"].iterdir():          # not processed in this test
    sb.shutil.rmtree(d)

# --- 7. the queue shows what exactly is being processed ----------------------------------------------------------------
r = upload(FRIEND, D2, files=[Up("a.png", png()), Up("b.png", png())])
sb.write_json(sb.STATUS, {"state": "busy", "current_uid": friend.uid, "pid": os.getpid(), "current_since": sb.now_iso(),
                          "cur_item": r["id"], "cur_members": [r["id"]], "cur_chunk": 1, "cur_chunks": 1, "cur_pages": "1–2",
                          "cur_files": ["a.png", "b.png"], "cur_pass": "clean", "cur_pass_since": sb.now_iso(),
                          "cur_model": "opus", "cur_turn": None})
q = next(x for x in sb.status(friend)["queue"] if x["id"] == r["id"])
p = q.get("progress") or {}
check("the queue item says which chunk, pages, files and pass", p.get("chunk") == 1 and p.get("chunks") == 1 and p.get("pages") == "1–2"
      and p.get("files") == ["a.png", "b.png"] and p.get("pass") == "clean" and p.get("model") == "opus", p)
check("the queue item lists the original file names", q["files"] == ["a.png", "b.png"], q["files"])
q_other = [x for x in sb.status(main)["queue"] if x.get("progress")]
check("nobody else's queue shows that progress", not q_other)
sb.write_json(sb.STATUS, {"state": "idle"})

print("\nALL OK" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
