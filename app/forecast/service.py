"""Forecast service — forecast every series and persist to the `forecast` table."""
from __future__ import annotations

import pandas as pd

from app import db
from app.config import CFG
from app.forecast.backends import make_backend
from app.forecast.features import FeatureBuilder


def run(horizon: int | None = None, backend_name: str | None = None) -> dict:
    from app import settings
    horizon = horizon or settings.eff_int("forecast_horizon", CFG.FORECAST_HORIZON)
    backend_name = backend_name or settings.eff_str("forecast_backend", CFG.FORECAST_BACKEND)
    fb = FeatureBuilder()
    backend = make_backend(backend_name)
    run_date = (fb.last_date + pd.Timedelta(days=1)).date()

    rows = []
    n_series = 0
    for s in fb.series_iter():
        future = fb.future_frame(s, horizon)
        try:
            fc = backend.forecast(s.hist, future)
        except Exception as e:
            print(f"[forecast] {s.store}/{s.menu_item_id}/{s.daypart} failed: {e}")
            continue
        n_series += 1
        fc = fc.reset_index(drop=True)
        for i, r in fc.iterrows():
            rows.append(dict(
                brand=s.brand, store=s.store, daypart=s.daypart, menu_item_id=s.menu_item_id,
                target_date=pd.Timestamp(r["date"]).date(), run_date=run_date, horizon_day=int(i) + 1,
                p05=round(float(r["p05"]), 3), p50=round(float(r["p50"]), 3),
                p95=round(float(r["p95"]), 3),
                expected_units=round(float(r["expected"]), 3),
                expected_net=round(float(r["expected"]) * s.unit_price, 2),
                backend=backend.name,
            ))
    db.init_output_tables(drop=False)   # ensure output tables exist
    with db.engine().begin() as c:       # clear prior run
        from sqlalchemy import text
        c.execute(text("DELETE FROM forecast"))
    n = db.write_df(pd.DataFrame(rows), "forecast")
    return {"backend": backend.name, "series_forecast": n_series, "rows_written": n,
            "run_date": str(run_date), "horizon": horizon}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2))
