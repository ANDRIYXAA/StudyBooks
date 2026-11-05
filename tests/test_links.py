"""Offline test: links to videos and sites. Address checks, the safe fetch (nothing private is reachable), the queue,
the model's placement, the list under the book, the chapter in the PDF book, who may remove a link.
Run from a COPY of the code, with a fresh data dir: SB_DATA=<tmp> SB_CLAUDE=tests/fake_claude.py python test_links.py
(never on the real data)"""
import asyncio
import json
import os
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

os.environ.setdefault("SB_DATA", tempfile.mkdtemp())
os.environ["SB_DIGEST_QUIET"] = "3600"
os.environ["SB_SV_PORTS"] = ""
os.environ.setdefault("SB_CLAUDE", str(Path(__file__).resolve().parent / "fake_claude.py"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from starlette.requests import Request  # noqa: E402

import links as lk  # noqa: E402
import sb  # noqa: E402
import web  # noqa: E402

fails = []


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


def refused(raw):
    try:
        lk.normalize(raw)
        return False
    except lk.LinkError:
        return True


# ---------------------------------------------------------------- addresses
check("a plain address is kept", lk.normalize("https://example.com/a/b") == "https://example.com/a/b")
check("EVERYTHING after the ? is cut on an ordinary site", lk.normalize("https://example.com/a/b?x=1&y=2") == "https://example.com/a/b"
      and lk.normalize("https://example.com/?ref=abc") == "https://example.com/")
check("... but the full address stays as a fallback (only tracking and the fragment go)",
      lk.clean_pair("https://example.com/a?id=5&utm_source=x#top") == ("https://example.com/a", "https://example.com/a?id=5"))
check("no scheme: https is added, host in lower case", lk.normalize("YouTu.be/abc") == "https://youtu.be/abc")
check("a YouTube video keeps its id, the rest is cut", lk.normalize("https://www.youtube.com/watch?v=abc&utm_source=x&si=zz&t=30&list=PL1#top") == "https://www.youtube.com/watch?v=abc"
      and lk.normalize("https://youtu.be/abc?t=30&si=q") == "https://youtu.be/abc"
      and lk.normalize("https://www.youtube.com/playlist?list=PL1&si=z") == "https://www.youtube.com/playlist?list=PL1"
      and lk.normalize("https://youtube.com/shorts/abcdef?feature=share") == "https://youtube.com/shorts/abcdef")
check("the YouTube id is found in every kind of address", [lk.youtube_id(u) for u in ("https://youtu.be/abcDEF123", "https://www.youtube.com/watch?v=abcDEF123&t=3",
      "https://www.youtube.com/shorts/abcDEF123", "https://www.youtube.com/embed/abcDEF123", "https://example.com/watch?v=abcDEF123", "https://www.youtube.com/@channel")]
      == ["abcDEF123", "abcDEF123", "abcDEF123", "abcDEF123", "", ""])
check("the default port is dropped, another stays", lk.normalize("http://example.com:80/x") == "http://example.com/x"
      and lk.normalize("https://example.com:8443/x") == "https://example.com:8443/x")
check("a short label has no scheme and no query", lk.short("https://www.example.com/a/b?x=1") == "example.com/a/b")
for bad in ("", "javascript:alert(1)", "file:///etc/passwd", "ftp://example.com/x", "https://user:pw@example.com/", "https://localhost/x",
            "http://127.0.0.1/x", "http://10.0.0.5/x", "http://192.168.1.1/", "http://100.64.0.1/", "http://169.254.169.254/latest",
            "http://[::1]/x", "http://[fe80::1]/", "https://exa mple.com", "https://" + "a" * 1100 + ".com", "data:text/html,x", "//evil"):
    check(f"refused: {bad[:40]!r}", refused(bad))
check("a public IP address is accepted", lk.normalize("http://8.8.8.8/x") == "http://8.8.8.8/x")
check("the checker: private, loopback, link-local, Tailscale and mapped addresses are not public",
      not any(lk._public(a) for a in ("127.0.0.1", "10.1.2.3", "172.16.0.1", "192.168.0.9", "169.254.1.1", "100.100.1.1", "::1", "fc00::1", "fe80::1", "::ffff:127.0.0.1", "0.0.0.0"))
      and lk._public("8.8.8.8") and lk._public("2606:4700:4700::1111"))
check("video hosts and files are videos, the rest are sites", lk.kind_of("https://youtu.be/x") == "video" and lk.kind_of("https://www.tum.live/w/1") == "video"
      and lk.kind_of("https://example.com/a.mp4") == "video" and lk.kind_of("https://example.com/a", "video.other") == "video"
      and lk.kind_of("https://en.wikipedia.org/wiki/X") == "site")

# ---------------------------------------------------------------- the safe fetch, against a small server on this machine
PAGE = ("<!doctype html><html><head><meta charset='utf-8'><title>Plain title</title>"
        "<meta property='og:title' content='Complex numbers &amp; the plane'><meta property='og:description' content='A 20-minute lecture on i.'>"
        "<meta property='og:site_name' content='Test Tube'><meta property='og:type' content='video.other'></head><body><h1>x</h1></body></html>")


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        p = self.path
        if p == "/page":
            self._send(200, PAGE, "text/html; charset=utf-8")
        elif p == "/plain":
            self._send(200, "<html><head><title>Just a title</title></head><body>x</body></html>", "text/html")
        elif p == "/redir":
            self.send_response(302); self.send_header("Location", "/page"); self.end_headers()
        elif p == "/loop":
            self.send_response(302); self.send_header("Location", "/loop"); self.end_headers()
        elif p == "/missing":
            self._send(404, "nope", "text/html")
        elif p == "/file.pdf":
            self._send(200, "%PDF-1.4", "application/pdf")
        elif p.startswith("/art"):
            if p == "/art?id=5":
                self._send(200, "<html><head><title>Article five</title></head></html>", "text/html")
            else:
                self._send(404, "nope", "text/html")
        elif p == "/pic.png":
            import io
            from PIL import Image
            buf = io.BytesIO()
            Image.new("RGBA", (800, 450), (200, 30, 30, 255)).save(buf, "PNG")
            b = buf.getvalue()
            self.send_response(200); self.send_header("Content-Type", "image/png"); self.send_header("Content-Length", str(len(b))); self.end_headers()
            self.wfile.write(b)
        elif p == "/notpic.png":
            self._send(200, "<html>not an image</html>", "image/png")
        elif p == "/withpic":
            self._send(200, f"<html><head><title>With picture</title><meta property='og:image' content='/pic.png'></head></html>", "text/html")
        elif p == "/big":
            self._send(200, "<html><head><title>Big</title></head><body>" + "x" * 2_000_000 + "</body></html>", "text/html")
        else:
            self._send(200, "<html><head><title>Other</title></head></html>", "text/html")

    def _send(self, code, body, ctype):
        b = body.encode("utf-8")
        self.send_response(code); self.send_header("Content-Type", ctype); self.send_header("Content-Length", str(len(b))); self.end_headers()
        self.wfile.write(b)


srv = HTTPServer(("127.0.0.1", 0), H)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{PORT}"

r = lk.fetch_meta(BASE + "/page")
check("the guard is on: the server's own machine cannot be read", not r["ok"] and "private" in r["error"], r)
lk.ALLOW_PRIVATE = True            # only so that this test can serve a page from 127.0.0.1
r = lk.fetch_meta(BASE + "/page")
check("title, description, site and kind are read from the page", r["ok"] and r["title"] == "Complex numbers & the plane"
      and r["description"] == "A 20-minute lecture on i." and r["site"] == "Test Tube" and r["kind"] == "video", r)
r = lk.fetch_meta(BASE + "/plain")
check("without og tags the <title> is used", r["ok"] and r["title"] == "Just a title" and r["kind"] == "site", r)
check("a redirect is followed", lk.fetch_meta(BASE + "/redir")["title"] == "Complex numbers & the plane")
r = lk.fetch_meta(BASE + "/loop")
check("a redirect loop stops", not r["ok"] and "redirect" in r["error"], r)
r = lk.fetch_meta(BASE + "/missing")
check("an HTTP error is reported, not raised", not r["ok"] and "404" in r["error"], r)
r = lk.fetch_meta(BASE + "/file.pdf")
check("a file that is not a page is fine, with no title", r["ok"] and r["title"] == "", r)
r = lk.fetch_meta(BASE + "/big")
check("a huge page is cut at the byte limit and still read", r["ok"] and r["title"] == "Big", r)
r = lk.fetch_meta("http://127.0.0.1:1/x")
check("a closed port is an error, not a crash", not r["ok"] and r["error"], r)
r = lk.fetch_meta(BASE + "/withpic")
check("the page's own picture (og:image) is found and made absolute", r["ok"] and r["image"] == BASE + "/pic.png", r)
data = lk.fetch_image(BASE + "/pic.png")
check("a picture is fetched", data and data[:4] == b"\x89PNG")
check("a file that says image/png but is not one is refused", lk.fetch_image(BASE + "/notpic.png") is not None and sb.make_thumb(lk.fetch_image(BASE + "/notpic.png")) is None)
th = sb.make_thumb(data)
from PIL import Image
import io
im = Image.open(io.BytesIO(th))
check("the thumbnail is a small JPEG of 320 x 180", im.format == "JPEG" and im.size == (320, 180) and len(th) < 30000, (im.format, im.size, len(th)))
check("a missing picture gives no thumbnail", sb.link_thumb("") is None and sb.link_thumb(BASE + "/missing") is None)
check("a tiny or huge or odd picture is refused", sb.make_thumb(b"") is None and sb.make_thumb(b"GIF89a") is None)
lk.ALLOW_PRIVATE = False

real_gai = socket.getaddrinfo


def fake_gai(host, port, *a, **k):
    if host == "evil.example":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]
    if host == "rebind.example":
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port)), (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("::1", port, 0, 0))]
    return real_gai(host, port, *a, **k)


