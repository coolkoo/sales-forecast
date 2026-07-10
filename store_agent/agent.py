#!/usr/bin/env python3
"""KFC Vietnam store-node health agent (reference implementation).

Runs on a store's back-office node. Every cycle it:
  1. PROBES local services (disk/CPU, network, systemd units, failed-login/intrusion),
  2. REPORTS the status batch to the platform listener  (POST /api/monitor/report),
  3. POLLS for remediation commands                     (GET  /api/monitor/commands),
  4. EXECUTES only whitelisted commands and CONFIRMS     (POST /api/monitor/command_result).

Design goals: **stdlib only** (no pip installs on store nodes), **safe by default**
(dry-run until you set SF_AGENT_EXECUTE=1), and **whitelist-only** remediation — the
agent will never run anything outside the ACTION_COMMANDS map below.

Configure via environment (see config.example.env) and run under systemd
(sales-forecast-agent.service). Manual run:  SF_STORE=KFC-HCM01 python3 agent.py
"""
from __future__ import annotations

import json
import os
import platform
import re
import shutil
import socket
import subprocess
import time
import urllib.error
import urllib.request

VERSION = "1.0.0"


def _split(s: str):
    return s.split() if s else []


# ---- configuration (environment-driven) ----
API = os.environ.get("SF_API", "http://192.168.50.85:8900").rstrip("/")
STORE = os.environ.get("SF_STORE", "")
TOKEN = os.environ.get("SF_NODE_TOKEN", "")
INTERVAL = int(os.environ.get("SF_AGENT_INTERVAL", "30"))
EXECUTE = os.environ.get("SF_AGENT_EXECUTE", "0") == "1"     # 0 = dry-run (safe default)
GATEWAY = os.environ.get("SF_AGENT_GATEWAY", "8.8.8.8")
FAILED_LOGIN_CRIT = int(os.environ.get("SF_AGENT_FAILED_LOGIN_CRIT", "8"))

# systemd unit names for the services this node runs (blank = "not monitored" → reported ok).
UNITS = {
    "pos": os.environ.get("SF_UNIT_POS", ""),
    "kds": os.environ.get("SF_UNIT_KDS", ""),
    "printer": os.environ.get("SF_UNIT_PRINTER", ""),
    "payment": os.environ.get("SF_UNIT_PAYMENT", ""),
    "backup": os.environ.get("SF_UNIT_BACKUP", ""),
}

# Whitelisted remediation commands. Each maps an action id → argv (a list, never a shell
# string). {ip} is substituted from the last detected intrusion source. Actions with an
# empty command are reported as "not supported on this node" rather than executed.
ACTION_COMMANDS = {
    "restart_pos": ["systemctl", "restart", UNITS["pos"] or "pos.service"],
    "reboot_terminal": _split(os.environ.get("SF_CMD_REBOOT_TERMINAL", "")),
    "restart_agent": ["systemctl", "restart", "sales-forecast-agent.service"],
    "clear_temp": _split(os.environ.get("SF_CMD_CLEAR_TEMP", "")),
    "failover_lte": _split(os.environ.get("SF_CMD_FAILOVER_LTE", "")),
    "restart_router": _split(os.environ.get("SF_CMD_RESTART_ROUTER", "")),
    "reconnect_gateway": ["systemctl", "restart", UNITS["payment"] or "payment-gw.service"],
    "restart_kds": ["systemctl", "restart", UNITS["kds"] or "kds.service"],
    "restart_spooler": ["systemctl", "restart", UNITS["printer"] or "cups.service"],
    "run_sync": _split(os.environ.get("SF_CMD_SYNC", "")),
    "block_ip": ["iptables", "-A", "INPUT", "-s", "{ip}", "-j", "DROP"],
    "force_logout": _split(os.environ.get("SF_CMD_FORCE_LOGOUT", "")),
    "rotate_creds": _split(os.environ.get("SF_CMD_ROTATE_CREDS", "")),
}

_STATE = {"last_intrusion_ip": ""}


def _http(method: str, path: str, body: dict | None = None) -> dict:
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("X-Node-Token", TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return {"error": f"http {e.code}", "detail": e.read().decode()[:200]}
    except Exception as e:
        return {"error": str(e)}


# ---------------------------------------------------------------- probes
def probe_host() -> dict:
    du = shutil.disk_usage("/")
    disk = round(du.used / du.total * 100, 1)
    try:
        load = os.getloadavg()[0]
    except (OSError, AttributeError):
        load = 0.0
    if disk > 93:
        return {"service": "host", "status": "critical", "metric": disk, "detail": f"Disk {disk}% — nearly full"}
    if disk > 86:
        return {"service": "host", "status": "warn", "metric": disk, "detail": f"Disk {disk}% — running high"}
    return {"service": "host", "status": "ok", "metric": disk, "detail": f"Disk {disk}% · load {load:.2f}"}


def probe_network() -> dict:
    try:
        out = subprocess.run(["ping", "-c", "3", "-W", "2", GATEWAY],
                             capture_output=True, text=True, timeout=12).stdout
        loss = re.search(r"(\d+)% packet loss", out)
        rtt = re.search(r"=\s*[\d.]+/([\d.]+)/", out)
        lossv = int(loss.group(1)) if loss else 100
        lat = float(rtt.group(1)) if rtt else None
        if lossv >= 50:
            return {"service": "network", "status": "critical", "metric": lossv, "detail": f"Uplink {lossv}% packet loss"}
        if lossv > 5 or (lat and lat > 100):
            return {"service": "network", "status": "warn", "metric": lossv,
                    "detail": f"Uplink {lossv}% loss · {lat}ms" if lat else f"Uplink {lossv}% loss"}
        return {"service": "network", "status": "ok", "metric": lat, "detail": f"Uplink up · {lat}ms latency"}
    except Exception as e:
        return {"service": "network", "status": "warn", "metric": None, "detail": f"ping unavailable: {e}"}


def probe_unit(service: str, unit: str) -> dict:
    if not unit:
        return {"service": service, "status": "ok", "metric": None, "detail": "not monitored on this node"}
    try:
        rc = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True, timeout=8)
        state = rc.stdout.strip()
        if state == "active":
            return {"service": service, "status": "ok", "metric": None, "detail": f"{unit} active"}
        return {"service": service, "status": "down", "metric": None, "detail": f"{unit} is {state}"}
    except Exception as e:
        return {"service": service, "status": "warn", "metric": None, "detail": f"probe error: {e}"}


