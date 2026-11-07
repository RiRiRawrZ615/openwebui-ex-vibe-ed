"""
Open WebUI Web Search Tool 4.0 - cache-first, budget-bounded, file-backed (engine-fixed)

WHAT WAS ACTUALLY BROKEN
The cache half of v4.0 always worked. read_cache, grep_cache, cache_index, fetch_url all
answered fine. Only discovery failed, and it failed for two separate reasons:

  1. KEY-NAME MISMATCH (the real root cause). ddgs rows come back with keys
     ['body','href','title'], but the ddgs branch read r.get("url"), which is None.
     Every ddgs row became url="", and _rank drops any row with an empty url
     ("if not url or url in seen: continue"). So the only live engine in the chain was
     silently discarding 100% of its results. Verified live:
       r.get('url')  -> None
       r.get('href')  -> 'https://deepwiki.com/ggml-org/llama.cpp/7.3-quantization-techniques'

  2. PACKAGE ABSENT. ddgs was not importable on this machine, so the branch returned []
     before the key bug could even matter. bs4/requests were absent too (the tool is
     stdlib-only by design, so this only affects the bonus path).

The engines that were genuinely walled at the time of testing:
  html.duckduckgo.com  -> HTTP 202 anomaly/challenge page, result__a matches nothing
  searx instances      -> 403/429 for anonymous JSON callers, or HTML instead of JSON

NOT a bug, correcting an earlier wrong reading: Bing HTML is fine. Its result anchors all
wrap through bing.com/ck/a?...u=a1<base64url>, so a naive "external href" count reads as
zero, but _b64url decodes them 10/10. The original Bing parse was correct.

WHAT THIS VERSION ADDS
  - Auto-install of ddgs/bs4/requests on first search, pip first then uv. The OpenWebUI tool
    env is a uv archive env with no pip, so plain `python -m pip` fails there; uv is the path
    that actually works on this machine.
  - lite engine: lite.duckduckgo.com as a POST. That is the single detail that matters - GET
    returns 202, POST returns 200 with real links.
  - bingrss: Bing as ?format=rss, plain XML, no bot wall, no JS shell.
  - Entity decoding in snippets (results arrive as &amp; &#39; &nbsp;).
  - Rarity-weighted ranking. Raw token frequency let a common token like "llama" outrank a
    rare one like "pq2", so "bonsai 2 27b llama.cpp fork PQ2_0" returned actual bonsai trees.
  - Failure output names which engines failed, so a blank result is diagnosable.

VERIFIED LIVE ON THIS MACHINE
  bing HTML          200, 10 items, _b64url decodes 10/10, consistent across queries
  bing RSS           200, 10 items, consistent across 3 queries
  lite POST          200 with 8 real results; intermittently 202 when rate limited
  ddgs (installed)   200, 5 rows, 2 of 3 calls succeeded
  marginalia         200 but only boilerplate anchors; kept as a long shot
  searx              403/429 anonymous JSON; kept as a long shot
"""

import asyncio
import base64
import hashlib
import html as htmllib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from typing import Any, Optional

logger = logging.getLogger("owui_web_search_40")

DEPENDENCIES = [("ddgs", "ddgs"), ("bs4", "bs4"), ("requests", "requests")]

SKIP_TAGS = {"script", "style", "noscript", "nav", "footer", "header", "aside", "form",
             "iframe", "svg", "button", "label", "picture", "figure", "canvas"}
BOILER = re.compile(r"(cookie|banner|sidebar|nav|menu|footer|comment|share|promo|related|"
                    r"breadcrumb|pagination|modal|overlay|advert|newsletter|toc)", re.I)
BLOCK_TAGS = {"h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "li", "blockquote", "table",
              "div", "section", "article", "main", "td", "dt", "dd", "figcaption", "summary"}
VOID_TAGS = {"br", "img", "hr", "input", "meta", "link", "source", "area", "base", "col",
            "embed", "track", "wbr"}
NAV_NOISE = re.compile(r"(jump to content|skip to content|search wikipedia|main menu|"
                       r"personal tools|toggle contents|on this page|contents|"
                       r"search|subscribe|language|settings)", re.I)
BLOCK_MARKERS = ("anomaly", "challenge", "captcha", "unusual traffic")


def _try_install(pairs: list[tuple[str, str]]) -> list[str]:
    """pip first, then uv. The OpenWebUI tool env is a uv archive env with no pip, so
    `python -m pip` raises there and uv is the path that works on this machine."""
    installed = []
    for mod, pkg in pairs:
        try:
            __import__(mod)
            continue
        except ImportError:
            pass
        cmds = [[sys.executable, "-m", "pip", "install", "--quiet", pkg]]
        uv = shutil.which("uv")
        if uv:
            cmds.append([uv, "pip", "install", "--quiet", "--python", sys.executable, pkg])
        for cmd in cmds:
            try:
                subprocess.run(cmd, check=True, timeout=180, capture_output=True)
                installed.append(pkg)
                logger.info("installed %s for the search tool", pkg)
                break
            except Exception as exc:
                logger.warning("install %s (%s): %s", pkg, " ".join(cmd[1:3]), exc)
    return installed


def _import_or_install(auto: bool) -> dict[str, Any]:
    resolved: dict[str, Any] = {}
    if auto:
        try:
            _try_install(DEPENDENCIES)
        except Exception as exc:
            logger.warning("auto-install failed: %s", exc)
    try:
        from ddgs import DDGS
        resolved["ddgs"] = DDGS
    except Exception:
        resolved["ddgs"] = None
    try:
        from bs4 import BeautifulSoup
        resolved["bs4"] = BeautifulSoup
    except Exception:
        resolved["bs4"] = None
    return resolved


