"""Studybooks web page: tab «Додати» (uploads) + tab «Книги» (latest PDFs, requests).

Every person sees only their own space. Two ways in, two instances of this same app:

  * tailnet (port 8810): identity comes from the reverse proxy, `Tailscale-User-Login` (set by
    `tailscale serve`, which strips client-sent copies). Listens on 127.0.0.1 only. The owner and
    the admin tools live here.
  * public (SB_PUBLIC=1, port 8811, behind the Cloudflare quick tunnel): Tailscale headers are IGNORED
    (anybody can send them). A person signs up / signs in with a username and password after a
    "are you a person" check (proof of work, or Cloudflare Turnstile when SB_TURNSTILE_* are set),
    gets a signed session cookie, and lives in their own space "pub-<name>". Public accounts can never be
    admins and /api/admin/* is closed on this instance. See accounts.py.

"Підключити Claude" runs the unmodified `claude auth login` for that person with their own
CLAUDE_CONFIG_DIR: they sign in on Anthropic's page and paste the code Anthropic shows them;
Claude Code stores its credentials itself — this app never reads or keeps them.

    uvicorn web:app --host 127.0.0.1 --port 8810
"""

import email.header
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

import accounts
import sb

L = sb.L

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
INDEX = Path(__file__).resolve().parent / "web" / "index.html"
N8N_KICK = "http://127.0.0.1:5678/webhook/studybooks-kick"
PUBLIC = os.environ.get("SB_PUBLIC") == "1"            # the instance behind the Cloudflare tunnel
MAX_PUBLIC_USERS = int(os.environ.get("SB_MAX_PUBLIC_USERS", "200"))
TURNSTILE_SECRET = os.environ.get("SB_TURNSTILE_SECRET", "")
TURNSTILE_SITEKEY = os.environ.get("SB_TURNSTILE_SITEKEY", "")
TURNSTILE_URL = os.environ.get("SB_TURNSTILE_URL", "https://challenges.cloudflare.com/turnstile/v0/siteverify")
# Cloudflare's free plan refuses request bodies over 100 MB, so an upload is capped below that on the public side
MAX_UPLOAD = 90 * 1024 ** 2 if PUBLIC else 1024 ** 3
PUBLIC_QUOTA = 3 * 1024 ** 3                              # what one public account may keep on the server
PUBLIC_MAX_QUEUED = 20
MAX_LIVE_LOGINS = 6
LOGIN_TTL = 15 * 60
COOKIE = "__Host-sb"

ACC = accounts.Store(sb.STATE / "accounts.json")
KEY = accounts.load_key(sb.STATE / "session.key")
LIM = accounts.Limiter()

_known = set()
_logins = {}          # uid -> {"proc", "buf": bytearray, "t"}
_logins_lock = threading.Lock()


def client_ip(request: Request) -> str:
    """The visitor's address. Behind the tunnel Cloudflare sets Cf-Connecting-Ip; only the public instance trusts it."""
    if PUBLIC:
        ip = (request.headers.get("cf-connecting-ip") or "").strip()
        if ip:
            return ip[:64]
    return request.client.host if request.client else "?"


def request_lang(request: Request):
    """Language of a request that has no signed-in person yet (sign-in page, 401): what the page sends."""
    hl = (request.headers.get("x-lang") or "").strip().lower()
    sb.set_lang(hl if hl in ("uk", "en") else "uk")


def who(request: Request) -> sb.Space:
    if PUBLIC:
        # NEVER read the Tailscale headers here: the tunnel lets anyone send whatever they like
        acct = ACC.read_session(KEY, request.cookies.get(COOKIE) or "")
        if not acct:
            request_lang(request)
            raise HTTPException(401, L("потрібен вхід", "sign-in required"))
        login, name, uid = acct, acct, "pub-" + acct
    else:
        login = (request.headers.get("tailscale-user-login") or "").strip()
        name = (request.headers.get("tailscale-user-name") or "").strip() or None
        if name and "=?" in name:   # tailscale sends non-ASCII names RFC 2047-encoded
            name = str(email.header.make_header(email.header.decode_header(name)))
        uid = sb.slugify(login, 60) if login else "local"
    sp = sb.Space(uid)
    if uid not in _known:
        sb.ensure_global()
        sp.ensure(login=login or "local", name=name)
        _known.add(uid)
    # language: what the page sends (X-Lang) wins and is remembered for the worker; else what we remembered
    hl = (request.headers.get("x-lang") or "").strip().lower()
    if hl in ("uk", "en") and sp.lang() != hl:
        sp.update_profile(lang=hl)
    sb.set_lang(hl if hl in ("uk", "en") else sp.lang())
    return sp


def viewer(request: Request) -> sb.Space:
    """Whose books and interactive pages a GET shows: the main book (the first admin's library) for EVERYBODY, so
    the same books and pages are seen by all. Without a session on the public instance it is read-only, and a link
    to a book or a page opens without an account. Everything that changes data keeps using who(), which answers 401
    without a session, and the page answers that with the sign-up window."""
    if PUBLIC and not ACC.read_session(KEY, request.cookies.get(COOKIE) or ""):
        request_lang(request)
        main = sb.main_space()
        if main is None:
            raise HTTPException(401, L("потрібен вхід", "sign-in required"))
        if not LIM.allow(f"view:{client_ip(request)}", 240, 60):
            raise HTTPException(429, L("забагато запитів, зачекай трохи", "too many requests, wait a little"))
        return main
    sp = who(request)
    return sb.main_space() or sp


def shared_view(sp: sb.Space, st: dict) -> dict:
    """Books, chapters and interactive pages on the status come from the main book for everybody. The person's own
    queue, login, engine, usage, exams, what is being made for them and the refs stay their own. Suggestions are not
    offered on shared pages yet (their targets would point into the wrong library)."""
    main = sb.main_space()
    st["shared"] = bool(main and main.uid != sp.uid)
    if not st["shared"]:
        for s in st["subjects"]:
            for i in s["interactive"]:
                i.pop("by", None)
                i["mine"] = True                      # the book's owner may change every page
            for d in s["days"]:
                for e in d["entries"]:
                    e.pop("by", None)
                    e["mine"] = True                  # the book's owner may move every entry
        return st
    own = {s["id"]: s for s in st["subjects"]}
    shared = []
    for s in sb.status(main)["subjects"]:
        o = own.get(s["id"], {})
        for i in s["interactive"]:
            i["mine"] = i.pop("by", None) == sp.uid       # the person who added a page may change it
        for d in s["days"]:
            for e in d["entries"]:
                e["mine"] = e.pop("by", None) == sp.uid           # what this person uploaded: they may change its date
        shared.append(s | {"making": o.get("making", []), "activity": o.get("activity"), "exams": o.get("exams", []),
                           "refs": o.get("refs", []), "open_refs": o.get("open_refs", []), "targets": []})
    st["subjects"] = shared
    return st


_members = [0.0, 0]


