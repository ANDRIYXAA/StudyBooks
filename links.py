"""Links (videos and sites) for the study books: checking an address, reading the title and description of the page,
and a fetch that cannot be used to reach the server's own network.

Pure functions; this module must NOT import sb (sb.py runs as __main__, see CLAUDE.md).

The fetch runs in the worker for a link that a signed-in person gave, so the address is not trusted:
  * only http and https, no user name or password in the address;
  * the address of every hop (redirects included) is resolved HERE, and the socket connects to the checked IP, so a name
    that points to a private address (127.x, 10.x, 192.168.x, 169.254.x, the 100.64/10 range of Tailscale, ::1, fe80::,
    fc00::/7 ...) is refused, and so is a DNS answer that changes between the check and the connection;
  * at most MAX_REDIRECTS redirects, TIMEOUT seconds per step, FETCH_BYTES bytes, HTML and JSON only.
Only the title, the description and the kind of the page leave this module: never the page itself."""
import html
import http.client
import ipaddress
import json
import re
import socket
import ssl
import urllib.parse
from html.parser import HTMLParser

MAX_URL = 1000
FETCH_BYTES = 400_000
TIMEOUT = 8
MAX_REDIRECTS = 4
ALLOW_PRIVATE = False          # tests only: lets a test serve a page from 127.0.0.1
UA = "Mozilla/5.0 (compatible; StudybooksLinkReader/1.0)"
VIDEO_HOSTS = ("youtube.com", "youtu.be", "youtube-nocookie.com", "vimeo.com", "dailymotion.com", "twitch.tv", "ted.com",
               "tum.live", "bilibili.com", "rutube.ru", "streamable.com", "loom.com", "wistia.com", "media.ccc.de")
VIDEO_EXT = (".mp4", ".webm", ".mov", ".m3u8", ".mkv")
TRACKING = re.compile(r"^(utm_.*|fbclid|gclid|dclid|igshid|mc_cid|mc_eid|si|feature|pp|ref_src)$", re.I)


class LinkError(ValueError):
    """args = (uk, en): the web layer turns it into a message in the person's language."""


def host_matches(host, names):
    return any(host == n or host.endswith("." + n) for n in names)


def _public(ip):
    ip = ipaddress.ip_address(ip)
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def keep_params(host, path):
    """The query parameters that say WHICH page it is, for the sites where the address is useless without them.
    Everything else after the "?" is cut (clean_pair keeps the full address as a fallback)."""
    if host_matches(host, ("youtube.com", "youtube-nocookie.com")):
        return {"/watch": ("v",), "/playlist": ("list",)}.get(path.rstrip("/"), ())
    return ()


def normalize(raw):
    """The short, clean address of a link: scheme and host in lower case, no fragment, and NOTHING after the "?" except
    what the site needs to find the page (a YouTube video id). Raises LinkError."""
    return clean_pair(raw)[0]


def clean_pair(raw):
    """-> (clean address, full address). The full one only loses the fragment and the tracking parameters: it is the
    fallback for a site whose page cannot be found without its query. Raises LinkError."""
    u = str(raw or "").strip()
    if not u or len(u) > MAX_URL or re.search(r"\s", u) or any(ord(c) < 32 or ord(c) == 127 for c in u):
        raise LinkError("некоректне посилання", "not a valid link")
    if not re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", u):
        u = "https://" + u                                 # "youtu.be/abc" without a scheme
    try:
        p = urllib.parse.urlsplit(u)
        port = p.port
    except ValueError:
        raise LinkError("некоректне посилання", "not a valid link")
    if p.scheme.lower() not in ("http", "https"):
        raise LinkError("приймаються лише посилання http і https", "only http and https links are accepted")
    if p.username or p.password:
        raise LinkError("посилання з логіном і паролем не приймаються", "links with a user name or password are not accepted")
    host = (p.hostname or "").lower().rstrip(".")
    if not host:
        raise LinkError("некоректне посилання", "not a valid link")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None:
        if not ALLOW_PRIVATE and not _public(ip):
            raise LinkError("адреса веде у приватну мережу", "this address points to a private network")
    else:
        if "." not in host:
            raise LinkError("некоректне посилання", "not a valid link")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise LinkError("некоректне посилання", "not a valid link")
    netloc = f"[{host}]" if ip is not None and ip.version == 6 else host
    if port and port != (443 if p.scheme.lower() == "https" else 80):
        netloc += f":{port}"
    path = p.path or "/"
    pairs = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)]
    keep = keep_params(host if ip is None else "", path)
    full = urllib.parse.urlunsplit((p.scheme.lower(), netloc, path, urllib.parse.urlencode(pairs), ""))
    clean = urllib.parse.urlunsplit((p.scheme.lower(), netloc, path, urllib.parse.urlencode([(k, v) for k, v in pairs if k in keep]), ""))
    if len(full) > MAX_URL:
        raise LinkError("надто довге посилання", "the link is too long")
    return clean, full


