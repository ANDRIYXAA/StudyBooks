"""Combined interactive pages: a person picks which pages of ONE subject to show on one page with a menu.
Only the choice is stored (`interactive/<subject>/_combined/<id>.json`); the pages themselves are not copied, not
changed and not removed, so an edited page shows its newest version in the combined page.

Pure functions on a directory; this module must NOT import sb (sb.py runs as __main__, see CLAUDE.md)."""
import json
import re
import secrets
import time
from pathlib import Path

MAX_PARTS = 24
MAX_PER_SUBJECT = 10
MAX_TITLE = 80
STEM_RE = re.compile(r"[A-Za-z0-9_-]+")
ID_RE = re.compile(r"c[0-9a-f]{10}")


class CombineError(ValueError):
    """Message is a (uk, en) pair so the web layer can answer in the person's language."""


def _dir(idir):
    return Path(idir) / "_combined"


def _read(f):
    try:
        return json.loads(Path(f).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def listing(idir):
    out = []
    d = _dir(idir)
    if d.is_dir():
        for f in sorted(d.glob("*.json")):
            m = _read(f)
            if isinstance(m, dict) and ID_RE.fullmatch(f.stem) and isinstance(m.get("parts"), list):
                out.append({"id": f.stem, "title": str(m.get("title") or f.stem), "parts": [str(x) for x in m["parts"]],
                            "created": m.get("created"), "by": m.get("by")})
    return out


def clean_title(title):
    t = re.sub(r"[\x00-\x1f\x7f]+", " ", str(title or "")).strip()
    t = re.sub(r"\s+", " ", t)
    return t[:MAX_TITLE]


def create(idir, title, stems, by):
    """Store one combined page. Raises CombineError(uk, en) for a bad choice. Returns the stored record."""
    idir = Path(idir)
    if not isinstance(stems, list) or not all(isinstance(s, str) for s in stems):
        raise CombineError("потрібен список сторінок", "a list of pages is needed")
    seen, parts = set(), []
    for s in stems:
        if s not in seen:
            seen.add(s)
            parts.append(s)
    if len(parts) < 2:
        raise CombineError("обери щонайменше дві сторінки", "choose at least two pages")
    if len(parts) > MAX_PARTS:
        raise CombineError(f"не більше {MAX_PARTS} сторінок", f"at most {MAX_PARTS} pages")
    for s in parts:
        if not STEM_RE.fullmatch(s) or not (idir / f"{s}.html").is_file() or not (idir / f"{s}.json").is_file():
            raise CombineError("такої сторінки немає", "no such page")
    title = clean_title(title)
    if not title:
        raise CombineError("потрібна назва", "a title is needed")
    if len(listing(idir)) >= MAX_PER_SUBJECT:
        raise CombineError(f"не більше {MAX_PER_SUBJECT} зведених сторінок на предмет; видали непотрібну",
                           f"at most {MAX_PER_SUBJECT} combined pages per subject; delete one you do not need")
    d = _dir(idir)
    d.mkdir(parents=True, exist_ok=True)
    cid = "c" + secrets.token_hex(5)
    while (d / f"{cid}.json").exists():            # never replace another person's set
        cid = "c" + secrets.token_hex(5)
    rec = {"title": title, "parts": parts, "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "by": by}
    tmp = d / f".{cid}.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(d / f"{cid}.json")
    return rec | {"id": cid}


def add(idir, cid, stems):
    """Add pages to a stored combined page (the new ones go to the end of its list; the menu sorts them by the study
    order anyway). Pages that are already in the set are skipped. Raises CombineError(uk, en). Returns the stored record."""
    idir = Path(idir)
    if not ID_RE.fullmatch(cid or ""):
        raise CombineError("такої зведеної сторінки немає", "no such combined page")
    f = _dir(idir) / f"{cid}.json"
    rec = _read(f)
    if not isinstance(rec, dict) or not isinstance(rec.get("parts"), list):
        raise CombineError("такої зведеної сторінки немає", "no such combined page")
    if not isinstance(stems, list) or not all(isinstance(s, str) for s in stems):
        raise CombineError("потрібен список сторінок", "a list of pages is needed")
    parts = [str(x) for x in rec["parts"]]
    fresh = []
    for s in stems:
        if s not in parts and s not in fresh:
            fresh.append(s)
    for s in fresh:
        if not STEM_RE.fullmatch(s) or not (idir / f"{s}.html").is_file() or not (idir / f"{s}.json").is_file():
            raise CombineError("такої сторінки немає", "no such page")
    if not fresh:
        raise CombineError("усі ці сторінки вже є в зведеній", "all these pages are already in the combined page")
    if len(parts) + len(fresh) > MAX_PARTS:
        raise CombineError(f"не більше {MAX_PARTS} сторінок", f"at most {MAX_PARTS} pages")
    rec["parts"] = parts + fresh
    tmp = _dir(idir) / f".{cid}.tmp"
    tmp.write_text(json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(f)
    return rec | {"id": cid}


def delete(idir, cid):
    if not ID_RE.fullmatch(cid or ""):
        return False
    f = _dir(idir) / f"{cid}.json"
    if not f.is_file():
        return False
    f.unlink()
    return True


def get(idir, cid):
    if not ID_RE.fullmatch(cid or ""):
        return None
    for c in listing(idir):
        if c["id"] == cid:
            return c
    return None


def first_rank(combo, pages):
    """Where a combined page stands among the pages of its subject: the place of its earliest part (gone parts do not count)."""
    rank = {p["stem"]: p.get("rank", 0) for p in pages}
    return min((rank[s] for s in combo["parts"] if s in rank), default=10**6)


def view(combo, pages):
    """The menu data of one combined page. `pages` = interactive_pages() of the subject, already in the order of the
    study material (`rank`). The topics and the pages in the menu follow that order, not the order the person ticked
    them; a part that is gone is left out. Titles that repeat get the day and time they were added."""
    by_stem = {p["stem"]: p for p in pages}
    chosen = sorted((by_stem[s] for s in combo["parts"] if s in by_stem), key=lambda p: p.get("rank", 0))
    seen = {}
    for p in chosen:
        k = (p["topic"], p["title"])
        seen[k] = seen.get(k, 0) + 1
    groups = {}
    for p in chosen:
        title = p["title"] or p["stem"]
        label = f"{title} · {(p['added'] or '')[:16].replace('T', ' ')}" if seen[(p["topic"], p["title"])] > 1 else title
        groups.setdefault(p["topic"] or "", []).append({"stem": p["stem"], "title": title, "label": label, "url": p["url"]})
    return [{"topic": k, "pages": v} for k, v in groups.items()]