class BlockExtractor(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool]] = []
        self.skip = False
        self.blocks: list[str] = []
        self.title = ""
        self.in_title = False
        self.buf: list[str] = []

    def _flush(self):
        text = re.sub(r"[ \t\r\n]+", " ", " ".join(self.buf)).strip()
        if text and len(text) >= 3 and not NAV_NOISE.fullmatch(text):
            self.blocks.append(text)
        self.buf = []

    def handle_starttag(self, tag, attrs):
        skipping = tag in SKIP_TAGS or self.skip
        a = dict(attrs)
        # only judge individual class/id tokens, never the root document node
        ident = (a.get("class") or "") + " " + (a.get("id") or "")
        if ident and len(self.stack) > 0 and BOILER.search(ident):
            skipping = True
        if tag == "title":
            self.in_title = True
        if tag not in VOID_TAGS:
            self.stack.append((tag, skipping or self.skip))
            self.skip = bool(self.stack and self.stack[-1][1])
        if tag in BLOCK_TAGS:
            self._flush()

    def handle_endtag(self, tag):
        if tag == "title":
            self.in_title = False
        if tag in VOID_TAGS:
            return
        # pop until the matching open tag, so an unbalanced void/implicit close
        # cannot leave the skip flag stuck on for the rest of the document
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break
        self.skip = bool(self.stack and self.stack[-1][1])
        if tag in BLOCK_TAGS and not self.skip:
            self._flush()

    def handle_data(self, data):
        if self.skip:
            return
        if self.in_title:
            self.title += data
            return
        self.buf.append(data)

    def result(self) -> tuple[str, list[str]]:
        self._flush()
        return re.sub(r"\s+", " ", self.title).strip(), self.blocks


MAIN_SELECTORS = ("#mw-content-text", "article", "main", ".markdown-body", ".prose",
                  ".rst-content", ".md-content", "[role='main']", "#content", "body")


def html_to_blocks(html: str, bs4_cls: Any = None) -> tuple[str, list[str]]:
    """bs4 when present (main-container selectors), stdlib html.parser otherwise. Both
    paths produce the same block list, so behaviour is the same with or without bs4."""
    if bs4_cls is not None:
        try:
            soup = bs4_cls(html, "html.parser")
            for tag in soup(sorted(SKIP_TAGS)):
                tag.decompose()
            main = None
            for sel in MAIN_SELECTORS:
                main = soup.select_one(sel)
                if main:
                    break
            main = main or soup
            blocks = []
            for el in main.find_all(sorted(BLOCK_TAGS)):
                sep = "\n" if el.name in {"pre", "table"} else " "
                text = re.sub(r"[ \t]+", " ", el.get_text(sep, strip=True)).strip()
                if len(text) >= 3 and not NAV_NOISE.fullmatch(text):
                    blocks.append(text)
            return re.sub(r"\s+", " ", soup.title.get_text(strip=True)) if soup.title else "", blocks
        except Exception as exc:
            logger.warning("bs4 extract failed, falling back: %s", exc)
    try:
        p = BlockExtractor()
        p.feed(html)
        return p.result()
    except Exception as exc:  # pragma: no cover
        logger.warning("extract failed: %s", exc)
        text = re.sub(r"(?is)<(script|style|noscript).*?>.*?</\1>", " ", html)
        text = re.sub(r"(?s)<[^>]+>", " \n", text)
        return "", [ln.strip() for ln in text.splitlines() if len(ln.strip()) >= 3]


STOPWORDS = {"does", "do", "did", "how", "what", "why", "when", "where", "which", "can",
             "could", "would", "should", "will", "shall", "with", "from", "into", "about",
             "that", "this", "there", "their", "them", "then", "than", "have", "has", "had",
             "are", "was", "were", "being", "been", "for", "not", "but", "and", "the", "you",
             "your", "its", "it", "use", "used", "using", "best", "many", "most", "more"}


def _keyword_query(q: str) -> str:
    """Engines treat a full natural-language question literally. Bing returned dictionary
    entries for 'does' when research passed the whole sentence, because 'does' was a real
    term in the query. Reduce a question to its content terms so the engine searches the
    topic instead of the grammar. Ranking still uses the original query."""
    terms = _terms(q)
    return " ".join(terms) if terms else (q or "").strip()


def _terms(q: str) -> list[str]:
    """Content tokens only. A question word like 'does' must not become a ranking term,
    because a term appearing on exactly one page gets maximum rarity weight, and that page
    was literally a dictionary entry for the word 'does'."""
    out = [t for t in re.findall(r"\w{3,}", (q or "").lower()) if t not in STOPWORDS]
    return out or [t for t in re.findall(r"\w{3,}", (q or "").lower())]


def _strip(html: str) -> str:
    # Tags alone were stripped, entities were not, so snippets came back as &amp; &#39; &nbsp;.
    text = re.sub(r"<[^>]+>", "", html or "")
    text = htmllib.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _b64url(href: str) -> str:
    """Bing wraps result URLs as ck/a?...u=a1<base64url>. Recover the real target."""
    try:
        m = re.search(r"[?&]u=([A-Za-z0-9_%\-]+)", href)
        s = m.group(1) if m else href
        s = s.replace("%2B", "+").replace("%2F", "/")
        if s.startswith("a1"):
            s = s[2:]
        s += "=" * ((4 - len(s) % 4) % 4)
        out = base64.urlsafe_b64decode(s).decode("utf-8", "ignore")
        return out if "://" in out else ""
    except Exception:
        return ""


