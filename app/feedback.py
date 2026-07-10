"""Ops feedback loop on anomalies (success metric #2: precision ≥ 80% actionable).

Statistical precision on synthetic labels is not the metric ops care about — the
metric is "of the anomalies we surfaced, what fraction did operations confirm as
actionable?". This records that human verdict per anomaly and reports the
confirmed precision, which is also the signal used to tune detection thresholds.
"""
from __future__ import annotations

import datetime

from sqlalchemy import Column, DateTime, MetaData, String, Table, text

from app import db

_MD = MetaData()
_fb = Table("anomaly_feedback", _MD,
            Column("anomaly_id", String, primary_key=True),
            Column("verdict", String),          # actionable | dismissed
            Column("note", String),
            Column("reviewer", String),
            Column("ts", DateTime))

VERDICTS = ("actionable", "dismissed")


def ensure():
    _MD.create_all(db.engine(), tables=[_fb], checkfirst=True)


def record(anomaly_id: str, verdict: str, note: str = "", reviewer: str = "") -> dict:
    if verdict not in VERDICTS:
        return {"error": f"verdict must be one of {VERDICTS}"}
    ensure()
    with db.engine().begin() as cx:
        cx.execute(text("DELETE FROM anomaly_feedback WHERE anomaly_id=:a"), {"a": anomaly_id})
        cx.execute(_fb.insert(), {"anomaly_id": anomaly_id, "verdict": verdict, "note": note,
                                  "reviewer": reviewer or "ops", "ts": datetime.datetime.utcnow()})
    return {"ok": True, "anomaly_id": anomaly_id, "verdict": verdict}


def for_anomalies() -> dict[str, dict]:
    ensure()
    df = db.read_sql("SELECT anomaly_id, verdict, note, reviewer FROM anomaly_feedback")
    return {r["anomaly_id"]: r for r in df.to_dict("records")}


def summary() -> dict:
    ensure()
    df = db.read_sql("SELECT verdict FROM anomaly_feedback")
    actionable = int((df["verdict"] == "actionable").sum()) if len(df) else 0
    dismissed = int((df["verdict"] == "dismissed").sum()) if len(df) else 0
    reviewed = actionable + dismissed
    prec = round(100 * actionable / reviewed, 1) if reviewed else None
    return {
        "reviewed": reviewed, "actionable": actionable, "dismissed": dismissed,
        "confirmed_precision_pct": prec,
        "target_pct": 80.0,
        "meets_target": (prec is not None and prec >= 80.0),
    }
