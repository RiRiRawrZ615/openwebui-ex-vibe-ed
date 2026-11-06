"""Compute layer: other devices as workers (pure).

Direction as RiRi set it: the laptop is the master, the phone is a worker. The phone is
not an interface in this design, it is a memory and compute pool that the master draws on.

Facts this module is built on, read from upstream llama.cpp tools/rpc/README and from
RiRi's own cross-platform-llm-client (ClusterWorkerService.kt, ClusterService.dart):
  - a worker runs ggml-rpc-server and is reached over TCP. Default port 50052.
  - a worker exposes the accelerator devices on its host; with no accelerator it exposes
    one CPU device. So a phone contributes compute only if the worker build has an
    accelerator backend; otherwise it contributes CPU plus memory.
  - the master reads the model and slices layers across the devices it has. Upstream
    states the RPC backend is proof-of-concept, fragile, insecure, and says never to run
    the RPC server on an open network. LAN-only is a rule here, not a suggestion.
  - the master, not this module, decides the actual assignment. So planned and observed
    are separate fields. Nothing in this module claims what llama.cpp did.
  - the phone advertises ram, bw, model, id, and a protocol attribute (rpc7) over mDNS
    service type _privatelm._tcp. Those values are reported by the worker at start, so
    the numbers in a worker record are what the worker said, not what this module assumed.

What is interpretation, not fact, and is named as such: the capacity multipliers, the
need factor, and the cost of wireless per token. They are assumptions with a name, so a
later context can disagree with them without guessing.
"""

from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlparse

DEFAULT_PORT = 50052
SERVICE_TYPE = "_privatelm._tcp."
PROTOCOL_ATTR = "v"

# Interpretation, stated as interpretation. A worker's own process, the OS, and the
# buffers that do not live in weights all take some of what it reports as free, so the
# share a device can actually hold is below what it reports.
WORKER_USABLE_FRACTION = 0.75
MASTER_USABLE_FRACTION = 0.70
NEED_FACTOR = 1.10  # model file plus KV cache and working buffers

# Speed used only when a worker never reported one. This is a placeholder, so any plan
# that uses it is marked as estimated rather than measured.
UNKNOWN_SPEED_GBS = 4.0

SAFE_HOST = re.compile(r"^[A-Za-z0-9.\-]+$")
LINK_LOCAL = "169.254."
QR_SCHEME = "privatelm"

# What counts as local here is defined explicitly. Python's ipaddress.is_private counts
# the IANA documentation ranges (203.0.113.0/24, 198.51.100.0/24, 192.0.2.0/24) as
# private, so a worker at a documentation address would pass a check that is supposed to
# refuse anything off the local network. A documentation range is not a local network, so
# it is refused by this list.
LOCAL_NETWORKS = (
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
)


def parse_advert(name: str, host: str, port: int, attrs: dict | None) -> dict:
    """Normalize an mDNS advert into a worker record.

    Missing attributes stay None. A plan that needs a value it does not have says so
    instead of filling in a number, because a guessed capacity reads like a real one.
    """
    attrs = attrs or {}
    ram = attrs.get("ram")
    bw = attrs.get("bw")
    return {
        "id": attrs.get("id"),
        "name": name or host,
        "host": host,
        "port": int(port) if port else DEFAULT_PORT,
        "ram_mb": int(ram) if str(ram).isdigit() else None,
        "bandwidth_gbs": float(bw) if str(bw).replace(".", "", 1).isdigit() else None,
        "model": attrs.get("model"),
        "protocol": attrs.get(PROTOCOL_ATTR),
        "transport": transport_of(host),
        "reported": bool(ram or bw),
    }


def parse_qr(data: str) -> dict | None:
    """Read a worker pairing code: scheme://worker?host=...&port=...

    QR is the fallback when mDNS registration fails, which is what the worker itself
    says to do. A code with no host or no port is refused, not partially accepted.
    """
    try:
        uri = urlparse(data.strip())
    except ValueError:
        return None
    if uri.scheme != QR_SCHEME:
        return None
    query = dict(p.split("=", 1) for p in uri.query.split("&") if "=" in p) if uri.query else {}
    host = query.get("host", "")
    port = query.get("port", "")
    if not host or not str(port).isdigit():
        return None
    return parse_advert(query.get("name", host), host, int(port), query)


def transport_of(host: str) -> str:
    """Wired or wireless, as a best-effort read of the address.

    USB tethering on Android presents a link-local address, so 169.254.x is treated as
    the wired path. mDNS does not reliably work over that link, which is why manual
    pairing exists. This is a heuristic, so a plan reports it as inferred.
    """
    if str(host).startswith(LINK_LOCAL):
        return "wired"
    return "wireless"


def is_private(host: str) -> bool:
    """Upstream rule: never run the RPC server on an open network.

    Only LOCAL_NETWORKS is allowed. Everything else is refused before it can become a
    connection. Documentation ranges are not in the list even though Python calls them
    private. A hostname is not resolved here, because resolving one would be a network
    action in a module that promises none, so a hostname is refused and must be entered
    as an address.
    """
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in net for net in LOCAL_NETWORKS)


def usable_ram_mb(record: dict, role: str) -> float | None:
    """What this device can actually hold, or None when it never reported RAM."""
    if record.get("ram_mb") is None:
        return None
    fraction = WORKER_USABLE_FRACTION if role == "worker" else MASTER_USABLE_FRACTION
    return record["ram_mb"] * fraction