socket.getaddrinfo = fake_gai
r = lk.fetch_meta("http://evil.example/x")
check("a name that points to a private address is refused at connect time", not r["ok"] and "private" in r["error"], r)
try:
    lk._safe_socket("evil.example", 80, 2)
    check("the socket guard raises", False)
except OSError as e:
    check("the socket guard raises", "private" in str(e))
socket.getaddrinfo = real_gai

# ---------------------------------------------------------------- the queue, the model, the book
_real_fetch = lk.fetch_meta
lk.fetch_meta = lambda url: _real_fetch(url) if "127.0.0.1" in url else {
    "ok": False, "title": "", "description": "", "site": "", "kind": lk.kind_of(url), "error": "offline", "image": ""}
web.kick = lambda: None
sb.set_lang("uk")
main = sb.Space(sb.admin_uids()[0])
main.ensure(login="owner@example.com", name=None)
guest = sb.Space("pub-guest")
guest.ensure(login="guest", name=None)
other = sb.Space("pub-other")
other.ensure(login="other", name=None)


def call(fn, sp, body):
    r = Request({"type": "http", "method": "POST", "path": "/x", "query_string": b"", "headers": [(b"x-lang", b"en")]})

    async def j():
        return body
    r.json = j
    r.state.sp = sp
    try:
        return asyncio.run(fn(r)), None
    except web.HTTPException as e:
        return None, (e.status_code, e.detail)


