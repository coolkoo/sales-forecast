"""End-to-end pipeline: ingest -> forecast -> anomaly -> inventory -> prep.

Run the whole thing:               python -m app.pipeline
Run one stage:                     python -m app.pipeline forecast
On the server this is invoked nightly (cron / systemd timer) against Postgres with
the Chronos-2 backend; locally it runs on SQLite with the seasonal backend.
"""
from __future__ import annotations

import json
import sys
import time

from app import db
from app.config import CFG


def stage_ingest():
    from app.ingest import load, conform
    counts = load.load_all()
    validation = conform.validate()
    return {"loaded": counts, "conform_validation": validation}


def stage_forecast():
    from app.forecast import service
    return service.run()


def stage_anomaly(full_history: bool = True):
    from app.anomaly import detect
    from app import settings
    # Settings anomaly_lookback (0 = full history) overrides; else full history for the demo set.
    lb = settings.eff_int("anomaly_lookback", 0)
    if lb and lb > 0:
        lookback = lb
    else:
        last = db.read_sql("SELECT MIN(business_date) a, MAX(business_date) b FROM sales_line")
        import pandas as pd
        lookback = (pd.to_datetime(last["b"].iloc[0]) - pd.to_datetime(last["a"].iloc[0])).days + 1
    return detect.run(lookback=lookback)


def stage_inventory():
    from app.inventory import planning
    return planning.run()


def stage_prep():
    from app.inventory import prep
    return prep.run()


def stage_alerts():
    from app import alerts
    try:
        return alerts.dispatch()   # only fires if enabled in the Alerts config
    except Exception as e:
        return {"error": str(e)}


STAGES = {
    "ingest": stage_ingest, "forecast": stage_forecast, "anomaly": stage_anomaly,
    "inventory": stage_inventory, "prep": stage_prep, "alerts": stage_alerts,
}


def run_all() -> dict:
    db.init_output_tables(drop=True)
    results = {}
    for name in ["ingest", "forecast", "anomaly", "inventory", "prep", "alerts"]:
        t0 = time.time()
        print(f"\n=== {name} ===")
        results[name] = STAGES[name]()
        results[name]["_seconds"] = round(time.time() - t0, 1)
        print(json.dumps(results[name], indent=2, default=str))
    return results


def main():
    args = sys.argv[1:]
    if args and args[0] in STAGES:
        print(json.dumps(STAGES[args[0]](), indent=2, default=str))
    else:
        run_all()


if __name__ == "__main__":
    main()
