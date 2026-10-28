"""Offline test of the PUBLIC side (the Cloudflare tunnel): sign-up/sign-in, the human check, sessions, limits, isolation.
Drives the ASGI app directly (no httpx on the server).
Run from a COPY of the code: SB_DATA=<tmp> python test_public.py   (never against the real data)"""
import asyncio
import http.server
import json
import os
import stat
import sys
import threading

os.environ["SB_PUBLIC"] = "1"

import accounts  # noqa: E402
import sb  # noqa: E402
import web  # noqa: E402

web.kick = lambda: None          # never wake a real worker from a test
fails = []
_n = [0]
HOST = "t.example"
OWNER = "owner@example.com"


def check(name, cond, extra=""):
    print(("ok   " if cond else "FAIL ") + name + (f"  [{extra}]" if extra and not cond else ""))
    if not cond:
        fails.append(name)


async def _asgi(method, path, headers, body):
    scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": method, "scheme": "https",
             "path": path.split("?")[0], "raw_path": path.encode(), "query_string": (path.split("?") + [""])[1].encode(),
             "headers": headers, "client": ("10.0.0.7", 5555), "server": (HOST, 443)}
    sent, state = [], {"done": False}

    async def receive():
        if not state["done"]:
            state["done"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.sleep(3600)

    async def send(m):
        sent.append(m)
    await web.app(scope, receive, send)
    start = next(m for m in sent if m["type"] == "http.response.start")
    data = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start["status"], [(k.decode().lower(), v.decode()) for k, v in start["headers"]], data


def call(method, path, body=None, cookie=None, origin=True, ip=None, lang="en", **headers):
    h = {"host": HOST, "x-lang": lang}
    if method == "POST":
        h["content-type"] = "application/json"
        if origin is True:
            h["origin"] = f"https://{HOST}"
        elif origin:
            h["origin"] = origin
    if cookie:
        h["cookie"] = f"{web.COOKIE}={cookie}"
    if ip:
        h["cf-connecting-ip"] = ip
    h.update({k.replace("_", "-"): v for k, v in headers.items()})
    raw = json.dumps(body).encode() if body is not None else b""
    st, hdrs, data = asyncio.run(_asgi(method, path, [(k.encode(), str(v).encode()) for k, v in h.items()], raw))
    try:
        js = json.loads(data)
    except ValueError:
        js = None
    return st, hdrs, js, data


def anon(**kw):
    """The request has no valid session: the server answers with the read-only main book, not with a person's space."""
    st, _, js, _ = call("GET", "/api/status", ip=fresh_ip(), **kw)
    return st == 200 and isinstance(js, dict) and js.get("anon") is True and js["user"]["login"] == ""


def fresh_ip():
    _n[0] += 1
    return f"11.0.{_n[0] // 250}.{_n[0] % 250 + 1}"


def pow_():
    st, _, ch, _ = call("GET", "/api/auth/challenge", ip=fresh_ip())
    assert st == 200
    return ch | {"n": accounts.solve(ch)}


def cookie_of(hdrs):
    for k, v in hdrs:
        if k == "set-cookie" and v.startswith(web.COOKIE + "="):
            return v
    return None


def token_of(hdrs):
    c = cookie_of(hdrs)
    return c.split(";")[0].split("=", 1)[1] if c else None


def register(name, pw="correct horse battery", ip=None, **kw):
    return call("POST", "/api/auth/register", {"username": name, "password": pw, "pow": pow_()}, ip=ip or fresh_ip(), **kw)


def login(name, pw, ip=None):
    return call("POST", "/api/auth/login", {"username": name, "password": pw, "pow": pow_()}, ip=ip or fresh_ip())


# --- the page and the front door ------------------------------------------------------------------------------------
st, hd, _, data = call("GET", "/")
hd = dict(hd)
check("the page is served to anybody", st == 200 and b"<html" in data)
check("the page carries a strict CSP, no framing, nosniff", "default-src 'none'" in hd.get("content-security-policy", "")
      and hd.get("x-frame-options") == "DENY" and hd.get("x-content-type-options") == "nosniff", str(hd))
check("config says this is the public instance", call("GET", "/api/auth/config")[2]["public"] is True)
sb.ensure_global()
sb.Space(sb.slugify(OWNER, 60)).ensure(login=OWNER, name=None)       # the main book's owner exists
check("/api/status without a session is the read-only main book", anon())
check("an unknown path is 404", call("GET", "/api/auth/challenge-x")[0] == 404)
for path in ("/api/upload", "/api/engine", "/api/suggest", "/api/claude/login", "/api/entry/date", "/api/request"):
    check(f"POST {path} needs sign-in", call("POST", path, {})[0] in (401, 422), path)

# --- forged Tailscale identity is worthless here ---------------------------------------------------------------------------
st, _, js, _ = call("GET", "/api/status", tailscale_user_login=OWNER, ip=fresh_ip())
check("a forged Tailscale-User-Login does not sign anybody in (still the anonymous view)", st == 200 and js.get("anon") is True and not js["is_admin"], str(js)[:200])
st, _, _, _ = call("GET", "/api/admin/accounts", tailscale_user_login=OWNER)
check("/api/admin is closed on the public instance", st == 403, str(st))
check("the admin list of merges too", call("GET", "/api/admin/merge", tailscale_user_login=OWNER)[0] == 403)

# --- human check -------------------------------------------------------------------------------------------------------------
st, _, js, _ = call("POST", "/api/auth/register", {"username": "alice", "password": "correct horse battery"}, ip=fresh_ip())
check("sign-up without the human check is refused", st == 400 and "human" in js["detail"], str(js))
bad = pow_() | {"n": 0}
st, _, js, _ = call("POST", "/api/auth/register", {"username": "alice", "password": "correct horse battery", "pow": bad}, ip=fresh_ip())
check("a wrong puzzle answer is refused", st == 400, str(js))
good = pow_()
good_bad_sig = dict(good, sig="0" * 64)
check("a forged puzzle is refused", call("POST", "/api/auth/register", {"username": "alice", "password": "correct horse battery", "pow": good_bad_sig}, ip=fresh_ip())[0] == 400)
call("POST", "/api/auth/register", {"username": "x", "password": "p", "pow": good}, ip=fresh_ip())          # consumes the puzzle
st, _, js, _ = call("POST", "/api/auth/register", {"username": "alice", "password": "correct horse battery", "pow": good}, ip=fresh_ip())
check("a solved puzzle cannot be used twice", st == 400, str(js))
check("an expired puzzle is refused", not accounts.check_pow(web.KEY, accounts.make_challenge(web.KEY, now=1000) | {"n": 0}, 0, now=10 ** 10))

# --- sign-up --------------------------------------------------------------------------------------------------------------------
for name, pw, why in (("Al", "correct horse battery", "short name"), ("bad name!", "correct horse battery", "symbols"),
                      ("alice", "short", "short password"), ("alice", "aaaaaaaaaaaa", "trivial password"), ("alice", "alicealice", "password = name")):
    st, _, js, _ = register(name, pw)
    check(f"sign-up refused: {why}", st == 400, f"{st} {js}")
st, hd, js, _ = register("Alice")
c = cookie_of(hd) or ""
check("sign-up works (names are case-folded)", st == 200 and js["username"] == "alice")
check("the session cookie is __Host-, HttpOnly, Secure, SameSite=Strict, Path=/",
      c.startswith("__Host-sb=") and "HttpOnly" in c and "Secure" in c and "SameSite=strict" in c.replace("Strict", "strict") and "Path=/" in c and "Domain" not in c, c)
alice = token_of(hd)
st, _, js, _ = call("GET", "/api/status", cookie=alice)
check("the cookie opens the person's own space", st == 200 and js["user"]["login"] == "alice" and js["public"] is True and js["is_admin"] is False, str(js)[:200])
check("their space is pub-alice", (sb.DATA / "users" / "pub-alice").is_dir() and js["user"]["uid"] == "pub-alice")
st, _, js, _ = register("alice", lang="en")
check("a taken name is refused (409, English)", st == 409 and "taken" in js["detail"], str(js))
st, _, js, _ = register("alice", lang="uk")
check("... and in Ukrainian", st == 409 and "зайняте" in js["detail"], str(js))
raw = (sb.STATE / "accounts.json").read_text(encoding="utf-8")
check("no plaintext password in accounts.json", "correct horse battery" not in raw and '"hash"' in raw and '"salt"' in raw)
check("accounts.json and the signing key are 0600",
      stat.S_IMODE((sb.STATE / "accounts.json").stat().st_mode) == 0o600 and stat.S_IMODE((sb.STATE / "session.key").stat().st_mode) == 0o600)
web.MAX_PUBLIC_USERS = 1
st, _, js, _ = register("carol")
check("the account cap stops sign-ups", st == 409 and "limit" in js["detail"], str(js))
web.MAX_PUBLIC_USERS = 25

# --- a public name can never become the owner ----------------------------------------------------------------------------------------
st, hd, js, _ = register("owner-example-com")
owner_clone = token_of(hd)
st2, _, js2, _ = call("GET", "/api/status", cookie=owner_clone)
check("a public account named like the owner lives in its own pub- space and is not an admin",
      st == 200 and js2["user"]["uid"] == "pub-owner-example-com" and js2["is_admin"] is False and "pub-owner-example-com" not in sb.admin_uids())
check("no public uid is ever an admin", all(not u.startswith("pub-") for u in sb.admin_uids()))
st, _, _, _ = call("GET", "/api/admin/accounts", cookie=owner_clone)
check("... and it cannot open the admin tools", st == 403)

# --- sign-in, lockout ------------------------------------------------------------------------------------------------------------------------
st, hd, js, _ = login("alice", "correct horse battery", ip="9.9.9.1")
check("sign-in works", st == 200 and token_of(hd))
st1, _, j1, _ = login("alice", "wrong password!!", ip="9.9.9.2")
st2, _, j2, _ = login("nobody", "wrong password!!", ip="9.9.9.2")
check("a wrong password and an unknown name look the same", st1 == st2 == 401 and j1 == j2, f"{j1} {j2}")
for i in range(8):
    login("alice", f"wrong password {i}", ip=f"9.9.8.{i}")
st, _, js, _ = login("alice", "correct horse battery", ip="9.9.7.1")
check("8 failures lock the name for a while, even with the right password", st == 429, f"{st} {js}")
web.LIM.hits.clear()
st, _, _, _ = login("alice", "correct horse battery", ip="9.9.7.2")
check("... and it recovers when the window passes", st == 200)
for i in range(12):
    login(f"ghost{i}", "wrong password", ip="8.8.8.8")
st, _, _, _ = login("alice", "correct horse battery", ip="8.8.8.8")
check("one address failing 12 times is locked out too", st == 429)
web.LIM.hits.clear()
for i in range(8):
    st, *_ = register(f"user{i}x", ip="7.7.7.7")
st, _, _, _ = register("lastone", ip="7.7.7.7")
check("sign-ups per address are limited", st == 429, str(st))
web.LIM.hits.clear()
web.MAX_PUBLIC_USERS = 25

# --- sessions ----------------------------------------------------------------------------------------------------------------------------------
tampered = alice[:-3] + ("A" if alice[-3] != "A" else "B") + alice[-2:]
check("a tampered cookie is refused", anon(cookie=tampered))
check("a cookie signed with another key is refused", anon(cookie=web.ACC.make_session(b"x" * 32, "alice", 1)))
check("an expired cookie is refused", anon(cookie=web.ACC.make_session(web.KEY, "alice", 1, now=1000)))
check("a cookie for a nonexistent account is refused", anon(cookie=web.ACC.make_session(web.KEY, "ghost", 1)))
st, hd, _, _ = call("POST", "/api/auth/logout", {}, cookie=alice)
check("logout clears the cookie", st == 200 and "Max-Age=0" in (cookie_of(hd) or "") or "expires" in (cookie_of(hd) or "").lower())

# --- changing one's own password -----------------------------------------------------------------------------------------------
st, hd, js, _ = call("POST", "/api/auth/password", {"old": "wrong password!!", "new": "another long password"}, cookie=alice)
check("changing the password needs the old one", st == 401, str(js))
st, hd, js, _ = call("POST", "/api/auth/password", {"old": "correct horse battery", "new": "short"}, cookie=alice)
check("... and a decent new one", st == 400, str(js))
st, hd, js, _ = call("POST", "/api/auth/password", {"old": "correct horse battery", "new": "another long password"}, cookie=alice)
new_cookie = token_of(hd)
check("a change gives a fresh session and kills the old one", st == 200 and new_cookie and anon(cookie=alice)
      and call("GET", "/api/status", cookie=new_cookie)[0] == 200)
check("only the new password works now", login("alice", "correct horse battery")[0] == 401 and login("alice", "another long password")[0] == 200)
alice = new_cookie
check("changing the password needs a session", call("POST", "/api/auth/password", {"old": "a", "new": "b"})[0] == 401)

# --- CSRF ---------------------------------------------------------------------------------------------------------------------------------------
check("a write without Origin is refused", call("POST", "/api/engine", {"engine": "claude"}, cookie=alice, origin=False)[0] == 403)
check("a write from another site is refused", call("POST", "/api/engine", {"engine": "claude"}, cookie=alice, origin="https://evil.example")[0] == 403)
check("a write from the page itself gets through", call("POST", "/api/engine", {"engine": "claude"}, cookie=alice)[0] == 200)
st, _, js, _ = call("POST", "/api/engine", {"engine": "x" * 2_000_000}, cookie=alice, **{"content-length": "2000000"})
check("a huge JSON body is refused before it is read", st == 413, str(st))

# --- isolation ---------------------------------------------------------------------------------------------------------------------------------------
asp, bsp = sb.Space("pub-alice"), sb.Space("pub-owner-example-com")
(asp.d["books"] / "math1").mkdir(parents=True, exist_ok=True)
(asp.d["books"] / "math1" / "clean.pdf").write_bytes(b"%PDF-1.4 alice")
alice = token_of(login("alice", "another long password")[1])
check("a person does not get their own book in the shared view (the main book or 404, never alice's file)",
      call("GET", "/books/math1/clean.pdf", cookie=alice)[3] != b"%PDF-1.4 alice")
check("another person gets 404, never alice's file", call("GET", "/books/math1/clean.pdf", cookie=owner_clone)[0] == 404)
check("path tricks in the subject do not leave the space", call("GET", "/books/..%2f..%2fx/clean.pdf", cookie=alice)[0] in (404, 422))

# --- upload caps -----------------------------------------------------------------------------------------------------------------------------------------
for i in range(web.PUBLIC_MAX_QUEUED):
    (asp.d["inbox"] / f"fake{i}").mkdir()
    (asp.d["inbox"] / f"fake{i}" / "submission.json").write_text("{}")
boundary = "X"
multipart = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"files\"; filename=\"a.pdf\"\r\n\r\n%PDF-1.4\r\n--{boundary}--\r\n").encode()


