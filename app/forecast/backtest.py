"""Backtest / calibration harness.

Walk-forward hindcast: hold out the last `horizon` days, forecast them from data
up to the cutoff, and score against the held-out actuals — MAE / MAPE / RMSE,
prediction-band coverage (is p05–p95 honest?), bias, and skill vs a
seasonal-naive baseline. Runs on the fast seasonal model so it works without a
GPU; the same harness can score Chronos-2 when pointed at it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app import db
from app.config import CFG
from app.forecast.backends import SeasonalBackend


def run(horizon: int | None = None, min_history: int = 120) -> dict:
    horizon = horizon or CFG.FORECAST_HORIZON
    s = db.read_sql("SELECT store, menu_item_id, daypart, business_date, qty FROM sales_line")
    s["business_date"] = pd.to_datetime(s["business_date"])
    s["qty"] = pd.to_numeric(s["qty"], errors="coerce")
    last = s["business_date"].max()
    cutoff = last - pd.Timedelta(days=horizon)
    backend = SeasonalBackend()

    err, ape, sq, bias, cov, n = [], [], [], [], [], 0
    naive_err = []
    per_store: dict[str, list] = {}

    for (store, item, dp), g in s.groupby(["store", "menu_item_id", "daypart"], sort=False):
        g = g.sort_values("business_date")
        if g["business_date"].nunique() < min_history:
            continue
        idx = pd.date_range(g["business_date"].min(), last, freq="D")
        y = g.set_index("business_date")["qty"].reindex(idx, fill_value=0).astype(float)
        train = y[y.index <= cutoff]
        test_idx = pd.date_range(cutoff + pd.Timedelta(days=1), last, freq="D")
        actual = y.reindex(test_idx).to_numpy()
        if len(train) < min_history or not len(test_idx):
            continue
        hist = pd.DataFrame({"y": train.values}, index=train.index)
        fut = pd.DataFrame(index=test_idx)
        try:
            fc = backend.forecast(hist, fut)
        except Exception:
            continue
        pred = fc["p50"].to_numpy()
        lo, hi = fc["p05"].to_numpy(), fc["p95"].to_numpy()
        m = min(len(pred), len(actual))
        pred, actual2, lo, hi = pred[:m], actual[:m], lo[:m], hi[:m]
        e = pred - actual2
        # seasonal-naive baseline: value 7 days before each test day
        nb = y.reindex(test_idx - pd.Timedelta(days=7)).to_numpy()[:m]
        nb = np.nan_to_num(nb, nan=float(train.mean()))
        err += list(np.abs(e)); sq += list(e ** 2); bias += list(e)
        naive_err += list(np.abs(nb - actual2))
        denom = np.where(actual2 == 0, np.nan, actual2)
        ape += list(np.abs(e) / denom)
        cov += list(((actual2 >= lo) & (actual2 <= hi)).astype(float))
        per_store.setdefault(store, []).extend(list(np.abs(e)))
        n += m

    def metrics(a):
        a = np.array(a, float)
        return float(np.nanmean(a)) if len(a) else float("nan")

    mae = metrics(err)
    naive_mae = metrics(naive_err)
    result = {
        "horizon_days": horizon, "cutoff": str(cutoff.date()), "through": str(last.date()),
        "points_scored": int(n), "series_scored": int(sum(1 for _ in per_store)),
        "mae": round(mae, 2),
        "mape_pct": round(metrics([x for x in ape if np.isfinite(x)]) * 100, 1),
        "rmse": round(float(np.sqrt(metrics(sq))), 2),
        "bias": round(metrics(bias), 2),
        "band_coverage_pct": round(metrics(cov) * 100, 1),  # target ~90 for p05-p95
        "naive_mae": round(naive_mae, 2),
        "skill_vs_naive_pct": round((1 - mae / naive_mae) * 100, 1) if naive_mae else None,
        "by_store": sorted(
            [{"store": k, "mae": round(metrics(v), 2)} for k, v in per_store.items()],
            key=lambda r: r["mae"]),
        "backend": "seasonal",
    }
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
