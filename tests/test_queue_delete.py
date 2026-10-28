"""Offline test: a person deletes waiting uploads and requests from the queue (also the one the worker is busy with:
its model call is stopped within seconds and the worker removes it), and opens the files of their uploads in the
browser (a PDF or a photo is shown, anything else is downloaded, nobody else's files, no path tricks).

Run from a COPY of the code, with a fresh data dir:
  SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_queue_delete.py   (never on the real data)"""
import asyncio
import io
import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_DIGEST_QUIET"] = "3600"
os.environ["SB_SV_PORTS"] = ""
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "tests" / "fake_claude.py"))
os.environ["FAKE_ARGS"] = str(Path(os.environ["SB_DATA"]) / "args.jsonl")

import sb  # noqa: E402
import web  # noqa: E402
from PIL import Image  # noqa: E402
from starlette.requests import Request  # noqa: E402

web.kick = lambda: None
sb.learn_note(auto=False)
fails = []
ME, OTHER = "owner@example.com", "other@example.com"


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


def upload(login, files=None, kind="notes"):
    return asyncio.run(web.api_upload(req(login), files=files or [Up("p1.png", png())], note="", subject="math1",
                                      kind=kind, topic="", topic_title="", code="", date=sb.now().date().isoformat()))["id"]


def delete(login, rid):
    try:
        return 200, asyncio.run(web.api_queue_delete(req(login, {"id": rid})))
    except web.HTTPException as e:
        return e.status_code, e.detail


def fetch(login, sid, idx):
    try:
        return 200, web.api_upload_file(req(login), sid, idx)
    except web.HTTPException as e:
        return e.status_code, e.detail


def add_request(sp, text="explain it"):
    rid = sb.new_id()
    sb.write_json(sp.d["requests"] / f"{rid}.json", {"id": rid, "kind": "explain", "subject": "math1", "topic": "",
                                                     "text": text, "created": sb.now_iso(), "label": text})
    return rid


me, other = web.who(req(ME)), web.who(req(OTHER))
sb.ensure_global()

# --- 1. the files of an upload ------------------------------------------------------------------------------------------
pdf = b"%PDF-1.4\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"
sid = upload(ME, files=[Up("My notes.png", png()), Up("script.html", b"<script>alert(1)</script>"), Up("sheet.pdf", pdf)])
st = sb.status(me)
q = next(x for x in st["queue"] if x["id"] == sid)
check("the queue item lists its files with links", [l["name"] for l in q["file_links"]] == ["My notes.png", "script.html", "sheet.pdf"], q["file_links"])
check("... and says it can be deleted", q["can_delete"] is True and q["deleting"] is False)
code, r = fetch(ME, sid, 0)
check("a photo is shown in the browser", code == 200 and r.media_type == "image/png" and r.headers["content-disposition"].startswith("inline"), str(r)[:80])
check("... in a sandbox with no scripts", "sandbox" in r.headers.get("content-security-policy", ""))
check("... under the name the person gave", "My notes.png" in r.headers["content-disposition"] or "My%20notes.png" in r.headers["content-disposition"], r.headers["content-disposition"])
code, r = fetch(ME, sid, 1)
check("an uploaded .html is a download, never a page of this site", code == 200 and r.media_type == "application/octet-stream" and r.headers["content-disposition"].startswith("attachment"), r.headers.get("content-disposition"))
code, r = fetch(ME, sid, 2)
check("a PDF is shown in the browser", code == 200 and r.media_type == "application/pdf" and r.headers["content-disposition"].startswith("inline"))
check("a number outside the list is 404", fetch(ME, sid, 3)[0] == 404 and fetch(ME, sid, -1)[0] == 404)
check("somebody else cannot open it", fetch(OTHER, sid, 0)[0] == 404)
for bad in ("../x", "..", "a/b", ".hidden", "x" * 80, sid + "/../" + sid):
    check(f"no path tricks: {bad[:20]!r}", fetch(ME, bad, 0)[0] in (404, 422))

# a symlink inside the upload folder must not lead out of it
secret = Path(os.environ["SB_DATA"]) / "secret.txt"
secret.write_text("secret", encoding="utf-8")
meta = sb.read_json(me.d["inbox"] / sid / "submission.json")
(me.d["inbox"] / sid / meta["files"][1]).unlink()
(me.d["inbox"] / sid / meta["files"][1]).symlink_to(secret)
check("a link that leaves the upload folder is not served", fetch(ME, sid, 1)[0] == 404)