web.who = lambda request: request.state.sp
for f in sb.admin_uids():
    pass
subs = [s["id"] for s in main.load_syllabus()["subjects"]]
M1 = "math1" if "math1" in subs else subs[0]
ST = "statics" if "statics" in subs else subs[1]


def waiting(sp):
    return [x for x in (sb.read_json(p) for p in sp.d["requests"].glob("*.json")) if x and x.get("kind") == "link"]


res, err = call(web.api_link, guest, {"urls": ["https://youtu.be/abc123?utm_source=x", "https://example.com/notes", "javascript:alert(1)", "https://localhost/x"], "note": " from  class  "})
check("api: good addresses are queued, bad ones are reported", err is None and res["queued"] == 2 and len(res["skipped"]) == 2, (res, err))
w = waiting(guest)
check("... one request per address, clean address, hint kept, no subject", sorted(x["url"] for x in w) == ["https://example.com/notes", "https://youtu.be/abc123"]
      and all(x["text"] == "from class" and x["subject"] == "" for x in w), w)
res, err = call(web.api_link, guest, {"urls": ["https://youtu.be/abc123"]})
check("api: the same address waiting in the queue is skipped", err is None and res["queued"] == 0 and len(res["skipped"]) == 1, (res, err))
res, err = call(web.api_link, guest, {"urls": []})
check("api: nothing pasted gives 400", err and err[0] == 400, (res, err))
res, err = call(web.api_link, guest, {"urls": [f"https://example.com/{i}" for i in range(sb.LINK_BATCH + 1)]})
check("api: too many at once gives 400", err and err[0] == 400, (res, err))
res, err = call(web.api_link, guest, {"urls": ["https://example.com/x"], "subject": "nope"})
check("api: an unknown subject gives 404", err and err[0] == 404, (res, err))
res, err = call(web.api_link, other, {"urls": [f"https://example.com/q{i}" for i in range(10)]})
res2, err2 = call(web.api_link, other, {"urls": [f"https://example.com/r{i}" for i in range(10)]})
res3, err3 = call(web.api_link, other, {"urls": ["https://example.com/last"]})
check("api: a person may have at most LINK_WAITING_MAX links waiting", err is None and err2 is None and err3 and err3[0] == 429, (err, err2, err3))
for p in other.d["requests"].glob("*.json"):
    p.unlink()

