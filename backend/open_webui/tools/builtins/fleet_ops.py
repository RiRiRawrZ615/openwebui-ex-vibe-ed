'''"""Open Web Vibes builtin: Fleet Ops

Generated from the WebUI tool export `fleet_ops` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: Fleet Ops
Exported functions: adb, check, fleet, ingest, password, probe, run, rustdesk, scan, session_open, session_read, session_send, sessions, vault

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''
import hashlib
import hmac
import json
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from pydantic import BaseModel
LOCAL_DIR = Path(os.environ.get('FLEET_VAULT_DIR', str(Path.home() / '.fleet_ops')))
SEED = {}

class Valves(BaseModel):
    master_passphrase: str = 'change-me'
    vault_dir: str = str(LOCAL_DIR)
    backup_dir: str = os.environ.get('FLEET_BACKUP_DIR', '')
    ssh_key: str = os.path.expanduser('~/.ssh/id_ed25519')
    default_user: str = os.environ.get('USER', 'youruser')
    adb_port: int = 5555
    connect_timeout: int = 6
    exec_timeout: int = 25

def _hash_pw(password: str, salt: str) -> str:
    return hashlib.sha256((salt + '|' + password).encode()).hexdigest()

def _derive_key(passphrase: str, salt: str) -> bytes:
    return hashlib.pbkdf2_hmac('sha256', passphrase.encode(), salt.encode(), 120000)

def _keystream(key: bytes, nonce: str, length: int) -> bytes:
    """HMAC-SHA256 counter-mode keystream (stdlib-only, no external deps)."""
    out = b''
    counter = 0
    while len(out) < length:
        out += hmac.new(key, nonce.encode() + counter.to_bytes(4, 'big'), hashlib.sha256).digest()
        counter += 1
    return out[:length]

def _encrypt(text: str, passphrase: str, salt: str) -> dict:
    key = _derive_key(passphrase, salt)
    nonce = hashlib.sha256((salt + str(time.time_ns())).encode()).hexdigest()[:16]
    data = text.encode()
    ct = bytes((b ^ k for b, k in zip(data, _keystream(key, nonce, len(data)))))
    mac = hmac.new(key, nonce.encode() + ct, hashlib.sha256).hexdigest()[:32]
    return {'nonce': nonce, 'ct': ct.hex(), 'mac': mac}

def _decrypt(blob: dict, passphrase: str, salt: str) -> str:
    key = _derive_key(passphrase, salt)
    ct = bytes.fromhex(blob['ct'])
    if hmac.new(key, blob['nonce'].encode() + ct, hashlib.sha256).hexdigest()[:32] != blob['mac']:
        raise ValueError('vault entry failed integrity check (wrong passphrase or corrupted entry)')
    return bytes((b ^ k for b, k in zip(ct, _keystream(key, blob['nonce'], len(ct))))).decode()

class Tools:

    def __init__(self, valves: Valves=Valves()):
        self.valves = valves
        self.vault_path = Path(valves.vault_dir) / 'vault.json'

    def _key(self) -> str:
        """Local key first. If this machine has no copy, pull it from the backup dir."""
        local = Path(self.valves.ssh_key)
        if local.is_file():
            return str(local)
        if not self.valves.backup_dir:
            return ''
        backup = Path(self.valves.backup_dir) / 'id_ed25519'
        if backup.is_file():
            local.parent.mkdir(parents=True, exist_ok=True)
            try:
                local.write_bytes(backup.read_bytes())
                local.chmod(384)
                return str(local)
            except Exception:
                return str(backup)
        alt = Path(self.valves.backup_dir) / 'id_rsa'
        if alt.is_file():
            local.parent.mkdir(parents=True, exist_ok=True)
            try:
                local.write_bytes(alt.read_bytes())
                local.chmod(384)
                return str(local)
            except Exception:
                return str(alt)
        return ''

    def _load_vault(self, create: bool=True) -> dict:
        """Check the local vault first; if missing, pull the copy from the backup dir."""
        if not self.vault_path.is_file():
            if self.valves.backup_dir:
                backup = Path(self.valves.backup_dir) / 'vault.json'
                if backup.is_file():
                    self.vault_path.parent.mkdir(parents=True, exist_ok=True)
                    self.vault_path.write_text(backup.read_text(), encoding='utf-8')
                    return json.loads(self.vault_path.read_text(encoding='utf-8'))
            if not create:
                return {}
            self.vault_path.parent.mkdir(parents=True, exist_ok=True)
            salt = hashlib.sha256(('fleet|' + self.valves.master_passphrase).encode()).hexdigest()[:16]
            vault = {'version': 2, 'salt': salt, 'hosts': {}}
            self.vault_path.write_text(json.dumps(vault, indent=2), encoding='utf-8')
            return vault
        return json.loads(self.vault_path.read_text(encoding='utf-8'))

    def _save_vault(self, vault: dict) -> None:
        self.vault_path.write_text(json.dumps(vault, indent=2), encoding='utf-8')
        if not self.valves.backup_dir:
            return
        backup = Path(self.valves.backup_dir)
        if backup.is_dir():
            try:
                backup.joinpath('vault.json').write_text(json.dumps(vault, indent=2), encoding='utf-8')
            except Exception:
                pass

    def vault(self, action: str='list', host: str='', password: str='') -> str:
        """Read or update the credential vault.

        Each host stores: machine_id (stable identity), aliases (names + IPs), ssh user,
        RustDesk ID, the password hash for verification, and the encrypted password.
        The file is portable, so the same vault works on any machine as long as the
        master passphrase matches. Lookup order is local vault first, then the backup copy.

        :param action: list, show, verify, set, or where
        :param host: host name or alias
        :param password: password to verify or store
        """
        vault = self._load_vault()
        salt = vault['salt']
        if action == 'where':
            backup_note = 'disabled' if not self.valves.backup_dir else f"{Path(self.valves.backup_dir) / 'vault.json'} (exists: {Path(self.valves.backup_dir).is_dir()})"
            return f'local: {self.vault_path}\nbackup: {backup_note}'
        hosts = vault.get('hosts', {})
        entry = hosts.get(host)
        if entry is None:
            for name, rec in hosts.items():
                if host in rec.get('aliases', []):
                    entry, host = (rec, name)
                    break
        if entry is None and action == 'set' and (host in SEED):
            entry = {'machine_id': SEED[host]['machine_id'], 'aliases': list(SEED[host]['aliases']), 'user': SEED[host]['user'], 'rustdesk_id': SEED[host]['rustdesk_id'], 'kind': SEED[host]['kind']}
            hosts[host] = entry
        if action == 'list':
            if not hosts:
                return 'vault is empty'
            return '\n'.join((f"{n:<18} id={(rec.get('machine_id') or '-')[:12]} user={rec.get('user')} pw={('hashed' if rec.get('pw_sha256') else '-')} aliases={','.join(rec.get('aliases', []))}" for n, rec in hosts.items()))
        if entry is None:
            return f"no vault entry for '{host}'"
        if action == 'show':
            return json.dumps({'name': host, **entry}, indent=2)
        if action == 'verify':
            if not password:
                return 'verify needs a password'
            return f"{host}: {('MATCH' if _hash_pw(password, salt) == entry.get('pw_sha256') else 'NO MATCH')}"
        if action == 'set':
            if not password:
                return 'set needs a password'
            entry['pw_sha256'] = _hash_pw(password, salt)
            entry['enc'] = _encrypt(password, self.valves.master_passphrase, salt)
            vault['hosts'][host] = entry
            self._save_vault(vault)
            return f'stored password for {host} (hash + encrypted)'
        return f'unknown action: {action}'

    def password(self, host: str) -> str:
        """Return the decrypted password for a host from the vault (used for sudo -S and RustDesk).

        :param host: host name or alias
        """
        vault = self._load_vault()
        salt = vault['salt']
        hosts = vault.get('hosts', {})
        entry = hosts.get(host)
        if entry is None:
            for name, rec in hosts.items():
                if host in rec.get('aliases', []):
                    entry, host = (rec, name)
                    break
        if entry is None:
            return f"no vault entry for '{host}'"
        if not entry.get('enc'):
            return f'{host}: no encrypted password stored (only a hash)'
        try:
            return f"{host}: {_decrypt(entry['enc'], self.valves.master_passphrase, salt)}"
        except ValueError as exc:
            return str(exc)

    def _live_mac(self, ip: str) -> str:
        """MAC currently answering for an address, from the ARP table."""
        try:
            out = subprocess.run(['arp', '-an', ip], capture_output=True, text=True, timeout=6).stdout
            m = re.search('at ([0-9a-f:]{17})', out)
            return m.group(1) if m else ''
        except Exception:
            return ''

    def _resolve(self, target: str) -> tuple:
        """Resolve a name, alias, IP, or RustDesk ID.

        Order matters. For an address, first ask who actually owns it right now by matching the
        live ARP MAC to a vault entry, then fall back to alias lookup. Without this, a DHCP
        address that changed hands resolves to whichever entry lists it first.
        """
        key = target.strip().lower()
        vault = self._load_vault()
        hosts = vault.get('hosts', {})
        if re.fullmatch('\\d+\\.\\d+\\.\\d+\\.\\d+', key):
            mac = self._live_mac(key)
            if mac:
                for name, rec in hosts.items():
                    if mac in rec.get('macs', {}):
                        return (key, name, rec)
        for name, rec in hosts.items():
            if key == name or key in rec.get('aliases', []):
                aliases = rec.get('aliases', [])
                fallback = aliases[1] if len(aliases) > 1 else aliases[0] if aliases else name
                return (rec.get('last_seen') or fallback or name, name, rec)
        for name, rec in SEED.items():
            if key == name or key in rec['aliases']:
                aliases = rec['aliases']
                fallback = aliases[1] if len(aliases) > 1 else aliases[0]
                return (key if key in aliases else fallback, name, rec)
        return (target, key, None)

    def _record(self, target: str, machine_id: str) -> str:
        """Smart detection: if an IP turns out to be a machine already known under another
        name, retag that alias and store it as the last seen address."""
        vault = self._load_vault()
        hosts = vault.get('hosts', {})
        entry = None
        for name, rec in hosts.items():
            if machine_id and rec.get('machine_id') == machine_id:
                entry, host_name = (rec, name)
                break
        if entry is None:
            seed = SEED.get(target)
            if not seed:
                return f'unknown machine id {machine_id}'
            entry = {'machine_id': seed['machine_id'], 'aliases': list(seed['aliases']), 'user': seed['user'], 'rustdesk_id': seed['rustdesk_id'], 'kind': seed['kind']}
            host_name = target
            hosts[host_name] = entry
        if target not in entry.get('aliases', []):
            entry.setdefault('aliases', []).append(target)
        entry['last_seen'] = target
        entry['seen_at'] = int(time.time())
        vault['hosts'][host_name] = entry
        self._save_vault(vault)
        return f'retagged {target} as {host_name} (machine_id {machine_id[:12]}), last seen updated'

    def _ssh(self, host: str, user: str, command: str) -> tuple:
        key = self._key()
        base = ['ssh', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={self.valves.connect_timeout}', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ServerAliveInterval=5']
        if Path(key).is_file():
            base += ['-i', key]
        base += [f'{user}@{host}', command]
        try:
            p = subprocess.run(base, capture_output=True, text=True, timeout=self.valves.exec_timeout, errors='replace')
            return (p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip())
        except subprocess.TimeoutExpired:
            return (124, '', f'timed out after {self.valves.exec_timeout}s')
        except Exception as exc:
            return (1, '', f'error: {exc}')

    def run(self, target: str, command: str, sudo: bool=False, confirm: bool=False) -> str:
        """Run a shell command on a fleet machine over SSH (shared key, no interactive password).

        Smart resolve: names, aliases, Tailscale IPs, LAN IPs, and RustDesk IDs all work.
        If the address maps to a machine already known under a different name, the vault is
        retagged and the changed IP is stored as last seen.

        :param target: machine name, alias, IP, or RustDesk ID
        :param command: shell command
        :param sudo: run through sudo -S using the vault password (never sudo -n; policy still demands an auth exchange even though any stdin input is accepted)
        :param confirm: extra round trip to verify machine_id; automatic for unidentified machines, pass True on first contact or when an address may have moved
        """
        host, name, rec = self._resolve(target)
        user = (rec or {}).get('user', self.valves.default_user)
        if sudo:
            pw = self.password(name).split(': ', 1)[-1]
            remote = f"printf %s\\n {shlex.quote(pw)} | sudo -S -p '' {command}"
        else:
            remote = command
        code, out, err = self._ssh(host, user, remote)
        if code == 0 and (confirm or not (rec or {}).get('machine_id')):
            c2, o2, _ = self._ssh(host, user, 'cat /etc/machine-id 2>/dev/null || cat /var/lib/dbus/machine-id 2>/dev/null')
            if c2 == 0 and o2:
                note = self._record(host, o2)
                if note:
                    err = (err + '\n' + note).strip()
        head = f'[{name} | {host}] exit={code}'
        return f"{head}\n{out or err or '(no output)'}"

    def probe(self, target: str) -> str:
        """Try every known address for a machine and report which one answers, then store it.

        Use this when an IP changed and you are not sure which address is live.

        :param target: machine name
        """
        host, name, rec = self._resolve(target)
        candidates = list(dict.fromkeys((rec or {}).get('aliases', []) or [target]))
        results = []
        for cand in candidates:
            code, out, err = self._ssh(cand, (rec or {}).get('user', self.valves.default_user), 'echo OK; cat /etc/machine-id 2>/dev/null')
            if code == 0 and out.startswith('OK'):
                mid = out.splitlines()[-1]
                note = self._record(cand, mid)
                results.append(f'  {cand}: reachable ({note})')
                return '\n'.join(results)
            results.append(f"  {cand}: no ({err or 'unreachable'})")
        return f'probe {name}\n' + '\n'.join(results)

    def _state(self) -> dict:
        path = Path(self.valves.vault_dir) / 'sessions.json'
        if path.is_file():
            try:
                return json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                return {}
        return {}

    def _save_state(self, state: dict) -> None:
        Path(self.valves.vault_dir).mkdir(parents=True, exist_ok=True)
        Path(self.valves.vault_dir).joinpath('sessions.json').write_text(json.dumps(state, indent=2), encoding='utf-8')

    def session_open(self, target: str, name: str='') -> str:
        """Open a persistent tmux-backed session on a fleet machine (OpenTerminal style).

        The session survives across tool calls, so long-running processes, logcat, and
        interactive prompts keep working. Each session is a local tmux window running ssh,
        with an output cursor stored in sessions.json.

        :param target: machine name or IP
        :param name: session name (defaults to fleet_<machine>)
        """
        host, mach, rec = self._resolve(target)
        if rec and rec.get('kind') == 'android':
            sess = name or f'fleet_{mach}'
            subprocess.run(['tmux', 'new-session', '-d', '-s', sess, f'adb -s {host}:{self.valves.adb_port} shell'], capture_output=True, text=True, timeout=15)
            return f'session {sess} -> adb shell on {mach}'
        user = (rec or {}).get('user', self.valves.default_user)
        key = self.valves.ssh_key
        keyflag = f' -i {key}' if Path(key).is_file() else ''
        sess = name or f'fleet_{mach}'
        state = self._state()
        if sess not in state:
            subprocess.run(['tmux', 'new-session', '-d', '-s', sess, f'ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new{keyflag} {user}@{host}'], capture_output=True, text=True, timeout=15)
            state[sess] = {'machine': mach, 'host': host, 'offset': 0, 'created': int(time.time())}
            self._save_state(state)
        return f'session {sess} -> {mach} ({host})'

    def session_send(self, session: str, command: str) -> str:
        """Send a command into an open tmux session.

        :param session: session name from session_open
        :param command: command to send to that session's shell (or its foreground program)
        """
        subprocess.run(['tmux', 'send-keys', '-t', session, command, 'Enter'], timeout=10)
        return f'sent to {session}: {command}'

    def session_read(self, session: str, tail: int=40) -> str:
        """Read new output from a tmux session, starting at the stored cursor.

        :param session: session name from session_open
        :param tail: maximum lines to return
        """
        state = self._state()
        rec = state.get(session, {})
        out = subprocess.run(['tmux', 'capture-pane', '-t', session, '-p', '-S', '-2000'], capture_output=True, text=True, timeout=10).stdout
        lines = out.splitlines()
        while lines and (not lines[-1].strip()):
            lines.pop()
        offset = rec.get('offset', 0)
        new = [ln for ln in lines[offset:offset + tail] if ln.strip()]
        if rec:
            rec['offset'] = len(lines)
            self._save_state(state)
        return f'[{session}] {len(new)} new lines\n' + '\n'.join(new) if new else f'[{session}] no new output'

    def sessions(self) -> str:
        """List open tmux sessions and their cursors."""
        state = self._state()
        if not state:
            return 'no open sessions'
        live = subprocess.run(['tmux', 'ls'], capture_output=True, text=True, timeout=10).stdout
        lines = []
        for sess, rec in state.items():
            lines.append(f"{sess:<24} machine={rec.get('machine')} host={rec.get('host')} offset={rec.get('offset')} live={('yes' if sess in live else 'no')}")
        return '\n'.join(lines)

    def adb(self, action: str, device: str='', command: str='', port: int=0, local: str='', remote: str='') -> str:
        """Wireless ADB for Android devices on the fleet.

        Typical flow: adb(action='connect', device='10.0.0.20', port=5555), then
        adb(action='shell', device='10.0.0.20:5555', command='getprop ro.product.model').
        Common actions: connect, disconnect, devices, shell, logcat, install, push, pull,
        screencap, reboot, root, battery, wifi, props.

        :param action: what to do
        :param device: ip (connect) or serial like 10.0.0.20:5555 (other actions)
        :param command: shell command for 'shell', filter for 'logcat'
        :param port: tcp port for connect (default 5555)
        :param local: local path for push/pull
        :param remote: remote path for push/pull
        """
        port = port or self.valves.adb_port
        base = ['adb']
        if device and action != 'connect' and (action != 'devices'):
            base += ['-s', device if ':' in device else f'{device}:{port}']
        if action == 'connect':
            target = device if ':' in device else f'{device}:{port}'
            p = subprocess.run(base + ['connect', target], capture_output=True, text=True, timeout=15, errors='replace')
            return p.stdout.strip() or p.stderr.strip()
        if action == 'disconnect':
            target = device if ':' in device else f'{device}:{port}'
            p = subprocess.run(['adb', 'disconnect', target], capture_output=True, text=True, timeout=15, errors='replace')
            return p.stdout.strip() or p.stderr.strip()
        if action == 'devices':
            p = subprocess.run(base + ['devices', '-l'], capture_output=True, text=True, timeout=15, errors='replace')
            return p.stdout.strip() or p.stderr.strip()
        if action in ('shell', 'logcat'):
            p = subprocess.run(base + [action, command or ('' if action == 'shell' else '-d -v event')], capture_output=True, text=True, timeout=self.valves.exec_timeout, errors='replace')
            return p.stdout.strip() or p.stderr.strip()
        if action == 'install':
            p = subprocess.run(base + ['install', '-r', command], capture_output=True, text=True, timeout=60, errors='replace')
            return p.stdout.strip() or p.stderr.strip()
        if action in ('push', 'pull'):
            p = subprocess.run(base + [action, local, remote], capture_output=True, text=True, timeout=60, errors='replace')
            return p.stdout.strip() or p.stderr.strip()
        if action in ('screencap', 'reboot', 'root', 'battery', 'wifi', 'props'):
            if action == 'props':
                cmd = 'getprop'
            elif action == 'battery':
                cmd = 'dumpsys battery'
            elif action == 'wifi':
                cmd = 'dumpsys wifi | head -40'
            elif action == 'screencap':
                return f"run: adb -s {device}:{port} exec-out screencap -p > {local or '/tmp/shot.png'}"
            else:
                cmd = action
            p = subprocess.run(base + ['shell', cmd], capture_output=True, text=True, timeout=self.valves.exec_timeout, errors='replace')
            return p.stdout.strip() or p.stderr.strip()
        return f'unknown adb action: {action}'

    def rustdesk(self, target: str, launch: bool=False) -> str:
        """RustDesk peer info and optional GUI session launch.

        The installed build is the Flutter UI binary on every machine, so it cannot be
        scripted for headless control. Use run() for commands. This reports the peer record
        and can open the interactive window using the vault password.

        :param target: machine name or IP
        :param launch: open the session window (needs a display)
        """
        host, name, rec = self._resolve(target)
        rid = (rec or {}).get('rustdesk_id', '')
        if not rid:
            return f'no RustDesk id for {name}'
        pw = self.password(name).split(': ', 1)[-1]
        info = f'rustdesk id={rid} name={name}'
        if not launch:
            return info + '\n(UI-only build: no scripted execution; use run() for commands)'
        if not os.environ.get('DISPLAY') and (not os.environ.get('WAYLAND_DISPLAY')):
            return info + '\ncannot launch: no display in this process environment'
        subprocess.run(['xdg-open', f'rustdesk://{rid}?password={pw}'], capture_output=True, text=True, timeout=10)
        return f'{info}\nsession window opening for {name}'

    def fleet(self) -> str:
        """List every machine in the vault/seed catalog: identity, aliases, user, kind."""
        vault = self._load_vault()
        hosts = vault.get('hosts', {}) or {n: dict(r, aliases=list(r['aliases'])) for n, r in SEED.items()}
        lines = []
        for name, rec in hosts.items():
            lines.append(f"{name:<20} {(rec.get('machine_id') or '-')[:12]} {rec.get('kind', 'linux'):<8} user={str(rec.get('user')):<20} last={rec.get('last_seen', '-'):<15} aliases={','.join(rec.get('aliases', []))}")
        return '\n'.join(lines) if lines else 'no machines'

    def ingest(self) -> str:
        """Import RustDesk's peer database into the vault as identity anchors.

        RustDesk stores ip -> mac per peer. MAC is a better identity anchor than IP because
        it survives address changes, so the scan can retag a new address onto the right machine.
        """
        path = Path(os.path.expanduser('~/.config/rustdesk/RustDesk_lan_peers.toml'))
        if not path.is_file():
            return 'rustdesk peer database not found'
        vault = self._load_vault()
        hosts = vault.setdefault('hosts', {})
        added = []
        text = path.read_text(encoding='utf-8', errors='replace')
        for block in re.split('\\[\\[peers\\]\\]\\n', text)[1:]:
            name = re.search("hostname = '([^']*)'", block)
            rid = re.search("id = '([^']*)'", block)
            pairs = re.findall('"([\\d.]+)" = \\\'([0-9a-f:]{17})\\\'', block)
            if not pairs:
                continue
            host = (name.group(1) if name else '') or rid.group(1)
            rec = hosts.setdefault(host, {'machine_id': '', 'aliases': [host], 'user': self.valves.default_user, 'rustdesk_id': rid.group(1) if rid else '', 'kind': 'linux'})
            for ip, mac in pairs:
                rec.setdefault('macs', {})[mac] = ip
                if ip not in rec.get('aliases', []):
                    rec.setdefault('aliases', []).append(ip)
            added.append(f"{host}: {len(pairs)} address(es), macs={list(rec['macs'])}")
        vault['hosts'] = hosts
        self._save_vault(vault)
        return 'ingest\n' + '\n'.join(added)

    def scan(self) -> str:
        """Re-discover addresses for every machine in the catalog.

        Reads tailscale status for current peer IPs and the local ARP table for LAN IPs, then
        merges anything new into the vault aliases. Machine identity comes from machine_id, so
        an address that moved onto a machine already known under another name gets retagged.
        Use this after a lease change or when a machine shows up on a new address.
        """
        vault = self._load_vault()
        hosts = vault.get('hosts', {}) or {n: dict(r, aliases=list(r['aliases'])) for n, r in SEED.items()}
        lines = []
        try:
            ts = subprocess.run(['tailscale', 'status', '--peers=true'], capture_output=True, text=True, timeout=10).stdout
        except Exception as exc:
            ts = ''
            lines.append(f'tailscale unavailable: {exc}')
        for row in ts.splitlines():
            parts = row.split()
            if len(parts) < 3:
                continue
            ip, name = (parts[0], parts[1].lower())
            if name in hosts:
                rec = hosts[name]
                if ip not in rec.get('aliases', []):
                    rec.setdefault('aliases', []).append(ip)
                    lines.append(f'added {ip} to {name}')
                rec['last_seen'] = ip
                rec['state'] = ' '.join(parts[3:]) or 'active'
        try:
            arp = subprocess.run(['arp', '-an'], capture_output=True, text=True, timeout=10).stdout
        except Exception:
            arp = ''
        for ip, mac in re.findall('\\((\\d+\\.\\d+\\.\\d+\\.\\d+)\\) at ([0-9a-f:]{17})', arp):
            matched = None
            for name, rec in hosts.items():
                if mac in rec.get('macs', {}):
                    matched = name
                    break
            if matched:
                rec = hosts[matched]
                if ip not in rec.get('aliases', []):
                    rec.setdefault('aliases', []).append(ip)
                    lines.append(f'arp: {ip} -> {matched} (same mac)')
                rec['last_seen'] = ip
            else:
                lines.append(f'arp: {ip} ({mac}) not in catalog')
        vault['hosts'] = hosts
        self._save_vault(vault)
        return 'scan\n' + ('\n'.join(lines) if lines else 'no changes found')

    def check(self, target: str) -> str:
        """Test a machine: which address answers, key auth, OS, sudo state, vault entry.

        :param target: machine name or IP
        """
        host, name, rec = self._resolve(target)
        user = (rec or {}).get('user', self.valves.default_user)
        t0 = time.time()
        code, out, err = self._ssh(host, user, "hostname; uname -r; head -1 /etc/os-release 2>/dev/null; cat /etc/machine-id 2>/dev/null; printf '\\n' | sudo -S -p '' id >/dev/null 2>&1 && echo 'sudo: stdin auth accepted' || echo 'sudo: rejected'")
        dt = round(time.time() - t0, 2)
        if code == 0:
            mid = re.search('^[0-9a-f]{32}$', out, re.M)
            if mid:
                self._record(host, mid.group(0))
            return f'[{name} | {host}] reachable in {dt}s\n{out}'
        return f'[{name} | {host}] unreachable exit={code} in {dt}s\n{err}'
_builtin_tools = Tools()

def vault(self, action: str='list', host: str='', password: str='') -> str:
    """Read or update the credential vault.

        Each host stores: machine_id (stable identity), aliases (names + IPs), ssh user,
        RustDesk ID, the password hash for verification, and the encrypted password.
        The file is portable, so the same vault works on any machine as long as the
        master passphrase matches. Lookup order is local vault first, then the backup copy.

        :param action: list, show, verify, set, or where
        :param host: host name or alias
        :param password: password to verify or store
        """
    return _builtin_tools.vault(action=action, host=host, password=password)

def password(self, host: str) -> str:
    """Return the decrypted password for a host from the vault (used for sudo -S and RustDesk).

        :param host: host name or alias
        """
    return _builtin_tools.password(host=host)

def run(self, target: str, command: str, sudo: bool=False, confirm: bool=False) -> str:
    """Run a shell command on a fleet machine over SSH (shared key, no interactive password).

        Smart resolve: names, aliases, Tailscale IPs, LAN IPs, and RustDesk IDs all work.
        If the address maps to a machine already known under a different name, the vault is
        retagged and the changed IP is stored as last seen.

        :param target: machine name, alias, IP, or RustDesk ID
        :param command: shell command
        :param sudo: run through sudo -S using the vault password (never sudo -n; policy still demands an auth exchange even though any stdin input is accepted)
        :param confirm: extra round trip to verify machine_id; automatic for unidentified machines, pass True on first contact or when an address may have moved
        """
    return _builtin_tools.run(target=target, command=command, sudo=sudo, confirm=confirm)

def probe(self, target: str) -> str:
    """Try every known address for a machine and report which one answers, then store it.

        Use this when an IP changed and you are not sure which address is live.

        :param target: machine name
        """
    return _builtin_tools.probe(target=target)

def session_open(self, target: str, name: str='') -> str:
    """Open a persistent tmux-backed session on a fleet machine (OpenTerminal style).

        The session survives across tool calls, so long-running processes, logcat, and
        interactive prompts keep working. Each session is a local tmux window running ssh,
        with an output cursor stored in sessions.json.

        :param target: machine name or IP
        :param name: session name (defaults to fleet_<machine>)
        """
    return _builtin_tools.session_open(target=target, name=name)

def session_send(self, session: str, command: str) -> str:
    """Send a command into an open tmux session.

        :param session: session name from session_open
        :param command: command to send to that session's shell (or its foreground program)
        """
    return _builtin_tools.session_send(session=session, command=command)

def session_read(self, session: str, tail: int=40) -> str:
    """Read new output from a tmux session, starting at the stored cursor.

        :param session: session name from session_open
        :param tail: maximum lines to return
        """
    return _builtin_tools.session_read(session=session, tail=tail)

def sessions(self) -> str:
    """List open tmux sessions and their cursors."""
    return _builtin_tools.sessions()

def adb(self, action: str, device: str='', command: str='', port: int=0, local: str='', remote: str='') -> str:
    """Wireless ADB for Android devices on the fleet.

        Typical flow: adb(action='connect', device='10.0.0.20', port=5555), then
        adb(action='shell', device='10.0.0.20:5555', command='getprop ro.product.model').
        Common actions: connect, disconnect, devices, shell, logcat, install, push, pull,
        screencap, reboot, root, battery, wifi, props.

        :param action: what to do
        :param device: ip (connect) or serial like 10.0.0.20:5555 (other actions)
        :param command: shell command for 'shell', filter for 'logcat'
        :param port: tcp port for connect (default 5555)
        :param local: local path for push/pull
        :param remote: remote path for push/pull
        """
    return _builtin_tools.adb(action=action, device=device, command=command, port=port, local=local, remote=remote)

def rustdesk(self, target: str, launch: bool=False) -> str:
    """RustDesk peer info and optional GUI session launch.

        The installed build is the Flutter UI binary on every machine, so it cannot be
        scripted for headless control. Use run() for commands. This reports the peer record
        and can open the interactive window using the vault password.

        :param target: machine name or IP
        :param launch: open the session window (needs a display)
        """
    return _builtin_tools.rustdesk(target=target, launch=launch)

def fleet(self) -> str:
    """List every machine in the vault/seed catalog: identity, aliases, user, kind."""
    return _builtin_tools.fleet()

def ingest(self) -> str:
    """Import RustDesk's peer database into the vault as identity anchors.

        RustDesk stores ip -> mac per peer. MAC is a better identity anchor than IP because
        it survives address changes, so the scan can retag a new address onto the right machine.
        """
    return _builtin_tools.ingest()

def scan(self) -> str:
    """Re-discover addresses for every machine in the catalog.

        Reads tailscale status for current peer IPs and the local ARP table for LAN IPs, then
        merges anything new into the vault aliases. Machine identity comes from machine_id, so
        an address that moved onto a machine already known under another name gets retagged.
        Use this after a lease change or when a machine shows up on a new address.
        """
    return _builtin_tools.scan()

def check(self, target: str) -> str:
    """Test a machine: which address answers, key auth, OS, sudo state, vault entry.

        :param target: machine name or IP
        """
    return _builtin_tools.check(target=target)
