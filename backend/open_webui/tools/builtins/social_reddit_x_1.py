'''"""Open Web Vibes builtin: Reddit + X Reader

Generated from the WebUI tool export `custom_social_reddit_x_1` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: Reddit + X Reader
Exported functions: fetch_social, reddit_subreddit, reddit_thread, reddit_timeline, reddit_user, social_search, x_profile, x_tweet

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''
import asyncio
import base64
import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, Optional
try:
    from ddgs import DDGS
except Exception:
    DDGS = None
try:
    import requests
except Exception:
    requests = None
logger = logging.getLogger('openwebui_social_1')

class Tools:

    class Valves:
        USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138 Safari/537.36'
        REDDIT_USER_AGENT = 'android:com.openwebui.social:1.0 (by /u/openwebui)'
        REQUEST_TIMEOUT = 25
        MAX_RESPONSE_BYTES = 4000000
        MAX_RETRIES = 2
        MIN_REQUEST_GAP = 1.2
        MAX_ITEMS = 50
        TEXT_CAP = 1800
        PUBLIC_REDDIT_CLIENT_ID = 'ohXpoqrZYub1kg'

    class UserValves:
        reddit_client_id = ''
        reddit_client_secret = ''
        reddit_username = ''
        reddit_password = ''
        twitterapi_io_key = ''
        nitter_instance = ''

    def __init__(self):
        self.valves = self.Valves()
        self.user_valves = self.UserValves()
        self._token = ''
        self._token_exp = 0.0
        self._last_request = 0.0

    async def _throttle(self) -> None:
        gap = time.time() - self._last_request
        if gap < self.valves.MIN_REQUEST_GAP:
            await asyncio.sleep(self.valves.MIN_REQUEST_GAP - gap)
        self._last_request = time.time()

    def _http_sync(self, url: str, headers: Optional[dict], method: str, data: Optional[bytes]) -> tuple[int, dict, str]:
        h = {'Accept': '*/*', 'Accept-Language': 'en-US,en;q=0.8'}
        h['User-Agent'] = (headers or {}).get('User-Agent') or self.valves.USER_AGENT
        h.update(headers or {})
        if requests is not None:
            r = requests.request(method, url, headers=h, data=data, timeout=self.valves.REQUEST_TIMEOUT, allow_redirects=True)
            return (r.status_code, dict(r.headers), r.text[:self.valves.MAX_RESPONSE_BYTES])
        req = urllib.request.Request(url, headers=h, data=data, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.valves.REQUEST_TIMEOUT) as resp:
                return (resp.status, dict(resp.headers), resp.read(self.valves.MAX_RESPONSE_BYTES).decode('utf-8', 'ignore'))
        except urllib.error.HTTPError as exc:
            return (exc.code, dict(exc.headers or {}), exc.read(200000).decode('utf-8', 'ignore'))

    async def _http(self, url: str, headers: Optional[dict]=None, method: str='GET', data: Optional[bytes]=None) -> tuple[int, dict, str, Optional[str]]:
        last_error = None
        for attempt in range(self.valves.MAX_RETRIES + 1):
            await self._throttle()
            try:
                status, hdr, body = await asyncio.to_thread(self._http_sync, url, headers, method, data)
                if status in (429, 500, 502, 503, 504) and attempt < self.valves.MAX_RETRIES:
                    retry_after = hdr.get('Retry-After') or hdr.get('retry-after')
                    try:
                        wait = min(60.0, float(retry_after)) if retry_after else 2.0 * 2 ** attempt
                    except ValueError:
                        wait = 2.0 * 2 ** attempt
                    last_error = f'HTTP {status}, retried and failed after waiting {wait:.1f}s'
                    await asyncio.sleep(wait)
                    continue
                return (status, hdr, body, None)
            except Exception as exc:
                last_error = str(exc)
                if attempt < self.valves.MAX_RETRIES:
                    await asyncio.sleep(1.0 * 2 ** attempt)
        return (0, {}, '', last_error)

    async def _reddit_token(self) -> tuple[Optional[str], Optional[str]]:
        if self._token and self._token_exp > time.time() + 60:
            return (self._token, None)
        uv = self.user_valves
        if uv.reddit_client_id:
            if uv.reddit_username and uv.reddit_client_secret:
                params = {'grant_type': 'password', 'username': uv.reddit_username, 'password': uv.reddit_password}
                auth = 'Basic ' + base64.b64encode(f'{uv.reddit_client_id}:{uv.reddit_client_secret}'.encode()).decode()
            else:
                params = {'grant_type': 'https://oauth.reddit.com/grants/installed_client', 'device_id': str(uuid.uuid4())}
                auth = 'Basic ' + base64.b64encode(f'{uv.reddit_client_id}:'.encode()).decode()
        else:
            params = {'grant_type': 'https://oauth.reddit.com/grants/installed_client', 'device_id': str(uuid.uuid4())}
            auth = 'Basic ' + base64.b64encode(f'{self.valves.PUBLIC_REDDIT_CLIENT_ID}:'.encode()).decode()
        status, _, body, err = await self._http('https://www.reddit.com/api/v1/access_token', headers={'Authorization': auth, 'User-Agent': self.valves.REDDIT_USER_AGENT, 'Content-Type': 'application/x-www-form-urlencoded'}, method='POST', data=urllib.parse.urlencode(params).encode())
        if status != 200:
            return (None, f"reddit token failed (HTTP {status}): {(body or err or '')[:200]}")
        try:
            token = json.loads(body).get('access_token')
        except Exception:
            return (None, 'reddit token returned non-JSON')
        if not token:
            return (None, 'reddit token response had no access_token')
        self._token = token
        self._token_exp = time.time() + 3600
        return (token, None)

    async def _reddit_api(self, path: str) -> tuple[Optional[dict], Optional[str]]:
        token, err = await self._reddit_token()
        if not token:
            return (None, err)
        status, _, body, err2 = await self._http('https://oauth.reddit.com' + path, headers={'Authorization': 'Bearer ' + token, 'User-Agent': self.valves.REDDIT_USER_AGENT})
        if status != 200:
            return (None, f"reddit {path} failed (HTTP {status}): {(body or err2 or '')[:200]}")
        try:
            return (json.loads(body), None)
        except Exception:
            return (None, 'reddit returned non-JSON')

    async def _archive(self, endpoint: str, params: dict) -> list[dict]:
        status, _, body, _ = await self._http('https://arctic-shift.photon-reddit.com/api/' + endpoint + '?' + urllib.parse.urlencode(params), headers={'Accept': 'application/json'})
        if status != 200:
            return []
        try:
            return json.loads(body).get('data', []) or []
        except Exception:
            return []

    @staticmethod
    def _reddit_id(value: str) -> str:
        raw = (value or '').strip()
        m = re.search('/comments/([0-9a-z]{5,10})', raw) or re.search('/([0-9a-z]{5,10})/comments', raw)
        if m:
            return m.group(1)
        bare = re.search('\\bt3_([0-9a-z]{5,10})\\b', raw) or re.search('\\b([0-9a-z]{6,8})\\b', raw)
        return bare.group(1) if bare else ''

    @staticmethod
    def _reddit_sub(value: str) -> str:
        m = re.search('reddit\\.com/r/([A-Za-z0-9_]+)', value or '')
        if m:
            return m.group(1)
        return (value or '').strip().lstrip('r/').split('/')[0]

    @staticmethod
    def _ts(epoch: Any) -> str:
        try:
            return time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(int(epoch or 0)))
        except Exception:
            return 'unknown time'

    @staticmethod
    def _clip(text: str, cap: int) -> str:
        text = (text or '').strip()
        return text[:cap] + ('...' if len(text) > cap else '')

    def _fmt_post(self, d: dict, cap: int) -> str:
        sub = d.get('subreddit') or '?'
        pid = d.get('id') or ''
        lines = [f"[{pid}] {self._ts(d.get('created_utc'))} | r/{sub} | u/{d.get('author')}", f"Title: {(d.get('title') or '').strip()}", f"Score {d.get('score')} | {d.get('num_comments')} comments"]
        body = (d.get('selftext') or '').strip()
        if body:
            lines.append('Body: ' + self._clip(body, cap))
        thread_url = f'https://reddit.com/r/{sub}/comments/{pid}'
        lines.append('URL: ' + thread_url)
        extra = (d.get('url') or '').strip()
        if extra and extra != thread_url:
            lines.append('Media/link: ' + extra)
        return '\n'.join(lines)

    def _fmt_comment(self, c: dict, cap: int) -> str:
        return f"[{c.get('id')}] {self._ts(c.get('created_utc'))} | u/{c.get('author')} | score {c.get('score')} | depth {c.get('depth')}\n" + self._clip(c.get('body'), cap)

    @staticmethod
    def _parse_atom(xml: str) -> list[str]:
        out = []
        for entry in re.findall('<entry>(.*?)</entry>', xml or '', re.S):

            def grab(pattern: str) -> str:
                m = re.search(pattern, entry, re.S)
                return m.group(1).strip() if m else ''
            title = grab('<title>(.*?)</title>')
            link = (re.search('<link href="([^"]+)"', entry) or ['', ''])[1]
            updated = grab('<updated>(.*?)</updated>')
            author = grab('<name>(.*?)</name>')
            raw = grab('<content[^>]*>(.*?)</content>')
            body = re.sub('\\s+', ' ', re.sub('<[^>]+>', ' ', raw)).strip()
            out.append(f'{updated} | {author}\n{title}\n{body[:900]}\nURL: {link}')
        return out

    async def reddit_timeline(self, subreddit: str, sort: str='hot', limit: int=10, time_filter: str='') -> str:
        """Read recent posts from a subreddit listing (hot/new/top/rising)."""
        sub = self._reddit_sub(subreddit)
        if not sub:
            return 'Subreddit is empty.'
        n = max(1, min(int(limit), self.valves.MAX_ITEMS))
        sort = (sort or 'hot').lower()
        path = f'/r/{sub}/{sort}?limit={n}'
        if sort == 'top' and time_filter:
            path += f'&t={time_filter}'
        data, err = await self._reddit_api(path)
        children = [c.get('data') for c in (data or {}).get('data', {}).get('children', []) if c.get('kind') == 't3']
        if not children:
            status, _, body, err2 = await self._http(f'https://www.reddit.com/r/{sub}/{sort}.rss?limit={n}')
            rows = self._parse_atom(body) if status == 200 else []
            if rows:
                return f'Reddit r/{sub} sorted by {sort} via RSS ({len(rows)} posts)\n\n' + '\n\n'.join(rows)
            return err or err2 or 'Reddit listing failed.'
        return f'Reddit r/{sub} sorted by {sort} ({len(children)} posts)\n\n' + '\n\n'.join((self._fmt_post(d, self.valves.TEXT_CAP) for d in children))

    async def _reddit_rows(self, query: str, subreddit: str, limit: int, sort: str) -> tuple[list[dict], str, Optional[str]]:
        """Reddit posts for a query: OAuth search first, then the Arctic Shift archive
        for historical coverage the live API no longer provides."""
        n = max(1, min(int(limit), self.valves.MAX_ITEMS))
        sub = self._reddit_sub(subreddit)
        scope = f'&subreddit={sub}' if sub else ''
        data, err = await self._reddit_api(f'/search?q={urllib.parse.quote(query)}&sort={sort}&limit={n}{scope}')
        rows = [c.get('data') for c in (data or {}).get('data', {}).get('children', []) if c.get('kind') == 't3']
        if rows:
            return (rows, 'live', err)
        rows = await self._archive('posts/search', {'subreddit': sub, 'title': query, 'limit': n})
        return (rows, 'archive', err)

    async def reddit_thread(self, url_or_id: str, max_comments: int=15, focus: str='') -> str:
        """Read one Reddit post plus its comments.
        Comment trees come from the Arctic Shift archive because the anonymous OAuth
        grant cannot read Reddit's comment endpoints. With authenticated UserValves
        credentials, the direct endpoint is tried first."""
        pid = self._reddit_id(url_or_id)
        if not pid:
            return 'Could not find a Reddit post id in that input.'
        n = max(1, min(int(max_comments), self.valves.MAX_ITEMS))
        post = None
        err = []
        data, e1 = await self._reddit_api(f'/api/info/t3?id=t3_{pid}')
        if data:
            kids = data.get('data', {}).get('children', [])
            post = kids[0].get('data') if kids else None
        if not post:
            archived = await self._archive('posts/ids', {'ids': pid})
            post = archived[0] if archived else None
            err.append(e1 or '')
        if not post:
            return f"Could not read Reddit post {pid}. {' '.join((x for x in err if x))}"
        comments: list[dict] = []
        data2, e2 = await self._reddit_api(f'/comments/{pid}?limit={n}&sort=top')
        if data2:
            comments = [c.get('data') for c in data2.get('data', {}).get('children', []) if c.get('kind') == 't1']
            if not comments:
                comments = await self._archive('comments/search', {'link_id': 't3_' + pid, 'limit': n})
        else:
            comments = await self._archive('comments/search', {'link_id': 't3_' + pid, 'limit': n})
            err.append(e2 or '')
        if focus.strip():
            terms = [x for x in re.findall('\\w+', focus.lower()) if len(x) > 2]
            scored = [(sum((t in (c.get('body') or '').lower() for t in terms)), i, c) for i, c in enumerate(comments)]
            if any((x[0] for x in scored)):
                scored.sort(key=lambda x: (-x[0], x[1]))
                comments = [c for _, _, c in scored]
        out = [self._fmt_post(post, self.valves.TEXT_CAP * 2), f'\nComments ({len(comments)} shown)\n']
        out.extend((self._fmt_comment(c, self.valves.TEXT_CAP) for c in comments))
        if not comments:
            out.append('No comments available (not archived, or the comment endpoint is blocked).')
        return '\n\n'.join(out)

    async def reddit_user(self, user: str, kind: str='submitted', limit: int=10) -> str:
        """Recent posts or comments by a Reddit user."""
        name = (user or '').strip().lstrip('@').lstrip('u/')
        if not name:
            return 'Username is empty.'
        n = max(1, min(int(limit), self.valves.MAX_ITEMS))
        kind = (kind or 'submitted').lower()
        is_post = kind in ('submitted', 'posts', 'submissions')
        data, err = await self._reddit_api(f'/user/{name}/{kind}?limit={n}')
        rows = [c.get('data') for c in (data or {}).get('data', {}).get('children', [])]
        source = 'live'
        if not rows:
            rows = await self._archive(('posts' if is_post else 'comments') + '/search', {'author': name, 'limit': n})
            source = 'archive'
        if not rows:
            return f"Could not read u/{name}. {err or 'no items returned'}"
        fmt = self._fmt_post if is_post else self._fmt_comment
        return f'Reddit u/{name} ({kind}, {source}) ({len(rows)} items)\n\n' + '\n\n'.join((fmt(c, self.valves.TEXT_CAP) for c in rows))

    async def reddit_subreddit(self, subreddit: str) -> str:
        """Subreddit metadata: description, subscribers, rules when available."""
        sub = self._reddit_sub(subreddit)
        if not sub:
            return 'Subreddit is empty.'
        data, err = await self._reddit_api(f'/r/{sub}/about')
        if not data:
            return err or 'Subreddit lookup failed.'
        d = data.get('data', {})
        lines = [f"r/{d.get('display_name')} | {d.get('subscribers')} subscribers", (d.get('public_description') or '').strip(), f"Created: {self._ts(d.get('created_utc'))} | {d.get('ban_count')} banned users"]
        return '\n'.join((x for x in lines if x))

    @staticmethod
    def _tweet_ref(value: str) -> tuple[str, str]:
        raw = (value or '').strip()
        m = re.search('(?:status|tweet)/(\\d{1,20})', raw)
        tid = m.group(1) if m else re.search('\\d{6,20}', raw).group(0) if re.search('\\d{6,20}', raw) else ''
        h = re.search('(?:twitter\\.com|x\\.com|fxtwitter\\.com|vxtwitter\\.com|xcancel\\.com)/([A-Za-z0-9_]{1,15})/', raw)
        return (h.group(1) if h else '', tid)

    def _fmt_tweet(self, t: dict, cap: int) -> str:
        author = (t.get('author') or {}).get('screen_name') or (t.get('user') or {}).get('screen_name') or '?'
        tid = t.get('id_str') or t.get('conversation_id_str') or t.get('id') or ''
        text = (t.get('text') or t.get('full_text') or '').strip()
        stats = []
        for key, label in (('likes', 'likes'), ('retweets', 'retweets'), ('favorite_count', 'likes'), ('retweet_count', 'retweets'), ('reply_count', 'replies'), ('views', 'views')):
            if t.get(key) is not None:
                stats.append(f'{label} {t[key]}')
        lines = [f"[{tid}] {t.get('created_at') or ''} | @{author}"]
        if stats:
            lines.append(' | '.join(stats))
        lines.append(self._clip(text, cap) or '(no text)')
        media = []
        m = t.get('media') or {}
        for key in ('all', 'photos', 'videos'):
            for item in m.get(key) or []:
                url = item.get('url') or item.get('thumbnail_url')
                if url:
                    media.append(f'{key}: {url}')
        if media:
            lines.append('Media: ' + '; '.join(media[:8]))
        quote = t.get('quote')
        if quote:
            qa = (quote.get('author') or {}).get('screen_name') or '?'
            lines.append(f"Quoted @{qa}: {self._clip(quote.get('text'), 400)}")
        if t.get('url'):
            lines.append('URL: ' + t['url'])
        elif tid:
            lines.append(f'URL: https://x.com/{author}/status/{tid}')
        return '\n'.join(lines)

    async def x_tweet(self, url_or_id: str) -> str:
        """Read one X post. Tries api.fxtwitter.com, then cdn.syndication.twimg.com."""
        handle, tid = self._tweet_ref(url_or_id)
        if not tid:
            return 'No tweet id found in that input.'
        status, _, body, err = await self._http(f"https://api.fxtwitter.com/{handle or 'i'}/status/{tid}", headers={'Accept': 'application/json'})
        if status == 200:
            try:
                return self._fmt_tweet(json.loads(body).get('tweet', {}), self.valves.TEXT_CAP)
            except Exception:
                pass
        status, _, body, err2 = await self._http(f'https://cdn.syndication.twimg.com/tweet-result?id={tid}&token=1', headers={'Accept': 'application/json'})
        if status == 200:
            try:
                return self._fmt_tweet(json.loads(body), self.valves.TEXT_CAP)
            except Exception:
                pass
        status, _, body, err3 = await self._http('https://r.jina.ai/https://x.com/i/status/' + tid)
        if status == 200 and body:
            return f'X post {tid} (reader fallback, may be login-gated content):\n{body[:self.valves.TEXT_CAP]}'
        return f"Could not read X post {tid}. {err or err2 or err3 or ''}"

    async def x_profile(self, handle: str, limit: int=20) -> str:
        """Recent posts from an X account, no key required.
        Primary route is the syndication timeline-profile page's embedded JSON.
        The profile card is best-effort from api.fxtwitter.com."""
        name = (handle or '').strip().lstrip('@')
        if not name:
            return 'Handle is empty.'
        n = max(1, min(int(limit), 40))
        out = []
        status, _, body, card_err = await self._http('https://api.fxtwitter.com/' + name, headers={'Accept': 'application/json'})
        if status == 200:
            try:
                u = json.loads(body).get('user', {})
                if u:
                    out.append(f"X @{u.get('screen_name', name)} | {u.get('name')}\n{u.get('followers')} followers | {u.get('tweets')} tweets\n" + (u.get('description') or '').strip())
            except Exception:
                pass
        status, _, html, err = await self._http('https://syndication.twitter.com/srv/timeline-profile/screen-name/' + name)
        tweets = []
        if status == 200:
            m = re.search('<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', html, re.S)
            if m:
                try:
                    d = json.loads(m.group(1))
                    entries = d.get('props', {}).get('pageProps', {}).get('timeline', {}).get('entries', [])
                    tweets = [e.get('content', {}).get('tweet') for e in entries if e.get('type') == 'tweet']
                except Exception:
                    tweets = []
        if not tweets:
            inst = (self.user_valves.nitter_instance or '').strip().lstrip('https://').rstrip('/')
            if inst:
                status, _, rss, err2 = await self._http(f'https://{inst}/{name}/rss')
                if status == 200:
                    items = re.findall('<item>.*?<title>(.*?)</title>.*?<link>(.*?)</link>.*?<pubDate>(.*?)</pubDate>', rss, re.S)
                    if items:
                        return f'X @{name} via Nitter RSS ({len(items)} items)\n\n' + '\n\n'.join((f'[{d.strip()}] @{name}\n{t.strip()}\n{u.strip()}' for t, u, d in items[:n]))
                return f"Nitter RSS fallback failed for @{name} on {inst}. {err2 or ''}"
        if not tweets:
            return f"Could not read the X timeline for @{name}. {card_err or err or 'no items returned'}"
        out.append(f'\nX @{name} latest posts ({len(tweets[:n])})\n')
        out.extend((self._fmt_tweet(t, self.valves.TEXT_CAP) for t in tweets[:n]))
        return '\n\n'.join(out)

    async def social_search(self, query: str, subreddit: str='', limit: int=10, sort: str='relevance', engine: str='auto') -> str:
        """Search Reddit and/or X. Reddit uses the keyless OAuth search plus the Arctic Shift
        archive. X search needs twitterapi_io_key; without it X falls back to web-search
        discovery because logged-out X search redirects to the login wall."""
        q = (query or '').strip()
        if not q:
            return 'Query is empty.'
        n = max(1, min(int(limit), 40))
        engine = (engine or 'auto').lower()
        wants_x = engine == 'x' or any((x in q.lower() for x in ('x.com', 'twitter', 'tweet')))
        wants_reddit = engine == 'reddit' or bool(self._reddit_sub(subreddit)) or 'reddit' in q.lower()
        if not wants_x and (not wants_reddit):
            wants_reddit = True
        if wants_reddit:
            rows, source, err = await self._reddit_rows(q, subreddit, n, sort)
            if rows:
                sub = self._reddit_sub(subreddit)
                head = f'Reddit search: {q}' + (f' in r/{sub}' if sub else '') + f' ({len(rows)} posts, {source})\n\n'
                return head + '\n\n'.join((self._fmt_post(d, self.valves.TEXT_CAP) for d in rows))
            first_error = err or 'no Reddit results'
        else:
            first_error = None
        key = (self.user_valves.twitterapi_io_key or '').strip()
        if key:
            status, _, body, err = await self._http('https://api.twitterapi.io/twitter/tweet/advanced_search?query=' + urllib.parse.quote(q) + '&queryType=Latest', headers={'X-API-Key': key})
            if status == 200:
                try:
                    tweets = json.loads(body).get('tweets', [])
                    if tweets:
                        return f'X search: {q} ({len(tweets)})\n\n' + '\n\n'.join((self._fmt_tweet(t, self.valves.TEXT_CAP) for t in tweets[:n]))
                except Exception:
                    pass
            return f"X search API failed (HTTP {status}): {(body or err or '')[:200]}"
        hits = await self._ddg(f'site:x.com {q}', n)
        if hits:
            return f'X search via web discovery: {q}\n\n' + '\n\n'.join((f"[{i}] {h.get('title')}\n{h.get('url')}\n{h.get('snippet')}" for i, h in enumerate(hits[:n], 1)))
        if first_error:
            return f'No results for {q!r}. {first_error}. X search needs twitterapi_io_key or ddgs.'
        return f'No results for {q!r}. Without twitterapi_io_key, X search is web discovery only, and that route needs ddgs installed.'

    async def _ddg(self, query: str, count: int) -> list[dict]:
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

    async def fetch_social(self, url: str, focus: str='') -> str:
        """Read a Reddit or X/Twitter link and return the post plus comments where available.
        Handles reddit.com, old.reddit.com, x.com, twitter.com, fxtwitter, vxtwitter, xcancel."""
        raw = url if '://' in url else 'https://' + url
        host = (urllib.parse.urlsplit(raw).hostname or '').lower()
        if 'reddit.com' in host:
            if re.search('/comments/', raw):
                text = await self.reddit_thread(raw, focus=focus)
            elif '/user/' in raw:
                text = await self.reddit_user(raw)
            elif re.search('/r/[^/]+/(hot|new|top|rising)', raw):
                sort = next((s for s in ('hot', 'new', 'top', 'rising') if f'/{s}' in raw), 'hot')
                text = await self.reddit_timeline(raw, sort=sort)
            elif re.search('/r/[^/]+/?$', raw):
                text = await self.reddit_timeline(raw)
            else:
                text = await self.reddit_timeline(raw)
            return text + (f'\n\nFocus: {focus}' if focus else '')
        if host in ('x.com', 'twitter.com', 'fxtwitter.com', 'vxtwitter.com', 'fixupx.com', 'xcancel.com'):
            handle, tid = self._tweet_ref(raw)
            if tid:
                return await self.x_tweet(raw)
            return await self.x_profile(handle or raw)
        return 'fetch_social handles reddit.com and x.com/twitter.com URLs only. Use the general fetch_url tool for other sites.'
_builtin_tools = Tools()

async def reddit_timeline(self, subreddit: str, sort: str='hot', limit: int=10, time_filter: str='') -> str:
    """Read recent posts from a subreddit listing (hot/new/top/rising)."""
    return await _builtin_tools.reddit_timeline(subreddit=subreddit, sort=sort, limit=limit, time_filter=time_filter)

async def reddit_thread(self, url_or_id: str, max_comments: int=15, focus: str='') -> str:
    """Read one Reddit post plus its comments.
        Comment trees come from the Arctic Shift archive because the anonymous OAuth
        grant cannot read Reddit's comment endpoints. With authenticated UserValves
        credentials, the direct endpoint is tried first."""
    return await _builtin_tools.reddit_thread(url_or_id=url_or_id, max_comments=max_comments, focus=focus)

async def reddit_user(self, user: str, kind: str='submitted', limit: int=10) -> str:
    """Recent posts or comments by a Reddit user."""
    return await _builtin_tools.reddit_user(user=user, kind=kind, limit=limit)

async def reddit_subreddit(self, subreddit: str) -> str:
    """Subreddit metadata: description, subscribers, rules when available."""
    return await _builtin_tools.reddit_subreddit(subreddit=subreddit)

async def x_tweet(self, url_or_id: str) -> str:
    """Read one X post. Tries api.fxtwitter.com, then cdn.syndication.twimg.com."""
    return await _builtin_tools.x_tweet(url_or_id=url_or_id)

async def x_profile(self, handle: str, limit: int=20) -> str:
    """Recent posts from an X account, no key required.
        Primary route is the syndication timeline-profile page's embedded JSON.
        The profile card is best-effort from api.fxtwitter.com."""
    return await _builtin_tools.x_profile(handle=handle, limit=limit)

async def social_search(self, query: str, subreddit: str='', limit: int=10, sort: str='relevance', engine: str='auto') -> str:
    """Search Reddit and/or X. Reddit uses the keyless OAuth search plus the Arctic Shift
        archive. X search needs twitterapi_io_key; without it X falls back to web-search
        discovery because logged-out X search redirects to the login wall."""
    return await _builtin_tools.social_search(query=query, subreddit=subreddit, limit=limit, sort=sort, engine=engine)

async def fetch_social(self, url: str, focus: str='') -> str:
    """Read a Reddit or X/Twitter link and return the post plus comments where available.
        Handles reddit.com, old.reddit.com, x.com, twitter.com, fxtwitter, vxtwitter, xcancel."""
    return await _builtin_tools.fetch_social(url=url, focus=focus)
