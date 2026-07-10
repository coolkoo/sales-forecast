"""Outcome / ROI instrumentation — evidences success metrics #3 (time-to-detect)
and #4 (analyst time saved), and rolls the five success metrics into one scorecard.

Time-to-detect is observed from the detection scheduler's cadence (an issue is
surfaced within one interval of landing). Analyst-time-saved is a transparent,
adjustable model driven by real anomaly volume and the auto-attribution coverage —
every field that goes into it is returned so the estimate can be audited/tuned.
"""
from __future__ import annotations

import json

from app import db
from app.config import CFG

WORKDAY_HOURS = 8.0


def _anomaly_stats() -> dict:
    if not db.table_exists("anomaly"):
        return {"count": 0, "with_drivers": 0}
    df = db.read_sql("SELECT anomaly_id, drivers FROM anomaly")
    if df.empty:
        return {"count": 0, "with_drivers": 0}
    def n_drivers(s):
        try:
            return len(json.loads(s)) if s else 0
        except Exception:
            return 0
    wd = int((df["drivers"].apply(n_drivers) >= 3).sum())
    return {"count": int(len(df)), "with_drivers": wd}


def roi() -> dict:
    from app import scheduler, feedback

    interval_min = CFG.DETECT_INTERVAL_MIN
    ttd_hours = round(interval_min / 60.0, 2)
    last = scheduler.last_run()

    st = _anomaly_stats()
    auto_pct = round(100 * st["with_drivers"] / st["count"], 1) if st["count"] else None

    # analyst-time model: each surfaced anomaly used to require a manual variance
    # investigation; the platform now auto-detects AND pre-attributes ≥3 drivers, so
    # that manual root-cause work is removed for the auto-attributed share. Extrapolate
    # the recent-window anomaly count to a month.
    per_month = st["count"] * (30.0 / max(CFG.ANOMALY_LOOKBACK, 1))
    mins_saved = per_month * CFG.MANUAL_MINUTES_PER_INVESTIGATION * (auto_pct or 0) / 100.0
    days_saved = round(mins_saved / 60.0 / WORKDAY_HOURS, 1)
    reduction_pct = auto_pct  # share of investigations now automated end-to-end

    fb = feedback.summary()
    return {
        "time_to_detect": {
            "detect_interval_min": interval_min,
            "time_to_detect_hours": ttd_hours,
            "target_hours": 2.0,
            "meets_target": ttd_hours <= 2.0,
            "baseline_hours": "24–48",
            "last_detect_run": last,
        },
        "analyst_time_saved": {
            "anomalies_recent": st["count"],
            "auto_attributed_pct": auto_pct,
            "manual_investigation_reduction_pct": reduction_pct,
            "analyst_days_saved_per_month": days_saved,
            "target_reduction_pct": 70.0,
            "meets_target": (reduction_pct is not None and reduction_pct >= 70.0),
            "model": {
                "minutes_per_investigation": CFG.MANUAL_MINUTES_PER_INVESTIGATION,
                "anomalies_per_month_est": round(per_month, 1),
                "workday_hours": WORKDAY_HOURS,
                "basis": "recent anomaly count extrapolated to 30d × minutes/investigation "
                         "× auto-attribution coverage",
            },
        },
        "confirmed_precision": fb,
        "driver_coverage": {
            "anomalies": st["count"], "with_3plus_drivers": st["with_drivers"],
            "pct": auto_pct, "target_min_factors": 3,
            "meets_target": (st["count"] > 0 and st["with_drivers"] == st["count"]),
        },
    }


def scorecard() -> dict:
    """Roll all five expected-outcome metrics into one pass/fail scorecard."""
    from app.forecast import backtest as bt
    from app import feedback

    sd = bt.run_store_daily()
    r = roi()
    fb = feedback.summary()
    rows = [
        {"metric": "Forecast accuracy (store-daily MAPE)", "target": "≤ 10%",
         "value": (f"{sd['mape_pct']}%" if sd["mape_pct"] is not None else "n/a"),
         "meets": bool(sd.get("meets_target"))},
        {"metric": "Anomaly precision (ops-confirmed actionable)", "target": "≥ 80%",
         "value": (f"{fb['confirmed_precision_pct']}%" if fb["confirmed_precision_pct"] is not None
                   else "awaiting ops feedback"),
         "meets": bool(fb.get("meets_target"))},
        {"metric": "Time-to-detect", "target": "< 2h (from 24–48h)",
         "value": f"{r['time_to_detect']['time_to_detect_hours']}h",
         "meets": bool(r["time_to_detect"]["meets_target"])},
        {"metric": "Analyst time saved (manual investigation)", "target": "≥ 70% / ~2 days/mo",
         "value": (f"{r['analyst_time_saved']['manual_investigation_reduction_pct']}% · "
                   f"{r['analyst_time_saved']['analyst_days_saved_per_month']} d/mo"),
         "meets": bool(r["analyst_time_saved"]["meets_target"])},
        {"metric": "Driver analysis coverage", "target": "≥ 3 factors/anomaly",
         "value": (f"{r['driver_coverage']['pct']}% of anomalies"
                   if r["driver_coverage"]["pct"] is not None else "n/a"),
         "meets": bool(r["driver_coverage"]["meets_target"])},
    ]
    return {"rows": rows, "meets_all": all(x["meets"] for x in rows)}