def upload(cookie):
    h = [(b"host", HOST.encode()), (b"origin", f"https://{HOST}".encode()), (b"cookie", f"{web.COOKIE}={cookie}".encode()),
         (b"content-type", f"multipart/form-data; boundary={boundary}".encode()), (b"x-lang", b"en")]
    st, _, data = asyncio.run(_asgi("POST", "/api/upload", h, multipart))
    return st, data


st, data = upload(alice)
check("too many queued uploads -> 429", st == 429, f"{st} {data[:100]}")
for i in range(web.PUBLIC_MAX_QUEUED):
    sb.shutil.rmtree(asp.d["inbox"] / f"fake{i}")
(asp.d["library"] / "x.bin").write_bytes(b"0" * 5000)
web.PUBLIC_QUOTA = 1000
st, data = upload(alice)
check("a full space -> 413", st == 413, f"{st} {data[:100]}")
web.PUBLIC_QUOTA = 3 * 1024 ** 3
check("an upload is capped under Cloudflare's 100 MB", web.MAX_UPLOAD < 100 * 1024 ** 2)

# --- Turnstile (Cloudflare's own human check) when keys are configured --------------------------------------------------------------------------------------
class Verify(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["content-length"])).decode()
        Verify.seen.append(body)
        ok = "response=good-token" in body and "secret=sekret" in body
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({"success": ok}).encode())


srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Verify)
threading.Thread(target=srv.serve_forever, daemon=True).start()
web.TURNSTILE_SECRET, web.TURNSTILE_SITEKEY, web.TURNSTILE_URL = "sekret", "1x-site", f"http://127.0.0.1:{srv.server_address[1]}/"
check("config hands the page the Turnstile site key", call("GET", "/api/auth/config")[2]["turnstile"] == "1x-site")
st, _, js, _ = call("POST", "/api/auth/register", {"username": "dave", "password": "correct horse battery", "turnstile": "bad-token"})
check("Turnstile: a rejected token is refused", st == 400, str(js))
st, _, js, _ = call("POST", "/api/auth/register", {"username": "dave", "password": "correct horse battery"})
check("Turnstile: no token is refused (the puzzle is no substitute)", st == 400)
st, _, js, _ = call("POST", "/api/auth/register", {"username": "dave", "password": "correct horse battery", "pow": pow_(), "turnstile": ""})
check("Turnstile: a puzzle alone does not pass", st == 400)
st, _, js, _ = call("POST", "/api/auth/register", {"username": "dave", "password": "correct horse battery", "turnstile": "good-token"}, ip="5.5.5.5")
check("Turnstile: an accepted token passes", st == 200, str(js))
check("Turnstile: the visitor's address is passed to Cloudflare", any("remoteip=5.5.5.5" in s for s in Verify.seen), str(Verify.seen))
srv.shutdown()
web.TURNSTILE_URL = "http://127.0.0.1:9/"
check("Turnstile unreachable -> refused, not waved through", call("POST", "/api/auth/register", {"username": "erin", "password": "correct horse battery", "turnstile": "good-token"})[0] == 400)
web.TURNSTILE_SECRET = ""