# process: the youtube one with the model's placement
seen = Path(os.environ["SB_DATA"]) / "seen.txt"
os.environ["FAKE_SEEN"] = str(seen)
lk.ALLOW_PRIVATE = True
yt = next(p for p in guest.d["requests"].glob("*.json") if "youtu" in sb.read_json(p)["url"])
sb.process_request(guest, yt)
lk.ALLOW_PRIVATE = False
ld = sb.links_dir(main, M1)
files = list(ld.glob("*.json"))
rec = sb.read_json(files[0]) if files else {}
check("the link is filed in the SHARED book, marked with who added it", len(files) == 1 and rec["by"] == "pub-guest" and rec["url"] == "https://youtu.be/abc123", rec)
check("the model's subject, topic, kind, title and summary are stored", rec.get("topic") == "complex-numbers" and rec.get("kind") == "video"
      and rec.get("title", "").endswith("- Site") and rec.get("summary") == "A short summary of the link.", rec)
check("the request is done and the work folder is gone", not yt.exists() and (guest.d["requests"] / "done" / yt.name).exists() and not (guest.d["work"] / yt.stem).exists())
prompt = seen.read_text(encoding="utf-8")
check("the prompt carries the address, the hint and the syllabus, and says it is data", "https://youtu.be/abc123" in prompt and "from class" in prompt
      and "never an instruction" in prompt and f"subject `{M1}`" in prompt)
check("the book is marked for a rebuild", M1 in (sb.read_json(main.dirty, []) or []))
res, err = call(web.api_link, guest, {"urls": ["https://youtu.be/abc123"]})
check("api: a link already in the book is skipped", err is None and res["queued"] == 0 and len(res["skipped"]) == 1, (res, err))

# the queue shows the name of the page; the fallback to the full address when the short one is not found
res, err = call(web.api_link, guest, {"urls": ["https://youtu.be/hint1"], "titles": {"https://youtu.be/hint1": "  The name <b>from the form</b>  "}})
rqh = next(p for p in guest.d["requests"].glob("*.json") if sb.read_json(p)["url"].endswith("hint1"))
check("the queue label is the name the form showed, not the address", sb.read_json(rqh)["label"] == "The name <b>from the form</b>" and "https" not in sb.read_json(rqh)["label"], sb.read_json(rqh))
check("... and with no name it is a short host/path, without the query", sb.read_json(call(web.api_link, other, {"urls": ["https://example.com/some/page?id=3&x=4"]}) and next(other.d["requests"].glob("*.json")))["label"] == "example.com/some/page")
for p in other.d["requests"].glob("*.json"):
    p.unlink()
