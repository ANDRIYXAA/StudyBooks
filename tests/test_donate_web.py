"""Offline test of the "Donor to <nickname>" endpoints (web.py). Run from a COPY of the code with a fresh data dir:
SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_donate_web.py   (never on the real data)"""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

from fastapi import HTTPException
from starlette.requests import Request

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_N8N_URL"] = "https://n8n.example.test:5678"
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "fake_claude.py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sb  # noqa: E402
import web  # noqa: E402

web.kick = lambda: None
fails = []
DONOR = "owner@example.com"
KEY = "sk-or-v1-donorkey0123456789abcdef"


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def req(login, body=None):
    raw = json.dumps(body).encode() if body is not None else b""

    async def receive():
        return {"type": "http.request", "body": raw, "more_body": False}
    return Request({"type": "http", "method": "POST", "path": "/", "query_string": b"",
                    "headers": [(b"tailscale-user-login", login.encode()), (b"x-lang", b"en"),
                                (b"content-type", b"application/json")]}, receive)


def call(fn, login, body=None):
    try:
        return 200, asyncio.run(fn(req(login, body)))
    except HTTPException as e:
        return e.status_code, e.detail


donor = sb.Space(sb.slugify(DONOR, 60)).ensure(login=DONOR, name="Owner Person")
for n in ("rita", "sam", "tom", "una", "vic", "wes"):
    sb.Space("pub-" + n).ensure(login=n, name=n)
sb._names.clear()

c, d = call(web.api_or_donate, DONOR, {"nick": "rita", "cap": 50})
check("no key of one's own: nothing to give (409)", c == 409, (c, d))

donor.or_key_file.write_text(KEY)
c, d = call(web.api_or_donate, DONOR, {"nick": "rita", "cap": 50})
check("a named person gets a gift", c == 200 and d["donate"]["to"][0]["name"] == "rita" and d["donate"]["to"][0]["cap"] == 50, (c, d))
check("the gift is stored under the person's uid, in the donor's profile only", donor.prof()["or_donate"] == {"pub-rita": 50})
check("the answer carries no uid and no key", "pub-rita" not in json.dumps(d) and KEY not in json.dumps(d), d)

c, d = call(web.api_or_donate, DONOR, {"nick": "rita", "cap": 80})
check("naming the same person again changes the cap, not the count", c == 200 and donor.prof()["or_donate"] == {"pub-rita": 80}, (c, d))
c, d = call(web.api_or_donate, DONOR, {"nick": "@Sam"})
check("the default cap is used when none is given", c == 200 and donor.prof()["or_donate"]["pub-sam"] == sb.DONATE_DEFAULT, (c, d))

c, d = call(web.api_or_donate, DONOR, {"nick": "nobody-here", "cap": 10})
check("an unknown nickname: 404 with one general message", c == 404 and "No such participant" in d, (c, d))
c, d = call(web.api_or_donate, DONOR, {"nick": "owner", "cap": 10})
check("one cannot give to oneself (same message)", c == 404, (c, d))
for bad in (0, -5, 5000, "abc"):
    c, d = call(web.api_or_donate, DONOR, {"nick": "tom", "cap": bad})
    check(f"cap {bad!r} is refused (400)", c == 400, (c, d))
call(web.api_or_donate, DONOR, {"nick": "tom", "cap": 10})
call(web.api_or_donate, DONOR, {"nick": "una", "cap": 10})
call(web.api_or_donate, DONOR, {"nick": "vic", "cap": 10})
c, d = call(web.api_or_donate, DONOR, {"nick": "wes", "cap": 10})
check(f"at most {sb.DONATE_MAX_PEOPLE} people at a time (409)", c == 409 and len(donor.prof()["or_donate"]) == sb.DONATE_MAX_PEOPLE, (c, d))
c, d = call(web.api_or_donate, DONOR, {"nick": "rita", "cap": 60})
check("...but one already named can still be changed", c == 200 and donor.prof()["or_donate"]["pub-rita"] == 60, (c, d))

# what the recipient's status shows
rita = sb.Space("pub-rita")
rita.update_profile(engine="openrouter", openrouter_model=sb.OR_AUTO)
js = json.dumps(sb.status(rita), ensure_ascii=False)
check("the recipient is ready and sees the donor's first name only", rita.engine_ok() and '"from": [{"name": "Owner"' in js, js[:200] if '"from"' not in js else "")
check("the recipient's status has no key, no uid and no e-mail of the donor", KEY not in js and donor.uid not in js and DONOR not in js)

# taking it back
did = next(x["id"] for x in sb.donate_view(donor)["to"] if x["name"] == "rita")
c, d = call(web.api_or_donate_remove, DONOR, {"id": "zzz"})
check("a wrong handle: 404", c == 404, (c, d))
c, d = call(web.api_or_donate_remove, DONOR, {"id": did})
check("the right handle takes the gift back", c == 200 and "pub-rita" not in (donor.prof().get("or_donate") or {}), (c, d))
check("...and the recipient is not ready any more", not rita.engine_ok())

# the admin tab links to n8n (the address comes from .env, only admins get it)
check("n8n link: the admin gets the address", sb.admin_uids() and sb.status(sb.Space(sb.admin_uids()[0]))["n8n_url"] == "https://n8n.example.test:5678", sb.admin_uids())
check("n8n link: somebody else gets nothing", sb.status(rita)["n8n_url"] is None)

# nickname lookups are rate-limited: the form is not a way to find out who has an account
web._donate_tries.clear()
codes = [call(web.api_or_donate, DONOR, {"nick": f"ghost{i}", "cap": 5})[0] for i in range(23)]
check("20 lookups an hour, then 429", codes[:20] == [404] * 20 and codes[20:] == [429] * 3, codes)

print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