# --- the tailnet instance: admin tools (same code, PUBLIC off) ------------------------------------------------------------------------------------------------
web.PUBLIC = False
st, _, js, _ = call("GET", "/api/auth/config")
check("tailnet instance: auth endpoints do not exist", call("GET", "/api/auth/challenge")[0] == 404 and js["public"] is False)
st, _, js, _ = call("GET", "/api/admin/accounts", tailscale_user_login=OWNER)
check("tailnet admin sees the accounts", st == 200 and {a["username"] for a in js["accounts"]} >= {"alice", "owner-example-com"} and js["max"] == 25, str(js)[:200])
check("a tailnet person who is not an admin cannot", call("GET", "/api/admin/accounts", tailscale_user_login="friend@example.com")[0] == 403)
(sb.STATE / "tunnel.log").write_text("x https://old-one.trycloudflare.com y\n2026 INF https://quiet-fox-1.trycloudflare.com |\n")
check("the tunnel address is read from cloudflared's log", call("GET", "/api/admin/accounts", tailscale_user_login=OWNER)[2]["url"] == "https://quiet-fox-1.trycloudflare.com")
check("tailnet: the old Tailscale identity still works", call("GET", "/api/status", tailscale_user_login=OWNER)[0] == 200)
st, _, js, _ = call("POST", "/api/admin/accounts/alice/reset", {}, tailscale_user_login=OWNER)
temp = js["temp_password"]
check("admin: a password reset returns a temporary password", st == 200 and len(temp) >= 12)
web.PUBLIC = True
check("a reset kills the old sessions", anon(cookie=alice))
check("the old password no longer works", login("alice", "correct horse battery")[0] == 401)
st, hd, _, _ = login("alice", temp)
check("the temporary password works", st == 200)
alice = token_of(hd)
web.PUBLIC = False
call("POST", "/api/admin/accounts/alice/disable", {}, tailscale_user_login=OWNER)
web.PUBLIC = True
check("a disabled account's cookie stops working", anon(cookie=alice))
web.LIM.hits.clear()
check("a disabled account cannot sign in (403)", login("alice", temp)[0] == 403)
web.PUBLIC = False
call("POST", "/api/admin/accounts/alice/enable", {}, tailscale_user_login=OWNER)
check("unknown account / action are refused", call("POST", "/api/admin/accounts/ghostx/reset", {}, tailscale_user_login=OWNER)[0] == 404
      and call("POST", "/api/admin/accounts/alice/delete", {}, tailscale_user_login=OWNER)[0] == 400)