SITE_SEPARATORS = (" - ", " | ", " \u2013 ", " \u2014 ", " \u00b7 ", " :: ")


def strip_site(title, site, host):
    """"Complex number - Wikipedia" -> "Complex number": the part after the last separator goes when it is the name of
    the site (og:site_name) or a part of the host. Used where no model cleans the title."""
    t = clean_text(title, 300)
    flat = lambda x: re.sub(r"[^a-z0-9]", "", x.casefold())
    for sep in SITE_SEPARATORS:
        if sep in t:
            head, tail = t.rsplit(sep, 1)
            ft = flat(tail)
            if head.strip() and ft and (ft == flat(site) or ft in flat(host)):
                return head.strip()
    return t


def short(url, limit=80):
    """host/path of an address for a label: no scheme, no query."""
    p = urllib.parse.urlsplit(url)
    t = (p.hostname or "") + (p.path if p.path != "/" else "")
    t = t[4:] if t.startswith("www.") else t
    return t if len(t) <= limit else t[:limit - 1] + "…"


def host_of(url):
    h = (urllib.parse.urlsplit(url).hostname or "").lower()
    return h[4:] if h.startswith("www.") else h


# ---------------------------------------------------------------- the safe fetch

def _safe_socket(host, port, timeout):
    last = None
    for fam, typ, proto, _, sa in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM):
        if not ALLOW_PRIVATE and not _public(sa[0]):
            last = OSError("the address points to a private network")
            continue
        try:
            return socket.create_connection((sa[0], sa[1]), timeout)      # the IP that was checked, not the name again
        except OSError as e:
            last = e
    raise last or OSError("no address")


class _HTTP(http.client.HTTPConnection):
    def connect(self):
        self.sock = _safe_socket(self.host, self.port, self.timeout)