# --- 2. deleting what waits ---------------------------------------------------------------------------------------------
(me.d["work"] / sid).mkdir(parents=True)
(me.d["work"] / sid / "pages.json").write_text("{}")
me.update_profile(run_now=[sid, "other-id"])
check("the file index knows the upload", any(v.get("sid") == sid for v in (sb.read_json(me.root / "file_hashes.json", {}) or {}).values()))
code, r = delete(ME, sid)
check("delete answers ok, not stopping", code == 200 and r == {"ok": True, "stopping": False}, str((code, r)))
check("the upload folder is gone", not (me.d["inbox"] / sid).exists())
check("its half-done work is gone", not (me.d["work"] / sid).exists())
check("nothing was processed or failed", not list(me.d["processed"].iterdir()) and not list(me.d["failed"].iterdir()))
check("the hand-start mark for it is gone, the others stay", me.run_now_ids() == {"other-id"})
check("the file index forgot it (the same file may be sent again)", not any(v.get("sid") == sid for v in (sb.read_json(me.root / "file_hashes.json", {}) or {}).values()))
check("no stale delete mark", not me.cancel_ids())
check("the queue no longer lists it", all(x["id"] != sid for x in sb.status(me)["queue"]))
check("deleting it again is 404", delete(ME, sid)[0] == 404)

rid = add_request(me)
q = next(x for x in sb.status(me)["queue"] if x["id"] == rid + ".json")
check("a request can be deleted too (its id has .json)", q["can_delete"] is True)
code, r = delete(ME, rid + ".json")
check("... by the id the page sends", code == 200 and not (me.d["requests"] / f"{rid}.json").exists())

other_sid = upload(OTHER)
check("nobody deletes another person's upload", delete(ME, other_sid)[0] == 404 and (other.d["inbox"] / other_sid).exists())
for bad in ("../" + other_sid, "..", "", "a/b", ".x", "x" * 80):
    check(f"bad id {bad[:20]!r} is 404", delete(ME, bad)[0] == 404)
sug = me.d["suggestions"] / "s1"
sug.mkdir(parents=True)
sb.write_json(sug / "s.json", {"status": "queued", "created": sb.now_iso()})
check("a suggestion in the queue is not deleted by this call", delete(ME, "s1")[0] == 404 and sug.exists())
import shutil  # noqa: E402
shutil.rmtree(sug)        # an incomplete suggestion record would break status()
delete(OTHER, other_sid)

# --- 3. a mark left behind (worker died) is cleaned up when the worker starts ---------------------------------------------
sid2 = upload(ME)
me.update_profile(cancel=[sid2, "nothing-like-it"])
sb.worker()
check("a marked upload is removed instead of processed", not (me.d["inbox"] / sid2).exists() and not list(me.d["processed"].iterdir()))
check("and the marks are cleared", not me.cancel_ids())
check("no model call was made for it", not Path(os.environ["FAKE_ARGS"]).exists() or not Path(os.environ["FAKE_ARGS"]).read_text().strip())

# --- 4. deleting what the worker is busy with ---------------------------------------------------------------------------
def busy_on(item):
    for _ in range(80):
        s = sb.read_json(sb.STATUS, {}) or {}
        if s.get("current_uid") == me.uid and (item in (s.get("cur_members") or []) or s.get("cur_item") == item) and Path(os.environ["FAKE_ARGS"]).exists():
            return True
        time.sleep(0.25)
    return False


os.environ["FAKE_SLEEP"] = "60"
sid3 = upload(ME)
wt = threading.Thread(target=sb.worker)
wt.start()
check("the worker is busy with the upload", busy_on(sid3))
check("status shows progress for it", next(x for x in sb.status(me)["queue"] if x["id"] == sid3).get("progress") is not None)
t0 = time.time()
code, r = delete(ME, sid3)
check("deleting it answers 'stopping'", code == 200 and r == {"ok": True, "stopping": True}, str((code, r)))
check("status says it is being deleted", any(x["id"] == sid3 and x["deleting"] for x in sb.status(me)["queue"]) or not (me.d["inbox"] / sid3).exists())
wt.join(30)
check("the worker stops within seconds, not after the 60 s call", not wt.is_alive() and time.time() - t0 < 15, f"{time.time() - t0:.1f}s")
check("the upload folder and the work folder are gone", not (me.d["inbox"] / sid3).exists() and not (me.d["work"] / sid3).exists())
check("it did not become a failure or an entry", not list(me.d["failed"].iterdir()) and not list(me.d["processed"].iterdir())
      and not sb.entries_of(sb.main_space() or me, "math1"))
check("the mark is cleared", not me.cancel_ids())
check("CancelledItem is a StoppedError, so failure handlers let it through", issubclass(sb.CancelledItem, sb.StoppedError) and not issubclass(sb.CancelledItem, Exception))

# a request the worker is busy with, and a second one behind it that must stay
rid1, rid2 = add_request(me, "first"), add_request(me, "second")
Path(os.environ["FAKE_ARGS"]).unlink(missing_ok=True)
first = min((rid1, rid2))        # the queue is oldest-first; both were created in order
wt = threading.Thread(target=sb.worker)
wt.start()
running = (sb.read_json(sb.STATUS, {}) or {}).get("cur_item")
check("the worker is busy with a request", busy_on(rid1) or busy_on(rid2))
cur = (sb.read_json(sb.STATUS, {}) or {}).get("cur_item")
code, r = delete(ME, cur)
check("deleting the running request answers 'stopping'", code == 200 and r.get("stopping") is True, str((code, r)))
os.environ.pop("FAKE_SLEEP")            # the rest of the queue goes fast now
wt.join(60)
check("the worker finished", not wt.is_alive())
check("the deleted request is gone and was not processed", not (me.d["requests"] / f"{cur}.json").exists())
other_rid = rid2 if cur == rid1 else rid1
check("the other request was not touched by the deletion (it ran normally or waits)", not (me.d["failed"] / f"{other_rid}.json").exists())
check("nothing failed", not list(me.d["failed"].iterdir()))

