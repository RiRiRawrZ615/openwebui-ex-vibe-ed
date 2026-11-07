"""Crash-surviving turn buffer with a throttled write policy.

A turn is lost today because the delta path keeps in-progress state in response_streams
and the chat record is written once, at the end. A process killed mid-turn therefore leaves
nothing behind. This module records each arrival to a per-chat buffer so a killed turn leaves
the deltas that already arrived.

The write policy is the point, not the recording. Deltas arrive many times per second, and a
write per arrival is what would wear out the drive. So:

- Deltas accumulate in memory and are written in one call when a line or byte threshold is
  crossed, or when a time window elapses, whichever comes first.
- No fsync. Data written and flushed to the OS survives a process death because it lives in
  the page cache. fsync is what damages endurance, and it buys nothing here.
- The file handle is opened once per turn and kept open, so a flush is one write, not an
  open/write/close cycle.
- A turn that finished writes a .done marker and the buffer is removed, so the chat record is
  the source of truth again and the buffer does not accumulate on disk.

Layout matches what owv-turns.sh expects, one JSON line per arrival:
    <state>/turns/<chat_id>/<message_id>.jsonl
    <state>/turns/<chat_id>/<message_id>.done

The state dir is resolved the same way the launcher resolves LOG_DIR, so the shell script and
this module read the same files.
"""
import json
import os
import time
from pathlib import Path

# Thresholds, overridable from the environment. A typical turn of a couple hundred deltas
# lands on two or three writes with these numbers rather than a couple hundred.
FLUSH_LINES = int(os.getenv('OWV_TURN_FLUSH_LINES', '64'))
FLUSH_BYTES = int(os.getenv('OWV_TURN_FLUSH_BYTES', '32768'))
FLUSH_SECONDS = float(os.getenv('OWV_TURN_FLUSH_SECONDS', '10'))

# Per-turn caps. A runaway turn should not grow the buffer without limit.
MAX_RECORDS = int(os.getenv('OWV_TURN_MAX_RECORDS', '4000'))
MAX_FILE_BYTES = int(os.getenv('OWV_TURN_MAX_FILE_BYTES', '1048576'))

DEFAULT_PRUNE_DAYS = float(os.getenv('OWV_TURN_PRUNE_DAYS', '7'))


def state_dir() -> Path:
    """The launcher's state dir. Same resolution order as owv.sh LOG_DIR."""
    xdg = os.getenv('XDG_STATE_HOME')
    base = Path(xdg) if xdg else Path.home() / '.local' / 'state'
    return base / 'openwebvibes'


def turn_dir(chat_id: str, root: Path | str = '') -> Path:
    root_path = Path(root) if root else state_dir()
    return root_path / 'turns' / str(chat_id)


