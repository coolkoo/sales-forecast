"""Drill-down + anomaly-inspection detail for the dashboard.

- anomaly_detail(id): the anomaly record plus the context time series that
  explains WHY it was flagged (actual vs expected around the window; comp% for
  fraud; on-hand vs theoretical for inventory).
- breakdown(by, filters): generic forecast breakdown powering chart drill-downs
  (category→items, daypart→items, item→stores, store→category).
- dow_detail / day_detail: breakdowns behind the day-of-week bars and trend points.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app import db
from app.analytics import summary as S


# --------------------------------------------------------------------------- #
# Chart drill-downs
# --------------------------------------------------------------------------- #
def breakdown(by: str, store: str = "", daypart: str = "", category: str = "",
              menu_item_id: str = "") -> list[dict]:
    fc = S._fc()
    menu = db.read_sql("SELECT menu_item_id, name, category FROM menu_item")
    fc = fc.merge(menu, on="menu_item_id", how="left")
    for col, val in [("store", store), ("daypart", daypart), ("category", category),
                     ("menu_item_id", menu_item_id)]:
        if val:
            fc = fc[fc[col] == val]
    keycol = {"menu_item": "name", "category": "category", "daypart": "daypart",
              "store": "store"}.get(by, "name")
    g = (fc.groupby(keycol).agg(net=("expected_net", "sum"), units=("expected_units", "sum"))
         .reset_index().rename(columns={keycol: "label"}).sort_values("net", ascending=False))
    return [{"label": str(r.label), "net": round(float(r.net), 0), "units": round(float(r.units), 0)}
            for r in g.itertuples(index=False)]


def dow_detail(dow: int, days: int = 120) -> dict:
    """For a given weekday: daypart split + top stores, from recent history."""
    df = S._hist()
    lo = S._CACHE["last"] - pd.Timedelta(days=days - 1)
    d = df[(df["dow"] == int(dow)) & (df["business_date"] >= lo)]
    dp = (d.groupby("daypart")["net"].sum().sort_values(ascending=False)
          .reset_index().rename(columns={"net": "value", "daypart": "label"}))
    st = (d.groupby("store")["net"].sum().sort_values(ascending=False)
          .reset_index().rename(columns={"net": "value", "store": "label"}))
    fmt = lambda g: [{"label": str(r.label), "value": round(float(r.value), 0)} for r in g.itertuples(index=False)]
    return {"daypart": fmt(dp), "stores": fmt(st)}


def day_detail(date: str) -> dict:
    """Store + category split for one calendar day (history if past, else forecast)."""
    d = pd.Timestamp(date)
    hist = S._hist()
    if d <= S._CACHE["last"]:
        src = hist[hist["business_date"] == d]
        net_col, store_col, cat_col = "net", "store", "category"
        by_store = src.groupby(store_col)[net_col].sum()
        by_cat = src.groupby(cat_col)[net_col].sum()
        source = "actual"
    else:
        fc = S._fc().merge(db.read_sql("SELECT menu_item_id, category FROM menu_item"),
                           on="menu_item_id", how="left")
        src = fc[fc["target_date"].astype(str) == str(d.date())]
        by_store = src.groupby("store")["expected_net"].sum()
        by_cat = src.groupby("category")["expected_net"].sum()
        source = "forecast"
    fmt = lambda s: [{"label": str(k), "value": round(float(v), 0)}
                     for k, v in s.sort_values(ascending=False).items()]
    return {"date": str(d.date()), "source": source, "stores": fmt(by_store), "category": fmt(by_cat)}


# --------------------------------------------------------------------------- #
# Anomaly inspection
# --------------------------------------------------------------------------- #
def _daily(store: str, daypart: str | None = None) -> pd.DataFrame:
    df = S._hist()
    d = df[df["store"] == store]
    if daypart and daypart not in ("ALL", "", None):
        d = d[d["daypart"] == daypart]
    return d.groupby("business_date").agg(units=("qty", "sum"), net=("net", "sum"),
                                          comp=("comp", "sum")).reset_index()


def _expected_units(g: pd.DataFrame) -> np.ndarray:
    """Day-of-week × EWMA-level expectation (same shape detection uses)."""
    s = g.set_index("business_date")["units"].astype(float)
    dow = s.index.dayofweek
    overall = max(s.mean(), 1e-6)
    prof = np.array([max(s[dow == dd].mean() / overall, 1e-3) if (dow == dd).any() else 1.0
                     for dd in range(7)])
    deseas = s.values / prof[dow]
    level = pd.Series(deseas, index=s.index).ewm(span=45, adjust=False).mean().shift(1)
    return level.values * prof[dow]


def anomaly_detail(anomaly_id: str) -> dict:
    row = db.read_sql("SELECT * FROM anomaly WHERE anomaly_id=:a", {"a": anomaly_id})
    if row.empty:
        return {"error": "not found"}
    a = row.iloc[0].to_dict()
    typ = a["type"]
    start = pd.Timestamp(a["start_date"]); end = pd.Timestamp(a["end_date"])
    out = {"anomaly": {k: (str(v) if isinstance(v, (pd.Timestamp,)) else v) for k, v in a.items()},
           "context_type": None, "series": []}

    if typ in ("POS_OUTAGE", "DEMAND_SPIKE", "DEMAND_DROP"):
        dp = a["daypart"] if typ == "POS_OUTAGE" else None
        g = _daily(a["store"], dp)
        g["expected"] = _expected_units(g)
        lo, hi = start - pd.Timedelta(days=21), end + pd.Timedelta(days=10)
        w = g[(g["business_date"] >= lo) & (g["business_date"] <= hi)].copy()
        out["context_type"] = "actual_vs_expected"
        out["unit_label"] = f"units ({'all dayparts' if not dp else dp})"
        out["series"] = [{"date": d.strftime("%Y-%m-%d"), "actual": round(float(u), 1),
                          "expected": round(float(e), 1) if pd.notna(e) else None,
                          "in_window": bool(start <= d <= end)}
                         for d, u, e in zip(w["business_date"], w["units"], w["expected"])]

    elif typ == "VOID_COMP_FRAUD":
        g = _daily(a["store"])
        g["comp_pct"] = g["comp"] / g["net"].clip(lower=1)
        base = float(g[g["business_date"] < start]["comp_pct"].tail(60).median())
        lo, hi = start - pd.Timedelta(days=21), end + pd.Timedelta(days=10)
        w = g[(g["business_date"] >= lo) & (g["business_date"] <= hi)]
        out["context_type"] = "comp_pct"
        out["baseline"] = round(base, 4)
        out["series"] = [{"date": d.strftime("%Y-%m-%d"), "actual": round(float(c) * 100, 2),
                          "expected": round(base * 100, 2), "in_window": bool(start <= d <= end)}
                         for d, c in zip(w["business_date"], w["comp_pct"])]

    elif typ == "INVENTORY_VARIANCE":
        inv = db.read_sql("SELECT as_of, on_hand_qty, theoretical_on_hand, variance_pct "
                          "FROM inventory_snapshot WHERE store=:s AND sku=:k ORDER BY as_of",
                          {"s": a["store"], "k": a["target"]})
        for c in ("on_hand_qty", "theoretical_on_hand", "variance_pct"):
            inv[c] = pd.to_numeric(inv[c], errors="coerce")
        out["context_type"] = "inventory"
        out["series"] = [{"date": str(r.as_of), "actual": round(float(r.on_hand_qty), 1),
                          "expected": round(float(r.theoretical_on_hand), 1),
                          "variance_pct": round(float(r.variance_pct), 3),
                          "in_window": str(r.as_of) == str(start.date())}
                         for r in inv.itertuples(index=False)]

    elif typ == "CHANNEL_OUTAGE" and db.table_exists("sales_channel"):
        tgt = a["target"]
        c = db.read_sql("SELECT business_date, channel, delivery_partner, qty FROM sales_channel "
                        "WHERE store=:s", {"s": a["store"]})
        c["business_date"] = pd.to_datetime(c["business_date"])
        c["delivery_partner"] = c["delivery_partner"].fillna("").astype(str)
        sub = c[(c["channel"] == tgt) | (c["delivery_partner"] == tgt)]
        g = sub.groupby("business_date", as_index=False)["qty"].sum().sort_values("business_date")
        g["expected"] = _expected_units(g.rename(columns={"qty": "units"}))
        lo, hi = start - pd.Timedelta(days=21), end + pd.Timedelta(days=10)
        w = g[(g["business_date"] >= lo) & (g["business_date"] <= hi)]
        out["context_type"] = "actual_vs_expected"
        out["unit_label"] = f"units — {tgt}"
        out["series"] = [{"date": d.strftime("%Y-%m-%d"), "actual": round(float(u), 1),
                          "expected": round(float(e), 1) if pd.notna(e) else None,
                          "in_window": bool(start <= d <= end)}
                         for d, u, e in zip(w["business_date"], w["qty"], w["expected"])]
    return out
