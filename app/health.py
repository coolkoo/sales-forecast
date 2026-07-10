"""Store-node health monitoring + remediation.

A lightweight health layer for the per-store nodes. Real store agents POST their
service status to the listener (`report`); where an agent hasn't reported, health
is derived deterministically from current signals (`refresh`) — notably correlated
with detected anomalies (a POS outage → POS service down, a channel outage →
network/payment degraded, comp/void fraud → a security alert) plus infrastructure
conditions (disk, printer, backup age) and a password-intrusion signal.

Operators can remediate the things that are remediable (restart a service, fail
over the network, block an intruding IP, rotate credentials…) — each action is
guarded, logged, and resolves the service.

Endpoints live under /api/monitor/* (see app/api/server.py).
"""
from __future__ import annotations

import datetime
import hashlib
import math

from sqlalchemy import (Column, DateTime, Float, Integer, MetaData, String, Table, text)

from app import db
from app.config import CFG


def _clean(rows: list[dict]) -> list[dict]:
    """NaN/NaT -> None so the JSON response is valid (read_sql yields NaN for NULLs)."""
    for r in rows:
        for k, v in list(r.items()):
            if isinstance(v, float) and math.isnan(v):
                r[k] = None
    return rows

_MD = MetaData()
_health = Table("store_health", _MD,
                Column("store", String), Column("service", String),
                Column("status", String), Column("metric", Float), Column("detail", String),
                Column("since", DateTime), Column("last_report", DateTime),
                Column("source", String), Column("remediated_at", DateTime))
_event = Table("health_event", _MD,
               Column("ts", DateTime), Column("store", String), Column("service", String),
               Column("kind", String), Column("status", String), Column("action", String),
               Column("actor", String), Column("note", String))
# Registry of store-node agents (presence) + the remediation command queue.
_agent = Table("node_agent", _MD, Column("store", String, primary_key=True),
               Column("hostname", String), Column("version", String), Column("ip", String),
               Column("last_seen", DateTime))
_cmd = Table("node_command", _MD, Column("id", Integer, primary_key=True, autoincrement=True),
             Column("store", String), Column("service", String), Column("action", String),
             Column("status", String), Column("requested_by", String), Column("requested_at", DateTime),
             Column("result", String), Column("completed_at", DateTime))

AGENT_ACTIVE_SEC = 180   # an agent seen within this window is 'live' → dispatch, else simulate

# Service catalog: what each store node monitors + which actions can remediate it.
SERVICES = [
    {"key": "pos", "name": "POS terminals", "icon": "🧾",
     "actions": [("restart_pos", "Restart POS service"), ("reboot_terminal", "Reboot terminal")]},
    {"key": "host", "name": "Back-office server", "icon": "🖥️",
     "actions": [("restart_agent", "Restart node agent"), ("clear_temp", "Clear temp / cache")]},
    {"key": "network", "name": "Network / uplink", "icon": "🌐",
     "actions": [("failover_lte", "Fail over to LTE"), ("restart_router", "Restart router")]},
    {"key": "payment", "name": "Payment gateway", "icon": "💳",
     "actions": [("reconnect_gateway", "Reconnect gateway")]},
    {"key": "kds", "name": "Kitchen display (KDS)", "icon": "👨‍🍳",
     "actions": [("restart_kds", "Restart KDS service")]},
    {"key": "printer", "name": "Receipt printer", "icon": "🖨️",
     "actions": [("restart_spooler", "Restart print spooler")]},
    {"key": "backup", "name": "DB sync / backup", "icon": "💾",
     "actions": [("run_sync", "Run sync now")]},
    {"key": "security", "name": "Security / access", "icon": "🛡️",
     "actions": [("block_ip", "Block source IP"), ("force_logout", "Force-logout sessions"),
                 ("rotate_creds", "Rotate credentials")]},
]
_BYKEY = {s["key"]: s for s in SERVICES}
_ACTIONS = {a[0]: (s["key"], a[1]) for s in SERVICES for a in s["actions"]}
SEV = {"ok": 0, "remediating": 1, "warn": 1, "critical": 2, "down": 3, "offline": 3}
REMEDIATION_HOLD_H = 24   # a remediated service stays resolved this long before re-evaluation


def _now():
    return datetime.datetime.utcnow()


