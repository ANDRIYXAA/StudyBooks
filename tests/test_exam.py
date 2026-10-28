"""Offline test of exam tickets: the request and its limits, the tickets.md format and its checks, the repair pass, the
two PDFs (and the page of every ticket inside them), the endpoints, isolation between people.
Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_exam.py"""
import asyncio
import datetime as dt
import io
import os
import re
import sys
import tempfile
from pathlib import Path

import pymupdf
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
D1 = (TODAY - dt.timedelta(days=7)).isoformat()
D2 = (TODAY - dt.timedelta(days=3)).isoformat()


def req(login="exam@example.com", lang="uk"):
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": [(b"tailscale-user-login", login.encode()), (b"x-lang", lang.encode())]})


class Up:
    def __init__(self, name, data):
        self.filename, self._b = name, io.BytesIO(data)

    async def read(self, n):
        return self._b.read(n)


_n = [0]


def png():
    """A different picture every time: the same file twice would be refused as a duplicate."""
    _n[0] += 1
    b = io.BytesIO()
    Image.new("RGB", (400, 300), (255, 255 - _n[0], 255)).save(b, "PNG")
    return b.getvalue()


def pdf_bytes():
    _n[0] += 1
    doc = pymupdf.open()
    doc.new_page(width=300, height=300).insert_text((20, 40), f"slide {_n[0]}")
    return doc.tobytes()


def upload(date, kind="notes", subject="math1", files=None):
    return asyncio.run(web.api_upload(req(), files=files or [Up("p1.png", png())], note="", subject=subject, kind=kind,
                                      topic="", topic_title="", code="", date=date))


def call(coro_fn, body, login="exam@example.com", lang="uk"):
    """-> (status, payload or detail)"""
    try:
        return 200, asyncio.run(coro_fn(_JsonRequest(body, login, lang)))
    except web.HTTPException as e:
        return e.status_code, e.detail


class _JsonRequest(Request):
    def __init__(self, body, login, lang):
        import json
        super().__init__({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                          "headers": [(b"tailscale-user-login", login.encode()), (b"x-lang", lang.encode())]})
        self._b = json.dumps(body).encode()

    async def json(self):
        import json
        return json.loads(self._b)


def exam(body, **kw):
    return call(web.api_exam, body, **kw)


def pdf_text(path):
    with pymupdf.open(path) as doc:
        return [p.get_text() for p in doc]


# --- material: two class days with text, one PDF kept as is ----------------------------------------------------------
D3 = (TODAY - dt.timedelta(days=1)).isoformat()      # a day that only has a PDF
upload(D1)
upload(D2)
upload(D1, kind="asis", files=[Up("slides.pdf", pdf_bytes())])
upload(D3, kind="asis", files=[Up("slides.pdf", pdf_bytes())])
w = sb.worker()
sp = web.who(req())
check("material is in place (2 text entries + 2 PDFs as is)", w.get("processed") == 4 and len(sb.entries_of(sp, "math1")) == 4, str(w))

# --- the request and its rules ---------------------------------------------------------------------------------------
base = {"subject": "math1", "tickets": 4, "per": 3}
check("unknown subject -> 404", exam(base | {"subject": "nope"})[0] == 404)
check("a day with no class is no source", exam(base | {"days": ["1999-01-01"]})[0] == 400)
check("a class with only a PDF as is is no source", exam(base | {"days": [D3]})[0] == 400)
check("0 tickets refused", exam(base | {"tickets": 0})[0] == 400)
check("too many tickets refused", exam(base | {"tickets": sb.EXAM_MAX_TICKETS + 1})[0] == 400)
check("too many questions refused", exam(base | {"per": sb.EXAM_MAX_PER + 1})[0] == 400)
check("a number that is not a number refused", exam(base | {"tickets": "many"})[0] == 400)
check("unknown make-up refused", exam(base | {"mix": "oral"})[0] == 400)
check("unknown level refused", exam(base | {"level": "brutal"})[0] == 400)
check("unknown language refused", exam(base | {"lang": "fr"})[0] == 400)
check("long wishes refused", exam(base | {"text": "x" * (sb.EXAM_WISH_MAX + 1)})[0] == 400)
st, detail = exam(base | {"tickets": 0}, lang="uk")
check("Ukrainian error text", "білетів від" in str(detail), str(detail))
st, detail = exam(base | {"tickets": 0}, lang="en")
check("English error text", "tickets" in str(detail) and "білет" not in str(detail), str(detail))
check("nothing was queued by the refused requests", not list(sp.d["requests"].glob("*.json")))