def probe_security() -> dict:
    """Count recent failed logins and surface the top source IP (password-intrusion signal)."""
    lines = ""
    for cmd in (["journalctl", "-u", "ssh", "--since", "-10min", "--no-pager"],
                ["journalctl", "_COMM=sshd", "--since", "-10min", "--no-pager"]):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=8)
            if r.returncode == 0 and r.stdout:
                lines = r.stdout
                break
        except Exception:
            continue
    if not lines:
        try:
            with open("/var/log/auth.log", errors="ignore") as fh:
                lines = "".join(fh.readlines()[-2000:])
        except Exception:
            return {"service": "security", "status": "ok", "metric": None, "detail": "No access anomalies"}
    ips = re.findall(r"Failed password for .* from ([\d.]+)", lines)
    if not ips:
        return {"service": "security", "status": "ok", "metric": 0, "detail": "No access anomalies"}
    top = max(set(ips), key=ips.count)
    n = ips.count(top)
    _STATE["last_intrusion_ip"] = top
    if n >= FAILED_LOGIN_CRIT:
        return {"service": "security", "status": "critical", "metric": n,
                "detail": f"{n} failed admin logins from {top} — possible password intrusion"}
    return {"service": "security", "status": "warn", "metric": n,
            "detail": f"{n} failed logins from {top}"}


def probe_all() -> list[dict]:
    return [
        probe_unit("pos", UNITS["pos"]), probe_host(), probe_network(),
        probe_unit("payment", UNITS["payment"]), probe_unit("kds", UNITS["kds"]),
        probe_unit("printer", UNITS["printer"]), probe_unit("backup", UNITS["backup"]),
        probe_security(),
    ]


# ---------------------------------------------------------------- remediation
def run_command(action: str) -> tuple[bool, str]:
    argv = ACTION_COMMANDS.get(action)
    if not argv:
        return False, f"no command configured for '{action}' on this node"
    argv = [a.replace("{ip}", _STATE.get("last_intrusion_ip", "")) for a in argv]
    if "{ip}" in "".join(ACTION_COMMANDS.get(action, [])) and not _STATE.get("last_intrusion_ip"):
        return False, "no intrusion source IP known to block"
    if not EXECUTE:
        return True, f"[dry-run] would execute: {' '.join(argv)}"
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60)
        ok = r.returncode == 0
        return ok, (f"{' '.join(argv)} → rc={r.returncode} " + (r.stdout or r.stderr).strip())[:300]
    except Exception as e:
        return False, f"exec error: {e}"


def handle_commands():
    res = _http("GET", f"/api/monitor/commands?store={STORE}")
    for c in res.get("commands", []) or []:
        ok, note = run_command(c["action"])
        print(f"[agent] command {c['id']} {c['action']}: {'done' if ok else 'FAILED'} — {note}", flush=True)
        _http("POST", "/api/monitor/command_result",
              {"command_id": c["id"], "status": "done" if ok else "failed", "result": note})


# ---------------------------------------------------------------- main loop
def cycle():
    services = probe_all()
    rep = _http("POST", "/api/monitor/report",
                {"store": STORE, "hostname": socket.gethostname(), "version": VERSION, "services": services})
    worst = max((s["status"] for s in services), key=lambda s: ["ok", "warn", "critical", "down"].index(s)
                if s in ["ok", "warn", "critical", "down"] else 0)
    print(f"[agent] reported {len(services)} services (worst={worst}) → {rep.get('ok', rep)}", flush=True)
    handle_commands()


def main():
    if not STORE:
        raise SystemExit("SF_STORE is required (e.g. SF_STORE=KFC-HCM01)")
    print(f"[agent] KFC store-node health agent v{VERSION} · store={STORE} · api={API} · "
          f"execute={'ON' if EXECUTE else 'DRY-RUN'} · interval={INTERVAL}s · host={platform.node()}", flush=True)
    while True:
        try:
            cycle()
        except Exception as e:
            print(f"[agent] cycle error: {e}", flush=True)
        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
