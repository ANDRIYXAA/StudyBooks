"""The model engines of Studybooks and the switching between them: names, the providers that speak the OpenAI chat API
(Grok, Gemini), how to read the output of the Codex CLI, how to tell a usage limit from a login problem, how long an
engine rests after a limit, and the note that hands a half-done task to the next engine.

Pure functions; this module must NOT import sb (sb.py runs as __main__, see CLAUDE.md)."""
import json
import re

ORDER = ("claude", "openrouter", "codex", "grok", "gemini")        # the order of the "switch between" box: also the order of use
LABEL = {"claude": "Claude", "openrouter": "OpenRouter", "codex": "Codex", "grok": "Grok", "gemini": "Gemini"}
PROVIDERS = {          # engines that are a plain HTTP API with the person's own key; the agent loop lives in sb.py
    "grok": {"base": "https://api.x.ai/v1", "model": "grok-4.7", "vision": True},
    "gemini": {"base": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "", "vision": True},
}
KEY_RE = re.compile(r"^[A-Za-z0-9_.\-]{16,300}$")
COOL_MIN, COOL_MAX, COOL_DEFAULT = 60, 6 * 3600, 20 * 60
CODEX_EFFORTS = ("low", "medium", "high", "xhigh")

_LIMIT = re.compile(r"usage limit|rate[- ]?limit|\b429\b|quota|too many requests|limit reached|session limit|weekly limit|hit your|"
                    r"try again|overloaded|\b529\b|capacity|resource[_ ]exhausted|exceeded your|out of (?:extra )?usage|credits? (?:have )?run out|"
                    r"insufficient (?:credit|balance|funds)", re.I)
_AUTH = re.compile(r"not logged in|log ?in required|please (?:log ?in|sign ?in)|sign ?in again|unauthori[sz]ed|\b401\b|invalid api key|"
                   r"api key (?:not valid|invalid)|incorrect api key|authentication|token (?:has )?expired|credentials?", re.I)


def classify(text):
    """"limit" (come back later), "auth" (the login or the key is the problem) or "other"."""
    t = str(text or "")
    if _LIMIT.search(t):
        return "limit"
    if _AUTH.search(t):
        return "auth"
    return "other"


def cool_seconds(text, default=COOL_DEFAULT):
    """How long an engine rests after a limit, from what it said ("try again in 3 hours", "retry after 120s"); else the default."""
    t = str(text or "").lower()
    m = re.search(r"(?:in|after|for)\s+(\d+(?:\.\d+)?)\s*(second|sec|s|minute|min|m|hour|hr|h|day|d)s?\b", t)
    if m:
        n, unit = float(m.group(1)), m.group(2)
        mult = {"s": 1, "sec": 1, "second": 1, "m": 60, "min": 60, "minute": 60, "h": 3600, "hr": 3600, "hour": 3600, "d": 86400, "day": 86400}[unit]
        return int(max(COOL_MIN, min(COOL_MAX, n * mult)))
    m = re.search(r"retry[- ]after[:= ]+(\d+)", t)
    if m:
        return int(max(COOL_MIN, min(COOL_MAX, int(m.group(1)))))
    return default


def handoff(prev_names, files):
    """The note that goes after the prompt when another engine continues a task: the work is in the files."""
    prev = ", ".join(LABEL.get(n, n) for n in prev_names)
    shown = "\n".join(f"- {f}" for f in files[:40]) or "- (nothing written yet)"
    return ("HANDOFF: another model (" + prev + ") started this task and stopped because its usage limit ran out. "
            "Everything it has done so far is in the current directory. Files there now:\n" + shown +
            "\n\nRead what is there first. Keep what is correct. Continue from that point and finish the task: do not start over, "
            "do not redo the parts that are finished, and write every output file the task asks for.")


def parse_codex(stdout):
    """`codex exec --json` -> {"text": the last agent message, "messages": n, "failed": bool, "error": str, "usage": {...}}."""
    texts, error, failed, usage = [], "", False, {}
    for line in str(stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        kind = str(ev.get("type") or "")
        if kind == "item.completed" and isinstance(ev.get("item"), dict) and ev["item"].get("type") == "agent_message":
            texts.append(str(ev["item"].get("text") or ""))
        elif kind == "turn.completed" and isinstance(ev.get("usage"), dict):
            usage = ev["usage"]
        elif kind in ("turn.failed", "error"):
            # an "error" event is also what Codex prints while it reconnects ("Reconnecting... 2/5"): only turn.failed or a
            # non-zero exit code is a failure; the last message is kept to say why
            failed = failed or kind == "turn.failed"
            e = ev.get("error")
            error = str((e.get("message") if isinstance(e, dict) else e) or ev.get("message") or error)
    return {"text": texts[-1] if texts else "", "messages": len(texts), "failed": failed, "error": error, "usage": usage}


def parse_login(text):
    """The sign-in link and the one-time code that `codex login --device-auth` prints (the exact wording is not documented)."""
    clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", str(text or ""))
    url = re.search(r"https://[^\s\"'<>]+", clean)
    code = re.search(r"\b[A-Z0-9]{4,6}-[A-Z0-9]{4,6}\b", clean)
    return (url.group(0).rstrip(".,)") if url else None), (code.group(0) if code else None), clean
