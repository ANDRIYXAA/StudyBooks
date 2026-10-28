"""Accounts, sessions and the "are you a person" check for the PUBLIC side of Studybooks (the Cloudflare tunnel).

Standard library only and it never imports `sb` (sb.py runs as __main__; see CLAUDE.md). web.py wires it up.

  * passwords: scrypt with a per-account random salt; only salt + hash are stored (accounts.json, 0600)
  * sessions: a signed cookie value  base64(json{u, sv, exp}).hmac ; `sv` is the account's session version, so
    "log out everywhere", a password reset and disabling an account all kill every old cookie at once
  * human check: a proof-of-work puzzle (the browser must find a number whose SHA-256 has N leading zero bits).
    It makes mass sign-ups and password guessing cost real time; it is not a captcha. web.py can swap in
    Cloudflare Turnstile when keys are configured.
  * a small in-memory rate limiter
"""

import base64
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path

USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{2,29}")
PASS_MIN, PASS_MAX = 10, 200
SESSION_TTL = 30 * 86400
POW_BITS, POW_TTL = 16, 600
SCRYPT = dict(n=2 ** 14, r=8, p=1, dklen=32)
_DUMMY_SALT = bytes(16)


class AccountError(Exception):
    """`key` is a short machine name; web.py turns it into a sentence in the reader's language."""

    def __init__(self, key):
        super().__init__(key)
        self.key = key


def _hash(password, salt):
    return hashlib.scrypt(password.encode("utf-8"), salt=salt, maxmem=64 * 1024 * 1024, **SCRYPT).hex()


def _now():
    return int(time.time())


def _iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S") + time.strftime("%z")[:3] + ":" + time.strftime("%z")[3:]


def check_password(name, password):
    if not isinstance(password, str) or not PASS_MIN <= len(password) <= PASS_MAX:
        raise AccountError("weak_password")
    if password.casefold() in (name, name * 2) or len(set(password)) < 4:
        raise AccountError("weak_password")


def _b64(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def load_key(path):
    """The signing key for cookies and puzzles; created once (atomically, so a second process never sees a half-written
    file) and readable only by us."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(secrets.token_bytes(32))
        try:
            os.link(tmp, path)          # fails when somebody else got there first: then theirs is the key
        except FileExistsError:
            pass
        finally:
            os.unlink(tmp)
    return path.read_bytes()


class Store:
    """accounts.json shared by the tailnet page (admin) and the public page; every change re-reads the file under a lock."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = self.path.with_name(self.path.name + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return {"users": {}}

    def _write(self, data):
        tmp = self.path.with_name(self.path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)

    def _txn(self, fn):
        with open(self.lock, "a+") as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)
            data = self._read()
            out = fn(data)
            self._write(data)
            return out

    # ---- reading
    def users(self):
        return self._read().get("users", {})

    def get(self, name):
        return self.users().get(name)

    def listing(self):
        return [{"username": n, "created": u.get("created"), "last_login": u.get("last_login"),
                 "disabled": bool(u.get("disabled"))} for n, u in sorted(self.users().items())]

    # ---- sign-up and sign-in
    def register(self, name, password, max_users):
        name = (name or "").strip().lower()
        if not USERNAME_RE.fullmatch(name):
            raise AccountError("bad_username")
        check_password(name, password)
        salt = secrets.token_bytes(16)
        digest = _hash(password, salt)          # the slow part stays outside the lock

        def go(data):
            users = data.setdefault("users", {})
            if name in users:
                raise AccountError("taken")
            if len(users) >= max_users:
                raise AccountError("full")
            users[name] = {"salt": salt.hex(), "hash": digest, "sv": 1, "disabled": False,
                           "created": _iso(), "last_login": None}
        self._txn(go)
        return name

    def login(self, name, password):
        """-> the account's session version. Same work whether or not the name exists, so timing tells nothing."""
        name = (name or "").strip().lower()
        u = self.get(name) if isinstance(password, str) and len(password) <= PASS_MAX else None
        salt = bytes.fromhex(u["salt"]) if u else _DUMMY_SALT
        digest = _hash(password if isinstance(password, str) and len(password) <= PASS_MAX else "x", salt)
        if not u or not hmac.compare_digest(digest, u["hash"]):
            raise AccountError("bad_login")
        if u.get("disabled"):
            raise AccountError("disabled")

        def go(data):
            data["users"][name]["last_login"] = _iso()
        self._txn(go)
        return u["sv"]

    # ---- admin actions
    def _change(self, name, fn):
        def go(data):
            u = data.get("users", {}).get(name)
            if not u:
                raise AccountError("unknown")
            fn(u)
        self._txn(go)

    def set_disabled(self, name, disabled):
        def f(u):
            u["disabled"] = bool(disabled)
            u["sv"] += 1
        self._change(name, f)

    def logout_everywhere(self, name):
        self._change(name, lambda u: u.__setitem__("sv", u["sv"] + 1))

    def change_password(self, name, old, new):
        """The person's own change: needs the old password; every old session dies; -> the new session version."""
        self.login(name, old)
        check_password(name, new)
        salt = secrets.token_bytes(16)
        digest = _hash(new, salt)

        def f(u):
            u["salt"], u["hash"] = salt.hex(), digest
            u["sv"] += 1
        self._change(name, f)
        return self.get(name)["sv"]

    def reset_password(self, name):
        """A temporary password for the admin to pass on; every old session dies."""
        temp = secrets.token_urlsafe(12)
        salt = secrets.token_bytes(16)
        digest = _hash(temp, salt)

        def f(u):
            u["salt"], u["hash"] = salt.hex(), digest
            u["sv"] += 1
        self._change(name, f)
        return temp

    # ---- sessions
    def make_session(self, key, name, sv, now=None):
        body = _b64(json.dumps({"u": name, "sv": sv, "exp": (now or _now()) + SESSION_TTL}).encode())
        return body + "." + _b64(hmac.new(key, body.encode(), "sha256").digest())

    def read_session(self, key, token, now=None):
        """-> username of a valid, current session, else None."""
        try:
            body, sig = token.split(".")
            if not hmac.compare_digest(_b64(hmac.new(key, body.encode(), "sha256").digest()), sig):
                return None
            d = json.loads(_unb64(body))
            if d["exp"] < (now or _now()):
                return None
            u = self.get(d["u"])
            if not u or u.get("disabled") or u["sv"] != d["sv"]:
                return None
            return d["u"]
        except Exception:
            return None


