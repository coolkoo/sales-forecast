"""Automated anomaly comms — route council-confirmed anomalies to the right team.

Channels: a log channel (always, zero-config), email (SMTP) and Teams/Slack
webhook when configured. Dedupes by (type, store, start_date) so re-runs don't
re-alert the same event, and supports per-event or daily-digest mode. Never
raises into the pipeline — a failed send just logs a failure.

Config + the notification log persist in tables the pipeline does not drop.
"""
from __future__ import annotations

import datetime
import json
import smtplib
from email.mime.text import MIMEText

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table, text

from app import db

# anomaly type -> (team label, urgency)
ROUTING = {
    "POS_OUTAGE":        ("Store manager · Operations", "high"),
    "VOID_COMP_FRAUD":   ("Loss prevention · Finance", "high"),
    "INVENTORY_VARIANCE":("Supply chain", "medium"),
    "DEMAND_SPIKE":      ("Operations · Marketing", "medium"),
    "DEMAND_DROP":       ("Area manager · Operations", "medium"),
}
CONFIG_KEYS = ["enabled", "mode", "min_confidence", "emails", "webhook_url",
               "smtp_host", "smtp_port", "smtp_user", "smtp_pass", "smtp_from"]
DEFAULTS = {"enabled": "false", "mode": "digest", "min_confidence": "0.6",
            "emails": "", "webhook_url": "", "smtp_host": "", "smtp_port": "587",
            "smtp_user": "", "smtp_pass": "", "smtp_from": "alerts@kfc-forecast.vn"}

_MD = MetaData()
_cfg = Table("alert_config", _MD, Column("key", String, primary_key=True), Column("value", String))
_log = Table("notification_log", _MD,
             Column("id", Integer, primary_key=True, autoincrement=True),
             Column("anomaly_id", String), Column("type", String), Column("store", String),
             Column("start_date", String), Column("team", String), Column("channel", String),
             Column("status", String), Column("message", String), Column("sent_at", DateTime))


def ensure():
    _MD.create_all(db.engine(), tables=[_cfg, _log], checkfirst=True)


def config() -> dict:
    ensure()
    raw = {r["key"]: r["value"] for r in db.read_sql("SELECT key, value FROM alert_config").to_dict("records")}
    return {k: raw.get(k, DEFAULTS[k]) for k in CONFIG_KEYS}


def set_config(values: dict) -> dict:
    ensure()
    with db.engine().begin() as cx:
        for k, v in (values or {}).items():
            if k not in CONFIG_KEYS:
                continue
            cx.execute(text("DELETE FROM alert_config WHERE key=:k"), {"k": k})
            cx.execute(_cfg.insert(), {"key": k, "value": str(v)})
    return config()


def _log_row(a, team, channel, status, message):
    with db.engine().begin() as cx:
        cx.execute(_log.insert(), {"anomaly_id": a.get("anomaly_id"), "type": a["type"], "store": a["store"],
                                   "start_date": str(a.get("start_date"))[:10], "team": team, "channel": channel,
                                   "status": status, "message": message, "sent_at": datetime.datetime.utcnow()})


def _send_email(cfg, subject, body) -> str:
    to = [e.strip() for e in cfg["emails"].split(",") if e.strip()]
    if not (cfg["smtp_host"] and to):
        return "skipped"
    try:
        msg = MIMEText(body, "plain", "utf-8")
        msg["Subject"] = subject; msg["From"] = cfg["smtp_from"]; msg["To"] = ", ".join(to)
        with smtplib.SMTP(cfg["smtp_host"], int(cfg["smtp_port"] or 587), timeout=10) as s:
            s.starttls()
            if cfg["smtp_user"]:
                s.login(cfg["smtp_user"], cfg["smtp_pass"])
            s.sendmail(cfg["smtp_from"], to, msg.as_string())
        return "sent"
    except Exception as e:
        return f"error: {str(e)[:60]}"


def _send_webhook(cfg, text_msg) -> str:
    if not cfg["webhook_url"]:
        return "skipped"
    try:
        import requests
        requests.post(cfg["webhook_url"], json={"text": text_msg}, timeout=10)
        return "sent"
    except Exception as e:
        return f"error: {str(e)[:60]}"


def _message(a) -> str:
    team, _ = ROUTING.get(a["type"], ("Operations", "medium"))
    conf = int(float(a.get("council_confidence") or 0) * 100)
    return (f"🚨 [{a.get('council_verdict', 'flagged').upper()} {conf}%] {a['type'].replace('_', ' ')} "
            f"at {a['store']} ({str(a.get('start_date'))[:10]}) — {a.get('description', '')} → {team}")


def _already(a) -> bool:
    r = db.read_sql("SELECT COUNT(*) c FROM notification_log WHERE type=:t AND store=:s AND start_date=:d",
                    {"t": a["type"], "s": a["store"], "d": str(a.get("start_date"))[:10]})
    return int(r["c"].iloc[0]) > 0


def dispatch(force: bool = False) -> dict:
    ensure()
    cfg = config()
    if not force and cfg["enabled"] != "true":
        return {"enabled": False, "note": "alerts disabled"}
    if not db.table_exists("anomaly"):
        return {"sent": 0}
    an = db.read_sql("SELECT * FROM anomaly WHERE council_verdict='confirmed' ORDER BY score DESC").to_dict("records")
    minc = float(cfg["min_confidence"] or 0)
    new = [a for a in an if float(a.get("council_confidence") or 0) >= minc and not _already(a)]
    if not new:
        return {"enabled": True, "sent": 0, "note": "no new confirmed anomalies"}

    results = {"email": None, "webhook": None, "logged": 0}
    if cfg["mode"] == "digest":
        body = "KFC Vietnam — anomaly digest\n\n" + "\n".join(_message(a) for a in new)
        subj = f"[KFC Forecast] {len(new)} anomalies flagged"
        results["email"] = _send_email(cfg, subj, body)
        results["webhook"] = _send_webhook(cfg, body)
        for a in new:
            team = ROUTING.get(a["type"], ("Operations", ""))[0]
            _log_row(a, team, "digest", "queued", _message(a)); results["logged"] += 1
    else:
        for a in new:
            team = ROUTING.get(a["type"], ("Operations", ""))[0]
            m = _message(a)
            es = _send_email(cfg, f"[KFC Forecast] {a['type']} at {a['store']}", m)
            ws = _send_webhook(cfg, m)
            _log_row(a, team, f"email:{es}|hook:{ws}", "sent", m); results["logged"] += 1
    return {"enabled": True, "sent": results["logged"], "email": results["email"],
            "webhook": results["webhook"], "mode": cfg["mode"]}


def recent(limit: int = 50) -> list[dict]:
    ensure()
    df = db.read_sql(f"SELECT type, store, start_date, team, channel, status, message, sent_at "
                     f"FROM notification_log ORDER BY id DESC LIMIT {int(limit)}")
    if "sent_at" in df:
        df["sent_at"] = df["sent_at"].astype(str)
    return df.to_dict("records")


def test() -> dict:
    """Send a sample alert through the configured channels (bypasses the enabled flag)."""
    ensure()
    cfg = config()
    sample = "🔔 [TEST] KFC Vietnam anomaly alert — routing + delivery check."
    return {"email": _send_email(cfg, "[KFC Forecast] Test alert", sample),
            "webhook": _send_webhook(cfg, sample),
            "note": "Log channel always records; email/webhook 'skipped' means not configured yet."}