def participants() -> int:
    """People who use the site: enabled public accounts plus the people of the tailnet (30-second cache)."""
    if time.time() - _members[0] > 30:
        pub = sum(1 for a in ACC.listing() if not a["disabled"])
        tail = sum(1 for d in sb.USERS.iterdir() if d.is_dir() and not d.name.startswith("pub-") and (d / "profile.json").exists())
        _members[:] = [time.time(), pub + tail]
    return _members[1]


_anon_cache = {}      # language -> (time, status): visitors without an account share one 3-second snapshot


def anon_status(request: Request) -> dict:
    """The main book as a stranger may see it: subjects, books, chapters, exams, interactive pages. Nobody's login,
    queue, requests, suggestions, engine, usage or errors."""
    sp = viewer(request)
    lang = sb.lang_name()
    hit = _anon_cache.get(lang)
    if hit and time.time() - hit[0] < 3:
        return hit[1]
    st = sb.status(sp)
    subjects = []
    for s in st["subjects"]:
        days = [d | {"entries": [{k: v for k, v in e.items() if k not in ("by", "by_name", "with", "mine", "can_undo", "can_edit")}
                                 for e in d["entries"]]} for d in s["days"]]      # who uploaded what: signed-in people only
        inter = [{k: v for k, v in i.items() if k not in ("by", "mine", "can_undo")} for i in s["interactive"]]
        subjects.append(s | {"making": [], "activity": None, "targets": [], "refs": [], "open_refs": [], "days": days,
                             "interactive": inter})
    out = {"ok": True, "anon": True, "participants": participants(), "public": True, "user": {"uid": "", "login": "", "name": None}, "is_admin": False,
           "admin_pending": 0, "subjects": subjects, "auto_activity": None, "queue": [], "failed": [], "recent": [],
           "suggestions": [], "suggest_limits": {}, "usage": {}, "limit_gate": None, "claude": {}, "blocked": None,
           "build_errors": [], "digest_errors": [], "paused": False, "soft_stop": False, "running_here": False,
           "worker": {"state": "idle"}, "general_refs": [],
           "engine": {"name": "claude", "ready": True, "openrouter": {}, "claude": {}}}
    _anon_cache[lang] = (time.time(), out)
    return out


def kick():
    """Wake the worker through n8n; if n8n is down, start it directly."""
    try:
        urllib.request.urlopen(urllib.request.Request(N8N_KICK, data=b"{}", method="POST",
                                                      headers={"Content-Type": "application/json"}), timeout=4)
    except Exception:
        sb.kick()


def space_bytes(sp: sb.Space) -> int:
    total = 0
    for key in ("inbox", "processed", "library", "interactive", "refs", "exams"):
        for root, _, names in os.walk(sp.d[key]):
            for n in names:
                try:
                    total += os.path.getsize(os.path.join(root, n))
                except OSError:
                    pass
    return total


def public_url():
    """The address of the quick tunnel: cloudflared writes it to its log when it starts."""
    try:
        with open(sb.STATE / "tunnel.log", "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 200_000))
            hits = re.findall(rb"https://[a-z0-9-]+\.trycloudflare\.com", f.read())
        return hits[-1].decode() if hits else None
    except OSError:
        return None


def safe_name(name, i):
    p = Path(name or f"file{i}")
    ext = re.sub(r"[^A-Za-z0-9.]", "", p.suffix.lower())[:8]
    return f"{i:02d}_{sb.slugify(p.stem, 60)}{ext}"


PAGE_CSP = ("default-src 'none'; script-src 'unsafe-inline' https://challenges.cloudflare.com; style-src 'unsafe-inline'; "
            "img-src 'self' data: blob:; connect-src 'self' https://challenges.cloudflare.com; frame-src https://challenges.cloudflare.com; "
            "worker-src blob:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


@app.middleware("http")
async def public_guard(request: Request, call_next):
    """Only on the public instance: no admin tools, no cross-site writes, safe headers."""
    if not PUBLIC:
        return await call_next(request)
    path = request.url.path
    if path.startswith("/api/admin"):
        return JSONResponse({"detail": "forbidden"}, status_code=403)
    if request.method not in ("GET", "HEAD", "OPTIONS"):
        origin, host = request.headers.get("origin"), request.headers.get("host", "")
        if not origin or urllib.parse.urlparse(origin).netloc != host:      # cookies are SameSite=Strict as well
            return JSONResponse({"detail": "bad origin"}, status_code=403)
        cl = request.headers.get("content-length", "")
        if path != "/api/upload" and cl.isdigit() and int(cl) > 1_000_000:
            return JSONResponse({"detail": "too large"}, status_code=413)
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    if path.startswith("/api/"):
        resp.headers.setdefault("Cache-Control", "no-store")
    return resp


@app.get("/", response_class=HTMLResponse)
def index():
    headers = {"Cache-Control": "no-store"}
    if PUBLIC:
        headers["Content-Security-Policy"] = PAGE_CSP
    return HTMLResponse(INDEX.read_text(encoding="utf-8"), headers=headers)


# ---------------------------------------------------------------- public side: sign-up, sign-in, human check

ACC_MSG = {
    "bad_username": ("ім'я: 3–30 символів, малі латинські літери, цифри, - і _", "username: 3–30 characters, lowercase letters, digits, - and _"),
    "weak_password": (f"пароль: від {accounts.PASS_MIN} символів і не надто простий", f"password: at least {accounts.PASS_MIN} characters and not too simple"),
    "taken": ("таке ім'я вже зайняте", "this username is taken"),
    "full": ("нові акаунти зараз не приймаються (досягнуто ліміт)", "new accounts are not accepted right now (limit reached)"),
    "bad_login": ("неправильне ім'я або пароль", "wrong username or password"),
    "disabled": ("акаунт вимкнено", "this account is disabled"),
    "unknown": ("такого акаунта немає", "no such account"),
}


def acc_msg(key):
    uk, en = ACC_MSG.get(key, ("помилка", "error"))
    return L(uk, en)


def need_public():
    if not PUBLIC:
        raise HTTPException(404)


async def json_body(request: Request) -> dict:
    try:
        b = await request.json()
    except Exception:
        b = {}
    return b if isinstance(b, dict) else {}


def turnstile_ok(token: str, ip: str) -> bool:
    data = urllib.parse.urlencode({"secret": TURNSTILE_SECRET, "response": token, "remoteip": ip}).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(TURNSTILE_URL, data=data), timeout=8) as r:
            return bool(json.load(r).get("success"))
    except Exception:
        return False


def check_human(body: dict, ip: str):
    """Cloudflare Turnstile when keys are configured, otherwise a one-time proof-of-work puzzle we issued."""
    if TURNSTILE_SECRET:
        ok = bool(body.get("turnstile")) and turnstile_ok(str(body["turnstile"])[:4096], ip)
    else:
        pw = body.get("pow")
        ok = isinstance(pw, dict) and accounts.check_pow(KEY, pw, pw.get("n"))
    if not ok:
        raise HTTPException(400, L("перевірку «ти людина» не пройдено, спробуй ще раз", "the human check failed, please try again"))


def session_response(name: str, sv: int) -> JSONResponse:
    resp = JSONResponse({"ok": True, "username": name})
    resp.set_cookie(COOKIE, ACC.make_session(KEY, name, sv), max_age=accounts.SESSION_TTL, httponly=True,
                    secure=True, samesite="strict", path="/")
    return resp


@app.get("/api/auth/config")
def auth_config():
    return {"public": PUBLIC, "turnstile": TURNSTILE_SITEKEY if (PUBLIC and TURNSTILE_SECRET and TURNSTILE_SITEKEY) else None,
            "max_upload": MAX_UPLOAD}


@app.get("/api/auth/challenge")
def auth_challenge(request: Request):
    need_public()
    request_lang(request)
    if not LIM.allow(f"chal:{client_ip(request)}", 60, 600):
        raise HTTPException(429, L("забагато запитів, зачекай трохи", "too many requests, wait a little"))
    return accounts.make_challenge(KEY)


@app.post("/api/auth/register")
async def auth_register(request: Request):
    need_public()
    request_lang(request)
    ip = client_ip(request)
    if not LIM.allow(f"reg:{ip}", 8, 3600):
        raise HTTPException(429, L("забагато спроб реєстрації з цієї адреси, зачекай годину", "too many sign-up attempts from this address, wait an hour"))
    body = await json_body(request)
    await run_in_threadpool(check_human, body, ip)
    try:
        name = await run_in_threadpool(ACC.register, body.get("username"), body.get("password"), MAX_PUBLIC_USERS)
    except accounts.AccountError as e:
        raise HTTPException(409 if e.key in ("taken", "full") else 400, acc_msg(e.key))
    return session_response(name, ACC.get(name)["sv"])


@app.post("/api/auth/login")
async def auth_login(request: Request):
    need_public()
    request_lang(request)
    ip = client_ip(request)
    body = await json_body(request)
    name = str(body.get("username") or "").strip().lower()[:30]
    if not LIM.allow(f"login:{ip}", 30, 900) or LIM.count(f"fail:{name}", 900) >= 8 or LIM.count(f"failip:{ip}", 900) >= 12:
        raise HTTPException(429, L("забагато невдалих спроб входу, зачекай 15 хвилин", "too many failed sign-in attempts, wait 15 minutes"))
    await run_in_threadpool(check_human, body, ip)
    try:
        sv = await run_in_threadpool(ACC.login, name, body.get("password"))
    except accounts.AccountError as e:
        if e.key == "bad_login":
            LIM.hit(f"fail:{name}", 900)
            LIM.hit(f"failip:{ip}", 900)
        raise HTTPException(401 if e.key == "bad_login" else 403, acc_msg(e.key))
    return session_response(name, sv)


@app.post("/api/auth/password")
async def auth_password(request: Request):
    """A signed-in person changes their own password (needs the old one)."""
    need_public()
    sp = who(request)
    name = sp.uid[len("pub-"):]
    if not LIM.allow(f"pw:{name}", 6, 900):
        raise HTTPException(429, L("забагато спроб, зачекай 15 хвилин", "too many attempts, wait 15 minutes"))
    body = await json_body(request)
    try:
        sv = await run_in_threadpool(ACC.change_password, name, body.get("old"), body.get("new"))
    except accounts.AccountError as e:
        raise HTTPException(401 if e.key == "bad_login" else 400, acc_msg(e.key))
    return session_response(name, sv)


@app.post("/api/auth/logout")
def auth_logout():
    need_public()
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE, path="/", secure=True, httponly=True, samesite="strict")
    return resp