# ---- the human check ------------------------------------------------------------------------------------------

_used = {}                       # puzzle signature -> expiry, so one solution cannot be replayed
_used_lock = threading.Lock()


def make_challenge(key, now=None, bits=POW_BITS):
    salt, exp = secrets.token_hex(12), (now or _now()) + POW_TTL
    sig = hmac.new(key, f"pow|{salt}|{bits}|{exp}".encode(), "sha256").hexdigest()
    return {"salt": salt, "bits": bits, "exp": exp, "sig": sig}


def _leading_zero_bits(digest):
    n = int.from_bytes(digest, "big")
    return len(digest) * 8 - n.bit_length()


def solve(challenge, limit=50_000_000):
    """What the browser does (kept here so tests can use it)."""
    for i in range(limit):
        if _leading_zero_bits(hashlib.sha256(f"{challenge['salt']}:{i}".encode()).digest()) >= challenge["bits"]:
            return i
    raise RuntimeError("no solution")


def check_pow(key, ch, number, now=None):
    """True when `number` solves a puzzle we issued, that has not expired and was not used before."""
    try:
        salt, bits, exp, sig = str(ch["salt"]), int(ch["bits"]), int(ch["exp"]), str(ch["sig"])
        number = int(number)
    except Exception:
        return False
    t = now or _now()
    good = hmac.new(key, f"pow|{salt}|{bits}|{exp}".encode(), "sha256").hexdigest()
    if not hmac.compare_digest(good, sig) or exp < t or bits < POW_BITS or number < 0:
        return False
    if _leading_zero_bits(hashlib.sha256(f"{salt}:{number}".encode()).digest()) < bits:
        return False
    with _used_lock:
        for k in [k for k, v in _used.items() if v < t]:
            del _used[k]
        if sig in _used:
            return False
        _used[sig] = exp
    return True


# ---- rate limits ------------------------------------------------------------------------------------------------

class Limiter:
    def __init__(self):
        self.hits = {}
        self.lock = threading.Lock()

    def _live(self, key, window, now):
        v = [t for t in self.hits.get(key, []) if now - t < window]
        self.hits[key] = v
        return v

    def count(self, key, window, now=None):
        with self.lock:
            return len(self._live(key, window, now or time.time()))

    def hit(self, key, window, now=None):
        with self.lock:
            now = now or time.time()
            self._live(key, window, now).append(now)

    def allow(self, key, limit, window, now=None):
        """Record an attempt and say whether it is within `limit` per `window` seconds."""
        with self.lock:
            now = now or time.time()
            v = self._live(key, window, now)
            if len(v) >= limit:
                return False
            v.append(now)
            if len(self.hits) > 5000:        # a flood of different keys must not grow the table forever
                for k in [k for k, x in self.hits.items() if not x or now - x[-1] > 3600]:
                    del self.hits[k]
            return True