st, j = exam(base | {"mix": "mixed", "lang": "en", "text": "more problems on moduli"})
check("a valid request is accepted", st == 200 and j.get("ok"), str(j))
rq = sb.read_json(sp.d["requests"] / f"{j['id']}.json")
check("the request records everything", rq["kind"] == "exam" and rq["days"] == [D1, D2] and rq["tickets"] == 4 and rq["per"] == 3
      and rq["mix"] == "mixed" and rq["level"] == "same" and rq["lang"] == "en" and rq["text"] == "more problems on moduli", str(rq))
check("the label tells what it is", "4×3" in rq["label"])
only_d2 = exam(base | {"days": [D2, "garbage", 5]})
check("a day filter is kept, garbage dropped", only_d2[0] == 200 and sb.read_json(sp.d["requests"] / f"{only_d2[1]['id']}.json")["days"] == [D2])
check("a third waiting request is refused", exam(base)[0] == 429)
q = sb.status(sp)["queue"]
check("status lists them as exam requests with their subject", sum(1 for x in q if x["kind"] == "exam" and x["subject"] == "math1") == 2, str(q))

# --- the worker makes the sets -----------------------------------------------------------------------------------------
seen = Path(tempfile.mkdtemp()) / "prompt.txt"
os.environ["FAKE_EXAM_SEEN"] = str(seen)
w = sb.worker()
check("worker made both sets", w.get("processed") == 2 and not w.get("blocked"), str(w))
sets = sb.exam_sets(sp, "math1")
check("two sets stored, newest first", len(sets) == 2 and sets[0]["created"] >= sets[1]["created"])
big = next(s for s in sets if s["tickets"] == 4 and s["days"] == [D1, D2])
d = sp.d["exams"] / "math1" / big["id"]
check("set files exist", all((d / n).exists() for n in ("tickets.pdf", "answers.pdf", "tickets.json", "meta.json")))
check("no half-built folder or work dir is left", not list((sp.d["exams"] / "math1").glob(".*.tmp")) and not (sp.d["work"] / big["id"]).exists())
check("the request moved to done", (sp.d["requests"] / "done" / f"{big['id']}.json").exists() and not (sp.d["requests"] / f"{big['id']}.json").exists())
check("meta: counts, make-up and language", big["per"] == 3 and big["mix"] == "mixed" and big["lang"] == "en" and big["problems"] == 4
      and big["wish"] == "more problems on moduli", str(big))
prompts = [p for p in seen.read_text(encoding="utf-8").split("\n=====PROMPT=====\n") if p.strip()]
p1 = next(p for p in prompts if "more problems on moduli" in p)
p2 = next(p for p in prompts if "more problems on moduli" not in p)
check("the prompt carries the numbers and the make-up", "Tickets: exactly 4" in p1 and "Questions per ticket: exactly 3" in p1
      and "exactly 2 theory question(s) first, then 1 problem(s)" in p1, p1[p1.find("## What to make"):][:400])
check("the prompt lists the text entries of every chosen day but not the PDFs as is", "slides" not in p1 and f"context/{D1}/" in p1 and f"context/{D2}/" in p1)
check("a day filter limits what Claude gets", f"context/{D1}/" not in p2 and f"context/{D2}/" in p2)
check("the prompt carries the wish and the language", "Language of the questions and the answers: English" in p1)
check("the prompt has no unfilled placeholder", all("{{" not in p for p in prompts))

tk = sb.read_json(d / "tickets.json")
check("4 tickets x 3 questions, 2 theory + 1 problem each", len(tk) == 4 and all([q["kind"] for q in t["questions"]] == ["theory", "theory", "problem"] for t in tk))
check("each question has an answer and a class day", all(q["answer"] and q["day"] in (D1, D2) for t in tk for q in t["questions"]))
tt, aa = pdf_text(d / "tickets.pdf"), pdf_text(d / "answers.pdf")
check("tickets.pdf has the tickets and no answers", any("Ticket 4" in p for p in tt) and not any("Model answer" in p for p in tt))
check("tickets.pdf has the name line and the questions", any("Name:" in p for p in tt) and any("Define notion 1.1" in p for p in tt))
check("answers.pdf has the model answers and where each question comes from", any("Model answer" in p for p in aa) and any("class of" in p for p in aa))
check("the math was typeset (no raw dollar signs)", not any("$" in p for p in tt + aa))
pg = big["pages"]
check("page of every ticket was found", all(isinstance(x, int) for x in pg["tickets"] + pg["answers"]), str(pg))
check("page numbers rise and match the heading", pg["tickets"] == sorted(pg["tickets"]) and all(
    re.search(rf"^Ticket {n}$", tt[p - 1], re.M) for n, p in enumerate(pg["tickets"], 1)))
