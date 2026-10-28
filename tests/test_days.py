"""Offline test of class-day chapters: the date a class was HELD decides the chapter, not the topic or the upload day.
Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_days.py"""
import asyncio
import os
import datetime as dt
import io
import re
import sys

os.environ.setdefault("SB_DIGEST_QUIET", "0")    # the chapter summaries are made in the same run here
import sb
import web
from PIL import Image
from starlette.requests import Request

fails = []
web.kick = lambda: None          # never wake a real worker from a test


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


TODAY = sb.now().date()
D1 = (TODAY - dt.timedelta(days=7)).isoformat()      # an earlier class
D2 = (TODAY - dt.timedelta(days=3)).isoformat()      # a later class
FUTURE = (TODAY + dt.timedelta(days=9)).isoformat()


def req(login="day@example.com", lang="uk"):
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": [(b"tailscale-user-login", login.encode()), (b"x-lang", lang.encode())]})


class Up:
    def __init__(self, name, data):
        self.filename, self._b = name, io.BytesIO(data)

    async def read(self, n):
        return self._b.read(n)


_shade = [0]


def png():
    """A different picture every time: the same file twice would be refused as a duplicate."""
    _shade[0] += 1
    b = io.BytesIO()
    Image.new("RGB", (400, 300), (255, 255 - _shade[0], 255)).save(b, "PNG")
    return b.getvalue()


def upload(date=None, kind="notes", subject="math1", note="", files=None, lang="uk"):
    kw = dict(files=files or [Up("p1.png", png())], note=note, subject=subject, kind=kind, topic="", topic_title="", code="")
    if date is not None:
        kw["date"] = date
    return asyncio.run(web.api_upload(req(lang=lang), **({"date": ""} | kw)))


def upload_err(**kw):
    try:
        upload(**kw)
    except web.HTTPException as e:
        return e.status_code, e.detail
    return None, None


# --- the date itself -----------------------------------------------------------------------------------------------
check("a real date is accepted", sb.class_date("2026-10-14") == "2026-10-14")
check("not a date -> None", sb.class_date("14.10.2026") is None and sb.class_date("2026-02-30") is None and sb.class_date(None) is None)
check("check_class_date accepts a past day", sb.check_class_date(D1) == D1)
for bad in ("", "yesterday", "2026-13-01", FUTURE, "2019-01-01"):
    try:
        sb.check_class_date(bad)
        check(f"rejects {bad!r}", False)
    except ValueError:
        check(f"rejects {bad!r}", True)
sb.set_lang("en")
try:
    sb.check_class_date(FUTURE)
except ValueError as e:
    check("English error for a future date", "future" in str(e))
sb.set_lang("uk")
try:
    sb.check_class_date(FUTURE)
except ValueError as e:
    check("Ukrainian error for a future date", "майбутньому" in str(e))
check("long and short day names", sb.day_title("2026-10-14") == "Wednesday, 14 October 2026" and sb.day_short("2026-10-04") == "04.10.2026")

# --- the upload endpoint -------------------------------------------------------------------------------------------
check("a future date is refused by the API", upload_err(date=FUTURE)[0] == 400)
check("garbage is refused by the API", upload_err(date="soon")[0] == 400)
upload(date=D2, note="later class")                                  # uploaded FIRST, held LATER
upload(date=D1, note="earlier class")                                # uploaded second, held earlier
upload(date=D1, note="supplement to the earlier class")               # a supplement for D1, uploaded last
upload(date=None)                                                     # no date -> today
ref = upload(kind="reference", subject="math1", files=[Up("book.pdf", b"%PDF-1.4 x")])
sp = web.who(req())
subs = {p.name: sb.read_json(p / "submission.json") for p in sp.d["inbox"].iterdir() if (p / "submission.json").exists()}
dates = sorted(s["date"] for s in subs.values() if s["kind"] == "notes")
check("the date is stored with the upload", dates == sorted([D1, D1, D2, TODAY.isoformat()]), str(dates))
check("a textbook upload has no class date", [s["date"] for s in subs.values() if s["kind"] == "reference"] == [None])
import shutil
for p_, s_ in subs.items():
    if s_["kind"] == "reference":
        shutil.rmtree(sp.d["inbox"] / p_)      # a fake PDF would only make the worker fail on it
w = sb.worker()
check("worker processed all four class uploads", w.get("processed") == 4 and not w.get("blocked"), str(w))