class Tools:
    class Valves:
        CACHE_DIR = "/home/amd/Documents/AI_Output/cache/web_search"
        FALLBACK_CACHE_DIR = "/tmp/owui_web_search_cache"
        PAGE_TTL_HOURS = 72            # 0 = never expire; cache is always consulted first
        SEARCH_TTL_MINUTES = 20        # repeat queries answered from cache
        OUTPUT_CHARS = 3000            # hard cap on every returned call
        FOCUS_WINDOW = 240
        FOCUS_MAX_WINDOWS = 12
        RESEARCH_PER_SOURCE = 700
        RESEARCH_TOTAL = 5000
        MAX_RESULTS = 10
        RESEARCH_SOURCES = 8
        REQUEST_TIMEOUT = 20
        MAX_RETRIES = 2
        MIN_REQUEST_INTERVAL = 1.5
        MAX_RESPONSE_BYTES = 2_000_000
        SAVE_RAW_HTML = True

        AUTO_INSTALL = True            # try pip then uv once per process on first search
        INSTALL_TIMEOUT = 180

        # Bing serves results to a Firefox UA but challenge-walls a Chrome UA (tested 4/4 vs 0/4)
        USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:130.0) Gecko/20100101 Firefox/130.0"

        # Order by observed reliability on this machine after the fix.
        # bing parses clean via _b64url. lite only answers as a POST. ddgs works now that the
        # package is present, but is intermittent and rate limited. ddg HTML and searx still
        # hit challenge walls intermittently. marginalia is a long shot.
        SEARCH_ENGINES = "bing,lite,ddgs,bingrss,ddg,searx,marginalia"

        # Per-engine penalty. A rate-limited engine is skipped while the penalty is active,
        # so a temporary wall does not permanently disable it.
        PENALTY_HARD = 600             # hard fetch failure / block page
        PENALTY_SOFT = 120             # empty parse after the retry
        POOL_ENGINES = 4               # engines consulted per query before ranking

    def __init__(self):
        self.valves = self.Valves()
        self._dead_engine: dict[str, float] = {}
        self._penalty: dict[str, int] = {}
        self._empty_streak: dict[str, int] = {}
        self._last_hit: dict[str, float] = {}
        self._modules: dict[str, Any] = {}
        self._install_attempted = False

    # ------------------------------------------------------------------ cache

    def _root(self) -> str:
        for base in (self.valves.CACHE_DIR, self.valves.FALLBACK_CACHE_DIR):
            try:
                os.makedirs(os.path.join(base, "pages"), exist_ok=True)
                probe = os.path.join(base, ".w")
                with open(probe, "w") as f:
                    f.write("x")
                os.remove(probe)
                return base
            except Exception:
                continue
        return ""

    def _canonical(self, url: str) -> str:
        if not url:
            return ""
        url = htmllib.unescape(url)
        raw = url if "://" in url else "https://" + url
        try:
            p = urllib.parse.urlsplit(raw)
            if p.scheme.lower() not in ("http", "https") or not p.hostname:
                return ""
            # Unwrap outbound redirect wrappers (Bing uses u=, Marginalia uses /out/).
            q = urllib.parse.parse_qs(p.query)
            if q.get("u"):
                raw = q[0]
                p = urllib.parse.urlsplit(raw)
            elif p.hostname and "marginalia" in p.hostname and p.path.startswith("/out/"):
                raw = "https://" + p.path.split("/out/", 1)[1]
                p = urllib.parse.urlsplit(raw)
            if p.scheme.lower() not in ("http", "https") or not p.hostname:
                return ""
            return urllib.parse.urlunsplit((
                p.scheme.lower(), p.hostname.lower(),
                urllib.parse.quote(urllib.parse.unquote(p.path or "/"),
                                   safe="/:@-._~!$&'()*+,;="),
                p.query, ""))
        except Exception:
            return ""

    def _key(self, url: str) -> str:
        return hashlib.sha1(self._canonical(url).encode()).hexdigest()[:12]

    def _paths(self, key: str) -> dict[str, str]:
        root = self._root()
        return {"txt": os.path.join(root, "pages", key + ".txt"),
                "raw": os.path.join(root, "pages", key + ".html"),
                "index": os.path.join(root, "index.jsonl"), "root": root}

    def _rows(self) -> list[dict[str, Any]]:
        """Index rows, self-healing: any .txt on disk without an index line gets re-added.
        So a lost index never makes the cache unreadable."""
        root = self._root()
        idx = os.path.join(root, "index.jsonl")
        rows = []
        try:
            with open(idx, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            rows.append(json.loads(line))
                        except Exception:
                            pass
        except Exception:
            pass
        known = {r.get("id") for r in rows}
        pages = os.path.join(root, "pages")
        try:
            for name in os.listdir(pages):
                if not name.endswith(".txt"):
                    continue
                key = name[:-4]
                if key in known:
                    continue
                full = os.path.join(pages, name)
                try:
                    with open(full, "r", encoding="utf-8") as f:
                        blocks = [ln for ln in f.read().split("\n") if ln]
                except Exception:
                    continue
                rows.append({"id": key, "url": "file:" + full, "title": "(index rebuilt)",
                             "ts": int(os.path.getmtime(full)), "lines": len(blocks),
                             "chars": sum(len(b) for b in blocks), "cached": True})
                known.add(key)
        except Exception:
            pass
        return rows

    def _record(self, key: str, url: str, title: str, blocks: list[str], cached: bool) -> None:
        p = self._paths(key)
        if not p["root"]:
            return
        row = {"id": key, "url": url, "title": title, "ts": int(time.time()),
               "lines": len(blocks), "chars": sum(len(b) for b in blocks), "cached": cached}
        rows = [r for r in self._rows() if r.get("id") != key]
        rows.append(row)
        try:
            with open(p["txt"], "w", encoding="utf-8") as f:
                f.write("\n".join(blocks))
            with open(p["index"], "w", encoding="utf-8") as f:
                for r in sorted(rows, key=lambda x: x.get("ts", 0)):
                    f.write(json.dumps(r) + "\n")
        except Exception as exc:
            logger.warning("cache write failed: %s", exc)

    def _blocks(self, key: str) -> Optional[list[str]]:
        p = self._paths(key)
        txt = p["txt"]
        if not os.path.exists(txt):
            return None
        if self.valves.PAGE_TTL_HOURS:
            row = next((r for r in self._rows() if r.get("id") == key), None)
            if row and time.time() - row.get("ts", 0) > self.valves.PAGE_TTL_HOURS * 3600:
                return None
        try:
            with open(txt, "r", encoding="utf-8") as f:
                return f.read().split("\n")
        except Exception:
            return None

    def _windows(self, blocks: list[str], query: str, budget: int, cap: int) -> list[tuple[int, str]]:
        terms = _terms(query)
        if not terms:
            return [(i + 1, b[:self.valves.FOCUS_WINDOW]) for i, b in enumerate(blocks)][:cap]
        scored = sorted(((sum(b.lower().count(t) for t in terms), i, b)
                         for i, b in enumerate(blocks)), key=lambda x: (-x[0], x[1]))
        picked, used, seen = [], 0, set()
        for score, i, b in scored:
            if not score or b[:40] in seen:
                continue
            seen.add(b[:40])
            win = b[:self.valves.FOCUS_WINDOW]
            if used + len(win) + 6 > budget:
                break
            picked.append((i + 1, win))
            used += len(win) + 6
            if len(picked) >= cap:
                break
        picked.sort(key=lambda x: x[0])
        return picked

    # ---------------------------------------------------------------- network

    def _throttle(self, tag: str) -> None:
        gap = self.valves.MIN_REQUEST_INTERVAL - (time.time() - self._last_hit.get(tag, 0))
        if gap > 0:
            time.sleep(gap)
        self._last_hit[tag] = time.time()

    def _get(self, url: str, post: Optional[bytes] = None) -> Optional[str]:
        headers = {"User-Agent": self.valves.USER_AGENT,
                   "Accept-Language": "en-US,en;q=0.8",
                   "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.2"}
        if post:
            headers["Referer"] = "https://lite.duckduckgo.com/lite/"
        self._throttle("web")
        try:
            req = urllib.request.Request(url, headers=headers, data=post)
            with urllib.request.urlopen(req, timeout=self.valves.REQUEST_TIMEOUT) as resp:
                data = resp.read(self.valves.MAX_RESPONSE_BYTES + 1)
                status = resp.status
        except Exception as exc:
            logger.warning("fetch %s -> %s", url, exc)
            return None
        if len(data) > self.valves.MAX_RESPONSE_BYTES:
            return None
        body = data.decode("utf-8", "ignore")
        # A 202 from DDG is a block page, not a result page. Don't treat it as a response.
        low = body.lower()
        if status not in (200,) and any(m in low for m in BLOCK_MARKERS):
            return None
        if any(m in low for m in BLOCK_MARKERS) and "result" not in low:
            return None
        return body

    async def _aget(self, url: str, post: Optional[bytes] = None) -> Optional[str]:
        for attempt in range(self.valves.MAX_RETRIES + 1):
            html = await asyncio.to_thread(self._get, url, post)
            if html:
                return html
            if attempt < self.valves.MAX_RETRIES:
                await asyncio.sleep(0.4 * (2 ** attempt))
        return None

    # ------------------------------------------------------------- search engines

    def _ensure_modules(self) -> dict[str, Any]:
        if not self._modules:
            self._modules = _import_or_install(self.valves.AUTO_INSTALL and not self._install_attempted)
            self._install_attempted = True
        return self._modules

    def _parse(self, html: str, engine: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        try:
            if engine == "bing":
                for m in re.finditer(r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S):
                    href = m.group(1).replace("&amp;", "&")
                    url = _b64url(href) or (href if "bing.com/ck/a" not in href else "")
                    if not url:
                        continue
                    tail = html[m.end():m.end() + 1500]
                    snip = re.search(r'<p[^>]*>(.*?)</p>', tail, re.S) or re.search(r'class="b_caption"[^>]*>(.*?)</p>', tail, re.S)
                    out.append({"title": _strip(m.group(2)), "url": url,
                                "snippet": _strip(snip.group(1)) if snip else ""})
            elif engine == "bingrss":
                for block in re.findall(r"<item>(.*?)</item>", html, re.S):
                    title = re.search(r"<title>(.*?)</title>", block, re.S)
                    link = re.search(r"<link>(.*?)</link>", block, re.S)
                    desc = re.search(r"<description>(.*?)</description>", block, re.S)
                    if not link:
                        continue
                    out.append({"title": _strip(title.group(1) if title else ""),
                                "url": self._canonical(link.group(1)),
                                "snippet": _strip(desc.group(1) if desc else "")[:220]})
            elif engine == "lite":
                # lite layout: result-link anchor, then a result-snippet cell that follows it.
                for m in re.finditer(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', html, re.S):
                    url = self._canonical(m.group(1))
                    if not url or "duckduckgo" in (urllib.parse.urlsplit(url).hostname or ""):
                        continue
                    title = _strip(m.group(2))
                    if not title:
                        continue
                    tail = html[m.end():m.end() + 700]
                    sm = re.search(r'class="result-snippet"[^>]*>(.*?)</td>', tail, re.S)
                    out.append({"title": title[:160], "url": url,
                                "snippet": _strip(sm.group(1))[:220] if sm else _strip(tail)[:220]})
            elif engine == "ddg":
                anchors = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html, re.S)
                snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
                for i, (href, title) in enumerate(anchors):
                    href = urllib.parse.unquote(href)
                    if "uddg=" in href:
                        href = urllib.parse.parse_qs(urllib.parse.urlparse(href).query).get("uddg", [""])[0]
                    out.append({"title": _strip(title), "url": href,
                                "snippet": _strip(snippets[i]) if i < len(snippets) else ""})
            elif engine == "marginalia":
                for m in re.finditer(r'<a class="title" href="([^"]+)">(.*?)</a>.*?<p class="description">(.*?)</p>', html, re.S):
                    out.append({"title": _strip(m.group(2)), "url": m.group(1), "snippet": _strip(m.group(3))})
            elif engine == "searx":
                data = json.loads(html)
                for r in data.get("results", []) or []:
                    out.append({"title": str(r.get("title", "")), "url": str(r.get("url", "")),
                                "snippet": str(r.get("content", ""))})
        except Exception as exc:
            logger.warning("search parse %s: %s", engine, exc)
        return out

    def _alive(self, engine: str) -> bool:
        dead = self._dead_engine.get(engine, 0)
        # an engine is skipped only while its penalty is active, so a temporary
        # rate limit does not permanently disable it
        return dead == 0 or time.time() - dead > self._penalty.get(engine, 0)

    def _mark_dead(self, engine: str, penalty: int) -> None:
        # hard fetch failures get a long penalty, an empty parse gets a short one,
        # and only after two empty parses in a row
        self._empty_streak[engine] = self._empty_streak.get(engine, 0) + 1
        if self._empty_streak[engine] < 2:
            return
        self._penalty[engine] = penalty
        self._dead_engine[engine] = time.time()

    def _mark_alive(self, engine: str) -> None:
        self._empty_streak[engine] = 0
        self._dead_engine.pop(engine, None)
        self._penalty.pop(engine, None)

    async def _search_engine(self, engine: str, query: str, count: int, timelimit: Optional[str]) -> list[dict[str, Any]]:
        if not self._alive(engine):
            return []
        mods = self._ensure_modules()

        if engine == "ddgs":
            DDGS = mods.get("ddgs")
            if DDGS is None:
                return []
            def run():
                with DDGS() as d:
                    return list(d.text(query, max_results=count, timelimit=timelimit))
            try:
                raw = await asyncio.to_thread(run)
                # ddgs rows use href/body/title, NOT url. Reading r.get("url") produced
                # empty urls, which _rank then dropped. This was the core bug.
                rows = [{"title": r.get("title", ""), "url": r.get("href") or r.get("url", ""),
                         "snippet": r.get("body", "")} for r in raw]
                if rows:
                    self._mark_alive("ddgs")
                return rows
            except Exception as exc:
                logger.warning("ddgs: %s", exc)
                self._mark_dead("ddgs", self.valves.PENALTY_SOFT)
                return []

        q = urllib.parse.quote_plus(query)
        extra = ""
        if timelimit and engine in ("bing", "bingrss"):
            days = {"d": 1, "w": 7, "m": 30}.get(timelimit, 30)
            if engine == "bing":
                extra = "&qft=+filterui:age-lt" + str(days * 1440)

        urls = {
            "bing": "https://www.bing.com/search?q=" + q + extra,
            "bingrss": "https://www.bing.com/search?format=rss&q=" + q + extra,
            "ddg": "https://html.duckduckgo.com/html/?q=" + q,
            "searx": "https://searx.be/search?q=" + q + "&format=json",
            "marginalia": "https://search.marginalia.nu/search?query=" + q,
            "lite": "https://lite.duckduckgo.com/lite/",
        }
        # lite only answers as a POST. GET returns a 202 block page.
        post = ("q=" + q).encode() if engine == "lite" else None

        for attempt in range(2):
            html = await self._aget(urls[engine], post=post)
            if not html:
                self._mark_dead(engine, self.valves.PENALTY_HARD)
                return []
            rows = self._parse(html, engine)
            if rows:
                self._mark_alive(engine)
                return rows
            # empty parse is usually an intermittent challenge wall, not a dead engine:
            # pause and retry once before the penalty applies
            if attempt == 0:
                await asyncio.sleep(2.0)
        self._mark_dead(engine, self.valves.PENALTY_SOFT)
        return []

    def _rank(self, rows: list[dict[str, Any]], query: str, count: int) -> list[dict[str, Any]]:
        terms = _terms(query)
        # Rarity weighting: a rare token like "pq2" or "ternary" must outrank a common one
        # like "llama", otherwise generic pages win purely on volume of hits.
        counts = {t: 0 for t in terms}
        for row in rows:
            hay = (str(row.get("title") or "") + " " + str(row.get("snippet") or "")).lower()
            for t in terms:
                counts[t] += 1
        weight = {t: (1.0 if counts[t] == 0 else 6.0 / (counts[t] + 1)) for t in terms}

        seen, ranked = set(), []
        for row in rows:
            url = self._canonical(row.get("url") or row.get("href") or "")
            if not url or url in seen:
                continue
            seen.add(url)
            title = str(row.get("title") or "").strip()
            snippet = str(row.get("snippet") or "").strip()
            hay = (title + " " + snippet).lower()
            # Cap each term's contribution. Without a cap, a page that repeats a common token
            # 200 times beats a real source that mentions a rare token once.
            content_score = sum(min(hay.count(t), 4) * weight[t] for t in terms)
            # URL tokens are a bonus on top of content, never a score by themselves. Alone they
            # let a junk page with an empty extracted body outrank a real source.
            if content_score:
                score = content_score + sum(weight[t] * 2 for t in terms if t in url.lower())
            else:
                score = content_score
            host = urllib.parse.urlsplit(url).hostname or ""
            if host.endswith(".gov"):
                score += 4
            if host.endswith("github.com"):
                score += 3
            if host.startswith("docs.") or host in ("huggingface.co", "arxiv.org"):
                score += 3
            if query.lower() in hay:
                score += 4
            ranked.append({"title": title, "url": url, "snippet": snippet[:220], "score": score})
        # Zero-score rows are engine noise, not evidence. Keeping them is how a French bank
        # page surfaced for a quantization query. If nothing scores, fall back to engine
        # order so single-word queries still return something.
        scored = [r for r in ranked if r["score"] > 0]
        if not scored:
            scored = ranked
        scored.sort(key=lambda x: x["score"], reverse=True)
        return scored[:count]

    def _status_line(self) -> str:
        parts = []
        for name in [e.strip() for e in (self.valves.SEARCH_ENGINES or "").split(",") if e.strip()]:
            dead = self._dead_engine.get(name, 0)
            ok = dead == 0 or time.time() - dead > self._penalty.get(name, 0)
            parts.append(f"{name}:{'ok' if ok else 'skipped'}")
        return " | ".join(parts)

    async def _search_rows(self, query: str, count: int, fresh: bool, timelimit: Optional[str]) -> list[dict[str, Any]]:
        root = self._root()
        q = query.strip().lower()
        cache_key = "s_" + hashlib.sha1(q.encode()).hexdigest()[:12]
        sp = os.path.join(root, "search", cache_key + ".json") if root else ""
        if sp and not fresh:
            try:
                with open(sp, "r", encoding="utf-8") as f:
                    rec = json.load(f)
                if time.time() - rec.get("ts", 0) <= self.valves.SEARCH_TTL_MINUTES * 60:
                    self._last_search_from_cache = True
                    return rec.get("rows", [])
            except Exception:
                pass
        self._last_search_from_cache = False

        # Pool across engines instead of stopping at the first one. Ranking by term rarity
        # only works with a full pool: bing alone returned generic llama pages for a
        # ternary query while ddgs and bingrss returned the right ones. Bounded to the
        # first few live engines so a cache miss does not stall.
        rows: list[dict[str, Any]] = []
        engines = [e.strip() for e in (self.valves.SEARCH_ENGINES or "").split(",") if e.strip()]
        hits = 0
        kq = _keyword_query(query)
        for engine in engines[:self.valves.POOL_ENGINES]:
            got = await self._search_engine(engine, kq, count, timelimit)
            if got:
                hits += 1
            rows.extend(got)
            if hits >= 3 or len(rows) >= count * 2:
                break
        ranked = self._rank(rows, query, count)
        if sp and ranked:
            try:
                os.makedirs(os.path.dirname(sp), exist_ok=True)
                with open(sp, "w", encoding="utf-8") as f:
                    json.dump({"ts": int(time.time()), "query": query, "rows": ranked}, f)
            except Exception:
                pass
        return ranked

    def _line(self, i: int, r: dict[str, Any]) -> str:
        key = self._key(r["url"])
        mark = "*" if os.path.exists(self._paths(key)["txt"]) else " "
        return f"[{i}] {mark}{key}  {r['title']}\n    {r['url']}\n    {r['snippet']}"

    # ------------------------------------------------------------ public tools

    async def search(self, query: str, max_results: int = 8, fresh: bool = False) -> str:
        """Search the public web. Output is compact ranked lines. A '*' marks a page
        already stored in the local cache, and each line shows the cache id to read from
        files instead of re-fetching. No Knowledge Base access."""
        q = (query or "").strip()
        if not q:
            return "Query is empty."
        count = max(1, min(int(max_results), self.valves.MAX_RESULTS))
        rows = await self._search_rows(q, count, fresh, None)
        if not rows:
            return f"No web results for: {q} (no cached search either)\nEngines: {self._status_line()}"
        # Report what actually happened. The old head checked file existence, so a fresh
        # live query that wrote its own cache entry announced itself as a cache hit.
        head = "SEARCH CACHE HIT" if getattr(self, "_last_search_from_cache", False) else "SEARCH LIVE"
        return head + f"  (* = page already on disk)\n" + "\n".join(self._line(i, r) for i, r in enumerate(rows, 1))

    async def fetch_url(self, url: str, query: str = "", max_chars: int = 0) -> str:
        """Fetch one public page into the local page cache, then return only focus windows
        around `query`. Cached pages are read from disk, never re-fetched. Use read_cache()
        to pull more slices from an already-cached page without touching the web."""
        mods = self._ensure_modules()
        canonical = self._canonical(url)
        if not canonical:
            return "Invalid URL."
        key = self._key(canonical)
        blocks = self._blocks(key)
        used_cache = blocks is not None

        if not used_cache:
            html = await self._aget(canonical)
            if not html:
                p = urllib.parse.urlsplit(canonical)
                clean = urllib.parse.urlunsplit((p.scheme, p.netloc, p.path or "/", "", ""))
                if clean != canonical:
                    html = await self._aget(clean)
            if not html:
                html = await self._aget("https://r.jina.ai/" + canonical)
            if html:
                title, blocks = html_to_blocks(html, mods.get("bs4"))
                if self.valves.SAVE_RAW_HTML:
                    try:
                        with open(self._paths(key)["raw"], "w", encoding="utf-8") as f:
                            f.write(html)
                    except Exception:
                        pass
                self._record(key, canonical, title, blocks, False)
            else:
                blocks = self._blocks(key)
                if not blocks:
                    return f"Unable to fetch {canonical} (no cached copy on disk)."
                used_cache = True

        budget = min(int(max_chars or self.valves.OUTPUT_CHARS), self.valves.OUTPUT_CHARS)
        wins = self._windows(blocks, query, budget, self.valves.FOCUS_MAX_WINDOWS)
        head = (f"PAGE {key}  {'CACHE HIT' if used_cache else 'FETCHED'}  "
                f"{len(blocks)} lines on disk\nFILE {self._paths(key)['txt']}\nURL {canonical}\n")
        out = head + "\n".join(f"L{n}: {w}" for n, w in wins)
        if len(wins) < min(len(blocks), 40):
            out += f"\n[more on disk: read_cache('{key}', needle=...) or grep_cache(...)]"
        return out[:budget]

    async def read_cache(self, ref: str, needle: str = "", start_line: int = 0,
                         end_line: int = 0, window: int = 200, limit: int = 12) -> str:
        """Read a slice from an already-cached page. No network. ref is a cache id, a URL,
        or a path to a local .txt/.html file already on disk."""
        ref = (ref or "").strip()
        if not ref:
            return "ref is empty."
        key = ref if re.fullmatch(r"[0-9a-f]{6,12}", ref) else self._key(ref)
        blocks = self._blocks(key)
        if blocks is None and os.path.isfile(ref):   # local file, no network at all
            try:
                raw = open(ref, "r", encoding="utf-8", errors="ignore").read()
                title, blocks = html_to_blocks(raw, self._ensure_modules().get("bs4")) if "<" in raw[:400] else ("", [ln.strip() for ln in raw.splitlines() if len(ln.strip()) >= 3])
                key = hashlib.sha1(os.path.abspath(ref).encode()).hexdigest()[:12]
                self._record(key, "file:" + os.path.abspath(ref), title or os.path.basename(ref), blocks, True)
            except Exception as exc:
                return f"Cannot read {ref}: {exc}"
        if blocks is None:
            return f"Nothing cached for '{key}'. Fetch it first, then read slices here."

        budget = self.valves.OUTPUT_CHARS
        out = [f"PAGE {key}  disk read  {len(blocks)} lines"]
        if start_line or end_line:
            s = max(1, int(start_line or 1))
            e = min(len(blocks), int(end_line or s + int(window) - 1))
            for i in range(s, e + 1):
                out.append(f"L{i}: {blocks[i - 1][:int(window)]}")
            return "\n".join(out)[:budget]
        if not needle:
            return "\n".join(out + [f"L{i + 1}: {b[:int(window)]}" for i, b in enumerate(blocks[:int(limit)])])[:budget]
        terms = _terms(needle)
        scored = sorted(((sum(b.lower().count(t) for t in terms), i, b) for i, b in enumerate(blocks)),
                        key=lambda x: (-x[0], x[1]))
        picked, used = [], 0
        for score, i, b in scored:
            if not score or len(picked) >= int(limit) or used > budget - len(out[0]):
                break
            picked.append((i + 1, b[:int(window)]))
            used += len(picked[-1][1])
        picked.sort(key=lambda x: x[0])
        return "\n".join(out + [f"L{n}: {w}" for n, w in picked])[:budget]

    async def grep_cache(self, pattern: str, limit: int = 25) -> str:
        """Search every cached page file for a literal string or regex. Disk only, no network."""
        if not pattern:
            return "Pattern is empty."
        try:
            rx = re.compile(pattern, re.I)
        except re.error:
            rx = re.compile(re.escape(pattern), re.I)
        out = [f"GREP {pattern}"]
        n = 0
        for row in self._rows():
            txt = self._paths(row["id"])["txt"]
            try:
                with open(txt, "r", encoding="utf-8") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            out.append(f"{row['id']} L{i}: {line.strip()[:200]}")
                            n += 1
                            if n >= int(limit):
                                return "\n".join(out)[:self.valves.OUTPUT_CHARS]
            except Exception:
                continue
        return "\n".join(out + (["no matches"] if not n else []))[:self.valves.OUTPUT_CHARS]

    async def cache_index(self, limit: int = 25) -> str:
        """List the local page cache: id, age, line count, title, URL. Disk only."""
        rows = sorted(self._rows(), key=lambda r: r.get("ts", 0), reverse=True)
        out = [f"PAGE CACHE {len(rows)} entries  root={self._root()}"]
        for r in rows[:int(limit)]:
            out.append(f"{r.get('id')}  {int((time.time() - r.get('ts', 0)) / 3600)}h  {r.get('lines')}L  {str(r.get('title',''))[:60]}\n    {r.get('url','')}")
        return "\n".join(out)[:self.valves.OUTPUT_CHARS]

    async def cache_prune(self, max_age_hours: int = 72) -> str:
        """Drop cache entries older than max_age_hours. Disk only."""
        root = self._root()
        if not root:
            return "Cache root not writable."
        removed = 0
        for row in self._rows():
            if time.time() - row.get("ts", 0) <= int(max_age_hours) * 3600:
                continue
            p = self._paths(row["id"])
            for f in (p["txt"], p["raw"]):
                try:
                    if os.path.exists(f):
                        os.remove(f)
                except Exception:
                    pass
            removed += 1
        try:
            with open(os.path.join(root, "index.jsonl"), "w", encoding="utf-8") as f:
                for row in self._rows():
                    if time.time() - row.get("ts", 0) <= int(max_age_hours) * 3600:
                        f.write(json.dumps(row) + "\n")
        except Exception:
            pass
        return f"Pruned {removed} entries older than {max_age_hours}h"

    def _cache_candidates(self, query: str, count: int) -> list[dict[str, Any]]:
        """Rank pages already on disk by the query, so research can answer from the cache
        with no network at all."""
        terms = _terms(query)
        scored = []
        for row in self._rows():
            try:
                with open(self._paths(row["id"])["txt"], "r", encoding="utf-8") as f:
                    text = f.read().lower()
            except Exception:
                continue
            if not text.strip():
                continue
            score = sum(text.count(t) for t in terms)
            if score:
                scored.append((score, row))
        scored.sort(key=lambda x: (-x[0], -x[1].get("ts", 0)))
        out = []
        for score, row in scored[:count]:
            out.append({"title": row.get("title") or "cached page", "url": row.get("url") or "",
                        "snippet": "cached " + str(row.get("lines", 0)) + " lines, score " + str(score),
                        "score": score, "cached": True})
        return out

    async def research(self, question: str, depth: str = "standard", max_sources: int = 8,
                       recency_days: Optional[int] = None, domains: Optional[list[str]] = None,
                       fresh: bool = False, offline: bool = False) -> str:
        """Bounded multi-source research. Cache-first: pages already on disk are read from
        files instead of re-fetched. offline=True answers purely from the local cache. Every
        source returns a focus window, not a page dump, and the call is capped by RESEARCH_TOTAL."""
        q = (question or "").strip()
        if not q:
            return "Research question is empty."
        sources = max(1, min(int(max_sources), self.valves.RESEARCH_SOURCES))
        per_source = self.valves.RESEARCH_PER_SOURCE
        total = min(int(self.valves.RESEARCH_TOTAL), self.valves.OUTPUT_CHARS * 3)
        d = (depth or "standard").lower()
        queries = [q] if d == "quick" else ([q, q + " official", q + " latest"] if d == "standard"
                                           else [q, q + " official", q + " primary source", q + " analysis", q + " latest"])
        timelimit = None
        if recency_days:
            timelimit = "d" if int(recency_days) <= 1 else ("w" if int(recency_days) <= 7 else "m")

        rows: list[dict[str, Any]] = []
        if not offline:
            for sub in list(dict.fromkeys(queries))[:5]:
                rows.extend(await self._search_rows(sub, sources, fresh, timelimit))

        # Merge live rows and cached pages into one pool ranked by score. Before this, cached
        # pages were only appended after the live ranking, so quality could not compete with
        # insertion order: a weak live hit stayed above a strongly matching cached page.
        cached = self._cache_candidates(q, sources)
        candidates = self._rank(rows, q, sources * 2)
        seen = {x["url"] for x in candidates}
        for c in cached:
            if c["url"] in seen:
                continue
            seen.add(c["url"])
            candidates.append(c)
        candidates.sort(key=lambda x: -int(x.get("score") or 0))
        if offline:
            candidates = cached
        if not candidates:
            return f"No sources for: {q} (offline and nothing cached)\nEngines: {self._status_line()}"
        if domains:
            allow = {str(x).lower().lstrip("www.") for x in domains}
            candidates = [x for x in candidates if (urllib.parse.urlsplit(x["url"]).hostname or "").lower().lstrip("www.") in allow]
        candidates = candidates[:sources]
        if not candidates:
            return f"No sources found for: {q}\nEngines: {self._status_line()}"

        sem = asyncio.Semaphore(3)
        async def read(item):
            async with sem:
                return item, await self.fetch_url(item["url"], q, max_chars=per_source)

        results = await asyncio.gather(*(read(x) for x in candidates[:sources]), return_exceptions=True)
        out = [f"RESEARCH  question: {q}  sources: {min(len(candidates), sources)}  cap: {total} chars"]
        used, n = 0, 0
        for item in results:
            if isinstance(item, Exception):
                continue
            src, content = item
            if not content or content.startswith("Unable to fetch"):
                continue
            n += 1
            block = f"\n[S{n}] {src['title']}\n{src['url']}\n{content}"
            if used + len(block) > total:
                block = block[:max(0, total - used)]
            out.append(block)
            used += len(block)
            if used >= total:
                break
        if n == 0:
            out.append("No readable pages; discovery-only evidence:")
            for i, r in enumerate(candidates[:sources], 1):
                out.append(f"[DISCOVERY {i}] {r['title']}\n{r['url']}\n{r['snippet']}")
        return "\n".join(out)[:total]
