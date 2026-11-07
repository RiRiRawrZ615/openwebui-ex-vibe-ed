'''"""Open Web Vibes builtin: Fleet SSH + RustDesk

Generated from the WebUI tool export `fleet_ssh` by tools/convert_builtins.py.
Do not edit by hand, because the next regeneration would overwrite it. User edits belong in
the overlay, which takes precedence over this file.

Original tool name: Fleet SSH + RustDesk
Exported functions: check, fleet, pull, push, run, run_sudo, rustdesk

Structure note: the original `Tools` class is kept intact and exposed through module-level
wrappers, so the tool's valves, helpers and instance state behave exactly as they did as a
DB tool. See the converter's docstring for why this is preferable to rewriting the bodies.
"""
'''
from __future__ import annotations
import os
import re
import shlex
import subprocess
import time
from pathlib import Path
from pydantic import BaseModel
SSH_KEY = os.path.expanduser('~/.ssh/id_ed25519')
BACKUP_KEY = os.environ.get('FLEET_BACKUP_KEY', '')
RUSTDESK_CONF = Path(os.path.expanduser('~/.config/rustdesk'))

def _hosts() -> dict:
    """Static host catalog: name -> (ssh target, rustdesk id).

    Replace with your own machines. Keys are the friendly names you can pass to any method;
    the first value is the address the tool connects to (Tailscale IP, LAN IP, or hostname).
    Leave it empty and pass a raw IP/ID as `target` instead, or let Fleet Ops build the catalog.
    """
    return {}

def _resolve(target: str) -> tuple:
    """Resolve a friendly name, Tailscale IP, or LAN IP to (host, machine name)."""
    key = target.strip().lower()
    table = _hosts()
    if key in table:
        ip, rid = table[key]
        return (ip, key)
    for name, (ip, rid) in table.items():
        if target in (ip, rid):
            return (target, name)
    return (target, key)

def _key(valves: Valves) -> str:
    """Prefer the Valves key; fall back to the backup key path."""
    key = valves.ssh_key
    if Path(key).is_file():
        return key
    if valves.backup_key and Path(valves.backup_key).is_file():
        return valves.backup_key
    return key

def _ssh_base(valves: Valves, host: str, user: str='') -> list:
    user = user or valves.default_user
    key = _key(valves)
    base = ['ssh', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={int(valves.connect_timeout)}', '-o', 'StrictHostKeyChecking=accept-new', '-o', 'ServerAliveInterval=5']
    if Path(key).is_file():
        base += ['-i', key]
    base.append(f'{user}@{host}')
    return base

def _run(cmd: list, timeout: int) -> tuple:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, errors='replace')
        return (p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip())
    except subprocess.TimeoutExpired:
        return (124, '', f'timed out after {timeout}s')
    except Exception as exc:
        return (1, '', f'error: {exc}')

def _peers() -> list:
    """Read RustDesk's peer database for names, platforms, LAN IPs, online state."""
    out = []
    path = RUSTDESK_CONF / 'RustDesk_lan_peers.toml'
    if not path.is_file():
        return out
    text = path.read_text(encoding='utf-8', errors='replace')
    for block in re.split('\\n\\[\\[peers\\]\\]\\n', text)[1:]:
        peer = {}
        for line in block.splitlines():
            m = re.match("^(\\w+) = (?:'(.*)'|(true|false))$", line.strip())
            if m:
                peer[m.group(1)] = m.group(2) if m.group(2) is not None else m.group(3)
            elif line.startswith('[peers.ip_mac]'):
                ipm = re.search('"([\\d.]+)"', block[block.find('[peers.ip_mac]'):])
                if ipm:
                    peer['lan_ip'] = ipm.group(1)
        if peer:
            out.append(peer)
    return out

class Valves(BaseModel):
    default_user: str = os.environ.get('USER', 'youruser')
    ssh_key: str = SSH_KEY
    backup_key: str = BACKUP_KEY
    rustdesk_password: str = ''
    sudo_password: str = ''
    connect_timeout: int = 6
    exec_timeout: int = 25