# --- 5. one file out of an upload with several -----------------------------------------------------------------------------
def delete_file(login, sid_, name):
    try:
        return 200, asyncio.run(web.api_queue_file_delete(req(login, {"id": sid_, "file": name})))
    except web.HTTPException as e:
        return e.status_code, e.detail


pdf2 = pdf + b"% second\n"
sidf = upload(ME, files=[Up("one.png", png()), Up("two.pdf", pdf2), Up("three.png", png())])
meta = sb.read_json(me.d["inbox"] / sidf / "submission.json")
q = next(x for x in sb.status(me)["queue"] if x["id"] == sidf)
check("every file link carries the stored name (the page deletes by name, not by position)", [l["file"] for l in q["file_links"]] == meta["files"], q["file_links"])
check("the upload is labelled by its file count", meta["label"] == "3 files", meta["label"])
(me.d["work"] / sidf).mkdir(parents=True)
(me.d["work"] / sidf / "pages.json").write_text(json.dumps({"pages": [], "members": [sidf]}))
(me.d["work"] / "leader-x").mkdir()
(me.d["work"] / "leader-x" / "pages.json").write_text(json.dumps({"pages": [], "members": ["leader-x", sidf]}))
(me.d["work"] / "unrelated").mkdir()
(me.d["work"] / "unrelated" / "pages.json").write_text(json.dumps({"pages": [], "members": ["unrelated"]}))
gone_name = meta["files"][1]
check("a stored name that is not in the upload is 404", delete_file(ME, sidf, "nothing.pdf")[0] == 404 and delete_file(ME, sidf, "../" + gone_name)[0] == 404)
check("somebody else cannot take a file out", delete_file(OTHER, sidf, gone_name)[0] == 404 and (me.d["inbox"] / sidf / gone_name).exists())
code, r = delete_file(ME, sidf, gone_name)
meta2 = sb.read_json(me.d["inbox"] / sidf / "submission.json")
check("the PDF is removed from the upload", code == 200 and not (me.d["inbox"] / sidf / gone_name).exists() and gone_name not in meta2["files"], str((code, r)))
check("the other two files are untouched", meta2["files"] == [meta["files"][0], meta["files"][2]] and all((me.d["inbox"] / sidf / f).is_file() for f in meta2["files"]))
check("the names the person gave follow", meta2["original_names"] == ["one.png", "three.png"], meta2["original_names"])
check("the label follows the new count", meta2["label"] == "2 files", meta2["label"])
check("the half-done work of the upload and of the group it was in is dropped, nobody else's",
      not (me.d["work"] / sidf).exists() and not (me.d["work"] / "leader-x").exists() and (me.d["work"] / "unrelated").exists())
digest = sb.sha(pdf2)
check("the same PDF may be sent again (no duplicate mark left)", sb.find_duplicate(me, "notes", digest) is None)
check("taking the same file out again is 404", delete_file(ME, sidf, gone_name)[0] == 404)
check("the queue shows two files now", [l["name"] for l in next(x for x in sb.status(me)["queue"] if x["id"] == sidf)["file_links"]] == ["one.png", "three.png"])

# the worker is busy with it: not now
sb.write_json(sb.STATUS, {"state": "busy", "current_uid": me.uid, "pid": os.getpid(), "cur_item": sidf, "cur_members": [sidf]})
code, r = delete_file(ME, sidf, meta2["files"][0])
check("while it is being processed: 409 and nothing is removed", code == 409 and (me.d["inbox"] / sidf / meta2["files"][0]).exists(), str((code, r)))
sb.write_json(sb.STATUS, {"state": "idle"})

# one file left: removing it removes the whole upload
code, r = delete_file(ME, sidf, meta2["files"][0])
code, r = delete_file(ME, sidf, meta2["files"][1])
check("the last file takes the whole upload with it", code == 200 and not (me.d["inbox"] / sidf).exists() and all(x["id"] != sidf for x in sb.status(me)["queue"]), str((code, r)))
check("... nothing is left marked", not me.cancel_ids() and not (me.d["work"] / sidf).exists())

# --- 6. the page -----------------------------------------------------------------------------------------------------------
html = (Path(sb.__file__).parent / "web" / "index.html").read_text(encoding="utf-8")
check("the page has the delete call, the file links and the theme button",
      "/api/queue/delete" in html and "/api/queue/file/delete" in html and "filesHTML" in html and 'id="themeBtn"' in html and 'data-theme="light"' in html)

print()
print("FAILED:" if fails else "ALL OK", fails if fails else "")
sys.exit(1 if fails else 0)