@app.get("/api/status")
def api_status(request: Request):
    if PUBLIC and not ACC.read_session(KEY, request.cookies.get(COOKIE) or ""):
        return JSONResponse(anon_status(request), headers={"Cache-Control": "no-store"})
    sp = who(request)
    st = shared_view(sp, sb.status(sp))
    st["public"] = PUBLIC
    st["participants"] = participants()
    with _logins_lock:
        st["login_pending"] = sp.uid in _logins and _logins[sp.uid]["proc"].poll() is None
    return JSONResponse(st, headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- Claude login of this person

def _drop_login(uid):
    l = _logins.pop(uid, None)
    if l and l["proc"].poll() is None:
        l["proc"].kill()


def _reader(proc, buf):
    fd = proc.stdout.fileno()
    while True:
        try:
            chunk = os.read(fd, 4096)
        except OSError:
            break
        if not chunk:
            break
        buf.extend(chunk)


@app.post("/api/claude/login")
def claude_login(request: Request):
    sp = who(request)
    claude = str(os.environ.get("SB_CLAUDE", sb.CLAUDE))
    with _logins_lock:
        for uid, l in list(_logins.items()):
            if time.time() - l["t"] > LOGIN_TTL:
                _drop_login(uid)
        if PUBLIC and sp.uid not in _logins and sum(l["proc"].poll() is None for l in _logins.values()) >= MAX_LIVE_LOGINS:
            raise HTTPException(503, L("зараз багато входів у Claude одночасно, спробуй за хвилину", "many Claude sign-ins are running right now, try again in a minute"))
        _drop_login(sp.uid)
        proc = subprocess.Popen([claude, "auth", "login", "--claudeai"], env=sp.claude_env(), cwd=str(sp.root),
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        buf = bytearray()
        threading.Thread(target=_reader, args=(proc, buf), daemon=True).start()
        _logins[sp.uid] = {"proc": proc, "buf": buf, "t": time.time()}
    for _ in range(150):
        m = re.search(rb"https://\S+", bytes(buf))
        if m and (b"Paste code" in buf or proc.poll() is not None):
            break
        if proc.poll() is not None:
            break
        time.sleep(0.1)
    m = re.search(rb"https://\S+", bytes(buf))
    if not m:
        with _logins_lock:
            _drop_login(sp.uid)
        raise HTTPException(500, L("Claude Code не видав посилання для входу: ", "Claude Code gave no sign-in link: ") + bytes(buf).decode("utf-8", "replace")[-300:])
    return {"ok": True, "url": m.group(0).decode()}


@app.post("/api/claude/code")
async def claude_code(request: Request):
    sp = who(request)
    code = ((await request.json()).get("code") or "").strip()
    if not code or len(code) > 500 or any(c in code for c in "\r\n"):
        raise HTTPException(400, L("встав код зі сторінки Anthropic", "paste the code from Anthropic's page"))
    with _logins_lock:
        l = _logins.get(sp.uid)
    if not l or l["proc"].poll() is not None:
        raise HTTPException(409, L("вхід не розпочато або він завершився — натисни «Підключити Claude» ще раз", "sign-in was not started or has ended: start it again"))
    start = len(l["buf"])
    try:
        l["proc"].stdin.write((code + "\n").encode())
        l["proc"].stdin.flush()
        l["proc"].wait(timeout=90)
    except subprocess.TimeoutExpired:
        pass
    out = bytes(l["buf"][start:]).decode("utf-8", "replace")
    with _logins_lock:
        _drop_login(sp.uid)
    sp.forget_claude_status()
    st = sp.claude_status(fresh=True)
    if st.get("loggedIn"):
        prof = sb.read_json(sp.profile, {}) or {}
        prof.pop("blocked", None)
        sb.write_json(sp.profile, prof)
        kick()
    return {"ok": bool(st.get("loggedIn")), "email": st.get("email"),
            "message": None if st.get("loggedIn") else re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", out)[-400:]}


@app.post("/api/claude/logout")
def claude_logout(request: Request):
    sp = who(request)
    subprocess.run([str(os.environ.get("SB_CLAUDE", sb.CLAUDE)), "auth", "logout"], env=sp.claude_env(),
                   capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
    sp.forget_claude_status()
    return {"ok": True}


# ---------------------------------------------------------------- engine: Claude Code or OpenRouter

_key_tries = {}       # uid -> [timestamps] of key checks: a person cannot use this form to test lists of keys


@app.get("/api/openrouter/models")
def api_or_models(request: Request):
    who(request)
    return {"models": sb.or_models()}


@app.post("/api/engine")
async def api_engine(request: Request):
    sp = who(request)
    body = await request.json()
    engine = body.get("engine")
    if engine not in ("claude", "openrouter"):
        raise HTTPException(400, L("невідомий двигун", "unknown engine"))
    fields = {"engine": engine}
    if engine == "openrouter":
        model = str(body.get("model") or "").strip()[:120] or sp.or_model()
        models = await run_in_threadpool(sb.or_models)
        if not model:
            raise HTTPException(400, L("обери модель", "choose a model"))
        if model != sb.OR_AUTO and models and not any(m["id"] == model for m in models):
            raise HTTPException(400, L("такої моделі немає серед тих, що вміють викликати інструменти", "no such model among those that can call tools"))
        fields["openrouter_model"] = model
    sp.update_profile(blocked=None, blocked_at=None, **fields)
    kick()
    return {"ok": True, "ready": sp.engine_ok()}


@app.post("/api/claude/settings")
async def api_claude_settings(request: Request):
    """Model and effort for this person's Claude Code runs; "" = the server default. Applies from the next call."""
    sp = who(request)
    body = await request.json()
    model, effort = str(body.get("model") or ""), str(body.get("effort") or "")
    if model and model not in sb.CLAUDE_MODELS:
        raise HTTPException(400, L("невідома модель", "unknown model"))
    if effort and effort not in sb.CLAUDE_EFFORTS:
        raise HTTPException(400, L("невідомий рівень effort", "unknown effort level"))
    sp.update_profile(claude_model=model or None, claude_effort=effort or None)
    return {"ok": True}


@app.post("/api/stop")
def api_stop(request: Request):
    """Stop what runs for this person now (the worker kills the model call within seconds; the item stays queued,
    it does not count as a failed attempt) and pause their queue until /api/resume."""
    sp = who(request)
    sp.update_profile(paused=True, paused_at=sb.now_iso(), soft_stop=None)
    return {"ok": True}


@app.post("/api/stop/soft")
def api_stop_soft(request: Request):
    """"Stop after this chunk": what runs finishes its chunk (nothing is thrown away), then the queue pauses.
    Nothing running = the same as /api/stop."""
    sp = who(request)
    if sb.worker_busy_for(sp):
        sp.update_profile(soft_stop=True)
    else:
        sp.update_profile(paused=True, paused_at=sb.now_iso(), soft_stop=None)
    return {"ok": True, "waiting": sp.soft_stopping()}


@app.post("/api/turbo")
async def api_turbo(request: Request):
    """Turbo mode (header checkbox): the worker runs up to SB_TURBO_PARALLEL chunks of an upload at the same time. It reads
    the flag before each chunk, so it also speeds up the upload that is running now. OpenRouter only: the Claude Code
    limits are a different matter."""
    sp = who(request)
    body = await request.json()
    on = bool((body if isinstance(body, dict) else {}).get("on"))
    if on and sp.engine() != "openrouter":
        raise HTTPException(409, L("Турбо працює лише з OpenRouter", "Turbo mode works with OpenRouter only"))
    sp.update_profile(turbo=True if on else None)
    if on:
        kick()
    return {"ok": True, "on": on}


@app.post("/api/resume")
def api_resume(request: Request):
    sp = who(request)
    sp.update_profile(paused=None, paused_at=None, soft_stop=None)     # also takes back a soft stop that has not happened yet
    kick()
    return {"ok": True}


@app.post("/api/limit/continue")
def api_limit_continue(request: Request):
    """The person saw that 70/80/90 % of a plan window is used and wants the queue to go on (up to the next step)."""
    sp = who(request)
    sp.ack_limits()
    kick()
    return {"ok": True}


@app.post("/api/openrouter/key")
async def api_or_key(request: Request):
    sp = who(request)
    now_ = time.time()
    tries = [t for t in _key_tries.get(sp.uid, []) if now_ - t < 3600]
    if len(tries) >= 10:
        raise HTTPException(429, L("забагато спроб, зачекай годину", "too many attempts, wait an hour"))
    _key_tries[sp.uid] = tries + [now_]
    key = str((await request.json()).get("key") or "").strip()
    try:
        info = await run_in_threadpool(sb.or_set_key, sp, key)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except sb.AuthError:
        raise HTTPException(400, L("OpenRouter не прийняв цей ключ", "OpenRouter did not accept this key"))
    except sb.TransientError as e:
        raise HTTPException(502, str(e))
    kick()
    return {"ok": True, "hint": "…" + key[-4:], "label": info.get("label"),
            "limit_remaining": info.get("limit_remaining"), "ready": sp.engine_ok()}


@app.get("/api/openrouter/free")
def api_or_free(request: Request):
    """The free models in the order the auto mode tries them, with what is parked right now."""
    sp = who(request)
    try:      # the dialog is where a person looks after topping up: ask OpenRouter again (at most every 10 minutes)
        sb.or_refresh_tier(sp, force=time.time() - float(sp.prof().get("or_tier_at") or 0) > 600)
    except Exception:
        pass
    ranked, cool, st = sb.or_free_ranked(sp), sb.or_cooling(sp), sb.or_state(sp)
    return {"models": [{"id": m["id"], "name": m["name"], "vision": m["vision"], "ctx": m.get("ctx"),
                        "quality": m["quality"], "qsrc": m["qsrc"], "enabled": m["enabled"],
                        "cool_until": (cool.get(m["id"]) or {}).get("until"), "why": (cool.get(m["id"]) or {}).get("why")}
                       for m in ranked],
            "auto": sb.or_auto_status(sp), "all_why": st.get("all_why")}


@app.post("/api/openrouter/free")
async def api_or_free_save(request: Request):
    sp = who(request)
    body = await request.json()
    known = {m["id"] for m in await run_in_threadpool(sb.or_free_ranked, sp)}
    order = [str(x) for x in (body.get("order") or []) if str(x) in known]
    exclude = [str(x) for x in (body.get("exclude") or []) if str(x) in known]
    if len(exclude) >= len(known):
        raise HTTPException(400, L("не можна вимкнути всі безкоштовні моделі", "you cannot disable all free models"))
    sp.update_profile(or_free_order=order or None, or_free_exclude=exclude or None)
    return {"ok": True}


@app.post("/api/openrouter/free/reset")
def api_or_free_reset(request: Request):
    """Lift all pauses (e.g. after buying credits, which raises the daily limit)."""
    sp = who(request)
    (sp.root / "or_state.json").unlink(missing_ok=True)
    kick()
    return {"ok": True}


@app.post("/api/openrouter/key/remove")
def api_or_key_remove(request: Request):
    sp = who(request)
    sp.or_key_file.unlink(missing_ok=True)
    return {"ok": True}


# ---------------------------------------------------------------- uploads and requests

class _Pasted:
    """Page code pasted into the form, read like an uploaded file."""
    def __init__(self, filename, text):
        self.filename, self._b = filename, io.BytesIO(text.encode("utf-8"))

    async def read(self, n):
        return self._b.read(n)


def pasted_page(code: str) -> "_Pasted":
    """Page code pasted into the form as a file: a whole HTML page, or React source (which the model rewrites as HTML)."""
    if re.search(r"<!doctype html|<html", code, re.I):
        return _Pasted("pasted-page.html", code)
    if re.search(r"export\s+default|from\s+['\"]react['\"]|import\s+React", code):     # JSX has <div>s too, so it is tested before the tags
        return _Pasted("pasted-page.jsx", code)
    if re.search(r"<(?:body|script|canvas|svg|div)", code, re.I):
        return _Pasted("pasted-page.html", code)
    raise HTTPException(400, L("це не схоже на код сторінки (потрібен HTML або React-код)",
                               "this does not look like page code (HTML or React source is needed)"))


def require_subject(sp: sb.Space, subject: str) -> None:
    """400 unless this is a subject of the shared book."""
    if not any(s["id"] == subject.strip() for s in sb.shared_target(sp).load_syllabus()["subjects"]):
        raise HTTPException(400, L("невідомий предмет", "unknown subject"))


class Received:
    """The files of one upload as they were stored: stored names, the names the person's files had, their hashes, and
    the files that were left out because they are known already."""

    def __init__(self):
        self.names, self.originals, self.digests = [], [], []
        self.skipped, self.skipped_by = [], []

    def take(self, index: list) -> tuple:
        """Remove the files at these positions and give them back as (names, originals, digests)."""
        taken = tuple([part[i] for i in index] for part in (self.names, self.originals, self.digests))
        for i in sorted(index, reverse=True):
            del self.names[i], self.originals[i], self.digests[i]
        return taken


def class_day(kind: str, date: str) -> str | None:
    """The day of the class an upload belongs to (a textbook has none; everything else is filed under its day)."""
    if kind == "reference":
        return None
    try:
        return sb.check_class_date(date) if date.strip() else sb.now().date().isoformat()
    except ValueError as e:
        raise HTTPException(400, str(e))


async def check_public_limits(sp: sb.Space) -> None:
    """Anyone with the link can sign up, so one account cannot fill the disk or the queue."""
    if await run_in_threadpool(space_bytes, sp) > PUBLIC_QUOTA:
        raise HTTPException(413, L("у твоєму просторі вже забагато даних (понад 3 ГБ)", "your space already holds too much data (over 3 GB)"))
    if sum(1 for d in sp.d["inbox"].iterdir() if d.is_dir() and not d.name.startswith(".")) >= PUBLIC_MAX_QUEUED:
        raise HTTPException(429, L("у черзі вже забагато завантажень, дочекайся обробки", "too many uploads are waiting in the queue, wait until they are processed"))


def check_asis_upload(sp: sb.Space, subject: str, files: list) -> None:
    """A PDF kept as it is needs a subject and only PDF files."""
    if not subject.strip():
        raise HTTPException(400, L("для PDF без редагування обери предмет", "choose a subject for a PDF as is"))
    if any(not (uf.filename or "").lower().endswith(".pdf") for uf in files):
        raise HTTPException(400, L("без редагування приймаються тільки PDF", "only PDF files are accepted as is"))
    require_subject(sp, subject)


async def receive_files(sp: sb.Space, kind: str, files: list, tmp: Path) -> Received:
    """Store the files in `tmp`, within MAX_UPLOAD; a file that is already known (also from somebody else) is dropped."""
    got, total = Received(), 0
    for i, uf in enumerate(files, 1):
        name = safe_name(uf.filename, i)
        h = hashlib.sha256()
        with open(tmp / name, "wb") as out:
            while chunk := await uf.read(1 << 20):
                total += len(chunk)
                if total > MAX_UPLOAD:
                    lim = f"{MAX_UPLOAD // 1024 ** 2} МБ" if MAX_UPLOAD < 1024 ** 3 else "1 ГБ"
                    raise HTTPException(413, L(f"завелике завантаження (більше {lim})",
                                               f"upload too large (over {lim.replace('МБ', 'MB').replace('ГБ', 'GB')})"))
                h.update(chunk)
                out.write(chunk)
        digest = h.hexdigest()
        dup = None if digest in got.digests else sb.find_duplicate(sp, kind, digest)
        if digest in got.digests or dup:      # the same file again: it is not processed twice
            (tmp / name).unlink()
            got.skipped.append(uf.filename or name)
            if dup and dup.get("by"):
                got.skipped_by.append({"file": uf.filename or name, "by": dup["by"]})
            continue
        got.names.append(name)
        got.originals.append(uf.filename)
        got.digests.append(digest)
    return got


def interactive_files(kind: str, tmp: Path, names: list) -> list:
    """Positions of the files that are interactive pages (or React source): the model fixes and files those."""
    if kind != "notes":
        return []
    return [i for i, n in enumerate(names)
            if Path(n).suffix.lower() in sb.JSX_EXT
            or (Path(n).suffix.lower() in sb.HTML_EXT and sb.looks_interactive(sb.read_html(tmp / n)))]


def submission_record(sid: str, kind: str, names: list, originals: list, label: str, day, form: dict, skipped: list) -> dict:
    return {"id": sid, "uploaded_at": sb.now_iso(), "files": names, "note": form["note"].strip(), "subject": form["subject"].strip(),
            "kind": kind, "label": label, "date": day,
            "topic": sb.slugify(form["topic"], 40) if form["topic"].strip() else "",
            "topic_title": form["topic_title"].strip()[:80], "original_names": originals, "skipped_duplicates": skipped}


def queue_upload(sp: sb.Space, kind: str, folder: Path, sid: str, record: dict, digests: list) -> None:
    """Write the record, move the folder into the inbox and remember the files (so the same file is not sent twice)."""
    sb.write_json(folder / "submission.json", record)
    folder.rename(sp.d["inbox"] / sid)
    sb.remember_files(sp, kind, sid, record["label"], digests)


@app.post("/api/upload")
async def api_upload(request: Request, files: list[UploadFile] = File([]), note: str = Form(""),
                     subject: str = Form(""), kind: str = Form("notes"), topic: str = Form(""),
                     topic_title: str = Form(""), date: str = Form(""), code: str = Form("")):
    sp = who(request)
    files = list(files)
    if kind not in ("notes", "reference", "asis"):
        raise HTTPException(400, L("невідомий вид завантаження", "unknown kind of upload"))
    if code.strip():        # a page copied out of an artifact on a phone: HTML, or React source
        if kind != "notes":
            raise HTTPException(400, L("код сторінки додається тільки до конспектів", "page code can only be added to notes"))
        files.append(pasted_page(code))
    if not files:
        raise HTTPException(400, L("немає що завантажувати", "nothing to upload"))
    if PUBLIC:
        await check_public_limits(sp)
    day = class_day(kind, date)
    if kind == "asis":
        check_asis_upload(sp, subject, files)
    form = {"note": note, "subject": subject, "topic": topic, "topic_title": topic_title}
    sid = sb.new_id()
    tmp = sp.d["inbox"] / f".tmp-{sid}"
    tmp.mkdir(parents=True)
    itmp = None                 # the folder of the interactive pages, once there are any (the cleanup below needs it)
    try:
        got = await receive_files(sp, kind, files, tmp)
        ia = interactive_files(kind, tmp, got.names)
        isid = ilabel = idigests = None
        if ia:
            if subject.strip():
                require_subject(sp, subject)
            isid = sb.new_id()
            itmp = sp.d["inbox"] / f".tmp-{isid}"
            itmp.mkdir(parents=True)
            inames, iorig, idigests = got.take(ia)
            for n in inames:
                shutil.move(str(tmp / n), str(itmp / n))
            ilabel = note.strip()[:80] or (iorig[0] if len(inames) == 1 else L(f"{len(inames)} інтерактивних сторінок", f"{len(inames)} interactive pages"))
            irecord = submission_record(isid, "ipage", inames, iorig, ilabel, day, form, [])
        if not got.names and not ia:
            who_ = ", ".join(sorted({x["by"] for x in got.skipped_by}))
            raise HTTPException(409, L("усі ці файли вже завантажені раніше, нічого нового", "all these files were uploaded before, nothing new")
                                + (L(f" (їх уже додав(-ла): {who_})", f" (already added by: {who_})") if who_ else ""))
        sids = []
        if got.names:
            label = note.strip()[:80] or (got.originals[0] if len(got.names) == 1 else L(f"{len(got.names)} файлів", f"{len(got.names)} files"))
            queue_upload(sp, kind, tmp, sid, submission_record(sid, kind, got.names, got.originals, label, day, form, got.skipped), got.digests)
            sids.append(sid)
        else:
            shutil.rmtree(tmp, ignore_errors=True)
        if ia:
            queue_upload(sp, kind, itmp, isid, irecord, idigests)
            sids.append(isid)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        if itmp is not None:
            shutil.rmtree(itmp, ignore_errors=True)
        raise
    kick()
    return {"ok": True, "id": sids[0], "files": len(got.names), "skipped": got.skipped, "skipped_by": got.skipped_by,
            "interactive": len(ia)}


@app.post("/api/entry/date")
async def api_entry_date(request: Request):
    """Move one entry to another class day (wrong date at upload, or a supplement for an earlier class)."""
    sp = who(request)
    lib = sb.shared_target(sp)        # entries live in the shared book; the owner moves any, others only their own
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    subject, topic, entry = str(body.get("subject") or ""), str(body.get("topic") or ""), str(body.get("entry") or "")
    if not any(s["id"] == subject for s in lib.load_syllabus()["subjects"]):
        raise HTTPException(404, L("невідомий предмет", "unknown subject"))
    if lib is not sp:
        ok_path = sb.SAFE_ID.match(topic) and sb.SAFE_ID.match(entry) and ".." not in topic + entry
        meta = sb.read_json(lib.d["library"] / subject / topic / entry / "meta.json", None) if ok_path else None
        if not meta:
            raise HTTPException(404, L("такого запису немає", "no such entry"))
        if sb.entry_by(meta, lib.uid) != sp.uid:
            raise HTTPException(403, L("дату змінює лише той, хто завантажив цей матеріал, і власник книги",
                                       "only the person who uploaded this material and the book's owner can change its date"))
    try:
        day = sb.set_entry_date(lib, subject, topic, entry, str(body.get("date") or ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    except ValueError as e:
        raise HTTPException(400, str(e))
    kick()
    return {"ok": True, "date": day}


@app.post("/api/request")
async def api_request(request: Request):
    sp = who(request)
    body = await request.json()
    kind, subject, text = body.get("kind"), body.get("subject", ""), (body.get("text") or "").strip()
    if kind not in ("explain", "interactive", "ipage_edit", "entry_edit", "book_edit") or not subject or not text:
        raise HTTPException(400, L("потрібні тип, предмет і текст запиту", "type, subject and request text are required"))
    extra = {}
    if kind == "book_edit":                     # a change somewhere in the shared clean book of one subject
        tgt = sb.shared_target(sp)
        if not sb.SAFE_ID.match(subject) or ".." in subject or not sb.book_edit_sections(sp, tgt, subject):
            raise HTTPException(404, L("у цій книзі немає розділів, які ти можеш змінити", "this book has no sections you may change"))
        if any(x.get("kind") == "book_edit" and x.get("subject") == subject
               for x in (sb.read_json(p, {}) or {} for p in sp.d["requests"].glob("*.json"))):
            raise HTTPException(409, L("зміна цієї книги вже чекає в черзі", "a change to this book is already waiting in the queue"))
    if kind == "entry_edit":                    # a change to one section of the shared clean book
        tgt = sb.shared_target(sp)
        topic, entry = str(body.get("topic") or ""), str(body.get("entry") or "")
        d, meta = sb.entry_dir(tgt, subject, topic, entry)
        if not d:
            raise HTTPException(404, L("такого розділу немає, або це PDF без редагування (в ньому немає тексту)",
                                       "no such section, or it is a PDF as is (it has no text)"))
        if not sb.can_edit_entry(sp, tgt, meta):
            raise HTTPException(403, L("цей розділ змінює лише той, хто його додав, і власник книги",
                                       "only the person who added this section and the book's owner can change it"))
        if any(x.get("kind") == "entry_edit" and (x.get("subject"), x.get("topic"), x.get("entry")) == (subject, topic, entry)
               for x in (sb.read_json(p, {}) or {} for p in sp.d["requests"].glob("*.json"))):
            raise HTTPException(409, L("зміна цього розділу вже чекає в черзі", "a change to this section is already waiting in the queue"))
        extra = {"topic": topic, "entry": entry}
    if kind == "ipage_edit":                    # a change to one page of the shared book
        tgt = sb.shared_target(sp)
        stem = str(body.get("stem") or "")
        f, meta = sb.ipage_file(tgt, subject, stem)
        if not f:
            raise HTTPException(404, L("такої інтерактивної сторінки немає", "no such interactive page"))
        if any(x.get("kind") == "ipage_edit" and x.get("stem") == stem and x.get("subject") == subject
               for x in (sb.read_json(p, {}) or {} for p in sp.d["requests"].glob("*.json"))):
            raise HTTPException(409, L("зміна цієї сторінки вже чекає в черзі", "a change to this page is already waiting in the queue"))
        extra = {"stem": stem}
    rid = sb.new_id()
    sb.write_json(sp.d["requests"] / f"{rid}.json", {
        "id": rid, "kind": kind, "subject": subject, "topic": body.get("topic") or "", "text": text[:2000],
        "created": sb.now_iso(), "label": text[:80]} | extra)
    kick()
    return {"ok": True, "id": rid}


@app.post("/api/ipage/undo")
async def api_ipage_undo(request: Request):
    """Bring back the version of an interactive page from before its last change on request (one step)."""
    sp = who(request)
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    subject, stem = str(body.get("subject") or ""), str(body.get("stem") or "")
    tgt = sb.shared_target(sp)
    f, meta = sb.ipage_file(tgt, subject, stem)
    if not f:
        raise HTTPException(404, L("такої інтерактивної сторінки немає", "no such interactive page"))
    if not sb.can_edit_ipage(sp, tgt, meta):
        raise HTTPException(403, L("цю сторінку змінює лише той, хто її додав, і власник книги",
                                   "only the person who added this page and the book's owner can change it"))
    prev = f.with_name(f.stem + ".prev.html")
    if not prev.exists():
        raise HTTPException(404, L("немає попередньої версії", "there is no earlier version"))
    old, cur = prev.read_text(encoding="utf-8"), f.read_text(encoding="utf-8")
    sb.atomic_write(f, old)
    sb.atomic_write(prev, cur)                     # undo again = redo
    sb.write_json(f.with_suffix(".json"), meta | {"sha": sb.hashlib.sha256(old.encode("utf-8")).hexdigest(), "edited": sb.now_iso()})
    tgt.mark_dirty(subject)
    return {"ok": True}


@app.post("/api/entry/undo")
async def api_entry_undo(request: Request):
    """Bring back the text of a clean-book section from before its last change on request (one step; again = redo)."""
    sp = who(request)
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    r = sb.undo_entry_edit(sp, str(body.get("subject") or ""), str(body.get("topic") or ""), str(body.get("entry") or ""))
    if not r["ok"]:
        raise HTTPException(r["code"], r["error"])
    kick()
    return {"ok": True}


@app.post("/api/request/{rid}/run")
def api_request_run(request: Request, rid: str):
    """Make this one waiting page (interactive page or explanation) or upload now, although the person's queue is paused.
    Only this item runs; the pause holds for everything else. Without a pause it is simply queued as usual."""
    sp = who(request)
    if not re.fullmatch(r"[0-9A-Za-z_.-]{1,64}", rid):
        raise HTTPException(400)
    r = sb.read_json(sp.d["requests"] / f"{rid}.json", None)
    upload = (sp.d["inbox"] / rid / "submission.json").exists()
    if not upload and (not isinstance(r, dict) or r.get("kind") not in ("interactive", "explain", "ipage_edit", "entry_edit", "book_edit")):
        raise HTTPException(404, L("такого запиту в черзі немає", "no such request in the queue"))
    if sp.paused():
        sp.update_profile(run_now=sorted(sp.run_now_ids() | {rid}))
    kick()
    return {"ok": True}


@app.post("/api/queue/delete")
async def api_queue_delete(request: Request):
    """Delete a waiting upload or request. If the worker is busy with it, the model call is stopped within seconds and
    the worker removes the item (`stopping`); otherwise it is gone at once."""
    sp = who(request)
    body = await request.json()
    r = sb.delete_queue_item(sp, str((body if isinstance(body, dict) else {}).get("id") or ""))
    if not r["ok"]:
        raise HTTPException(404, r["error"])
    kick()
    return r


@app.post("/api/queue/file/delete")
async def api_queue_file_delete(request: Request):
    """Take one file out of a waiting upload (`id` = the upload, `file` = the stored name from `file_links`)."""
    sp = who(request)
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    r = sb.delete_upload_file(sp, str(body.get("id") or ""), str(body.get("file") or ""))
    if not r["ok"]:
        raise HTTPException(409 if r.get("busy") else 404, r["error"])
    kick()
    return r


# what a browser may show for an uploaded file; anything else is sent as a download. The type is never guessed from
# the file, so an uploaded .html or .svg cannot run as a page of this site.
SHOWN = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
         ".webp": "image/webp", ".bmp": "image/bmp", ".txt": "text/plain; charset=utf-8", ".md": "text/plain; charset=utf-8",
         ".tex": "text/plain; charset=utf-8", ".m": "text/plain; charset=utf-8", ".c": "text/plain; charset=utf-8",
         ".cpp": "text/plain; charset=utf-8", ".h": "text/plain; charset=utf-8", ".py": "text/plain; charset=utf-8"}


@app.get("/api/upload/{sid}/file/{idx}")
def api_upload_file(request: Request, sid: str, idx: int):
    """Open one file of the person's own upload (waiting, done or failed) in the browser, or download it."""
    sp = who(request)
    hit = sb.upload_file(sp, sid, idx)
    if not hit:
        raise HTTPException(404, L("такого файлу немає", "no such file"))
    f, name = hit
    mime = SHOWN.get(f.suffix.lower())
    headers = {"Cache-Control": "no-store"}
    if mime and mime != "application/pdf":
        headers["Content-Security-Policy"] = "sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:"
    return FileResponse(f, media_type=mime or "application/octet-stream", filename=name,
                        content_disposition_type="inline" if mime else "attachment", headers=headers)


@app.post("/api/exam")
async def api_exam(request: Request):
    sp = who(request)
    body = await request.json()
    try:
        rid = sb.create_exam(sp, body if isinstance(body, dict) else {})
    except sb.ExamError as e:
        raise HTTPException(e.code, e.msg)
    kick()
    return {"ok": True, "id": rid}


@app.post("/api/exam/delete")
async def api_exam_delete(request: Request):
    sp = who(request)
    body = await request.json()
    body = body if isinstance(body, dict) else {}
    try:
        sb.delete_exam(sp, str(body.get("subject") or ""), str(body.get("id") or ""))
    except sb.ExamError as e:
        raise HTTPException(e.code, e.msg)
    return {"ok": True}


def need_admin(request: Request) -> sb.Space:
    sp = who(request)
    if sp.uid not in sb.admin_uids():
        raise HTTPException(403, L("тільки для адміна", "admins only"))
    return sp


@app.get("/api/admin/accounts")
def api_accounts(request: Request):
    need_admin(request)
    accts = []
    for a in ACC.listing():
        p = sb.read_json(sb.USERS / ("pub-" + a["username"]) / "profile.json", {}) or {}
        accts.append(a | {"engine": p.get("engine") or "claude", "or_paid": p.get("or_paid"),
                          "or_cap": p.get("or_daily_cap")})
    return {"url": public_url(), "max": MAX_PUBLIC_USERS, "accounts": accts}


@app.get("/api/admin/supervisor")
def api_supervisor(request: Request):
    need_admin(request)
    return sb.supervisor_view()


@app.post("/api/admin/supervisor/run")
async def api_supervisor_run(request: Request):
    need_admin(request)
    await run_in_threadpool(sb.supervise, True)
    return sb.supervisor_view()


@app.get("/api/admin/skill")
def api_skill(request: Request):
    need_admin(request)
    return sb.skill_info()


@app.post("/api/admin/skill/learn")
def api_skill_learn(request: Request):
    """Start a learning session now (Opus, at most 20 % of the owner's 5-hour window); the worker runs it."""
    need_admin(request)
    sb.learn_note(request=True, retry_at=None)
    kick()
    return {"ok": True}


@app.post("/api/admin/skill/auto")
async def api_skill_auto(request: Request):
    need_admin(request)
    sb.learn_note(auto=bool((await json_body(request)).get("auto")))
    return {"ok": True}


@app.post("/api/admin/skill/rollback")
async def api_skill_rollback(request: Request):
    need_admin(request)
    try:
        v = sb.rollback_skill(str((await json_body(request)).get("v") or ""))
    except LookupError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "v": v}


@app.post("/api/admin/accounts/{name}/{action}")
def api_account_action(request: Request, name: str, action: str):
    need_admin(request)
    if not accounts.USERNAME_RE.fullmatch(name) or action not in ("disable", "enable", "logout", "reset"):
        raise HTTPException(400)
    try:
        if action in ("disable", "enable"):
            ACC.set_disabled(name, action == "disable")
        elif action == "logout":
            ACC.logout_everywhere(name)
        else:
            return {"ok": True, "temp_password": ACC.reset_password(name)}
    except accounts.AccountError as e:
        raise HTTPException(404, acc_msg(e.key))
    return {"ok": True}


@app.post("/api/suggest")
async def api_suggest(request: Request):
    sp = who(request)
    try:
        body = await request.json()
        sid = sb.create_suggestion(sp, body if isinstance(body, dict) else {})
    except sb.SuggestError as e:
        raise HTTPException(e.code, e.msg)
    kick()
    return {"ok": True, "id": sid}


@app.post("/api/suggest/{sid}/undo")
def api_suggest_undo(request: Request, sid: str):
    r = sb.undo_suggestion(who(request), sid)
    if r.get("ok"):
        kick()
    return r


@app.get("/api/admin/merge")
def api_merge_list(request: Request):
    need_admin(request)
    return sb.merge_overview(sb.main_space())


@app.post("/api/admin/merge/state")
async def api_merge_state(request: Request):
    need_admin(request)
    body = await request.json()
    st = sb.merge_state()
    if "paused" in body:
        st["paused"] = bool(body["paused"])
    uid = sb.slugify(str(body.get("uid") or ""), 60) if body.get("uid") else None
    if uid and body.get("block") is True:
        st["blocked"].append(uid)
    elif uid and body.get("block") is False:
        st["blocked"] = [u for u in st["blocked"] if u != uid]
    sb.save_merge_state(st)
    return {"ok": True}


@app.post("/api/admin/merge/{cid}/{action}")
async def api_merge_action(request: Request, cid: str, action: str):
    sp = need_admin(request)
    try:
        note = (await request.json()).get("note", "")
    except Exception:
        note = ""
    r = sb.merge_action(sp, cid, action, note)
    if r.get("ok"):
        kick()
    return r


@app.post("/api/retry/{name}")
def api_retry(request: Request, name: str):
    sp = who(request)
    if not re.fullmatch(r"[0-9a-z.-]+", name):
        raise HTTPException(400)
    r = sb.retry_failed(sp, name)
    kick()
    return r


@app.get("/books/{subject}/{edition}.pdf")
def book(request: Request, subject: str, edition: str):
    sp = viewer(request)
    if edition not in ("clean", "original") or not re.fullmatch(r"[a-z0-9-]+", subject):
        raise HTTPException(404)
    f = sp.d["books"] / subject / f"{edition}.pdf"
    if not f.exists():
        raise HTTPException(404, L("книги ще немає", "the book does not exist yet"))
    s = sb.find_subject(sp.load_syllabus(), subject)
    title = (s or {}).get("title", subject)
    fname = f"{title} ({'clean' if edition == 'clean' else 'original'}).pdf"
    return FileResponse(f, media_type="application/pdf", filename=fname, content_disposition_type="inline",
                        headers={"Cache-Control": "no-store"})


@app.get("/exams/{subject}/{eid}/{which}.pdf")
def exam_pdf(request: Request, subject: str, eid: str, which: str):
    if which not in ("tickets", "answers") or not re.fullmatch(r"[a-z0-9-]+", subject) or not re.fullmatch(r"[0-9a-z-]+", eid):
        raise HTTPException(404)
    # The status lists a person's exam sets from THEIR OWN space (exams are not part of the shared book), while
    # viewer() is the main book for everybody. Look in the person's space first, then in the main book (what
    # visitors without an account are shown).
    spaces = []
    try:
        spaces.append(who(request))
    except HTTPException:
        pass
    v = viewer(request)
    if all(v.uid != o.uid for o in spaces):
        spaces.append(v)
    sp = next((o for o in spaces if (o.d["exams"] / subject / eid / f"{which}.pdf").exists()), None)
    if sp is None:
        raise HTTPException(404, L("такого набору білетів немає", "no such ticket set"))
    f = sp.d["exams"] / subject / eid / f"{which}.pdf"
    s = sb.find_subject(sp.load_syllabus(), subject)
    fname = f"{(s or {}).get('title', subject)} ({'tickets' if which == 'tickets' else 'tickets with answers'}).pdf"
    return FileResponse(f, media_type="application/pdf", filename=fname, content_disposition_type="inline",
                        headers={"Cache-Control": "no-store"})


@app.get("/i/{subject}/{name}.html")
def interactive(request: Request, subject: str, name: str):
    sp = viewer(request)
    if not re.fullmatch(r"[a-z0-9-]+", subject) or not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise HTTPException(404)
    f = sp.d["interactive"] / subject / f"{name}.html"
    if not f.exists():
        raise HTTPException(404)
    # Pages may be edited from other people's suggestions: run them in an opaque origin with no network, so
    # even a page that slipped a script past the checks can neither call /api/* nor send anything out.
    return FileResponse(f, media_type="text/html; charset=utf-8", headers={
        "Content-Security-Policy": "sandbox allow-scripts; default-src 'none'; script-src 'unsafe-inline'; "
                                   "style-src 'unsafe-inline'; img-src data:; font-src data:; connect-src 'none'; "
                                   "form-action 'none'; base-uri 'none'",
        "X-Content-Type-Options": "nosniff", "Referrer-Policy": "no-referrer"})
