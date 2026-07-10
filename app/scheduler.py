"""Intra-day anomaly-detection scheduler (success metric #3: detect < 2 hours).

Re-runs the anomaly stage every SF_DETECT_INTERVAL_MIN minutes against the latest
data (forecasts still refresh nightly via the full pipeline), recording a heartbeat
so time-to-detect is observable. This is what turns the 24–48h manual-review lag
into a sub-2-hour automated SLA: an issue in the data is surfaced within one
detection interval of landing.

Run standalone:  python -m app.scheduler
In the container it is launched by entrypoint.sh alongside the API + MCP.
"""
from __future__ import annotations

import datetime
import time

from sqlalchemy import Column, DateTime, Integer, MetaData, String, Table

from app import db
from app.config import CFG

_MD = MetaData()
_hb = Table("detect_run", _MD,
            Column("ts", DateTime), Column("status", String), Column("anomalies", Integer),
            Column("seconds", Integer))


def ensure():
    _MD.create_all(db.engine(), tables=[_hb], checkfirst=True)


def _heartbeat(status: str, anomalies: int, seconds: int):
    ensure()
    with db.engine().begin() as cx:
        cx.execute(_hb.insert(), {"ts": datetime.datetime.utcnow(), "status": status,
                                  "anomalies": int(anomalies), "seconds": int(seconds)})


def last_run() -> dict | None:
    if not db.table_exists("detect_run"):
        return None
    df = db.read_sql("SELECT ts, status, anomalies, seconds FROM detect_run ORDER BY ts DESC")
    if df.empty:
        return None
    r = df.iloc[0].to_dict()
    r["ts"] = str(r["ts"])
    return r


def run_once() -> dict:
    from app.anomaly import detect
    t0 = time.time()
    try:
        res = detect.run()
        n = int(res.get("detected", 0))
        _heartbeat("ok", n, int(time.time() - t0))
        return {"ok": True, "detected": n}
    except Exception as e:
        _heartbeat("error", 0, int(time.time() - t0))
        return {"ok": False, "error": str(e)}


def loop():
    interval = max(CFG.DETECT_INTERVAL_MIN, 1) * 60
    print(f"[scheduler] intra-day detection every {CFG.DETECT_INTERVAL_MIN} min "
          f"(time-to-detect SLA ~{CFG.DETECT_INTERVAL_MIN/60:.1f}h)")
    while True:
        time.sleep(interval)     # sleep first so we don't collide with the startup pipeline
        print("[scheduler] running anomaly detection ...")
        print("[scheduler]", run_once())


if __name__ == "__main__":
    loop()