ents = sb.entries_of(sp, "math1")
check("4 entries (the textbook is not one)", len(ents) == 4, str(len(ents)))
check("entries are ordered by class date, then by upload", [m["date"] for m in ents] == sorted([D1, D1, D2, TODAY.isoformat()]),
      str([m["date"] for m in ents]))
check("the class date is in meta.json", all(sb.read_json(m["dir"] / "meta.json")["date"] == m["date"] for m in ents))
e_d1 = [m for m in ents if m["date"] == D1]
check("the supplement comes after the first upload of that day", e_d1[0]["note"] == "earlier class" and e_d1[1]["note"].startswith("supplement"))

s, parts = sb.book_parts(sp, "math1")
days = [p[0] for p in parts]
check("chapters are class days, oldest first", days == sorted({D1, D2, TODAY.isoformat()}), str(days))
check("the supplement is in the SAME chapter as its class", [len(p[2]) for p in parts if p[0] == D1] == [2])
check("chapter titles are the dates", parts[0][1] == sb.day_title(D1))

# --- digests per class day --------------------------------------------------------------------------------------------
for day in days:
    dg = sb.digest_dir(sp, "math1", day)
    check(f"digest for {day} lives under that day", (dg / "summary.md").exists() and (dg / "glossary.json").exists(), str(dg))
check("no digest is left over per topic", not any((sp.d["library"] / "math1" / "complex-numbers" / "_digest").glob("*")))
check("digests are all fresh", not sb.stale_digests(sp))

# --- the books -------------------------------------------------------------------------------------------------------
clean = (sp.d["build"] / "math1" / "clean.typ").read_text(encoding="utf-8")
orig = (sp.d["build"] / "math1" / "original.typ").read_text(encoding="utf-8")
h1 = re.findall(r'#heading\(level: 1, "([^"]+)"\)', clean)
check("clean book: level-1 headings are the dates in order", h1[:3] == [sb.day_title(d) for d in days] , str(h1))
check("the chapters carry date labels for the glossary links", f"<ch-{D1}>" in clean and f"(Ch." in clean)
check("original book has the same chapters", [x for x in re.findall(r'#heading\(level: 1, "([^"]+)"\)', orig)] == [sb.day_title(d) for d in days])
check("a topic name is not a chapter heading", "#heading(level: 1, \"Complex numbers\")" not in clean)
check("entries uploaded after their class say so", clean.count("added later") == 3 and orig.count("added later") == 3, str(clean.count("added later")))
check("both PDFs were built", (sp.d["books"] / "math1" / "clean.pdf").stat().st_size > 1000 and (sp.d["books"] / "math1" / "original.pdf").stat().st_size > 1000)

# --- status for the page ---------------------------------------------------------------------------------------------
st = sb.status(sp)
sj = next(x for x in st["subjects"] if x["id"] == "math1")
check("status lists the class days", [d["date"] for d in sj["days"]] == days, str([d["date"] for d in sj["days"]]))
check("status: entries under a day carry what the date editor needs", set(sj["days"][0]["entries"][0]) >= {"topic", "entry", "title"})
check("suggestion targets are grouped by class date", {t["group"] for t in sj["targets"] if t["type"] == "entry"} == {sb.day_short(d) for d in days})

# --- moving an entry to another day (a wrong date, or a supplement for an earlier class) ----------------------------
m = next(x for x in sb.entries_of(sp, "math1") if x["date"] == D2)
sb.set_lang("uk")
check("moving to a valid day works", sb.set_entry_date(sp, "math1", m["topic"], m["dir"].name, D1) == D1)
check("the move is stored", sb.read_json(m["dir"] / "meta.json")["date"] == D1)
_, parts2 = sb.book_parts(sp, "math1")
check("after the move that chapter has 3 entries and D2 is gone", [len(p[2]) for p in parts2 if p[0] == D1] == [3] and D2 not in [p[0] for p in parts2])
check("the subject is marked for a rebuild", "math1" in (sb.read_json(sp.dirty, []) or []))
check("the digest of the new chapter is stale, so it is regenerated", any(x[1] == D1 for x in sb.stale_digests(sp)))
for bad in (FUTURE, "x"):
    try:
        sb.set_entry_date(sp, "math1", m["topic"], m["dir"].name, bad)
        check(f"move to {bad!r} refused", False)
    except ValueError:
        check(f"move to {bad!r} refused", True)
