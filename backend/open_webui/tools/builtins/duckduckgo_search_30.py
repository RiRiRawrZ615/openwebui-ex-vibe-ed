'''"""Open Web Vibes builtin: Web Search

Generated from the WebUI tool export `custom_duckduckgo_search_30` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: Web Search
Exported functions: fetch_url, research, search

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''
import asyncio
import json
import logging
import re
import ssl
import urllib.parse
import urllib.request
from typing import Any, Optional
try:
    from ddgs import DDGS
except Exception:
    DDGS = None
try:
    import requests
except Exception:
    requests = None
try:
    from bs4 import BeautifulSoup, Comment
except Exception:
    BeautifulSoup = None
    Comment = None
logger = logging.getLogger('openwebui_web_search_30')

class Tools:

    class Valves:
        USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36'
        REQUEST_TIMEOUT = 20
        MAX_RESPONSE_BYTES = 8000000
        MAX_RETRIES = 2
        MAX_RESULTS = 10
        RESEARCH_SOURCES = 8

    def __init__(self):
        self.valves = self.Valves()

    def _fetch_sync(self, url: str) -> str:
        headers = {'User-Agent': self.valves.USER_AGENT, 'Accept-Language': 'en-US,en;q=0.8', 'Accept': 'text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.2'}
        if requests is not None:
            r = requests.get(url, headers=headers, timeout=self.valves.REQUEST_TIMEOUT, allow_redirects=True, stream=True)
            r.raise_for_status()
            data = bytearray()
            try:
                for part in r.iter_content(chunk_size=65536):
                    if part:
                        data.extend(part)
                        if len(data) > self.valves.MAX_RESPONSE_BYTES:
                            raise ValueError('response exceeds size limit')
            finally:
                r.close()
            return bytes(data).decode(r.encoding or 'utf-8', 'ignore')
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=self.valves.REQUEST_TIMEOUT, context=ssl.create_default_context()) as resp:
            data = resp.read(self.valves.MAX_RESPONSE_BYTES + 1)
        if len(data) > self.valves.MAX_RESPONSE_BYTES:
            raise ValueError('response exceeds size limit')
        return data.decode('utf-8', 'ignore')

    async def _fetch(self, url: str) -> tuple[Optional[str], Optional[str]]:
        last_error = 'fetch failed'
        for attempt in range(self.valves.MAX_RETRIES + 1):
            try:
                return (await asyncio.to_thread(self._fetch_sync, url), None)
            except Exception as exc:
                last_error = str(exc)
                if attempt < self.valves.MAX_RETRIES:
                    await asyncio.sleep(0.4 * 2 ** attempt)
        return (None, last_error)

    def _canonical(self, url: str) -> str:
        if not url:
            return ''
        raw = url if '://' in url else 'https://' + url
        try:
            p = urllib.parse.urlsplit(raw)
            if p.scheme.lower() not in ('http', 'https') or not p.hostname:
                return ''
            return urllib.parse.urlunsplit((p.scheme.lower(), p.hostname.lower(), urllib.parse.quote(urllib.parse.unquote(p.path or '/'), safe="/:@-._~!$&'()*+,;="), p.query, ''))
        except Exception:
            return ''

    def _search_queries(self, query: str) -> list[str]:
        q = query.strip()
        low = q.lower()
        queries = [q]
        if any((x in low for x in ('latest', 'current', 'recent', 'today', 'news'))):
            queries.append(q + ' 2026')
        if any((x in low for x in ('api', 'sdk', 'library', 'package', 'documentation', 'linux'))):
            queries.append(q + ' official documentation')
        return list(dict.fromkeys(queries))[:3]

    async def _ddg(self, query: str, count: int) -> list[dict[str, Any]]:
        if DDGS is None:
            return []
        try:

            def run():
                with DDGS() as ddgs:
                    return list(ddgs.text(query, max_results=count))
            return await asyncio.to_thread(run)
        except Exception as exc:
            logger.warning('DDGS failed: %s', exc)
            return []

    async def _html_search(self, query: str, engine: str, count: int) -> list[dict[str, Any]]:
        base = 'https://html.duckduckgo.com/html/?q=' if engine == 'ddg' else 'https://www.bing.com/search?q='
        html, _ = await self._fetch(base + urllib.parse.quote_plus(query))
        if not html or BeautifulSoup is None:
            return []
        soup = BeautifulSoup(html, 'html.parser')
        out = []
        selector = '.result' if engine == 'ddg' else 'li.b_algo'
        for node in soup.select(selector):
            a = node.select_one('.result__a' if engine == 'ddg' else 'h2 a')
            if not a:
                continue
            p = node.select_one('.result__snippet' if engine == 'ddg' else 'p')
            out.append({'title': a.get_text(' ', strip=True), 'href': a.get('href', ''), 'body': p.get_text(' ', strip=True) if p else ''})
            if len(out) >= count:
                break
        return out

    def _rank(self, rows: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
        q_tokens = [x for x in re.findall('\\w+', query.lower()) if len(x) > 2]
        seen = set()
        ranked = []
        for row in rows:
            url = self._canonical(row.get('href') or row.get('url') or '')
            if not url or url in seen:
                continue
            seen.add(url)
            title = str(row.get('title') or '').strip()
            body = str(row.get('body') or row.get('snippet') or '').strip()
            hay = (title + ' ' + body).lower()
            score = sum((hay.count(t) for t in q_tokens))
            host = urllib.parse.urlsplit(url).hostname or ''
            if host.endswith('.gov'):
                score += 4
            if host.endswith('github.com'):
                score += 3
            if host.startswith('docs.'):
                score += 3
            if query.lower() in hay:
                score += 4
            ranked.append({'title': title, 'url': url, 'snippet': body[:320], 'score': score})
        ranked.sort(key=lambda x: x['score'], reverse=True)
        return ranked

    def _extract(self, html: str, query: str='') -> str:
        if BeautifulSoup is None:
            text = re.sub('(?is)<(script|style|noscript).*?>.*?</\\1>', ' ', html)
            return re.sub('\\s+', ' ', re.sub('(?s)<[^>]+>', ' ', text)).strip()
        soup = BeautifulSoup(html, 'html.parser')
        for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside', 'form', 'noscript', 'iframe', 'svg']):
            tag.decompose()
        main = None
        for selector in ('#mw-content-text', 'article', 'main', '.markdown-body', '.documentation', '.rst-content', '.md-content', "[role='main']", '#content', 'body'):
            main = soup.select_one(selector)
            if main:
                break
        main = main or soup
        parts = []
        for el in main.find_all(['h1', 'h2', 'h3', 'h4', 'p', 'pre', 'table', 'ul', 'ol', 'blockquote']):
            text = el.get_text('\n' if el.name in {'pre', 'table', 'ul', 'ol'} else ' ', strip=True)
            text = re.sub('[ \\t]+', ' ', text).strip()
            if not text or len(text) < 3:
                continue
            if el.name == 'pre':
                text = '```text\n' + text + '\n```'
            parts.append(text)
        full = '\n\n'.join(parts)
        if not query.strip():
            return full
        terms = [x for x in re.findall('\\w+', query.lower()) if len(x) > 2]
        units = full.split('\n\n')
        scored = sorted(((sum((u.lower().count(t) for t in terms)), i, u) for i, u in enumerate(units)), key=lambda x: (-x[0], x[1]))
        selected = [u for score, _, u in scored[:24] if score]
        return '\n\n'.join(selected) if selected else full

    async def search(self, query: str, max_results: int=8) -> str:
        """Search the public web. This tool does not access private Knowledge Bases."""
        if not (query or '').strip():
            return 'Query is empty.'
        count = max(1, min(int(max_results), self.valves.MAX_RESULTS))
        rows = []
        for q in self._search_queries(query):
            rows.extend(await self._ddg(q, count))
        if len(rows) < 4:
            rows.extend(await self._html_search(query, 'ddg', count))
        if len(rows) < 4:
            rows.extend(await self._html_search(query, 'bing', count))
        ranked = self._rank(rows, query)[:count]
        if not ranked:
            return 'No web results found.'
        return '\n\n'.join((f"[S{i}] {r['title']}\nURL: {r['url']}\nSnippet: {r['snippet']}" for i, r in enumerate(ranked, 1)))

    async def fetch_url(self, url: str, query: str='') -> str:
        """Fetch and extract one public webpage. Use query to focus extraction."""
        canonical = self._canonical(url)
        if not canonical:
            return 'Invalid URL.'
        html, err = await self._fetch(canonical)
        if html:
            return self._extract(html, query)[:self.valves.MAX_RESPONSE_BYTES]
        parsed = urllib.parse.urlsplit(canonical)
        clean = urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, parsed.path or '/', '', ''))
        if clean != canonical:
            html, _ = await self._fetch(clean)
            if html:
                return self._extract(html, query)
        jina = 'https://r.jina.ai/' + canonical
        html, _ = await self._fetch(jina)
        if html:
            return self._extract(html, query)
        return f"Unable to fetch {canonical}: {err or 'unknown error'}"

    async def research(self, question: str, depth: str='standard', max_sources: int=8, recency_days: Optional[int]=None, domains: Optional[list[str]]=None) -> str:
        """Run bounded multi-source public-web research and return source-labeled evidence."""
        if not question.strip():
            return 'Research question is empty.'
        source_count = max(1, min(int(max_sources), self.valves.RESEARCH_SOURCES))
        queries = [question, question + ' official', question + ' latest']
        if depth.lower() == 'quick':
            queries = queries[:1]
        elif depth.lower() == 'deep':
            queries.append(question + ' primary source')
            queries.append(question + ' analysis')
        rows = []
        for q in list(dict.fromkeys(queries))[:5]:
            rows.extend(await self._ddg(q, source_count))
        if len(rows) < 4:
            rows.extend(await self._html_search(question, 'ddg', source_count))
        if len(rows) < 4:
            rows.extend(await self._html_search(question, 'bing', source_count))
        candidates = self._rank(rows, question)[:source_count]
        if domains:
            allow = {d.lower().lstrip('www.') for d in domains}
            candidates = [x for x in candidates if (urllib.parse.urlsplit(x['url']).hostname or '').lower().lstrip('www.') in allow]
        sem = asyncio.Semaphore(5)

        async def read(item):
            async with sem:
                content = await self.fetch_url(item['url'], question)
                return (item, content)
        fetched = await asyncio.gather(*(read(x) for x in candidates), return_exceptions=True)
        out = [f'Research evidence\nQuestion: {question}\n']
        n = 0
        for item in fetched:
            if isinstance(item, Exception):
                continue
            result, content = item
            if not content or content.startswith('Unable to fetch') or content == 'Invalid URL.':
                continue
            n += 1
            out.extend([f"[S{n}] {result['title']}", f"URL: {result['url']}", 'Evidence:', content[:12000], ''])
        if not n:
            ranked = candidates
            if ranked:
                out.append('No source pages were readable, so returning search-result evidence:')
                for i, r in enumerate(ranked, 1):
                    out.extend([f"[DISCOVERY {i}] {r['title']}", f"URL: {r['url']}", r['snippet'], ''])
            else:
                out.append('No useful sources were found.')
        return '\n'.join(out)
_builtin_tools = Tools()

async def search(self, query: str, max_results: int=8) -> str:
    """Search the public web. This tool does not access private Knowledge Bases."""
    return await _builtin_tools.search(query=query, max_results=max_results)

async def fetch_url(self, url: str, query: str='') -> str:
    """Fetch and extract one public webpage. Use query to focus extraction."""
    return await _builtin_tools.fetch_url(url=url, query=query)

async def research(self, question: str, depth: str='standard', max_sources: int=8, recency_days: Optional[int]=None, domains: Optional[list[str]]=None) -> str:
    """Run bounded multi-source public-web research and return source-labeled evidence."""
    return await _builtin_tools.research(question=question, depth=depth, max_sources=max_sources, recency_days=recency_days, domains=domains)