check("answers.pdf pages match too", all(re.search(rf"^Ticket {n}$", aa[p - 1], re.M) for n, p in enumerate(pg["answers"], 1)))

# --- status and the endpoints -------------------------------------------------------------------------------------------
subj = next(s for s in sb.status(sp)["subjects"] if s["id"] == "math1")
check("status lists the sets with their links", len(subj["exams"]) == 2 and subj["exams"][0]["tickets_url"].startswith("/exams/math1/")
      and "pages" in subj["exams"][0])
check("status carries the limits for the page", sb.status(sp)["exam_limits"]["max_tickets"] == sb.EXAM_MAX_TICKETS)
r = web.exam_pdf(req(), "math1", big["id"], "tickets")
check("tickets.pdf is served inline", r.path == d / "tickets.pdf" and "inline" in r.headers["content-disposition"])
check("answers.pdf is served", web.exam_pdf(req(), "math1", big["id"], "answers").path == d / "answers.pdf")
for bad in (("math1", big["id"], "tickets.json"), ("math1", "../x", "tickets"), ("../math1", big["id"], "tickets"), ("math1", "nope", "tickets")):
    try:
        web.exam_pdf(req(), *bad)
        check(f"refused {bad}", False)
    except web.HTTPException as e:
        check(f"refused {bad}", e.status_code in (404,))
try:
    web.exam_pdf(req("other@example.com"), "math1", big["id"], "tickets")
    check("another person cannot read the PDF", False)
except web.HTTPException as e:
    check("another person cannot read the PDF", e.status_code == 404)
other = web.who(req("other@example.com"))
check("another person sees no sets", all(not s["exams"] for s in sb.status(other)["subjects"]))
b0 = web.space_bytes(sp)
(sp.d["exams"] / "pad.bin").write_bytes(b"x" * 5000)
check("the quota counts the exams folder", web.space_bytes(sp) - b0 == 5000)
(sp.d["exams"] / "pad.bin").unlink()

# --- a set in another language and the other make-ups ---------------------------------------------------------------
for lang, word in (("uk", "Білет"), ("de", "Prüfungskarte")):
    st, j = exam(base | {"tickets": 2, "per": 2, "lang": lang, "mix": "theory"})
    sb.worker()
    s_ = next(s for s in sb.exam_sets(sp, "math1") if s["id"] == j["id"])
    text = pdf_text(sp.d["exams"] / "math1" / s_["id"] / "tickets.pdf")
    check(f"{lang}: the PDF words are in that language", any(re.search(rf"^{word} 2$", p, re.M) for p in text), str(text[:1]))
    check(f"{lang}: found the pages", s_["pages"]["tickets"] == [1, 2] and s_["problems"] == 0, str(s_["pages"]))
st, j = exam(base | {"tickets": 3, "per": 1, "mix": "problems"})
sb.worker()
s_ = next(s for s in sb.exam_sets(sp, "math1") if s["id"] == j["id"])
check("problems only", s_["problems"] == 3 and s_["per"] == 1)

# --- the format and its checks (no Claude) -------------------------------------------------------------------------
GOOD = ("=== TICKET 1 ===\n--- QUESTION | theory | " + D1 + " ---\nWhat is a limit of a sequence?\n--- ANSWER ---\nA number $L$ such that every tail is close.\n"
        "--- QUESTION | problem ---\nCompute $1+1$ for the first time.\n--- ANSWER ---\nIt is $2$, by adding the two numbers.\n"
        "=== TICKET 2 ===\n--- QUESTION | theory | 1999-01-01 ---\nWhat is a supremum of a set?\n--- ANSWER ---\nThe least upper bound of the set.\n"
        "--- QUESTION | problem ---\nCompute $2+2$ for the second time.\n--- ANSWER ---\nIt is $4$, by adding the two numbers.\n")