web.PUBLIC = True
st, hd, _, _ = login("alice", temp)
alice = token_of(hd)
web.PUBLIC = False
call("POST", "/api/admin/accounts/alice/logout", {}, tailscale_user_login=OWNER)
web.PUBLIC = True
check("'log out everywhere' kills the cookie", anon(cookie=alice))

# --- reading without an account: the main book, read-only --------------------------------------------------------------------------------
web.LIM.hits.clear()
web._anon_cache.clear()
web._members[0] = 0
main = sb.main_space()
(main.d["books"] / "math1").mkdir(parents=True, exist_ok=True)
(main.d["books"] / "math1" / "clean.pdf").write_bytes(b"%PDF-1.4 main book")
(main.d["interactive"] / "math1").mkdir(parents=True, exist_ok=True)
(main.d["interactive"] / "math1" / "page1.html").write_text("<p>hello</p>", encoding="utf-8")
sb.write_json(main.d["interactive"] / "math1" / "page1.json", {"title": "Page one", "topic": "t", "added": sb.now_iso()})
sb.write_json(main.d["requests"] / "secret-req.json", {"id": "secret-req", "kind": "interactive", "subject": "math1", "text": "my private wish",
                                                       "created": sb.now_iso()})
main.update_profile(blocked="owner secret error")
st, _, _, data = call("GET", "/books/math1/clean.pdf", ip=fresh_ip())
check("a link to a book opens without an account (the main book)", st == 200 and data == b"%PDF-1.4 main book", str(st))
st, hd, _, data = call("GET", "/i/math1/page1.html", ip=fresh_ip())
check("... and an interactive page, still in the CSP sandbox", st == 200 and b"hello" in data and "sandbox" in dict(hd).get("content-security-policy", ""), str(st))
check("a book that does not exist is 404, not 401", call("GET", "/books/math1/original.pdf", ip=fresh_ip())[0] == 404)
st, _, js, data = call("GET", "/api/status", ip=fresh_ip())
math = next(x for x in js["subjects"] if x["id"] == "math1")
check("anonymous status lists the subjects and the main book's PDFs", st == 200 and "clean" in math["books"] and math["interactive"][0]["stem"] == "page1", str(js)[:300])
check("... but nothing private: no login, queue, requests, error, engine, accounts",
      b"owner-example" not in data and b"secret" not in data and b"my private wish" not in data
      and js["queue"] == [] and math["making"] == [] and js["blocked"] is None and js["is_admin"] is False and js["suggestions"] == [], data[:300])