def ensure():
    _MD.create_all(db.engine(), tables=[_health, _event, _agent, _cmd], checkfirst=True)


def _h01(*parts) -> float:
    """Deterministic pseudo-random in [0,1) from the inputs (stable across refreshes)."""
    x = int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:8], 16)
    return (x % 100000) / 100000.0


def _stores() -> list[str]:
    if not db.table_exists("store"):
        return []
    return db.read_sql("SELECT store FROM store ORDER BY store")["store"].tolist()


def _recent_anomaly_types(days: int = 7) -> dict[str, set]:
    """{store: {anomaly types active in the last `days`}} — health correlates only with
    *current* issues, so a store whose last outage was months ago reads healthy today."""
    if not db.table_exists("anomaly"):
        return {}
    rows = db.read_sql("SELECT store, type, end_date FROM anomaly").to_dict("records")
    dated = [(r["store"], r["type"], _parse_ts(r["end_date"])) for r in rows]
    ends = [d for _, _, d in dated if d]
    cutoff = (max(ends) - datetime.timedelta(days=days)) if ends else None
    out: dict[str, set] = {}
    for store, typ, end in dated:
        if cutoff is None or (end and end >= cutoff):
            out.setdefault(store, set()).add(typ)
    return out


def _target(store: str, anoms: set, allow_infra: bool = False, allow_intrusion: bool = False) -> dict[str, dict]:
    """Deterministic 'what the agent would report' for one store. Anomaly-correlated issues
    (POS/network/payment) always apply; infrastructure warnings and the password-intrusion
    signal only apply to designated 'problem' stores, so most of the fleet reads healthy."""
    t: dict[str, dict] = {}

    # POS — a detected outage means terminals are down
    if "POS_OUTAGE" in anoms:
        t["pos"] = {"status": "down", "metric": 0, "detail": "3 of 6 terminals offline — POS service not responding"}
    elif allow_infra and _h01(store, "pos") > 0.5:
        t["pos"] = {"status": "warn", "metric": 5, "detail": "Terminal 3 intermittent — 5 of 6 online"}
    else:
        t["pos"] = {"status": "ok", "metric": 6, "detail": "6 of 6 terminals online"}

    # Back-office host — disk/CPU
    disk = round(52 + 40 * _h01(store, "host"), 1)
    if allow_infra and disk > 86:
        t["host"] = ({"status": "critical", "metric": disk, "detail": f"Disk {disk}% — nearly full"} if disk > 93
                     else {"status": "warn", "metric": disk, "detail": f"Disk {disk}% — running high"})
    else:
        t["host"] = {"status": "ok", "metric": min(disk, 84.0), "detail": f"Disk {min(disk,84.0)}% · CPU nominal"}

    # Network — channel outages point at connectivity
    if "CHANNEL_OUTAGE" in anoms:
        t["network"] = {"status": "warn", "metric": 7.5, "detail": "Uplink packet loss 7.5% — digital orders affected"}
    elif allow_infra and _h01(store, "net") > 0.6:
        t["network"] = {"status": "warn", "metric": 120, "detail": "Latency 120ms — degraded"}
    else:
        t["network"] = {"status": "ok", "metric": 22, "detail": "Uplink up · 22ms latency"}

    # Payment gateway
    if "CHANNEL_OUTAGE" in anoms and _h01(store, "pay") > 0.55:
        t["payment"] = {"status": "warn", "metric": None, "detail": "Gateway reconnecting — declined-rate elevated"}
    else:
        t["payment"] = {"status": "ok", "metric": None, "detail": "Gateway connected"}

    # KDS
    t["kds"] = ({"status": "warn", "metric": None, "detail": "KDS lagging — orders slow to appear"}
                if allow_infra and _h01(store, "kds") > 0.7 else {"status": "ok", "metric": None, "detail": "KDS responsive"})

    # Receipt printer
    t["printer"] = ({"status": "warn", "metric": None, "detail": "Printer offline — spooler stalled"}
                    if allow_infra and _h01(store, "prn") > 0.55 else {"status": "ok", "metric": None, "detail": "Printer ready"})

    # Backup / DB sync freshness (kept comfortably fresh — rarely an issue)
    age = round(2 + 18 * _h01(store, "bak"), 1)
    t["backup"] = ({"status": "warn", "metric": age, "detail": f"Last sync {age}h ago — stale"} if allow_infra and age > 22
                   else {"status": "ok", "metric": min(age, 20.0), "detail": f"Last sync {min(age,20.0)}h ago"})

    # Security / access — password intrusion (only the designated store) + fraud correlation
    if allow_intrusion:
        fails = int(8 + 30 * _h01(store, "sec2"))
        t["security"] = {"status": "critical", "metric": fails,
                         "detail": f"{fails} failed admin logins from a single IP — possible password intrusion"}
    elif "VOID_COMP_FRAUD" in anoms:
        t["security"] = {"status": "warn", "metric": None, "detail": "Unusual void/comp pattern — review access"}
    else:
        t["security"] = {"status": "ok", "metric": None, "detail": "No access anomalies"}
    return t


