"""Feature / covariate construction for the forecaster.

Restaurant demand is covariate-driven (day-of-week, daypart, weather, holidays,
promos), so this is a first-class component. For each series
(store x menu_item x daypart) it produces a continuous daily target plus:

  past covariates (history only):   temp_f, is_rain
  known-future covariates (horizon):dow_sin/cos, is_weekend, holiday, promo, temp_f, is_rain

Future weather uses historical *climatology* (region x day-of-year normals) as a
stand-in for a real weather-forecast feed — swap in a live provider later without
touching the model.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from app import db
from app.config import CFG


@dataclass
class Series:
    brand: str
    store: str
    daypart: str
    menu_item_id: str
    menu_item: str
    unit_price: float
    hist: pd.DataFrame       # index=date, cols: y + past/known covariates


class FeatureBuilder:
    def __init__(self):
        self.sales = db.read_sql("SELECT * FROM sales_line")
        self.sales["business_date"] = pd.to_datetime(self.sales["business_date"])
        self.store = db.read_sql("SELECT store, brand, region, opened FROM store").set_index("store")
        self.cal = db.read_sql("SELECT business_date, holiday FROM calendar")
        self.cal["business_date"] = pd.to_datetime(self.cal["business_date"])
        self.cal["holiday_flag"] = (self.cal["holiday"].fillna("").astype(str) != "").astype(int)
        self.promo = db.read_sql("SELECT * FROM promo_event")
        wx = db.read_sql("SELECT region, business_date, temp_f, is_rain FROM weather")
        wx["business_date"] = pd.to_datetime(wx["business_date"])
        self.wx = wx
        # climatology: region x day-of-year normals (forward weather proxy)
        wx = wx.assign(doy=wx["business_date"].dt.dayofyear)
        self.clim = wx.groupby(["region", "doy"]).agg(temp_f=("temp_f", "mean"),
                                                       is_rain=("is_rain", "mean")).reset_index()
        self.last_date = self.sales["business_date"].max()

    # -- covariate frame for arbitrary dates (past uses actuals, future uses climatology) --
    def covariates(self, store: str, dates: pd.DatetimeIndex) -> pd.DataFrame:
        region = self.store.loc[store, "region"]
        df = pd.DataFrame({"business_date": dates})
        df["dow"] = df["business_date"].dt.dayofweek
        df["dow_sin"] = np.sin(2 * np.pi * df["dow"] / 7)
        df["dow_cos"] = np.cos(2 * np.pi * df["dow"] / 7)
        df["is_weekend"] = (df["dow"] >= 5).astype(int)
        df = df.merge(self.cal[["business_date", "holiday_flag"]], on="business_date", how="left")
        # weather: actuals where available, else climatology
        act = self.wx[self.wx["region"] == region][["business_date", "temp_f", "is_rain"]]
        df = df.merge(act, on="business_date", how="left")
        need = df["temp_f"].isna()
        if need.any():
            cl = self.clim[self.clim["region"] == region].set_index("doy")
            doy = df.loc[need, "business_date"].dt.dayofyear
            df.loc[need, "temp_f"] = doy.map(cl["temp_f"]).values
            df.loc[need, "is_rain"] = doy.map(cl["is_rain"]).values
        df["holiday_flag"] = df["holiday_flag"].fillna(0).astype(int)
        # store-age (new-store maturation): years since open, clipped — known past & future
        opened = self.store.loc[store, "opened"] if "opened" in self.store.columns else None
        if opened is not None and str(opened) != "nan":
            age = (df["business_date"] - pd.Timestamp(str(opened))).dt.days / 365.0
            df["store_age"] = age.clip(0, 3).values
        else:
            df["store_age"] = 3.0
        return df.set_index("business_date")

    def promo_flag(self, brand: str, store: str, item: str, dates: pd.DatetimeIndex) -> np.ndarray:
        flag = np.zeros(len(dates))
        d = np.asarray(dates)
        for p in self.promo.itertuples(index=False):
            if p.menu_item_id != item:
                continue
            if p.scope == "store" and p.target != store:
                continue
            if p.scope == "brand" and p.target != brand:
                continue
            m = (d >= np.datetime64(p.start_date)) & (d <= np.datetime64(p.end_date))
            flag[m] = 1.0
        return flag

    def series_iter(self, min_history: int | None = None):
        min_history = min_history or CFG.MIN_HISTORY_DAYS
        grp = self.sales.groupby(["brand", "store", "daypart", "menu_item_id"], sort=True)
        for (brand, store, daypart, item), g in grp:
            if g["business_date"].nunique() < min_history:
                continue
            g = g.sort_values("business_date")
            start = max(g["business_date"].min(), self.last_date - pd.Timedelta(days=CFG.FORECAST_LOOKBACK))
            idx = pd.date_range(start, self.last_date, freq="D")
            y = g.set_index("business_date")["qty"].reindex(idx, fill_value=0)
            cov = self.covariates(store, idx)
            cov["promo"] = self.promo_flag(brand, store, item, idx)
            hist = cov.copy()
            hist["y"] = y.values
            yield Series(brand=brand, store=store, daypart=daypart, menu_item_id=item,
                         menu_item=str(g["menu_item"].iloc[0]),
                         unit_price=float(g["unit_price"].iloc[-1]), hist=hist)

    def future_frame(self, s: Series, horizon: int) -> pd.DataFrame:
        idx = pd.date_range(self.last_date + pd.Timedelta(days=1), periods=horizon, freq="D")
        cov = self.covariates(s.store, idx)
        cov["promo"] = self.promo_flag(s.brand, s.store, s.menu_item_id, idx)
        return cov