tk_, errs = sb.parse_tickets(GOOD)
check("parser reads tickets, kinds, days, answers", [len(t["questions"]) for t in tk_] == [2, 2] and tk_[0]["questions"][0]["day"] == D1
      and tk_[0]["questions"][1]["day"] is None and tk_[1]["questions"][0]["answer"].startswith("The least") and not errs)
check("a good set passes", sb.validate_tickets(tk_, 2, 2, "mixed", [D1, D2]) == [])
check("a date outside the chosen classes is dropped, not an error", tk_[1]["questions"][0]["day"] is None)
check("wrong number of tickets", any("exactly 3" in e for e in sb.validate_tickets(tk_, 3, 2, "mixed", [D1])))
check("wrong number of questions", any("has 2 questions, exactly 3" in e for e in sb.validate_tickets(tk_, 2, 3, "mixed", [D1])))
check("theory-only set with problems in it fails", any("0 problem(s)" in e for e in sb.validate_tickets(tk_, 2, 2, "theory", [D1])))
check("problems-only set with theory in it fails", any("2 problem(s)" in e for e in sb.validate_tickets(tk_, 2, 2, "problems", [D1])))
all_theory = GOOD.replace("--- QUESTION | problem ---\nCompute $1+1$", "--- QUESTION | theory ---\nCompute $1+1$")
check("mixed with the wrong split fails", any("ticket 1 has 0 problems" in e for e in sb.validate_tickets(sb.parse_tickets(all_theory)[0], 2, 2, "mixed", [D1])))
check("the make-up rule: 1 of 2, 1 of 3, 2 of 4, 2 of 5, none for one question",
      [sb.exam_problems(p, "mixed") for p in (1, 2, 3, 4, 5)] == [None, 1, 1, 2, 2]
      and sb.exam_problems(3, "theory") == 0 and sb.exam_problems(3, "problems") == 3)
dup = GOOD.replace("What is a supremum of a set?", "What is a limit of a sequence?")
check("a repeated question is caught", any("same question" in e for e in sb.validate_tickets(sb.parse_tickets(dup)[0], 2, 2, "mixed", [D1])))
for name, bad in (("an image", "![x](a.png)"), ("a link", "[x](https://evil.example)"), ("a web address", "see https://evil.example now")):
    t_, _ = sb.parse_tickets(GOOD.replace("by adding the two numbers.", bad + " by adding", 1))
    check(f"{name} is refused", any("images, links" in e for e in sb.validate_tickets(t_, 2, 2, "mixed", [D1])))
short = GOOD.replace("It is $2$, by adding the two numbers.", "ok")
check("a too short answer is caught", any("answer is missing" in e for e in sb.validate_tickets(sb.parse_tickets(short)[0], 2, 2, "mixed", [D1])))
check("numbering must run 1, 2, 3", any("numbered" in e for e in sb.validate_tickets(sb.parse_tickets(GOOD.replace("TICKET 2", "TICKET 5"))[0], 2, 2, "mixed", [D1])))
check("an answer without a question is reported", sb.parse_tickets("=== TICKET 1 ===\n--- ANSWER ---\nx\n")[1])
check("text before the first ticket is ignored", sb.parse_tickets("Here are your tickets:\n\n" + GOOD)[0][0]["n"] == 1)
check("a markdown rule inside a text is not a marker", len(sb.parse_tickets(GOOD.replace("What is a limit of a sequence?", "Before\n---\nAfter"))[0][0]["questions"]) == 2)

# --- repair pass, failure, raw fallback -----------------------------------------------------------------------------
def fix_prompts():
    return [p for p in seen.read_text(encoding="utf-8").split("\n=====PROMPT=====\n") if "a program that reads it found these problems" in p]


os.environ["FAKE_EXAM"] = "short"
st, j = exam(base | {"tickets": 3, "per": 2})
w = sb.worker()
s_ = next((s for s in sb.exam_sets(sp, "math1") if s["id"] == j["id"]), None)
check("a short file is repaired by one more Claude call", s_ is not None and s_["tickets"] == 3 and not w.get("blocked"), str(w))
fp = fix_prompts()
check("the repair prompt names what is wrong and what is needed", len(fp) == 1 and "exactly 3" in fp[0] and "exactly 3 tickets, each with exactly 2 questions" in fp[0]
      and "{{" not in fp[0], str(fp)[:300])