for topic, entry in (("..", m["dir"].name), (m["topic"], "../x"), (m["topic"], "nope")):
    try:
        sb.set_entry_date(sp, "math1", topic, entry, D1)
        check(f"bad target {topic}/{entry} refused", False)
    except (ValueError, LookupError):
        check(f"bad target {topic}/{entry} refused", True)

# --- the HTTP endpoint ------------------------------------------------------------------------------------------------------
import json as _json


def jreq(body, lang="uk"):
    data = _json.dumps(body).encode()

    async def receive():
        return {"type": "http.request", "body": data, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": [(b"tailscale-user-login", b"day@example.com"), (b"x-lang", lang.encode())]}, receive)


def api_date(**body):
    try:
        return asyncio.run(web.api_entry_date(jreq(body))), None
    except web.HTTPException as e:
        return None, e.status_code


m2 = sb.entries_of(sp, "math1")[-1]
ok_, _ = api_date(subject="math1", topic=m2["topic"], entry=m2["dir"].name, date=D2)
check("POST /api/entry/date moves an entry", ok_ == {"ok": True, "date": D2} and sb.read_json(m2["dir"] / "meta.json")["date"] == D2, str(ok_))
check("the endpoint rejects a future date with 400", api_date(subject="math1", topic=m2["topic"], entry=m2["dir"].name, date=FUTURE)[1] == 400)
check("the endpoint answers 404 for an unknown subject / entry",
      api_date(subject="nope", topic="a", entry="b", date=D1)[1] == 404 and api_date(subject="math1", topic=m2["topic"], entry="zzz", date=D1)[1] == 404)

# --- entries from before dates existed fall back to the day they were uploaded ------------------------------------------
old = sp.d["library"] / "math1" / "numbers" / "2026-09-01_1000_legacy"
old.mkdir(parents=True)
sb.write_json(old / "meta.json", {"title": "Legacy", "title_original": "Legacy", "lang_original": "en", "kind": "lecture",
                                  "subject": "math1", "topic": "numbers", "topic_title": "Numbers",
                                  "added": "2026-09-01T10:00:00+02:00", "order": 1, "note": "", "pages": [], "figures": []})
(old / "clean.md").write_text("# Legacy\n\nText.\n", encoding="utf-8")
(old / "original.md").write_text("# Legacy\n", encoding="utf-8")
leg = next(x for x in sb.entries_of(sp, "math1") if x["title"] == "Legacy")
check("a legacy entry (no date) is dated by its upload day and sorts first", leg["date"] == "2026-09-01" and sb.entries_of(sp, "math1")[0]["title"] == "Legacy")

# --- explanations follow their topic to its latest chapter ------------------------------------------------------------------
x = sp.d["library"] / "math1" / "complex-numbers" / "_explain" / "2026-10-20_1200_why"
x.mkdir(parents=True)
(x / "explanation.md").write_text("# Why\n\nBecause.\n", encoding="utf-8")
sb.write_json(x / "meta.json", {"title": "Why", "topic": "complex-numbers", "request": "why?", "added": "2026-10-20T12:00:00+02:00"})
_, parts3 = sb.book_parts(sp, "math1")
last_cn = max(m["date"] for m in sb.entries_of(sp, "math1") if m["topic"] == "complex-numbers")
check("an explanation goes to the latest chapter of its topic", [p[0] for p in parts3 if p[3]] == [last_cn], str([p[0] for p in parts3 if p[3]]))

# --- the prompt knows the class date and what the day already holds ------------------------------------------------------
sub = {"date": D1, "subject": "math1", "uploaded_at": sb.now_iso()}
brief = sb.same_day_brief(sp, sub)
check("the ingest prompt sees what the day already has", "complex-numbers" in brief and "Lecture 2" in brief, brief)
check("an empty day says so", sb.same_day_brief(sp, {"date": "2020-01-01", "subject": "math1", "uploaded_at": sb.now_iso()}) == "(nothing yet)")
p = sb.render_prompt("ingest.md", pages="- page 1", part="x", note="n", date=sb.day_title(D1), same_day=brief, subject_hint="math1",
                     syllabus="s", uploads="", texts="", existing=sb.lecture_candidates(sp, sub))
check("the ingest prompt has no unfilled placeholders", "{{" not in p.replace("{{fig:f1}}", ""), str(re.findall(r"\{\{\w+\}\}", p)))

print("\n" + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