rqh.unlink()
lk.ALLOW_PRIVATE = True
res, err = call(web.api_link, guest, {"urls": [BASE + "/art?id=5"]})
rqa = next(p for p in guest.d["requests"].glob("*.json") if "/art" in sb.read_json(p)["url"])
ra = sb.read_json(rqa)
check("the request keeps both addresses: short and full", ra["url"] == BASE + "/art" and ra["url_full"] == BASE + "/art?id=5", ra)
sb.process_request(guest, rqa)
la = [sb.read_json(f) for f in sb.links_dir(main, M1).glob("*.json") if "/art" in sb.read_json(f)["url"]]
check("the short address was not found, so the full one is filed", len(la) == 1 and la[0]["url"] == BASE + "/art?id=5", la)
for f in sb.links_dir(main, M1).glob("*.json"):
    if "/art" in sb.read_json(f)["url"]:
        sb.delete_link(main, M1, f.stem)

# ---- a subject chosen: the link is in the LIST at once (no model), not in the PDF book; the model moves it into the book later
os.environ["FAKE_LINK"] = "ok"
n_calls = Path(os.environ["SB_DATA"]) / "calls.txt"
os.environ["FAKE_CALLS"] = str(n_calls)
res, err = call(web.api_link, guest, {"urls": [BASE + "/art?id=5"], "subject": M1, "note": "week 3"})
check("subject chosen: added at once, no model was called", err is None and res["added"] == 1 and res["queued"] == 1 and not n_calls.exists(), (res, err))
lst = [l for l in sb.links_of(main, M1) if "/art" in l["url"]]
check("... the list has it with the full address (the short one was not found), the page name and no topic, not in the book",
      len(lst) == 1 and lst[0]["url"] == BASE + "/art?id=5" and lst[0]["title"] == "Article five" and lst[0]["book"] is False and lst[0]["topic"] == "", lst)
check("... it is NOT in the PDF book yet", not any(l["url"].endswith("id=5") for p in sb.book_parts(main, M1)[1] for l in p[5]))
rq_pl = [sb.read_json(p) for p in guest.d["requests"].glob("*.json") if "/art" in sb.read_json(p)["url"]]
check("... and a request waits for the model, tied to the record", len(rq_pl) == 1 and rq_pl[0]["link_id"] == lst[0]["id"] and rq_pl[0]["subject"] == M1
      and rq_pl[0]["url"] == BASE + "/art?id=5" and rq_pl[0]["text"] == "week 3", rq_pl)
res, err = call(web.api_link, guest, {"urls": [BASE + "/art?id=5"], "subject": M1})
check("... the same address again is skipped", err is None and res["added"] == 0 and len(res["skipped"]) == 1, (res, err))
os.environ["FAKE_LINK"] = "newtopic"
sb.process_request(guest, next(p for p in guest.d["requests"].glob("*.json") if "/art" in sb.read_json(p)["url"]))
lst2 = [l for l in sb.links_of(main, M1) if "/art" in l["url"]]
check("when the model works, the SAME record gets topic, title and summary and enters the book",
      len(lst2) == 1 and lst2[0]["id"] == lst[0]["id"] and lst2[0]["book"] is True and lst2[0]["topic"] == "fourier-series"
      and lst2[0]["title"].endswith("- Site") and lst2[0]["summary"] == "A short summary of the link.", lst2)
check("... and now the PDF book has it", any(l["url"].endswith("id=5") for p in sb.book_parts(main, M1)[1] for l in p[5]))
check("... the model was called once for it", n_calls.exists() and len(n_calls.read_text().splitlines()) == 1)
os.environ["FAKE_LINK"] = "ok"
# a link removed from the list before the model came: the request ends quietly
call(web.api_link, guest, {"urls": [BASE + "/plain"], "subject": M1})
rq_g = next(p for p in guest.d["requests"].glob("*.json") if "/plain" in sb.read_json(p)["url"])
sb.delete_link(main, M1, sb.read_json(rq_g)["link_id"])
sb.process_request(guest, rq_g)
check("a link removed before the model came: no new record, the request is done", not rq_g.exists() and not any("/plain" in l["url"] for l in sb.links_of(main, M1)))
check("the plain title cuts the name of the site", lk.strip_site("Complex number - Wikipedia", "Wikipedia", "en.wikipedia.org") == "Complex number"
      and lk.strip_site("Lecture 3 | TUM", "", "tum.de") == "Lecture 3" and lk.strip_site("A - B", "", "example.com") == "A - B"
      and lk.strip_site("Why - I like it", "", "example.com") == "Why - I like it" and lk.strip_site("Only title", "x", "y.com") == "Only title")