def plan(model_mb: float, master: dict, workers: list[dict]) -> dict:
    """Plan a load. Fastest first, each up to its usable capacity.

    Refuses when the model cannot fit, rather than promising a load that will fail at
    load time. Workers that never reported RAM are listed as unknown, not counted as
    capacity, so a refusal based on them is honest about why.
    """
    if model_mb <= 0:
        return {"ok": False, "reason": "model size unknown", "assignment": [], "unknown": []}

    need = model_mb * NEED_FACTOR
    master_role = {
        "name": master.get("name") or "master",
        "role": "master",
        "usable_mb": usable_ram_mb(master, "master"),
        "speed_gbs": master.get("bandwidth_gbs"),
        "estimated": master.get("bandwidth_gbs") is None,
    }
    devices = [master_role]
    unknown = []

    for w in workers:
        if not SAFE_HOST.match(str(w.get("host", ""))):
            unknown.append({"name": w.get("name"), "reason": "host is not a plain address"})
            continue
        if not is_private(str(w.get("host", ""))):
            unknown.append({"name": w.get("name"), "reason": "address is not on a private network"})
            continue
        cap = usable_ram_mb(w, "worker")
        if cap is None:
            unknown.append({"name": w.get("name"), "reason": "worker did not report RAM"})
            continue
        devices.append({
            "name": w.get("name") or w.get("host"),
            "role": "worker",
            "host": w.get("host"),
            "port": w.get("port", DEFAULT_PORT),
            "usable_mb": cap,
            "reported_mb": w.get("ram_mb"),
            "speed_gbs": w.get("bandwidth_gbs"),
            "estimated": w.get("bandwidth_gbs") is None,
            "transport": w.get("transport"),
            "reachable": bool(w.get("reachable", True)),
        })

    measured = [d for d in devices if not d["estimated"]]
    total = sum(d["usable_mb"] for d in devices)
    if total < need:
        return {
            "ok": False,
            "reason": (
                f"model needs about {round(need, 1)} MB including cache, usable total is "
                f"{round(total, 1)} MB; add a worker, free memory on them, or use a smaller model"
            ),
            "need_mb": round(need, 1),
            "usable_total_mb": round(total, 1),
            "assignment": [],
            "unknown": unknown,
        }

    # Fastest first, capped at each device's usable memory. The master is in this sort
    # too, because a slow master should not be given work a faster worker can hold.
    order = sorted(devices, key=lambda d: d["speed_gbs"] or UNKNOWN_SPEED_GBS, reverse=True)
    left = need
    for d in order:
        share = min(d["usable_mb"], left)
        d["assigned_mb"] = round(share, 1)
        left -= share

    used_workers = [d for d in order if d.get("role") == "worker" and d.get("assigned_mb", 0) > 0]
    return {
        "ok": True,
        "need_mb": round(need, 1),
        "usable_total_mb": round(total, 1),
        "assignment": devices,
        "workers_used": [d["name"] for d in used_workers],
        "wireless_used": [d["name"] for d in used_workers if d["transport"] == "wireless"],
        "wired_used": [d["name"] for d in used_workers if d["transport"] == "wired"],
        "unknown": unknown,
        "assumptions": {
            "worker_fraction": WORKER_USABLE_FRACTION,
            "master_fraction": MASTER_USABLE_FRACTION,
            "need_factor": NEED_FACTOR,
            "unknown_speed_gbs": UNKNOWN_SPEED_GBS,
        },
        # The master decides the real slice. This plan is a proposal until llama.cpp
        # reports what it loaded, so nothing here may be read as an observed result.
        "status": "planned",
        "observed": None,
        "measured_workers": len(measured),
    }


def rpc_args(plan_result: dict) -> list[str]:
    """The --rpc arguments for the workers this plan actually uses.

    Only reachable workers. A worker listed here but not reachable is not emitted,
    because a plan that hands the launcher an unreachable host produces a startup
    failure the user cannot read.
    """
    args = []
    for d in plan_result.get("assignment", []):
        if d.get("role") != "worker":
            continue
        if d.get("assigned_mb", 0) <= 0:
            continue
        if not d.get("reachable", True):
            continue
        # Re-checked here rather than trusted from the plan, so a hand-built assignment
        # cannot smuggle a non-local host into a launcher argument.
        if not SAFE_HOST.match(str(d.get("host", ""))) or not is_private(str(d.get("host", ""))):
            continue
        args.append(f"{d['host']}:{d['port']}")
    return args


def status(plan_result: dict) -> dict:
    """Compact status for the tab. Planned and observed are never merged."""
    return {
        "ok": plan_result.get("ok", False),
        "status": plan_result.get("status", "planned"),
        "observed": plan_result.get("observed"),
        "workers": [
            {
                "name": d.get("name"),
                "transport": d.get("transport"),
                "reported_mb": d.get("reported_mb"),
                "usable_mb": d.get("usable_mb"),
                "assigned_mb": d.get("assigned_mb"),
                "speed_gbs": d.get("speed_gbs"),
                "estimated": d.get("estimated"),
            }
            for d in plan_result.get("assignment", [])
            if d.get("role") == "worker"
        ],
        "unresolved": plan_result.get("unknown", []),
        "reason": plan_result.get("reason"),
    }


__all__ = [
    "DEFAULT_PORT",
    "SERVICE_TYPE",
    "WORKER_USABLE_FRACTION",
    "MASTER_USABLE_FRACTION",
    "NEED_FACTOR",
    "UNKNOWN_SPEED_GBS",
    "parse_advert",
    "parse_qr",
    "transport_of",
    "is_private",
    "usable_ram_mb",
    "plan",
    "rpc_args",
    "status",
]
