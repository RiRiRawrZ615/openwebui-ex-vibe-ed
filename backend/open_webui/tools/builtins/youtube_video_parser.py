'''"""Open Web Vibes builtin: YouTube Video Parser

Generated from the WebUI tool export `youtube_video_parser` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: YouTube Video Parser
Exported functions: get_youtube_video

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''
import asyncio
import html
import json
import re
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from pydantic import BaseModel

class Tools:

    class Valves(BaseModel):
        preferred_language: str = 'en'
        timeout: int = 15

    def __init__(self):
        self.valves = self.Valves()

    def _get(self, url: str, data: bytes=None, headers: dict=None) -> str:
        h = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36', 'Accept-Language': 'en-US,en;q=0.9'}
        if headers:
            h.update(headers)
        req = Request(url, data=data, headers=h)
        with urlopen(req, timeout=self.valves.timeout) as r:
            return r.read(2000000).decode('utf-8', 'replace')

    def _video_id(self, url: str) -> str:
        vid = url.strip() if re.fullmatch('[A-Za-z0-9_-]{11}', url.strip()) else ''
        if not vid:
            p = urlparse(url)
            q = parse_qs(p.query)
            vid = (q.get('v') or [''])[0]
            if not vid:
                parts = [x for x in p.path.split('/') if x]
                if p.netloc.endswith('youtu.be') and parts:
                    vid = parts[0]
                elif len(parts) >= 2 and parts[0] in ('shorts', 'embed', 'live'):
                    vid = parts[1]
        if not re.fullmatch('[A-Za-z0-9_-]{11}', vid or ''):
            raise ValueError('Invalid YouTube URL or video ID.')
        return vid

    def _metadata(self, video_id: str) -> dict:
        meta = {'title': '', 'channel': '', 'thumbnail': '', 'description': ''}
        try:
            raw = self._get('https://www.youtube.com/oembed?url=' + video_id + '&format=json')
            data = json.loads(raw)
            meta['title'] = data.get('title', '')
            meta['channel'] = data.get('author_name', '')
        except Exception:
            pass
        meta['thumbnail'] = 'https://i.ytimg.com/vi/' + video_id + '/hqdefault.jpg'
        return meta

    def _transcript(self, video_id: str):
        errors = []
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            api = YouTubeTranscriptApi()
            tl = api.list(video_id)
            lang = self.valves.preferred_language
            track = None
            try:
                track = tl.find_manually_controlled_transcript([lang])
            except Exception:
                track = tl.find_transcript([lang]) or next(iter(tl))
            fetched = api.fetch(video_id, languages=[track.language_code])
            lines = [s.text.replace('\n', ' ') for s in fetched]
            return ('\n'.join(lines), track.language_code)
        except Exception as exc:
            errors.append('library: ' + str(exc))
        endpoint = 'https://www.youtube.com/youtubei/v1/player?prettyPrint=false'
        for client in ({'clientName': 'WEB', 'clientVersion': '2.20250101.00.00', 'hl': 'en'}, {'clientName': 'ANDROID', 'clientVersion': '19.44.38', 'androidSdkVersion': 30, 'hl': 'en'}):
            try:
                payload = json.dumps({'videoId': video_id, 'context': {'client': client}}).encode()
                raw = self._get(endpoint, headers={'Content-Type': 'application/json'}, data=payload)
                data = json.loads(raw)
                tracks = ((data.get('captions') or {}).get('playerCaptionsTracklistRenderer') or {}).get('captionTracks') or []
                for track in tracks:
                    base = track.get('baseUrl')
                    if not base:
                        continue
                    for fmt in ('&fmt=json3', ''):
                        try:
                            body = self._get(base + fmt)
                            if fmt:
                                segs = []
                                for ev in json.loads(body).get('events', []):
                                    if ev.get('segs'):
                                        segs.append(''.join((s.get('utf8', '') for s in ev['segs'])))
                                if segs:
                                    return ('\n'.join(segs), track.get('languageCode', ''))
                            else:
                                out = []
                                for start, text in re.findall('<text start="([^"]+)"[^>]*>(.*?)</text>', body, re.S):
                                    out.append(html.unescape(re.sub('<[^>]+>', '', text)).replace('\n', ' '))
                                if out:
                                    return ('\n'.join(out), track.get('languageCode', ''))
                        except Exception:
                            continue
            except Exception as exc:
                errors.append('innertube: ' + str(exc))
        raise Exception('Transcript unavailable. ' + ' | '.join(errors))

    async def get_youtube_video(self, url: str) -> str:
        """Read a YouTube video and return captions/transcript when available.
        Use this whenever the user provides a YouTube URL or video ID and wants
        the spoken content, transcript, or a summary of what happens in the video.
        :param url: YouTube watch, shorts, embed, or youtu.be URL, or a bare video ID
        :return: Video metadata plus transcript, or metadata with a description fallback
        """
        try:
            video_id = self._video_id(url)
        except Exception as exc:
            return 'YouTube parser error: ' + str(exc)
        canonical = 'https://www.youtube.com/watch?v=' + video_id
        metadata = await asyncio.to_thread(self._metadata, video_id)
        try:
            transcript, language = await asyncio.to_thread(self._transcript, video_id)
            return 'YOUTUBE VIDEO CONTEXT\nVideo ID: ' + video_id + '\nURL: ' + canonical + '\nTitle: ' + (metadata['title'] or 'Unknown') + '\nChannel: ' + (metadata['channel'] or 'Unknown') + '\nThumbnail: ' + metadata['thumbnail'] + '\nCaption language: ' + str(language) + '\n\nTRANSCRIPT / CAPTIONS\n' + transcript
        except Exception as exc:
            return 'YOUTUBE VIDEO CONTEXT\nVideo ID: ' + video_id + '\nURL: ' + canonical + '\nTitle: ' + (metadata['title'] or 'Unknown') + '\nChannel: ' + (metadata['channel'] or 'Unknown') + '\nThumbnail: ' + metadata['thumbnail'] + '\n\nTRANSCRIPT / CAPTIONS\nUnavailable: ' + str(exc) + '\n\nVIDEO DESCRIPTION\n' + (metadata['description'] or 'No public description was available.')
_builtin_tools = Tools()

async def get_youtube_video(self, url: str) -> str:
    """Read a YouTube video and return captions/transcript when available.
        Use this whenever the user provides a YouTube URL or video ID and wants
        the spoken content, transcript, or a summary of what happens in the video.
        :param url: YouTube watch, shorts, embed, or youtu.be URL, or a bare video ID
        :return: Video metadata plus transcript, or metadata with a description fallback
        """
    return await _builtin_tools.get_youtube_video(url=url)