def refresh() -> dict:
    """Recompute health for all stores (agent-reported services are preserved), honoring
    a hold on recently-remediated services so operator actions 'stick'."""
    ensure()
    stores = _stores()
    if not stores:
        return {"stores": 0}
    anom = _recent_anomaly_types()
    now = _now()
    # Concentrate issues on a few "problem" stores (prefer those with current anomalies)
    # so the majority of the fleet reads healthy; one problem store carries the intrusion.
    problem = sorted(stores, key=lambda s: (len(anom.get(s, set())), _h01(s, "pick")), reverse=True)[:3]
    intrusion_store = next((s for s in problem if not anom.get(s)), problem[0] if problem else None)
    existing = {(r["store"], r["service"]): r for r in db.read_sql(
        "SELECT store, service, status, detail, since, source, remediated_at FROM store_health").to_dict("records")}
    rows = []
    for st in stores:
        is_problem = st in problem
        tgt = _target(st, anom.get(st, set()) if is_problem else set(),
                      allow_infra=is_problem, allow_intrusion=(st == intrusion_store))
        for svc, info in tgt.items():
            prev = existing.get((st, svc))
            src = (prev or {}).get("source")
            rem = (prev or {}).get("remediated_at")
            # keep agent-reported services as-is; keep a dispatched-but-unconfirmed remediation
            if src == "agent":
                continue
            if (prev or {}).get("status") == "remediating":
                rows.append({"store": st, "service": svc, "status": "remediating", "metric": None,
                             "detail": (prev or {}).get("detail") or "Remediation in progress",
                             "since": _parse_ts((prev or {}).get("since")) or now, "last_report": now,
                             "source": "derived", "remediated_at": _parse_ts(rem)})
                continue
            status, metric, detail = info["status"], info["metric"], info["detail"]
            remediated_at = _parse_ts(rem)
            if remediated_at and (now - remediated_at) < datetime.timedelta(hours=REMEDIATION_HOLD_H):
                status, detail = "ok", "Resolved by operator — monitoring"
            since = _parse_ts((prev or {}).get("since")) if prev and prev.get("status") == status else now
            rows.append({"store": st, "service": svc, "status": status, "metric": metric,
                         "detail": detail, "since": since or now, "last_report": now,
                         "source": "derived", "remediated_at": remediated_at})
    with db.engine().begin() as cx:
        cx.execute(text("DELETE FROM store_health WHERE source <> 'agent'"))
        if rows:
            cx.execute(_health.insert(), rows)
    return {"stores": len(stores), "services": len(rows)}


def _parse_ts(v):
    """Parse a stored timestamp to a *native* datetime, or None. Rejects NaT and
    out-of-range values (a bad write must not propagate through refresh)."""
    if v is None or v == "" or str(v) in ("None", "NaT", "nan"):
        return None
    dt = None
    if isinstance(v, datetime.datetime):
        dt = v
    else:
        try:
            dt = datetime.datetime.fromisoformat(str(v).replace("Z", "").split(".")[0])
        except Exception:
            return None
    # normalize pandas Timestamp → native, drop tzinfo, and reject absurd years
    try:
        dt = datetime.datetime(dt.year, dt.month, dt.day, dt.hour, dt.minute, dt.second)
    except Exception:
        return None
    return dt if 1970 <= dt.year <= 9999 else None


def _agent_active(store: str) -> bool:
    if not db.table_exists("node_agent"):
        return False
    r = db.read_sql("SELECT last_seen FROM node_agent WHERE store=:s", {"s": store})
    if r.empty:
        return False
    ls = _parse_ts(r["last_seen"].iloc[0])
    return bool(ls and (_now() - ls).total_seconds() <= AGENT_ACTIVE_SEC)