class _HTTPS(http.client.HTTPSConnection):
    def connect(self):
        sock = _safe_socket(self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


def _get(url, accept, limit=None):
    """-> (final url, status, headers, body bytes). Follows redirects by hand, every hop is checked again."""
    for _ in range(MAX_REDIRECTS + 1):
        p = urllib.parse.urlsplit(url)
        if p.scheme not in ("http", "https") or not p.hostname:
            raise OSError("not an http address")
        cls = _HTTPS if p.scheme == "https" else _HTTP
        conn = cls(p.hostname, p.port or (443 if p.scheme == "https" else 80), timeout=TIMEOUT)
        try:
            conn.request("GET", (p.path or "/") + ("?" + p.query if p.query else ""),
                         headers={"User-Agent": UA, "Accept": accept, "Accept-Language": "en,uk;q=0.8,de;q=0.6",
                                  "Accept-Encoding": "identity", "Connection": "close"})
            resp = conn.getresponse()
            if resp.status in (301, 302, 303, 307, 308) and resp.getheader("Location"):
                url = urllib.parse.urljoin(url, resp.getheader("Location"))
                continue
            body = resp.read(limit or FETCH_BYTES)
            return url, resp.status, resp, body
        finally:
            conn.close()
    raise OSError("too many redirects")


class _Head(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title, self.meta, self._in_title, self.done = "", {}, False, False

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "title" and not self.title:
            self._in_title = True
        elif tag == "meta":
            key = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if key and "content" in a and key not in self.meta:
                self.meta[key] = a["content"]
        elif tag == "body":
            self.done = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self.done = True

    def handle_data(self, data):
        if self._in_title:
            self.title += data


def clean_text(s, limit):
    s = re.sub(r"[\x00-\x1f\x7f]+", " ", html.unescape(str(s or "")))
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[:limit - 1].rstrip() + "…"


def _decode(body, headers):
    enc = "utf-8"
    m = re.search(r"charset=([\w-]+)", headers.getheader("Content-Type") or "", re.I)
    if m:
        enc = m.group(1)
    else:
        mb = re.search(rb"<meta[^>]+charset=[\"']?([\w-]+)", body[:3000], re.I)
        if mb:
            enc = mb.group(1).decode("ascii", "ignore")
    try:
        return body.decode(enc, "replace")
    except LookupError:
        return body.decode("utf-8", "replace")


def parse_page(text):
    """The title, description, site name and type of an HTML page: {"title", "description", "site", "og_type"}."""
    p = _Head()
    try:
        p.feed(text[:300_000])
    except Exception:
        pass
    m = p.meta
    return {"title": clean_text(m.get("og:title") or m.get("twitter:title") or p.title, 200),
            "description": clean_text(m.get("og:description") or m.get("description") or m.get("twitter:description"), 400),
            "site": clean_text(m.get("og:site_name"), 80), "og_type": clean_text(m.get("og:type"), 40).lower(),
            "image": clean_text(m.get("og:image") or m.get("og:image:url") or m.get("twitter:image") or m.get("twitter:image:src"), 800)}


def _oembed_url(url):
    h = host_of(url)
    q = urllib.parse.quote(url, safe="")
    if host_matches(h, ("youtube.com", "youtu.be", "youtube-nocookie.com")):
        return f"https://www.youtube.com/oembed?format=json&url={q}"
    if host_matches(h, ("vimeo.com",)):
        return f"https://vimeo.com/api/oembed.json?url={q}"
    return None


def youtube_id(url):
    """The video id in a YouTube address (watch?v=, youtu.be/, /shorts/, /embed/, /live/), else ""."""
    p = urllib.parse.urlsplit(url)
    h = (p.hostname or "").lower()
    if h.endswith("youtu.be"):
        cand = p.path.strip("/").split("/")[0]
    elif host_matches(h, ("youtube.com", "youtube-nocookie.com")):
        q = dict(urllib.parse.parse_qsl(p.query))
        parts = p.path.strip("/").split("/")
        cand = q.get("v", "") if parts[0] == "watch" else parts[1] if len(parts) > 1 and parts[0] in ("shorts", "embed", "live") else ""
    else:
        return ""
    return cand if re.fullmatch(r"[A-Za-z0-9_-]{6,20}", cand) else ""


def fetch_image(url, limit=2_000_000):
    """The bytes of a picture (for a thumbnail), or None. Same safe fetch; only image/* answers are taken."""
    try:
        final, status, resp, body = _get(url, "image/avif,image/webp,image/*;q=0.8,*/*;q=0.1", limit)
    except (OSError, ValueError, http.client.HTTPException, ssl.SSLError):
        return None
    if status != 200 or not (resp.getheader("Content-Type") or "").lower().startswith("image/") or not body:
        return None
    return body


def kind_of(url, og_type=""):
    """"video" for the known video sites, files and pages that say so, else "site"."""
    h, path = host_of(url), urllib.parse.urlsplit(url).path.lower()
    if host_matches(h, VIDEO_HOSTS) or path.endswith(VIDEO_EXT) or (og_type or "").startswith("video"):
        return "video"
    return "site"


def fetch_meta(url):
    """What can be read from the address without a browser: {"ok", "title", "description", "site", "kind", "error"}.
    A failure is not an error for the caller: the link is still filed, from the address and the student's note."""
    out = {"ok": False, "title": "", "description": "", "site": "", "kind": kind_of(url), "error": "", "image": ""}
    try:
        oe = _oembed_url(url)
        if oe:
            try:
                _, status, resp, body = _get(oe, "application/json")
                d = json.loads(body.decode("utf-8", "replace")) if status == 200 else {}
                if d.get("title"):
                    out.update(ok=True, title=clean_text(d["title"], 200), site=clean_text(d.get("provider_name"), 80),
                               description=clean_text(("by " + d["author_name"]) if d.get("author_name") else "", 200),
                               image=clean_text(d.get("thumbnail_url"), 800))
                    yid = youtube_id(url)
                    if not out["image"] and yid:
                        out["image"] = f"https://i.ytimg.com/vi/{yid}/hqdefault.jpg"
                    return out
            except (OSError, ValueError, http.client.HTTPException, ssl.SSLError):
                pass
        final, status, resp, body = _get(url, "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1")
        ctype = (resp.getheader("Content-Type") or "").lower()
        if status >= 400:
            out["error"] = f"HTTP {status}"
        elif "html" not in ctype and "xml" not in ctype:
            out.update(ok=True, kind=kind_of(final) if not ctype.startswith("video/") else "video")
        else:
            page = parse_page(_decode(body, resp))
            img = urllib.parse.urljoin(final, page["image"]) if page["image"] else ""
            out.update(ok=bool(page["title"] or page["description"]), title=page["title"], description=page["description"],
                       site=page["site"], kind=kind_of(final, page["og_type"]),
                       image=img if img.startswith(("http://", "https://")) else "")
    except (OSError, ValueError, http.client.HTTPException, ssl.SSLError) as e:
        out["error"] = clean_text(f"{type(e).__name__}: {e}", 160)
    return out
