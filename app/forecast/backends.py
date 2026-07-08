"""Forecast backends — pluggable, one contract.

    forecast(hist, future) -> DataFrame[date, p05, p50, p95, expected]

  hist:   DataFrame indexed by date with column 'y' (+ covariate columns)
  future: DataFrame indexed by date with known-future covariate columns

Two implementations:
  ChronosBackend  — real Chronos-2 (amazon/chronos-2) with covariates, on GPU.
                    Adapted from traderific's signals/chronos_engine.py.
  SeasonalBackend — zero-dependency day-of-week x level baseline with an empirical
                    prediction band. Runs anywhere; used for local dev + as the
                    automatic fallback when torch/Chronos isn't available.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.config import CFG

PAST_COV = ["temp_f", "is_rain"]
KNOWN_COV = ["dow_sin", "dow_cos", "is_weekend", "holiday_flag", "promo", "temp_f", "is_rain", "store_age"]
_Q = [0.05, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 0.95]


class SeasonalBackend:
    """Multiplicative day-of-week profile on an EWMA level, with an empirical band."""
    name = "seasonal"

    def forecast(self, hist: pd.DataFrame, future: pd.DataFrame) -> pd.DataFrame:
        y = hist["y"].astype(float)
        dow = hist.index.dayofweek
        overall = max(y.mean(), 1e-6)
        prof = np.ones(7)
        for d in range(7):
            vals = y[dow == d]
            if len(vals):
                prof[d] = max(vals.mean() / overall, 1e-3)
        # recent level via EWMA of deseasonalised series
        deseas = y.values / prof[dow]
        level = pd.Series(deseas).ewm(span=28, adjust=False).mean().iloc[-1]
        # empirical residual scale (deseasonalised)
        resid = deseas[-90:] if len(deseas) >= 90 else deseas
        sigma = float(np.std(resid)) if len(resid) > 3 else max(level * 0.3, 1.0)

        fdow = future.index.dayofweek
        base = level * prof[fdow]
        # promo / holiday adjustments from known-future covariates
        if "holiday_flag" in future:
            base = base * np.where(future["holiday_flag"].values == 1, 0.8, 1.0)
        if "promo" in future:
            base = base * np.where(future["promo"].values == 1, 1.4, 1.0)
        expected = np.clip(base, 0, None)
        z = 1.645
        spread = z * sigma * prof[fdow]
        return pd.DataFrame({
            "date": future.index,
            "p05": np.clip(expected - spread, 0, None),
            "p50": expected,
            "p95": expected + spread,
            "expected": expected,
        })


class ChronosBackend:
    """Real Chronos-2 quantile forecast with covariates (server/GPU)."""
    name = "chronos"

    def __init__(self):
        from chronos import Chronos2Pipeline   # noqa: imported lazily (heavy)
        self.pipe = Chronos2Pipeline.from_pretrained(CFG.CHRONOS_MODEL_ID, device_map=CFG.CHRONOS_DEVICE)

    def forecast(self, hist: pd.DataFrame, future: pd.DataFrame) -> pd.DataFrame:
        h = len(future)
        ctx = pd.DataFrame({"item_id": "X", "timestamp": hist.index, "target": hist["y"].astype(float).values})
        futdf = pd.DataFrame({"item_id": "X", "timestamp": future.index})
        if CFG.USE_COVARIATES:
            for col in PAST_COV:
                if col in hist:
                    ctx[col] = np.nan_to_num(hist[col].astype(float).values)
            for col in KNOWN_COV:
                if col in hist:
                    ctx[col] = np.nan_to_num(hist[col].astype(float).values)
                if col in future:
                    futdf[col] = np.nan_to_num(future[col].astype(float).values)
        out = self.pipe.predict_df(ctx, future_df=futdf, prediction_length=h, freq="D",
                                   quantile_levels=_Q, target="target", validate_inputs=False)
        # out has one row per horizon step with quantile columns
        out = out.tail(h).reset_index(drop=True)
        exp = out["0.5"].to_numpy(float)
        return pd.DataFrame({
            "date": future.index,
            "p05": np.clip(out["0.05"].to_numpy(float), 0, None),
            "p50": np.clip(exp, 0, None),
            "p95": np.clip(out["0.95"].to_numpy(float), 0, None),
            "expected": np.clip(exp, 0, None),
        })


def make_backend(name: str | None = None):
    name = (name or CFG.FORECAST_BACKEND).lower()
    if name == "chronos":
        try:
            return ChronosBackend()
        except Exception as e:   # torch/chronos/GPU missing -> graceful fallback
            print(f"[forecast] Chronos-2 unavailable ({e}); falling back to seasonal backend")
            return SeasonalBackend()
    return SeasonalBackend()