def report(payload: dict, ip: str = "") -> dict:
    """Listener: a store node agent posts its service statuses here (+ registers presence)."""
    ensure()
    store = str(payload.get("store", "")).strip()
    services = payload.get("services") or []
    if not store or not services:
        return {"error": "store and services[] required"}
    now = _now()
    with db.engine().begin() as cx:
        cx.execute(text("DELETE FROM node_agent WHERE store=:s"), {"s": store})
        cx.execute(_agent.insert(), {"store": store, "hostname": str(payload.get("hostname", "")),
                   "version": str(payload.get("version", "")), "ip": ip, "last_seen": now})
        for s in services:
            svc = str(s.get("service", ""))
            if svc not in _BYKEY:
                continue
            status = str(s.get("status", "ok"))
            cx.execute(text("DELETE FROM store_health WHERE store=:st AND service=:sv"),
                       {"st": store, "sv": svc})
            cx.execute(_health.insert(), {"store": store, "service": svc, "status": status,
                       "metric": s.get("metric"), "detail": str(s.get("detail", "")),
                       "since": now, "last_report": now, "source": "agent", "remediated_at": None})
            cx.execute(_event.insert(), {"ts": now, "store": store, "service": svc, "kind": "report",
                       "status": status, "action": None, "actor": "agent", "note": str(s.get("detail", ""))})
    return {"ok": True, "store": store, "services": len(services)}


def _overall(statuses) -> str:
    return max(statuses, key=lambda s: SEV.get(s, 0)) if statuses else "ok"


def fleet() -> dict:
    ensure()
    if db.read_sql("SELECT COUNT(*) c FROM store_health")["c"].iloc[0] == 0:
        refresh()
    df = db.read_sql("SELECT store, service, status, detail, last_report FROM store_health")
    stores = []
    counts = {"ok": 0, "remediating": 0, "warn": 0, "critical": 0, "down": 0}
    services_down = 0
    security_alerts = 0
    for st, g in df.groupby("store"):
        recs = g.to_dict("records")
        statuses = [r["status"] for r in recs]
        ov = _overall(statuses)
        counts[ov] = counts.get(ov, 0) + 1
        services_down += sum(1 for s in statuses if SEV.get(s, 0) >= 2)
        sec = [r for r in recs if r["service"] == "security" and SEV.get(r["status"], 0) >= 2]
        security_alerts += len(sec)
        bad = [r for r in recs if SEV.get(r["status"], 0) >= 1]
        secr = next((r for r in recs if r["service"] == "security"), {})
        stores.append({"store": st, "overall": ov,
                       "services": [{"service": r["service"], "status": r["status"]} for r in recs],
                       "issues": len(bad),
                       "security": {"status": secr.get("status", "ok"), "detail": secr.get("detail", "")},
                       "top_issue": (sorted(bad, key=lambda r: SEV.get(r["status"], 0), reverse=True)[0]["detail"]
                                     if bad else "All services healthy")})
    stores.sort(key=lambda s: (-SEV.get(s["overall"], 0), s["store"]))
    return {"summary": {"stores": len(stores), "healthy": counts.get("ok", 0),
                        "degraded": counts.get("warn", 0) + counts.get("remediating", 0),
                        "critical": counts.get("critical", 0) + counts.get("down", 0),
                        "services_down": services_down, "security_alerts": security_alerts},
            "stores": stores, "catalog": SERVICES}


def store_detail(store: str) -> dict:
    ensure()
    df = db.read_sql("SELECT * FROM store_health WHERE store=:s", {"s": store})
    by = {r["service"]: r for r in df.to_dict("records")}
    services = []
    for s in SERVICES:
        r = by.get(s["key"], {})
        st = r.get("status", "ok")
        remediable = SEV.get(st, 0) >= 1 and st != "remediating" and bool(s["actions"])
        services.append({"key": s["key"], "name": s["name"], "icon": s["icon"],
                         "status": st, "metric": r.get("metric"), "detail": r.get("detail", "—"),
                         "since": str(r.get("since", "")), "last_report": str(r.get("last_report", "")),
                         "source": r.get("source", "derived"),
                         "actions": [{"id": a[0], "label": a[1]} for a in s["actions"]] if remediable else []})
    ev = db.read_sql("SELECT ts, service, kind, status, action, actor, note FROM health_event "
                     "WHERE store=:s ORDER BY ts DESC", {"s": store}).head(15).to_dict("records")
    for e in ev:
        e["ts"] = str(e["ts"])
    return {"store": store, "overall": _overall([s["status"] for s in services]),
            "services": _clean(services), "events": _clean(ev)}