enabled = sum(1 for a in web.ACC.listing() if not a["disabled"])
check("the status carries the number of participants (enabled accounts + tailnet people), also for strangers",
      js["participants"] == enabled + sum(1 for d in sb.USERS.iterdir() if d.is_dir() and not d.name.startswith("pub-") and (d / "profile.json").exists())
      and js["participants"] >= enabled + 1, (js["participants"], enabled))
check("an anonymous visitor cannot change anything: upload, request, suggest, exam, engine",
      all(call("POST", p, {}, ip=fresh_ip())[0] in (401, 422) for p in ("/api/upload", "/api/request", "/api/suggest", "/api/exam", "/api/engine", "/api/stop", "/api/request/x/run")))
check("the admin tools stay closed", call("GET", "/api/admin/accounts", ip=fresh_ip())[0] == 403)
st, hd, js, _ = register("viewerbob")
bob = token_of(hd)
check("a signed-in person sees the same shared main book (books and pages), not an empty own space",
      call("GET", "/books/math1/clean.pdf", cookie=bob, ip=fresh_ip())[3] == b"%PDF-1.4 main book"
      and call("GET", "/i/math1/page1.html", cookie=bob, ip=fresh_ip())[0] == 200)
st, _, js_bob, _ = call("GET", "/api/status", cookie=bob, ip=fresh_ip())
bob_math = next(x for x in js_bob["subjects"] if x["id"] == "math1")
check("... their status says shared, lists the main book's pages, and keeps their own login name",
      st == 200 and js_bob["shared"] is True and bob_math["interactive"][0]["stem"] == "page1" and js_bob["user"]["login"] == "viewerbob"
      and bob_math["targets"] == [], str(js_bob)[:300])
ip = fresh_ip()
codes = [call("GET", "/books/math1/clean.pdf", ip=ip)[0] for _ in range(242)]
check("one address cannot hammer the anonymous view (429 after 240 a minute)", codes[:240] == [200] * 240 and codes[240] == 429, codes[238:])
main.update_profile(blocked=None)
(main.d["requests"] / "secret-req.json").unlink()

print("\n" + ("ALL OK" if not fails else f"{len(fails)} FAILED: {fails}"))
sys.exit(1 if fails else 0)
