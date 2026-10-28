"""Offline test of the Ukrainian/English layer on the server side.
Run from a COPY of the code: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_i18n.py"""
import os
import re
import sys
from pathlib import Path

import sb

sb.SUG_MIN_GAP = 0
fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


CYR = re.compile(r"[А-Яа-яІіЇїЄєҐґ]")
LATIN_ONLY = lambda s: bool(s) and not CYR.search(str(s))
UK_ONLY = lambda s: bool(CYR.search(str(s)))

# --- the helper itself ------------------------------------------------------------------------------------------
sb.set_lang("uk")
check("default language is Ukrainian", sb.L("так", "yes") == "так" and sb.lang_name() == "Ukrainian")
sb.set_lang("en")
check("English on request", sb.L("так", "yes") == "yes" and sb.lang_name() == "English")
sb.set_lang("de")
check("an unknown language falls back to Ukrainian", sb.L("так", "yes") == "так")

# --- profile language and the web layer ------------------------------------------------------------------------
friend = sb.Space("friend").ensure(login="friend@example.com", name="Friend")
check("a person without a saved language reads Ukrainian", friend.lang() == "uk")
friend.update_profile(lang="en")
check("language is saved in the profile", friend.lang() == "en")

import web  # noqa: E402
from starlette.requests import Request  # noqa: E402


def req(lang=None, login="web@example.com"):
    headers = [(b"tailscale-user-login", login.encode())]
    if lang:
        headers.append((b"x-lang", lang.encode()))
    return Request({"type": "http", "method": "GET", "path": "/", "headers": headers, "query_string": b""})


sp = web.who(req("en"))
check("X-Lang: en sets the language of the request and is remembered", sb.L("a", "b") == "b" and sp.lang() == "en")
web.who(req("uk"))
check("switching back is remembered too", sb.L("a", "b") == "a" and sp.lang() == "uk")
web.who(req())
check("no header -> the remembered language", sb.L("a", "b") == "a")
web.who(req("en"))
web.who(req("xx"))
check("a bogus header is ignored", sb.L("a", "b") == "b" and sp.lang() == "en")

# --- API errors come in the person's language ---------------------------------------------------------------------
E = {"type": "entry", "subject": "math1", "topic": "complex-numbers", "entry": "2026-10-16_2145_complex-numbers"}


def err(**body):
    try:
        sb.create_suggestion(friend, body)
    except sb.SuggestError as e:
        return e.msg
    return None


sb.set_lang("en")
m_en = err(target=E, text="short")
sb.set_lang("uk")
m_uk = err(target=E, text="short")
check("suggestion error: English / Ukrainian", "too short" in m_en and LATIN_ONLY(m_en) and "занадто коротко" in m_uk, (m_en, m_uk))
sb.set_lang("en")
check("target errors in English", LATIN_ONLY(err(target={"type": "entry", "subject": "nope"}, text="x" * 30 + " different words here")))
check("unknown engine message", LATIN_ONLY(sb.Space("someone").ensure().engine_missing() or "x"))
sb.set_lang("uk")
check("... and the Ukrainian original is unchanged", sb.Space("someone").ensure().engine_missing() == "Claude не підключений")
sb.set_lang("en")
check("engine_missing in English", sb.Space("someone").ensure().engine_missing() == "Claude is not connected")

# --- code checks produce English problems ------------------------------------------------------------------------
sb.set_lang("en")
old = "# T\n\nLet $z = a + bi$.\n"
probs = [sb.validate_change("entry", old, old),
         sb.validate_change("entry", old, old + "\n\nSee [x](https://e.example)\n"),
         sb.validate_change("interactive", "<html><body>x</body></html>", "<html><body>x<script>fetch('/a')</script></body></html>")]
check("validate_change problems are English", all(p and LATIN_ONLY(p) for p in probs), probs)
sb.set_lang("uk")
check("validate_change problems are Ukrainian", UK_ONLY(sb.validate_change("entry", old, old + "\n\n[x](https://e.example)\n") or ""), "")

# --- limits text -----------------------------------------------------------------------------------------------------
sb.set_lang("en")
check("wait text units", sb._wait_text(30) == "30 s" and sb._wait_text(600) == "10 min" and sb._wait_text(7200) == "2 h")
sb.set_lang("uk")
check("wait text units (uk)", sb._wait_text(30) == "30 с" and sb._wait_text(600) == "10 хв" and sb._wait_text(7200) == "2 год")

