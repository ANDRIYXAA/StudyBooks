"""Offline test: the shared main book. Everybody sees the main book's books and interactive pages; a request made by
someone else lands in the main book (marked `by`), the model call still runs on the requester's own login.

Run from a COPY of the code, with a fresh data dir: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_shared.py
(never on the real data)"""
import os
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_DIGEST_QUIET"] = "3600"
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "fake_claude.py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import sb  # noqa: E402
import web  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)
guest = sb.Space("pub-guest")
guest.ensure(login="guest", name=None)
check("the main book is the first admin's library", sb.main_space().uid == main.uid)

# the main book already has one interactive page
(main.d["interactive"] / "math1").mkdir(parents=True, exist_ok=True)
(main.d["interactive"] / "math1" / "owner-page.html").write_text("<p>owner</p>", encoding="utf-8")
sb.write_json(main.d["interactive"] / "math1" / "owner-page.json", {"title": "Owner page", "topic": "t", "added": sb.now_iso()})

# a guest asks for an interactive page
sb.write_json(guest.d["requests"] / "rq-guest.json", {"id": "rq-guest", "kind": "interactive", "subject": "math1",
                                                       "topic": "", "text": "a spring", "created": sb.now_iso()})
sb.process_request(guest, guest.d["requests"] / "rq-guest.json")

pages = sorted((main.d["interactive"] / "math1").glob("*.json"))
check("the guest's page lands in the MAIN book", len(pages) == 2, [p.name for p in pages])
guest_meta = [sb.read_json(p) for p in pages if sb.read_json(p).get("request") == "a spring"]
check("... marked with who asked for it", guest_meta and guest_meta[0].get("by") == "pub-guest", guest_meta)
check("nothing is written into the guest's own space", not list((guest.d["interactive"] / "math1").glob("*.html")))
check("the request is done", (guest.d["requests"] / "done" / "rq-guest.json").exists() and not (guest.d["requests"] / "rq-guest.json").exists())
check("the main book is marked dirty so its books are rebuilt", "math1" in (sb.read_json(main.dirty, []) or []))

# the owner's own page keeps no `by`
owner_meta = sb.read_json(main.d["interactive"] / "math1" / "owner-page.json")
check("the owner's own page has no `by`", "by" not in owner_meta)

# status: the guest sees the shared pages, keeps their own login and queue
st = web.shared_view(guest, sb.status(guest))
check("the guest's status says shared", st["shared"] is True)
math = next(x for x in st["subjects"] if x["id"] == "math1")
stems = sorted(i["stem"] for i in math["interactive"])
check("the guest's status lists both shared pages", len(stems) == 2 and "owner-page" in stems, stems)
check("the guest's status has no suggestion targets on shared pages", math["targets"] == [])
check("the owner's status is not marked shared", web.shared_view(main, sb.status(main))["shared"] is False)

print("\nALL OK" if not fails else f"\nFAILED: {fails}")
sys.exit(1 if fails else 0)
