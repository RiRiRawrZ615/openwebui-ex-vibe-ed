'''"""Open Web Vibes builtin: YouTube Visual Reader

Generated from the WebUI tool export `custom_youtube_visual_1` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: YouTube Visual Reader
Exported functions: read_youtube_video, youtube_frame_at, youtube_frame_urls, youtube_visual_scenes

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''
import asyncio
import html
import io
import json
import re
import shutil
import subprocess
import urllib.request
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen
from PIL import Image
from pydantic import BaseModel
UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/131 Safari/537.36', 'Accept-Language': 'en-US,en;q=0.9'}
GRID_FALLBACK = {'L3': (3, 3), 'L2': (5, 5), 'L1': (4, 4), 'L0': (3, 3)}

class Tools:

    class Valves(BaseModel):
        vision_api_base: str = 'http://127.0.0.1:8004/v1'
        vision_model: str = 'qwen3.8-flash-next-iq2_xs'
        vision_timeout: int = 120
        vision_max_tokens: int = 700
        vision_system_prompt: str = 'You describe video frames. Output only the requested lines, no reasoning.'
        max_groups: int = 4
        max_frames: int = 12
        tiles_per_sheet: int = 4
        scene_threshold: float = 18.0
        output_cap: int = 6000
        fallback_urls: bool = True
        storyboard_level: str = 'L1'
        ytdlp_path: str = 'yt-dlp'
        preferred_language: str = 'en'
        timeout: int = 20

    def __init__(self):
        self.valves = self.Valves()

    def _get(self, url, data=None, headers=None):
        h = dict(UA)
        if headers:
            h.update(headers)
        req = Request(url, data=data, headers=h)
        with urlopen(req, timeout=self.valves.timeout) as r:
            return r.read(3000000)

    def _video_id(self, url):
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

    def _metadata(self, video_id):
        meta = {'title': '', 'channel': '', 'description': '', 'duration': 0}
        try:
            raw = self._get('https://www.youtube.com/oembed?url=' + video_id + '&format=json')
            d = json.loads(raw.decode('utf-8', 'replace'))
            meta['title'] = d.get('title', '')
            meta['channel'] = d.get('author_name', '')
        except Exception:
            pass
        try:
            payload = json.dumps({'videoId': video_id, 'context': {'client': {'clientName': 'WEB', 'clientVersion': '2.20250101.00.00', 'hl': 'en'}}}).encode()
            d = json.loads(self._get('https://www.youtube.com/youtubei/v1/player?prettyPrint=false', data=payload, headers={'Content-Type': 'application/json'}).decode())
            vd = d.get('videoDetails') or {}
            meta['title'] = meta['title'] or vd.get('title', '')
            meta['channel'] = meta['channel'] or vd.get('author', '')
            meta['description'] = vd.get('shortDescription', '')
            meta['duration'] = int(vd.get('lengthSeconds') or 0)
        except Exception:
            pass
        return meta

    def _transcript(self, video_id):
        errors = []
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            api = YouTubeTranscriptApi()
            tl = api.list(video_id)
            lang = self.valves.preferred_language
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
                raw = self._get(endpoint, headers={'Content-Type': 'application/json'}, data=payload).decode()
                data = json.loads(raw)
                tracks = ((data.get('captions') or {}).get('playerCaptionsTracklistRenderer') or {}).get('captionTracks') or []
                for track in tracks:
                    base = track.get('baseUrl')
                    if not base:
                        continue
                    for fmt in ('&fmt=json3', ''):
                        try:
                            body = self._get(base + fmt).decode('utf-8', 'replace')
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

    def _storyboard_spec(self, video_id):
        """Signed storyboard URL + per-group durations via yt-dlp.

        YouTube stopped returning storyboards to anonymous InnerTube clients, so the
        signature has to come from yt-dlp. The signature is per-video: reusing another
        video's sqp/sigh returns 403. If yt-dlp is unavailable we fall back to the
        classic thumbnail frames.
        """
        path = shutil.which(self.valves.ytdlp_path)
        if not path:
            return None
        level = (self.valves.storyboard_level or 'L1').strip().upper()
        fmt = {'L0': 'sb3', 'L1': 'sb1', 'L2': 'sb2', 'L3': 'sb0'}.get(level, 'sb1')
        args = [path, '-j', '--skip-download', '-f', fmt, 'https://www.youtube.com/watch?v=' + video_id]
        if shutil.which('node'):
            args.insert(1, '--js-runtimes')
            args.insert(2, 'node')
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=self.valves.timeout * 4)
            if out.returncode:
                return None
            d = json.loads(out.stdout)
            url = d.get('url') or ''
            if '$M' not in url:
                return None
            return {'url': url, 'cols': int(d.get('columns') or 5), 'rows': int(d.get('rows') or 5), 'tile_w': int(d.get('width') or 160), 'tile_h': int(d.get('height') or 90), 'groups': [{'index': int(f.get('index') or i), 'duration': float(f.get('duration') or 0)} for i, f in enumerate(d.get('fragments') or [])]}
        except Exception:
            return None

    def _fetch_group(self, spec, group_index):
        url = spec['url'].replace('$M', str(group_index))
        try:
            raw = self._get(url)
            im = Image.open(io.BytesIO(raw))
            cols, rows = (spec['cols'], spec['rows'])
            if im.size != (spec['tile_w'] * cols, spec['tile_h'] * rows):
                cols = max(1, im.width // spec['tile_w'])
                rows = max(1, im.height // spec['tile_h'])
            tiles = []
            for r in range(rows):
                for c in range(cols):
                    box = (c * spec['tile_w'], r * spec['tile_h'], (c + 1) * spec['tile_w'], (r + 1) * spec['tile_h'])
                    tiles.append((box, im.crop(box)))
            return (tiles, (cols, rows))
        except Exception as exc:
            return (None, str(exc))

    def _sig(self, tile):
        """Block signature for change detection. No numpy: PIL only."""
        g = tile.convert('L').resize((8, 5))
        px = list(g.getdata())
        return (sum(px) / len(px), sum((v * v for v in px)) / len(px))

    def _changed(self, a, b, threshold):
        d0 = abs(a[0] - b[0])
        d1 = abs(a[1] - b[1]) ** 0.5
        return d0 + d1 > threshold

    def _time(self, spec, group_i, tile_i, cols, rows):
        elapsed = sum((g['duration'] for g in spec['groups'][:group_i]))
        span = next((g['duration'] for g in spec['groups'] if g['index'] == group_i), 0)
        per = span / max(1, cols * rows)
        return elapsed + tile_i * per

    def _describe(self, frames, focus):
        """Describe frames in contact sheets, one vision call per sheet.

        Timestamps are paired to cells in code, not by the model. Asking a small
        vision model to match cell numbers it has to read off the image makes it
        reason about the mapping instead of describing the picture.
        """
        results = []
        cap = self.valves.tiles_per_sheet
        frames = [f for f in frames if f.get('image') is not None]
        for i in range(0, len(frames), cap):
            chunk = frames[i:i + cap]
            blob, n = self._sheet(chunk)
            if not blob:
                continue
            stamps = [self._fmt_time(f['time']) for f in chunk]
            prompt = 'Contact sheet of ' + str(n) + ' video frames in reading order (top-left to bottom-right). Write exactly ' + str(n) + ' lines, one per frame, each a short description of what is visible in that frame.' + (' Focus: ' + focus + '.' if focus else '') + ' Start the first line immediately.'
            out, verr = self._vision(prompt, blob)
            if verr:
                return (None, verr)
            out = self._clean(out)
            parts = [p.strip() for p in out.split('\n') if p.strip()]
            if len(parts) == n:
                results.extend((f'{stamps[j]}  {parts[j]}' for j in range(n)))
            elif out:
                results.append('[' + ', '.join(stamps) + '] ' + out)
        return (results, None)

    def _sheet(self, frames):
        tiles = [f['image'] for f in frames if f.get('image') is not None]
        n = min(self.valves.tiles_per_sheet, len(tiles))
        if not tiles:
            return (None, 0)
        tw, th = tiles[0].size
        side = int(n ** 0.5) or 1
        side = min(side, n)
        cols = side
        rows = (n + side - 1) // side
        sheet = Image.new('RGB', (cols * tw, rows * th), (16, 16, 16))
        for i in range(n):
            x, y = (i % cols * tw, i // cols * th)
            sheet.paste(tiles[i], (x, y))
        out = io.BytesIO()
        sheet.save(out, 'JPEG', quality=88)
        return (out.getvalue(), n)

    def _vision(self, prompt, image_bytes):
        import base64
        msgs = []
        if self.valves.vision_system_prompt:
            msgs.append({'role': 'system', 'content': self.valves.vision_system_prompt})
        msgs.append({'role': 'user', 'content': [{'type': 'text', 'text': prompt}, {'type': 'image_url', 'image_url': {'url': 'data:image/jpeg;base64,' + base64.b64encode(image_bytes).decode()}}]})
        payload = json.dumps({'model': self.valves.vision_model, 'messages': msgs, 'max_tokens': self.valves.vision_max_tokens}).encode()
        req = Request(self.valves.vision_api_base.rstrip('/') + '/chat/completions', data=payload, headers={'Content-Type': 'application/json'})
        try:
            d = json.loads(urlopen(req, timeout=self.valves.vision_timeout).read().decode())
            msg = (d.get('choices') or [{}])[0].get('message') or {}
            text = (msg.get('content') or '').strip() or (msg.get('reasoning_content') or '').strip()
            return (text or None, None)
        except Exception as exc:
            return (None, 'vision call failed: ' + str(exc))

    def _clean(self, text):
        """Drop thinking-model echoes of the prompt before the actual answer."""
        if not text:
            return ''
        lines = [l for l in text.split('\n') if l.strip() and (not re.search('we need|the user|instructions say|cell timestamps', l, re.I))]
        return '\n'.join(lines).strip()

    def _fmt_time(self, s):
        s = int(s)
        return f'{s // 60:02d}:{s % 60:02d}'

    def _collect(self, video_id):
        spec = self._storyboard_spec(video_id)
        if not spec:
            return (None, None, None)
        frames = []
        for gi in range(min(self.valves.max_groups, len(spec['groups']))):
            tiles, grid = self._fetch_group(spec, gi)
            if not tiles:
                continue
            cols, rows = grid
            prev = None
            for ti, (box, tile) in enumerate(tiles):
                s = self._sig(tile)
                frames.append({'group': gi, 'tile': ti, 'grid': (cols, rows), 'tile_w': spec['tile_w'], 'tile_h': spec['tile_h'], 'time': self._time(spec, gi, ti, cols, rows), 'group_url': spec['url'].replace('$M', str(gi)), 'sig': s, 'changed': prev is None or self._changed(prev, s, self.valves.scene_threshold), 'image': tile})
                prev = s
        return (spec, frames, None)

    async def read_youtube_video(self, url: str, visual: str='none', focus: str='') -> str:
        """Read a YouTube video: captions plus optional visual track.

        Use this whenever the user provides a YouTube URL or video ID. Default behavior
        is caption-only, which is the cheap path. Set visual="scenes" when the picture
        matters (what is on screen, visible text, action, layout), or visual="sample"
        for a sparse time-ordered pass over the whole video.
        :param url: YouTube watch, shorts, embed, or youtu.be URL, or a bare video ID
        :param visual: "none" for captions only, "sample" for evenly spaced frames,
                       "scenes" for only frames that changed
        :param focus: optional instruction for what to look for visually
        :return: Metadata, transcript, and a time-coded visual pass when requested
        """
        try:
            vid = self._video_id(url)
        except Exception as exc:
            return 'YouTube parser error: ' + str(exc)
        canonical = 'https://www.youtube.com/watch?v=' + vid
        meta = await asyncio.to_thread(self._metadata, vid)
        head = 'YOUTUBE VIDEO CONTEXT\nVideo ID: ' + vid + '\nURL: ' + canonical + '\nTitle: ' + (meta['title'] or 'Unknown') + '\nChannel: ' + (meta['channel'] or 'Unknown') + '\nDuration: ' + self._fmt_time(meta['duration'])
        try:
            transcript, lang = await asyncio.to_thread(self._transcript, vid)
            head += '\nCaption language: ' + str(lang) + '\n\nTRANSCRIPT / CAPTIONS\n' + transcript
        except Exception as exc:
            head += '\n\nTRANSCRIPT / CAPTIONS\nUnavailable: ' + str(exc)
            head += '\n\nVIDEO DESCRIPTION\n' + (meta['description'] or 'No public description was available.')
        if visual == 'none' or not visual:
            return head
        spec, frames, err = await asyncio.to_thread(self._collect, vid)
        if not frames:
            return head + '\n\nVISUAL TRACK\nNo storyboard frames available (' + str(err or 'no signed storyboard') + '). ' + ('Fallback thumbnail: https://i.ytimg.com/vi/' + vid + '/hq720.jpg\n' if self.valves.fallback_urls else '')
        if visual == 'sample':
            step = max(1, len(frames) // max(1, min(self.valves.max_frames, 12)))
            picked = frames[::step][:self.valves.max_frames]
        else:
            picked = [f for f in frames if f['changed']][:self.valves.max_frames]
        lines = ['\n\nVISUAL TRACK', 'Source: storyboard frames at ' + f"{picked[0]['tile_w']}x{picked[0]['tile_h']}, " + f"{picked[0]['grid'][0]}x{picked[0]['grid'][1]} grid.", 'Low resolution frames, so descriptions are approximate for small detail.']
        if visual == 'sample':
            n = max(1, int(len(frames) / max(1, min(self.valves.max_frames, 12))))
            lines.append('Sampling: 1 frame per ~' + str(n) + ' tiles. Not every tile was read.')
        res, verr = await asyncio.to_thread(self._describe, picked, focus)
        if verr:
            lines.append('\nVision endpoint unavailable (' + verr + ').')
            for f in picked:
                lines.append(self._fmt_time(f['time']) + ' tile ' + str(f['tile'] + 1) + (' (scene change)' if f['changed'] else ''))
        else:
            lines.extend(res)
        return '\n'.join(lines)[:self.valves.output_cap]

    async def youtube_frame_at(self, url: str, time: str, focus: str='') -> str:
        """Describe one specific moment in a YouTube video.

        Use when the user asks what is on screen at a particular timestamp. Resolution
        is a storyboard tile, so it is approximate for small text.
        :param url: YouTube URL or video ID
        :param time: timestamp as mm:ss, hh:mm:ss, or plain seconds
        :param focus: what to look for in that frame
        """
        vid = self._video_id(url)
        parts = [int(x) for x in re.split('[:,]', time.strip()) if x.isdigit()]
        if not parts:
            return 'Invalid time. Use mm:ss, hh:mm:ss, or seconds.'
        target = parts[0] * 60 + parts[1] if len(parts) >= 2 else parts[0]
        if len(parts) == 3:
            target = parts[0] * 3600 + parts[1] * 60 + parts[2]
        spec, frames, err = await asyncio.to_thread(self._collect, vid)
        if not frames:
            return 'No storyboard frames for ' + vid + ' (' + str(err or 'no signed storyboard') + ').'
        best = min(frames, key=lambda f: abs(f['time'] - target))
        blob = await asyncio.to_thread(self._encode, best['image'])
        prompt = 'Frame at ' + self._fmt_time(target) + ', closest available storyboard tile at ' + self._fmt_time(best['time']) + '. ' + (focus or 'Describe what is visible.') + ' Note any readable text. Keep it to 3 short sentences.'
        out, verr = await asyncio.to_thread(self._vision, prompt, blob)
        if verr:
            return f"Frame at {self._fmt_time(target)} ({self._fmt_time(best['time'])} actual):\n" + 'Vision endpoint unavailable (' + verr + ').\nTile image: ' + self._tile_url(best)
        return f"Frame at {self._fmt_time(target)} (tile read at {self._fmt_time(best['time'])}):\n{out}"

    async def youtube_visual_scenes(self, url: str, max_scenes: int=8, focus: str='') -> str:
        """Time-coded scene map of a YouTube video, without reading the transcript.

        Use when the user wants the visual structure of a video: what happens on screen
        and when. Only frames that differ from the previous one are described, so the
        output stays small.
        :param url: YouTube URL or video ID
        :param max_scenes: maximum scenes to describe
        :param focus: what matters visually
        """
        vid = self._video_id(url)
        spec, frames, err = await asyncio.to_thread(self._collect, vid)
        if not frames:
            return 'No storyboard frames for ' + vid + ' (' + str(err or 'no signed storyboard') + ').'
        scenes = [f for f in frames if f['changed']][:min(max_scenes, 16)]
        if not scenes:
            return 'No scene changes detected in ' + str(len(frames)) + ' sampled tiles for ' + vid + '.'
        res, verr = await asyncio.to_thread(self._describe, scenes, focus)
        header = 'VISUAL SCENE MAP - ' + vid + '\n' + f"{len(scenes)} scene boundaries from {len(frames)} sampled tiles (storyboard, {scenes[0]['grid'][0]}x{scenes[0]['grid'][1]} grid).\n"
        if verr:
            return header + 'Vision endpoint unavailable (' + verr + ').\n' + '\n'.join((self._fmt_time(f['time']) + '  ' + self._tile_url(f) for f in scenes))
        return header + '\n'.join(res)

    def _encode(self, tile):
        out = io.BytesIO()
        tile.save(out, 'JPEG', quality=88)
        return out.getvalue()

    def _tile_url(self, frame):
        """Storyboards are served as whole group images, not single tiles, so the
        URL identifies the group and the tile position inside it."""
        cols, rows = frame['grid']
        return f"{frame['group_url']}  tile {frame['tile'] + 1} of {cols * rows} ({cols}x{rows} grid)"

    async def youtube_frame_urls(self, url: str) -> str:
        """List timestamped storyboard frame URLs for a YouTube video.

        Use this when vision is unavailable or the user wants the frames themselves
        rather than descriptions. Costs almost no context.
        :param url: YouTube URL or video ID
        :return: Frame timestamps with image URLs
        """
        vid = self._video_id(url)
        spec, frames, err = await asyncio.to_thread(self._collect, vid)
        if not frames:
            return 'No storyboard frames for ' + vid + ' (' + str(err or 'no signed storyboard') + '). ' + ('Signature needed via yt-dlp; YouTube does not serve storyboards to anonymous InnerTube requests.' if not shutil.which(self.valves.ytdlp_path) else '')
        cols, rows = frames[0]['grid']
        lines = ['STORYBOARD FRAMES - ' + vid + f' ({cols}x{rows} tiles per group)']
        current = None
        for f in frames[:40]:
            if f['group_url'] != current:
                current = f['group_url']
                lines.append('\nGroup ' + str(f['group']) + ': ' + current)
            lines.append('  ' + self._fmt_time(f['time']) + '  tile ' + str(f['tile'] + 1))
        return '\n'.join(lines)[:self.valves.output_cap]
_builtin_tools = Tools()

async def read_youtube_video(self, url: str, visual: str='none', focus: str='') -> str:
    """Read a YouTube video: captions plus optional visual track.

        Use this whenever the user provides a YouTube URL or video ID. Default behavior
        is caption-only, which is the cheap path. Set visual="scenes" when the picture
        matters (what is on screen, visible text, action, layout), or visual="sample"
        for a sparse time-ordered pass over the whole video.
        :param url: YouTube watch, shorts, embed, or youtu.be URL, or a bare video ID
        :param visual: "none" for captions only, "sample" for evenly spaced frames,
                       "scenes" for only frames that changed
        :param focus: optional instruction for what to look for visually
        :return: Metadata, transcript, and a time-coded visual pass when requested
        """
    return await _builtin_tools.read_youtube_video(url=url, visual=visual, focus=focus)

async def youtube_frame_at(self, url: str, time: str, focus: str='') -> str:
    """Describe one specific moment in a YouTube video.

        Use when the user asks what is on screen at a particular timestamp. Resolution
        is a storyboard tile, so it is approximate for small text.
        :param url: YouTube URL or video ID
        :param time: timestamp as mm:ss, hh:mm:ss, or plain seconds
        :param focus: what to look for in that frame
        """
    return await _builtin_tools.youtube_frame_at(url=url, time=time, focus=focus)

async def youtube_visual_scenes(self, url: str, max_scenes: int=8, focus: str='') -> str:
    """Time-coded scene map of a YouTube video, without reading the transcript.

        Use when the user wants the visual structure of a video: what happens on screen
        and when. Only frames that differ from the previous one are described, so the
        output stays small.
        :param url: YouTube URL or video ID
        :param max_scenes: maximum scenes to describe
        :param focus: what matters visually
        """
    return await _builtin_tools.youtube_visual_scenes(url=url, max_scenes=max_scenes, focus=focus)

async def youtube_frame_urls(self, url: str) -> str:
    """List timestamped storyboard frame URLs for a YouTube video.

        Use this when vision is unavailable or the user wants the frames themselves
        rather than descriptions. Costs almost no context.
        :param url: YouTube URL or video ID
        :return: Frame timestamps with image URLs
        """
    return await _builtin_tools.youtube_frame_urls(url=url)