class TurnBuffer:
    """One turn's pending deltas. Nothing hits the disk until a threshold is crossed."""

    def __init__(self, chat_id: str, message_id: str, root: Path | str = ''):
        self.chat_id = str(chat_id)
        self.message_id = str(message_id)
        self.dir = turn_dir(self.chat_id, root)
        self.path = self.dir / f'{self.message_id}.jsonl'
        self.done_path = self.dir / f'{self.message_id}.done'

        self.pending: list[str] = []
        self.pending_bytes = 0
        self.records = 0
        self.written_lines = 0
        self.writes = 0
        self.truncated = False
        self.last_flush = time.time()
        self.handle = None

    def record(self, text: str, kind: str = 'delta') -> bool:
        """Buffer one arrival. Returns True when this call triggered a write."""
        if self.truncated or not text:
            return False

        if self.records >= MAX_RECORDS:
            self.truncated = True
            return False

        line = json.dumps(
            {'t': time.time(), 'type': kind, 'text': text},
            ensure_ascii=False,
            separators=(',', ':'),
        )
        self.pending.append(line)
        self.pending_bytes += len(line.encode('utf-8')) + 1
        self.records += 1

        if self.pending_bytes >= MAX_FILE_BYTES:
            self.truncated = True
            self.flush()
            return True

        if len(self.pending) >= FLUSH_LINES or self.pending_bytes >= FLUSH_BYTES:
            self.flush()
            return True

        if time.time() - self.last_flush >= FLUSH_SECONDS and self.pending:
            self.flush()
            return True

        return False

    def flush(self) -> int:
        """Write everything pending in a single write. No fsync, on purpose."""
        if not self.pending:
            return 0

        payload = '\n'.join(self.pending) + '\n'
        try:
            self.dir.mkdir(parents=True, exist_ok=True)
            handle = self.handle
            if handle is None:
                handle = open(self.path, 'a', encoding='utf-8')
                self.handle = handle
            handle.write(payload)
            handle.flush()  # to the OS page cache, which survives a process death
        except OSError:
            # A buffer must never take the turn down with it. The chat record still saves.
            self.pending.clear()
            self.pending_bytes = 0
            return 0

        lines = len(self.pending)
        self.pending.clear()
        self.pending_bytes = 0
        self.written_lines += lines
        self.writes += 1
        self.last_flush = time.time()
        return lines

    def finish(self, interrupted: bool = False) -> dict:
        """Close the turn. A finished turn is removed; an interrupted one is left behind."""
        self.flush()
        if self.handle is not None:
            try:
                self.handle.close()
            except OSError:
                pass
            self.handle = None

        if interrupted:
            return {
                'chat_id': self.chat_id,
                'message_id': self.message_id,
                'interrupted': True,
                'lines': self.written_lines,
                'writes': self.writes,
                'path': str(self.path),
            }

        # The chat record now holds the turn, so the buffer is dropped rather than marked.
        try:
            if self.path.exists():
                self.path.unlink()
        except OSError:
            pass
        try:
            self.done_path.write_text('done\n', encoding='utf-8')
        except OSError:
            pass
        return {
            'chat_id': self.chat_id,
            'message_id': self.message_id,
            'interrupted': False,
            'lines': self.written_lines,
            'writes': self.writes,
            'removed': True,
        }


def buffer_for(chat_id: str, message_id: str, root: Path | str = '') -> TurnBuffer | None:
    if not chat_id or not message_id:
        return None
    return TurnBuffer(chat_id, message_id, root)


def read_turn(chat_id: str, message_id: str, root: Path | str = '') -> dict | None:
    """Reconstruct a turn from the buffer. Returns None when there is nothing to recover."""
    path = turn_dir(chat_id, root) / f'{message_id}.jsonl'
    if not path.exists():
        return None

    items = []
    for raw in path.read_text(encoding='utf-8').splitlines():
        if not raw:
            continue
        try:
            items.append(json.loads(raw))
        except (ValueError, TypeError):
            # A half-written last line is expected from a kill. Keep the readable ones.
            continue

    return {
        'chat_id': str(chat_id),
        'message_id': str(message_id),
        'items': items,
        'text': ''.join(item.get('text', '') for item in items),
        'finished': (turn_dir(chat_id, root) / f'{message_id}.done').exists(),
        'bytes': path.stat().st_size,
    }


def interrupted_turns(chat_id: str = '', root: Path | str = '') -> list[dict]:
    """Buffers with no .done marker, oldest first. These are the turns a crash lost."""
    root_path = Path(root) if root else state_dir()
    scope = root_path / 'turns'
    if chat_id:
        scope = scope / str(chat_id)
    if not scope.exists():
        return []

    found = []
    for file in scope.rglob('*.jsonl'):
        marker = file.with_suffix('.done')
        if marker.exists():
            continue
        found.append(
            {
                'chat_id': file.parent.name,
                'message_id': file.stem,
                'path': str(file),
                'bytes': file.stat().st_size,
                'age_seconds': time.time() - file.stat().st_mtime,
                'lines': sum(1 for line in file.read_text(encoding='utf-8').splitlines() if line),
            }
        )

    return sorted(found, key=lambda item: item['age_seconds'], reverse=True)


def prune(root: Path | str = '', max_age_days: float = DEFAULT_PRUNE_DAYS) -> int:
    """Drop buffers older than max_age_days. A kill leaves a buffer; it should not live forever."""
    root_path = Path(root) if root else state_dir()
    scope = root_path / 'turns'
    if not scope.exists():
        return 0

    cutoff = time.time() - max_age_days * 86400
    removed = 0
    for file in scope.rglob('*.jsonl'):
        if file.stat().st_mtime < cutoff:
            try:
                file.unlink()
                marker = file.with_suffix('.done')
                if marker.exists():
                    marker.unlink()
                removed += 1
            except OSError:
                continue
    return removed