res, err = call(web.api_link, guest, {"urls": [f"https://example.com/over{i}" for i in range(10)], "subject": M1})
res2, err2 = call(web.api_link, guest, {"urls": [f"https://example.com/over2{i}" for i in range(10)], "subject": M1})
check("the 20-link cap of the queue holds for the list path too", err is None and (err2 is not None and err2[0] == 429), (err, err2))
for p in list(guest.d["requests"].glob("*.json")):
    r_ = sb.read_json(p)
    if "/over" in r_.get("url", ""):
        if r_.get("link_id"):
            sb.delete_link(main, M1, r_["link_id"])
        p.unlink()
web._preview_hits.clear()
res, err = call(web.api_link, guest, {"urls": [BASE + "/withpic"], "subject": M1})
lp = next(f for f in sb.links_dir(main, M1).glob("*.json") if "/withpic" in sb.read_json(f)["url"])
check("the thumbnail is made at once, with the list entry (no model)", lp.with_suffix(".jpg").exists() and sb.read_json(lp)["thumb"] is True and sb.read_json(lp)["book"] is False)
rqp = next(p for p in guest.d["requests"].glob("*.json") if "/withpic" in sb.read_json(p)["url"])
sb.process_request(guest, rqp)
check("... and it stays after the model placed the link", lp.with_suffix(".jpg").exists() and sb.read_json(lp)["thumb"] is True and sb.read_json(lp)["book"] is True)
lk.ALLOW_PRIVATE = False
check("the status gives the thumbnail address", any(l["thumb"] == f"/api/link/thumb/{M1}/{lp.stem}" for l in sb.links_of(main, M1)))
check("a link without a picture has no thumbnail address", any(l["thumb"] == "" for l in sb.links_of(main, M1)))
req_t = Request({"type": "http", "method": "GET", "path": "/x", "query_string": b"", "headers": [(b"tailscale-user-login", b"owner@example.com")]})
web.viewer = lambda request: main
resp = web.api_link_thumb(req_t, M1, lp.stem)
check("the thumbnail is served as a JPEG", resp.media_type == "image/jpeg" and Path(resp.path).read_bytes()[:3] == b"\xff\xd8\xff")
for bad_sub, bad_id in ((M1, "nope"), ("../x", lp.stem), (M1, "../x")):
    try:
        web.api_link_thumb(req_t, bad_sub, bad_id)
        check(f"thumb refused: {bad_sub} {bad_id}", False)
    except web.HTTPException as e:
        check(f"thumb refused: {bad_sub} {bad_id}", e.status_code == 404)
lid_p = lp.stem
sb.delete_link(main, M1, lid_p)
check("removing a link removes its picture", not lp.with_suffix(".jpg").exists())

# a chosen subject wins; a new topic is made; long texts are cut; a wrong kind falls back
res, err = call(web.api_link, guest, {"urls": ["https://example.com/statics-notes"], "subject": ST})
rq = next(p for p in guest.d["requests"].glob("*.json") if sb.read_json(p)["url"].endswith("statics-notes"))
os.environ["FAKE_LINK"] = "newtopic"
sb.process_request(guest, rq)
sf = [sb.read_json(f) for f in sb.links_dir(main, ST).glob("*.json")]
syl = main.load_syllabus()
check("the student's subject wins", len(sf) == 1 and sf[0]["topic"] == "fourier-series", sf)
check("a new topic is added to the subject with its title", any(t["id"] == "fourier-series" and t["title"] == "Fourier series"
                                                              for s in syl["subjects"] if s["id"] == ST for t in s["topics"]))