def events(limit: int = 40) -> list[dict]:
    ensure()
    df = db.read_sql("SELECT ts, store, service, kind, status, action, actor, note FROM health_event "
                     "ORDER BY ts DESC")
    df = df.head(limit)
    rows = df.to_dict("records")
    for r in rows:
        r["ts"] = str(r["ts"])
    return _clean(rows)


def remediate(store: str, service: str, action: str, actor: str = "operator") -> dict:
    """Remediate a store service. If a live node agent is present, DISPATCH the action
    to it (status → 'remediating', agent executes and confirms). Otherwise SIMULATE
    (resolve + 24h hold) so the platform is useful before real agents are deployed."""
    ensure()
    if action not in _ACTIONS:
        return {"error": f"unknown action '{action}'"}
    svc_key, label = _ACTIONS[action]
    if service and service != svc_key:
        return {"error": f"action '{action}' does not apply to service '{service}'"}
    if db.read_sql("SELECT status FROM store_health WHERE store=:s AND service=:v",
                   {"s": store, "v": svc_key}).empty:
        return {"error": "no such store/service"}
    now = _now()
    if _agent_active(store):
        with db.engine().begin() as cx:
            cx.execute(_cmd.insert(), {"store": store, "service": svc_key, "action": action,
                       "status": "pending", "requested_by": actor, "requested_at": now,
                       "result": None, "completed_at": None})
            cx.execute(text("UPDATE store_health SET status='remediating', detail=:d, since=:t "
                            "WHERE store=:s AND service=:v"),
                       {"d": f"Dispatched '{label}' to node — awaiting confirmation", "t": now,
                        "s": store, "v": svc_key})
            cx.execute(_event.insert(), {"ts": now, "store": store, "service": svc_key,
                       "kind": "remediation_requested", "status": "remediating", "action": action,
                       "actor": actor, "note": label})
        return {"ok": True, "dispatched": True, "store": store, "service": svc_key, "action": action,
                "label": label, "result": f"{label} dispatched to {store} node — awaiting confirmation"}
    with db.engine().begin() as cx:
        cx.execute(text("UPDATE store_health SET status='ok', detail=:d, since=:t, "
                        "remediated_at=:t, source='derived' WHERE store=:s AND service=:v"),
                   {"d": f"Resolved via '{label}' — monitoring", "t": now, "s": store, "v": svc_key})
        cx.execute(_event.insert(), {"ts": now, "store": store, "service": svc_key, "kind": "remediation",
                   "status": "ok", "action": action, "actor": actor, "note": label})
    return {"ok": True, "simulated": True, "store": store, "service": svc_key, "action": action,
            "label": label, "result": f"{label} — {store} {svc_key} resolved (simulated; no live agent)"}


def pending_commands(store: str) -> dict:
    """Agent poll: return + claim pending remediation commands for this store."""
    ensure()
    df = db.read_sql("SELECT id, service, action FROM node_command "
                     "WHERE store=:s AND status='pending' ORDER BY id", {"s": store})
    rows = df.to_dict("records")
    if rows:
        with db.engine().begin() as cx:
            for r in rows:
                cx.execute(text("UPDATE node_command SET status='dispatched' WHERE id=:i"),
                           {"i": int(r["id"])})
    return {"store": store, "commands": [{"id": int(r["id"]), "service": r["service"],
                                          "action": r["action"]} for r in rows]}