class Tools:

    def __init__(self, valves: Valves=Valves()):
        self.valves = valves

    def fleet(self) -> str:
        """List all machines: name, SSH target (Tailscale IP), RustDesk ID, platform, online state."""
        table = _hosts()
        peers = {p.get('id'): p for p in _peers()}
        lines = []
        for name, (ip, rid) in table.items():
            peer = peers.get(rid, {})
            lines.append(f"{name:<20} {ip:<16} rd={rid:<11} {peer.get('platform', '?'):<8} lan={peer.get('lan_ip', '-'):<14} {('online' if peer.get('online') == 'true' else 'offline')}")
        if not lines:
            peers = _peers()
            if not peers:
                return 'no peers found: add entries to the host catalog or run RustDesk once on the LAN'
            return '\n'.join((f"{p.get('id', '?'):<12} {p.get('hostname', '?'):<20} {p.get('platform', '?'):<8} lan={p.get('lan_ip', '-'):<14} {('online' if p.get('online') == 'true' else 'offline')}" for p in peers))
        return '\n'.join(lines)

    def run(self, target: str, command: str) -> str:
        """Run a shell command on a remote machine over SSH (key auth).

        Args:
            target: machine name from the host catalog, Tailscale IP, or LAN IP
            command: shell command to execute on the remote machine
        """
        host, name = _resolve(target)
        cmd = _ssh_base(self.valves, host) + [command]
        code, out, err = _run(cmd, int(self.valves.exec_timeout))
        head = f'[{name} | {host}] exit={code}'
        return f"{head}\n{out or err or '(no output)'}"

    def run_sudo(self, target: str, command: str) -> str:
        """Run a command on a remote machine as root.

        Note on many distros: 'sudo -n' fails because the sudo policy still requires an
        authentication exchange, but PAM accepts any input on stdin (passwd -d), so piping
        the password from Valves to 'sudo -S' authenticates. Always pipe; never rely on sudo -n.
        Set sudo_password in Valves; do not hardcode it in the tool.

        Args:
            target: machine name or IP
            command: the command to run as root (without a leading 'sudo')
        """
        host, name = _resolve(target)
        pw = self.valves.sudo_password
        if not pw:
            return 'sudo_password is not set in Valves'
        remote = f"printf %s\\n {shlex.quote(pw)} | sudo -S -p '' {command}"
        cmd = _ssh_base(self.valves, host) + [remote]
        code, out, err = _run(cmd, int(self.valves.exec_timeout))
        err = re.sub('^\\[sudo.*?\\]\\s*\\n', '', err)
        return f"[{name} | {host}] sudo exit={code}\n{out or err or '(no output)'}"

    def push(self, target: str, local_path: str, remote_path: str) -> str:
        """Copy a file from this machine to a remote machine over SSH.

        Args:
            target: machine name or IP
            local_path: path on this machine
            remote_path: destination path on the target machine
        """
        host, name = _resolve(target)
        src = Path(local_path).expanduser()
        if not src.is_file():
            return f'local file not found: {src}'
        key = _key(self.valves)
        cmd = ['scp', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={int(self.valves.connect_timeout)}', '-o', 'StrictHostKeyChecking=accept-new']
        if Path(key).is_file():
            cmd += ['-i', key]
        cmd += [str(src), f'{self.valves.default_user}@{host}:{remote_path}']
        code, out, err = _run(cmd, int(self.valves.exec_timeout) + 10)
        if code == 0:
            return f'copied {src} -> {name}:{remote_path}'
        return f'scp failed exit={code}\n{out or err}'

    def pull(self, target: str, remote_path: str, local_path: str) -> str:
        """Copy a file from a remote machine to this machine.

        Args:
            target: machine name or IP
            remote_path: path on the target machine
            local_path: destination path on this machine
        """
        host, name = _resolve(target)
        key = _key(self.valves)
        cmd = ['scp', '-o', 'BatchMode=yes', '-o', f'ConnectTimeout={int(self.valves.connect_timeout)}', '-o', 'StrictHostKeyChecking=accept-new']
        if Path(key).is_file():
            cmd += ['-i', key]
        dest = Path(local_path).expanduser()
        dest.parent.mkdir(parents=True, exist_ok=True)
        cmd += [f'{self.valves.default_user}@{host}:{remote_path}', str(dest)]
        code, out, err = _run(cmd, int(self.valves.exec_timeout) + 10)
        if code == 0 and dest.is_file():
            return f'saved {name}:{remote_path} -> {dest} ({dest.stat().st_size} bytes)'
        return f'scp failed exit={code}\n{out or err}'

    def rustdesk(self, target: str, launch: bool=False) -> str:
        """RustDesk info for a machine, and optionally open a GUI session.

        The installed RustDesk is the Flutter UI binary, not rustdesk-cli, so it cannot be
        scripted for headless control. This reports the peer record and can only open the
        interactive window. If launch=True, a session window is opened on this machine's
        desktop using the password from Valves.

        Args:
            target: machine name or IP
            launch: open the RustDesk session window (GUI, requires a display)
        """
        host, name = _resolve(target)
        peers = {p.get('id'): p for p in _peers()}
        table = _hosts()
        rid = table.get(name, (None, None))[1]
        if not rid:
            return f'no RustDesk id for {name} (add it to the host catalog)'
        peer = peers.get(rid, {})
        info = f"rustdesk id={rid} name={name} platform={peer.get('platform', '?')} online={peer.get('online', '?')}"
        if not launch:
            return info + '\n(UI-only build: no scripted remote execution; use run() for commands)'
        if not os.environ.get('DISPLAY') and (not os.environ.get('WAYLAND_DISPLAY')):
            return info + '\ncannot launch: no display available in this process environment'
        password = self.valves.rustdesk_password
        code, out, err = _run(['xdg-open', f'rustdesk://{rid}?password={password}'], 10)
        if code != 0:
            code, out, err = _run(['rustdesk', rid, password], 10)
        return f"{info}\nlaunch exit={code}\n{out or err or 'session window opening'}"

    def check(self, target: str) -> str:
        """Test SSH connectivity to a machine: key auth, hostname, OS, sudo state.

        Args:
            target: machine name or IP
        """
        host, name = _resolve(target)
        t0 = time.time()
        cmd = _ssh_base(self.valves, host) + ["hostname; uname -r; head -1 /etc/os-release 2>/dev/null; sudo -k; printf '\\n' | sudo -S -p '' id >/dev/null 2>&1 && echo 'sudo: stdin auth accepted' || echo 'sudo: stdin auth rejected'"]
        code, out, err = _run(cmd, int(self.valves.connect_timeout) + 8)
        dt = round(time.time() - t0, 2)
        if code == 0:
            return f'[{name} | {host}] reachable in {dt}s\n{out}'
        return f'[{name} | {host}] unreachable exit={code} in {dt}s\n{err}'
_builtin_tools = Tools()

def fleet(self) -> str:
    """List all machines: name, SSH target (Tailscale IP), RustDesk ID, platform, online state."""
    return _builtin_tools.fleet()

def run(self, target: str, command: str) -> str:
    """Run a shell command on a remote machine over SSH (key auth).

        Args:
            target: machine name from the host catalog, Tailscale IP, or LAN IP
            command: shell command to execute on the remote machine
        """
    return _builtin_tools.run(target=target, command=command)

def run_sudo(self, target: str, command: str) -> str:
    """Run a command on a remote machine as root.

        Note on many distros: 'sudo -n' fails because the sudo policy still requires an
        authentication exchange, but PAM accepts any input on stdin (passwd -d), so piping
        the password from Valves to 'sudo -S' authenticates. Always pipe; never rely on sudo -n.
        Set sudo_password in Valves; do not hardcode it in the tool.

        Args:
            target: machine name or IP
            command: the command to run as root (without a leading 'sudo')
        """
    return _builtin_tools.run_sudo(target=target, command=command)

def push(self, target: str, local_path: str, remote_path: str) -> str:
    """Copy a file from this machine to a remote machine over SSH.

        Args:
            target: machine name or IP
            local_path: path on this machine
            remote_path: destination path on the target machine
        """
    return _builtin_tools.push(target=target, local_path=local_path, remote_path=remote_path)

def pull(self, target: str, remote_path: str, local_path: str) -> str:
    """Copy a file from a remote machine to this machine.

        Args:
            target: machine name or IP
            remote_path: path on the target machine
            local_path: destination path on this machine
        """
    return _builtin_tools.pull(target=target, remote_path=remote_path, local_path=local_path)

def rustdesk(self, target: str, launch: bool=False) -> str:
    """RustDesk info for a machine, and optionally open a GUI session.

        The installed RustDesk is the Flutter UI binary, not rustdesk-cli, so it cannot be
        scripted for headless control. This reports the peer record and can only open the
        interactive window. If launch=True, a session window is opened on this machine's
        desktop using the password from Valves.

        Args:
            target: machine name or IP
            launch: open the RustDesk session window (GUI, requires a display)
        """
    return _builtin_tools.rustdesk(target=target, launch=launch)

def check(self, target: str) -> str:
    """Test SSH connectivity to a machine: key auth, hostname, OS, sudo state.

        Args:
            target: machine name or IP
        """
    return _builtin_tools.check(target=target)
