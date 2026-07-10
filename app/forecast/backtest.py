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


def run_store_daily(horizon: int | None = None, min_history: int = 120) -> dict:
    """Backtest at the DAILY STORE-LEVEL grain (success metric #1: MAPE ≤ 10%).

    Aggregating across items/dayparts removes most of the per-series noise, so this
    is the grain the target is defined at. Holds out the last `horizon` days per
    store, forecasts them from the seasonal backend, and scores MAPE / MAE / band
    coverage per store and overall.
    """
    horizon = horizon or CFG.FORECAST_HORIZON
    s = db.read_sql("SELECT store, business_date, qty FROM sales_line")
    s["business_date"] = pd.to_datetime(s["business_date"])
    s["qty"] = pd.to_numeric(s["qty"], errors="coerce")
    daily = s.groupby(["store", "business_date"], as_index=False)["qty"].sum()
    last = daily["business_date"].max()
    cutoff = last - pd.Timedelta(days=horizon)
    backend = SeasonalBackend()

    per_store, ape_all, err_all, cov_all = [], [], [], []
    for store, g in daily.groupby("store"):
        g = g.sort_values("business_date")
        idx = pd.date_range(g["business_date"].min(), last, freq="D")
        y = g.set_index("business_date")["qty"].reindex(idx, fill_value=0).astype(float)
        train = y[y.index <= cutoff]
        test_idx = pd.date_range(cutoff + pd.Timedelta(days=1), last, freq="D")
        if len(train) < min_history or not len(test_idx):
            continue
        try:
            fc = backend.forecast(pd.DataFrame({"y": train.values}, index=train.index),
                                  pd.DataFrame(index=test_idx))
        except Exception:
            continue
        actual = y.reindex(test_idx).to_numpy()
        pred = fc["p50"].to_numpy()
        lo, hi = fc["p05"].to_numpy(), fc["p95"].to_numpy()
        m = min(len(pred), len(actual))
        actual, pred, lo, hi = actual[:m], pred[:m], lo[:m], hi[:m]
        denom = np.where(actual == 0, np.nan, actual)
        ape = np.abs(pred - actual) / denom
        cov = ((actual >= lo) & (actual <= hi)).astype(float)
        mape = float(np.nanmean(ape)) * 100
        per_store.append({"store": store, "mape_pct": round(mape, 1),
                          "mae": round(float(np.mean(np.abs(pred - actual))), 1),
                          "coverage_pct": round(float(np.mean(cov)) * 100, 1)})
        ape_all += [x for x in ape if np.isfinite(x)]
        err_all += list(np.abs(pred - actual))
        cov_all += list(cov)

    overall_mape = round(float(np.mean(ape_all)) * 100, 1) if ape_all else None
    return {
        "grain": "store_daily", "horizon_days": horizon,
        "cutoff": str(cutoff.date()), "through": str(last.date()),
        "stores_scored": len(per_store),
        "mape_pct": overall_mape,
        "mae": round(float(np.mean(err_all)), 1) if err_all else None,
        "band_coverage_pct": round(float(np.mean(cov_all)) * 100, 1) if cov_all else None,
        "target_mape_pct": 10.0,
        "meets_target": (overall_mape is not None and overall_mape <= 10.0),
        "by_store": sorted(per_store, key=lambda r: r["mape_pct"]),
        "backend": "seasonal",
    }


def hindcast_series(store: str, item: str, daypart: str = "", days: int = 14,
                    min_history: int = 90) -> dict:
    """For one store/item(/daypart), forecast the held-out last `days` and return the
    per-day predicted band vs the actuals — so the chart can overlay 'where it fell'."""
    p = {"s": store, "i": item}
    w = "store=:s AND menu_item_id=:i"
    if daypart and daypart not in ("All", ""):
        w += " AND daypart=:d"; p["d"] = daypart
    s = db.read_sql(f"SELECT business_date, qty FROM sales_line WHERE {w}", p)
    if s.empty:
        return {"rows": [], "error": "no data for this series"}
    s["business_date"] = pd.to_datetime(s["business_date"])
    s["qty"] = pd.to_numeric(s["qty"], errors="coerce")
    g = s.groupby("business_date")["qty"].sum()          # sum across dayparts when "All"
    last = g.index.max()
    idx = pd.date_range(g.index.min(), last, freq="D")
    y = g.reindex(idx, fill_value=0).astype(float)
    cutoff = last - pd.Timedelta(days=days)
    train = y[y.index <= cutoff]
    if len(train) < min_history:
        return {"rows": [], "error": "not enough history to backtest this series"}
    test_idx = pd.date_range(cutoff + pd.Timedelta(days=1), last, freq="D")
    fc = SeasonalBackend().forecast(pd.DataFrame({"y": train.values}, index=train.index),
                                    pd.DataFrame(index=test_idx))
    actual = y.reindex(test_idx).to_numpy()
    rows, err = [], []
    for i, d in enumerate(test_idx):
        a = float(actual[i]); p50 = float(fc["p50"].iloc[i])
        lo = float(fc["p05"].iloc[i]); hi = float(fc["p95"].iloc[i])
        rows.append({"date": d.strftime("%Y-%m-%d"), "p05": round(lo, 1), "p50": round(p50, 1),
                     "p95": round(hi, 1), "actual": round(a, 1), "in_band": bool(lo <= a <= hi)})
        err.append(abs(p50 - a))
    return {"rows": rows, "cutoff": str(cutoff.date()), "through": str(last.date()),
            "mae": round(float(np.mean(err)), 2) if err else None,
            "mape_pct": round(float(np.nanmean([e / a for e, r in zip(err, rows) for a in [r["actual"]] if a > 0])) * 100, 1) if err else None,
            "coverage_pct": round(100 * sum(1 for r in rows if r["in_band"]) / len(rows), 1) if rows else None,
            "backend": "seasonal"}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