def command_result(command_id: int, status: str, result: str = "", actor: str = "agent") -> dict:
    """Agent ack: the node reports the outcome of a dispatched remediation command."""
    ensure()
    r = db.read_sql("SELECT store, service, action FROM node_command WHERE id=:i", {"i": command_id})
    if r.empty:
        return {"error": "unknown command id"}
    row = r.iloc[0].to_dict()
    ok = (status == "done")
    now = _now()
    with db.engine().begin() as cx:
        cx.execute(text("UPDATE node_command SET status=:st, result=:r, completed_at=:t WHERE id=:i"),
                   {"st": "done" if ok else "failed", "r": str(result)[:500], "t": now, "i": command_id})
        if ok:
            cx.execute(text("UPDATE store_health SET status='ok', detail=:d, since=:t, remediated_at=:t "
                            "WHERE store=:s AND service=:v"),
                       {"d": f"Resolved by node: {row['action']}", "t": now,
                        "s": row["store"], "v": row["service"]})
        else:
            cx.execute(text("UPDATE store_health SET detail=:d WHERE store=:s AND service=:v"),
                       {"d": f"Remediation failed: {str(result)[:120]}", "s": row["store"], "v": row["service"]})
        cx.execute(_event.insert(), {"ts": now, "store": row["store"], "service": row["service"],
                   "kind": "remediation_result", "status": "ok" if ok else "failed",
                   "action": row["action"], "actor": actor, "note": str(result)[:200]})
    return {"ok": True, "command_id": command_id, "status": "done" if ok else "failed"}


# --- Demo virtual agent: makes remediation dispatch through the real command channel
# (Remediating→Resolved) and auto-confirm, without a physical store node. Disabled by
# SF_DEMO_AGENT=0 once real agents are deployed (a real agent's report always wins). ---
_MOCK_RESULT = {
    "restart_pos": "systemctl restart pos.service → active (6/6 terminals online)",
    "reboot_terminal": "terminal reboot signaled → back online",
    "restart_agent": "node agent restarted → reporting",
    "clear_temp": "cleared 1.2 GB temp/cache → disk recovered",
    "failover_lte": "uplink failed over to LTE → link up",
    "restart_router": "router restarted → uplink restored",
    "reconnect_gateway": "payment gateway reconnected → healthy",
    "restart_kds": "KDS service restarted → responsive",
    "restart_spooler": "print spooler restarted → printer ready",
    "run_sync": "DB sync completed → up to date",
    "block_ip": "iptables DROP applied to source IP → blocked",
    "force_logout": "all active sessions force-logged-out",
    "rotate_creds": "credentials rotated → new keys issued",
}
_VIRTUAL_HOST = "virtual-agent"


def register_virtual_agents():
    """Present a virtual agent for every store lacking a live *real* agent."""
    if not CFG.DEMO_AGENT:
        return
    ensure()
    now = _now()
    real = set()
    for r in db.read_sql("SELECT store, hostname, last_seen FROM node_agent").to_dict("records"):
        ls = _parse_ts(r.get("last_seen"))
        if (str(r.get("hostname")) not in ("", _VIRTUAL_HOST, "None")
                and ls and (now - ls).total_seconds() <= AGENT_ACTIVE_SEC):
            real.add(r["store"])
    with db.engine().begin() as cx:
        for st in _stores():
            if st in real:
                continue
            # replace any stale/virtual registration for this store (upsert)
            cx.execute(text("DELETE FROM node_agent WHERE store=:s"), {"s": st})
            cx.execute(_agent.insert(), {"store": st, "hostname": _VIRTUAL_HOST, "version": "demo",
                                         "ip": "127.0.0.1", "last_seen": now})


def process_virtual_commands(min_age_sec: int = 4) -> int:
    """Auto-confirm dispatched commands for virtual-agent stores after a short delay,
    so the UI shows Remediating… then Resolved (real command-channel flow, mocked node)."""
    if not CFG.DEMO_AGENT or not db.table_exists("node_command"):
        return 0
    now = _now()
    virt = {r["store"] for r in db.read_sql(
        "SELECT store, hostname FROM node_agent").to_dict("records") if str(r.get("hostname")) == _VIRTUAL_HOST}
    if not virt:
        return 0
    rows = db.read_sql("SELECT id, store, action, requested_at FROM node_command "
                       "WHERE status IN ('pending','dispatched')").to_dict("records")
    done = 0
    for r in rows:
        if r["store"] not in virt:
            continue
        req = _parse_ts(r["requested_at"])
        if req and (now - req).total_seconds() < min_age_sec:
            continue    # let it show 'Remediating…' briefly
        note = _MOCK_RESULT.get(r["action"], f"{r['action']} applied") + " (virtual agent)"
        command_result(int(r["id"]), "done", note, actor=_VIRTUAL_HOST)
        done += 1
    return done