# --- prompts ask for the reader's language ---------------------------------------------------------------------------
kw = dict(kind="k", subject="s", title="t", topic="x", current="current.md", out="new.md", refs="r", refs_list="l")
sb.set_lang("en")
check("prompt: reply language English", "in English addressed" in sb.render_prompt("suggest.md", lang_name=sb.lang_name(), **kw)
      and "in English for the admin" in sb.render_prompt("merge.md", lang_name=sb.lang_name(), subject="s", files="f", out="o", refs="r", refs_list="l"))
sb.set_lang("uk")
check("prompt: reply language Ukrainian", "in Ukrainian addressed" in sb.render_prompt("suggest.md", lang_name=sb.lang_name(), **kw))

# --- the worker writes in the language of the person it works for ----------------------------------------------------
ADMIN = "owner-example-com"
admin = sb.Space(ADMIN).ensure(login="admin@example.com", name="Admin")
uk_friend = sb.Space("ukfriend").ensure(login="u@example.com", name="U")
uk_friend.update_profile(lang="uk")
for sp_ in (friend, uk_friend):
    d = sp_.d["library"] / "math1" / "complex-numbers" / "2026-10-16_2145_complex-numbers"
    d.mkdir(parents=True)
    sb.write_json(d / "meta.json", {"title": "Complex numbers", "title_original": "Komplexe Zahlen", "lang_original": "de",
                                    "kind": "lecture", "subject": "math1", "topic": "complex-numbers",
                                    "topic_title": "Complex numbers", "added": "2026-10-16T21:45:00+02:00", "order": 1,
                                    "note": "", "pages": [], "figures": []})
    (d / "clean.md").write_text("# Complex numbers\n\nLet $z = a + bi$.\n", encoding="utf-8")
    (d / "original.md").write_text("# Komplexe Zahlen\n", encoding="utf-8")

os.environ["FAKE_SUG"] = "evil"            # the fake model proposes a draft with a link and a script: the checks refuse it
for sp_, who_ in ((friend, "en"), (uk_friend, "uk")):
    sb.set_lang(who_)
    sb.create_suggestion(sp_, {"target": E, "text": "Please add a note about the case z = 0 to this page, thanks."})
sb.set_lang("uk")
sb.worker()
r_en, r_uk = sb.suggestions_of(friend)[-1], sb.suggestions_of(uk_friend)[-1]
check("worker: refusal text in English for the English reader", r_en["status"] == "rejected" and r_en["reason"].startswith("The change was not applied")
      and LATIN_ONLY(r_en["reason"]), r_en["reason"])
check("worker: refusal text in Ukrainian for the Ukrainian reader", r_uk["reason"].startswith("Зміну не застосовано"), r_uk["reason"])

# a submission that cannot be read fails with an explanation in the owner's language
for sp_ in (friend, uk_friend):
    sdir = sp_.d["inbox"] / "bad1"
    sdir.mkdir(parents=True)
    (sdir / "01_x.bin").write_bytes(b"\x00\x01")
    sb.write_json(sdir / "submission.json", {"id": "bad1", "uploaded_at": sb.now_iso(), "files": ["01_x.bin"], "note": "",
                                            "subject": "", "kind": "notes", "label": "x", "original_names": ["x.bin"]})
sb.worker()
fe = sb.read_json(friend.d["failed"] / "bad1" / "submission.json", {}) or {}
fu = sb.read_json(uk_friend.d["failed"] / "bad1" / "submission.json", {}) or {}
check("worker: failed upload explained in English / Ukrainian", "no file could be read" in (fe.get("last_error") or "")
      and "жодного файлу" in (fu.get("last_error") or ""), (fe.get("last_error"), fu.get("last_error")))

# status texts follow the request language
sb.set_lang("en")
check("status: limits text in English", LATIN_ONLY((sb.status(friend)["suggest_limits"].get("why") or "x")))
other_view = sb.status(uk_friend)       # someone else's queue is being worked on: the pill text follows the reader
check("status builds in English without errors", other_view["ok"] and LATIN_ONLY(sb.status(friend)["engine"]["name"]))

print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