os.environ["FAKE_EXAM"] = "badtex"
st, j = exam(base | {"tickets": 2, "per": 2})
sb.worker()
s_ = next((s for s in sb.exam_sets(sp, "math1") if s["id"] == j["id"]), None)
check("broken math is repaired by Claude", s_ is not None and not any(q.get("text_raw") for t in sb.read_json(sp.d["exams"] / "math1" / s_["id"] / "tickets.json") for q in t["questions"]))
fp = fix_prompts()
check("the repair prompt quoted the typesetting error", len(fp) == 2 and "does not typeset" in fp[1] and "Could not convert" in fp[1], str(fp[-1:])[:300])
os.environ["FAKE_EXAM"] = "short_always"
before = len(sb.exam_sets(sp, "math1"))
st, j = exam(base | {"tickets": 3, "per": 2})
sb.worker()
check("a set that stays wrong is not stored", len(sb.exam_sets(sp, "math1")) == before and not (sp.d["exams"] / "math1" / j["id"]).exists())
left = sb.read_json(sp.d["requests"] / f"{j['id']}.json") or {}
check("the request stays for another try, with the reason", left.get("attempts") == 1 and "exactly 3" in left.get("last_error", ""), str(left))
for _ in range(2):
    sb.worker()
check("after three failures it goes to the failed list", (sp.d["failed"] / f"{j['id']}.json").exists() and not (sp.d["requests"] / f"{j['id']}.json").exists())
check("the failed request can be retried", sb.retry_failed(sp, f"{j['id']}.json").get("ok") and (sp.d["requests"] / f"{j['id']}.json").exists())
(sp.d["requests"] / f"{j['id']}.json").unlink()
os.environ["FAKE_EXAM"] = "ok"

sb_run_llm = sb.run_llm
sb.run_llm = lambda *a, **k: None                       # Claude cannot repair anything now
broken = Path(tempfile.mkdtemp()) / "tickets.md"
broken.write_text(GOOD.replace("$1+1$", "$\\frac{1}{$"), encoding="utf-8")
got = sb.load_tickets(sp, broken, broken.parent, {"id": "x", "tickets": 2, "per": 2, "mix": "mixed", "days": [D1]})
sb.run_llm = sb_run_llm
check("a formula that cannot be repaired stays as raw text, the rest is typeset", got[0]["questions"][1].get("text_raw") and not got[0]["questions"][0].get("text_raw"))
rq2 = {"lang": "en"}
typ = sb.exam_typst(sp, "Math 1", got, rq2, True)
out = Path(tempfile.mkdtemp()) / "x.pdf"
src = sp.d["build"] / "x.typ"
src.write_text(typ, encoding="utf-8")
sb.typst_compile(sp.root, src, out)
check("raw items still make a valid PDF", out.exists() and out.stat().st_size > 1000)

# --- limits and deleting ---------------------------------------------------------------------------------------------
sb.EXAM_MAX_DAY = len([1 for f in (sp.d["requests"] / "done").glob("*.json") if (sb.read_json(f) or {}).get("kind") == "exam"])
st, detail = exam(base)
check("the daily limit stops more requests", st == 429 and "на добу" in str(detail), f"{st} {detail}")
sb.EXAM_MAX_DAY = 8
sb.EXAM_MAX_SETS = len(sb.exam_sets(sp, "math1"))
check("a full shelf stops more requests", exam(base)[0] == 409)
sb.EXAM_MAX_SETS = 20
n0 = len(sb.exam_sets(sp, "math1"))
check("deleting asks for a real set", call(web.api_exam_delete, {"subject": "math1", "id": "nope"})[0] == 404
      and call(web.api_exam_delete, {"subject": "math1", "id": "../x"})[0] == 400)
check("another person cannot delete it", call(web.api_exam_delete, {"subject": "math1", "id": big["id"]}, login="other@example.com")[0] == 404)
check("a set is deleted", call(web.api_exam_delete, {"subject": "math1", "id": big["id"]})[0] == 200 and not d.exists()
      and len(sb.exam_sets(sp, "math1")) == n0 - 1)

# --- the page ----------------------------------------------------------------------------------------------------------
html = (Path(web.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
check("the page has the third tab and its form", 'data-tab="exams"' in html and 'id="tab-exams"' in html and 'id="exmake"' in html)

# nothing left behind
for p in (sp.root / "build" / "x.typ",):
    p.unlink(missing_ok=True)
print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