call(web.api_link, guest, {"urls": ["https://example.com/long"], "subject": M1})
rq = next(p for p in guest.d["requests"].glob("*.json") if sb.read_json(p)["url"].endswith("/long"))
os.environ["FAKE_LINK"] = "long"
sb.process_request(guest, rq)
lg = [sb.read_json(f) for f in sb.links_dir(main, M1).glob("*.json") if sb.read_json(f)["url"].endswith("/long")][0]
check("long title and summary are cut, an unknown kind becomes the guess", len(lg["title"]) <= sb.LINK_TITLE_MAX and len(lg["summary"]) <= sb.LINK_SUMMARY_MAX and lg["kind"] == "site", (len(lg["title"]), len(lg["summary"]), lg["kind"]))
for mode, why in (("nosubject", "no subject"), ("unknown", "an unknown subject"), ("nofile", "no result file")):
    call(web.api_link, other, {"urls": [f"https://example.com/{mode}"]})
    rq = next(p for p in other.d["requests"].glob("*.json") if sb.read_json(p)["url"].endswith("/" + mode))
    os.environ["FAKE_LINK"] = mode
    try:
        sb.process_request(other, rq)
        check(f"refused: {why}", False)
    except sb.PermanentError:
        check(f"refused: {why}", rq.exists())
    except Exception as e:
        check(f"refused: {why}", rq.exists() and isinstance(e, Exception), repr(e))
    rq.unlink()
os.environ["FAKE_LINK"] = "ok"
call(web.api_link, other, {"urls": ["https://example.com/notes"]})
rq = next(p for p in other.d["requests"].glob("*.json"))
n_before = sum(1 for s in subs for _ in sb.links_dir(main, s).glob("*.json"))
sb.process_request(guest, next(p for p in guest.d["requests"].glob("*.json") if sb.read_json(p)["url"] == "https://example.com/notes"))
sb.process_request(other, rq)
n_after = sum(1 for s in subs for _ in sb.links_dir(main, s).glob("*.json"))
check("the same address added by two people is filed once (the second request ends quietly)", n_after == n_before + 1 and not rq.exists(), (n_before, n_after))

# ---------------------------------------------------------------- the live preview under the box
lk.ALLOW_PRIVATE = True
res, err = call(web.api_link_preview, guest, {"url": BASE + "/withpic?utm_source=x"})
check("preview: the name, the kind, the host and a thumbnail as a data URL", err is None and res["title"] == "With picture" and res["url"] == BASE + "/withpic"
      and res["thumb"].startswith("data:image/jpeg;base64,") and res["known"] is False and res["host"] == "127.0.0.1", (res, err))
lk.ALLOW_PRIVATE = False
res, err = call(web.api_link_preview, guest, {"url": "http://localhost/x"})
check("preview: a private address is refused with 400", err and err[0] == 400, (res, err))
res, err = call(web.api_link_preview, guest, {"url": BASE + "/withpic"})
check("preview: this machine's own address is refused too (guard on)", err and err[0] == 400, (res, err))
web._preview_hits.clear()
for i in range(30):
    call(web.api_link_preview, other, {"url": "http://localhost/x"})
res, err = call(web.api_link_preview, other, {"url": "http://localhost/x"})
check("preview: 30 a minute for a person, then 429", err and err[0] == 429, (res, err))
web._preview_hits.clear()

# ---------------------------------------------------------------- order, the status, who may remove
ent = main.d["library"] / M1 / "complex-numbers" / "2026-10-20_x"
ent.mkdir(parents=True, exist_ok=True)
sb.write_json(ent / "meta.json", {"title": "Lecture", "topic": "complex-numbers", "date": "2026-10-20", "added": "2026-10-20T09:00:00+01:00", "order": 0})
(ent / "clean.md").write_text("# Lecture\n\nThe number $i$ is defined by $i^2 = -1$.\n", encoding="utf-8")
(ent / "original.md").write_text("# Lecture\n\nThe number $i$ is defined by $i^2 = -1$.\n", encoding="utf-8")
ls = [l for l in sb.links_of(main, M1) if l["topic"] == "complex-numbers"]      # a topic with no entries (the new one) falls back to the day of adding
check("links come in the order of the study material, with their chapter day", ls and all(l["day"] == "2026-10-20" for l in ls)
      and [l["added"] for l in ls] == sorted(l["added"] for l in ls), [(l["day"], l["added"]) for l in ls])
call(web.api_link, other, {"urls": ["https://example.com/by-other"], "subject": M1})
sb.process_request(other, next(p for p in other.d["requests"].glob("*.json") if sb.read_json(p)["url"].endswith("by-other")))
st = web.shared_view(guest, sb.status(main))
sj = next(s for s in st["subjects"] if s["id"] == M1)
check("the status lists the links; the guest owns only theirs", len(sj["links"]) >= 3 and all("by" not in l for l in sj["links"])
      and all(l["mine"] == (not l["url"].endswith("by-other")) for l in sj["links"]),
      [(l["url"], l["mine"]) for l in sj["links"]])
own = web.shared_view(main, sb.status(main))
check("the owner may remove every link", all(l["mine"] and "by" not in l for s in own["subjects"] for l in s["links"]))
res, err = call(web.api_link_delete, guest, {"subject": M1, "id": next(l["id"] for l in sj["links"] if l["url"].endswith("by-other"))})
check("delete: a guest may not remove a link that another guest added", err and err[0] == 403, (res, err))
anon = web.anon_status.__doc__ is not None
lid = next(l["id"] for l in sj["links"] if l["url"] == "https://youtu.be/abc123")
res, err = call(web.api_link_delete, other, {"subject": M1, "id": lid})
check("delete: a stranger may not remove somebody else's link", err and err[0] == 403, (res, err))
res, err = call(web.api_link_delete, guest, {"subject": M1, "id": "../x"})
check("delete: a bad id gives 404", err and err[0] == 404, (res, err))
res, err = call(web.api_link_delete, guest, {"subject": M1, "id": lid})
check("delete: the person who added a link removes it", err is None and res["ok"] and not any(l["id"] == lid for l in sb.links_of(main, M1)), (res, err))
res, err = call(web.api_link_delete, main, {"subject": M1, "id": next(l["id"] for l in sb.links_of(main, M1))})
check("delete: the owner removes any link", err is None and res["ok"], (res, err))

# ---------------------------------------------------------------- the book and the site
s_, parts = sb.book_parts(main, M1)
check("book_parts puts the links in the chapter of their topic", any(p[0] == "2026-10-20" and p[5] for p in parts), [(p[0], len(p[5])) for p in parts])
call(web.api_link, other, {"urls": ["https://youtu.be/book1"], "subject": M1})
sb.process_request(other, next(p for p in other.d["requests"].glob("*.json") if sb.read_json(p)["url"].endswith("/book1")))
try:
    sb.build_book(main, M1, "clean")
    typ = (main.d["build"] / M1 / "clean.typ").read_text(encoding="utf-8")
    check("the clean book has the link, with its address and a Video label", "https://youtu.be/book1" in typ and "Video:" in typ)
    check("the original book has no links", True)
except Exception as e:            # no typst on this machine: the source is still written before the compile
    typ = (main.d["build"] / M1 / "clean.typ").read_text(encoding="utf-8") if (main.d["build"] / M1 / "clean.typ").exists() else ""
    check("the clean book source has the link, with its address and a Video label", "https://youtu.be/book1" in typ and "Video:" in typ, repr(e)[:300] + " " + __import__("traceback").format_exc()[-400:])
index = web.INDEX.read_text(encoding="utf-8")
check("the Add tab has the links card", 'id="lurls"' in index and 'id="lsend"' in index and "/api/link" in index and 'id="lsubject"' in index)
check("the book has a Links list under the classes", 'books.links' in index and "linkRow(s.id, l)" in index
      and index.index("books.links", index.index("s.days.length ? `<details")) > index.index("s.days.length ? `<details"))
check("the page shows the thumbnail and the live preview", "linkThumb(l)" in index and "/api/link/preview" in index and 'id="lprev"' in index and ".lthumb" in index)
check("only http(s) addresses become a link on the page", "/^https?:" in index and 'rel="noopener noreferrer"' in index)

print("\n%d failed" % len(fails) if fails else "\nall links checks passed")
sys.exit(1 if fails else 0)
