"""Analytics layer — cross-cutting KPIs, an auto-generated narrative summary, and
chart-ready aggregates for the dashboard.

History aggregates read `sales_line` once and cache (keyed by the latest business
date, so a pipeline reload invalidates it); forward-looking aggregates read the
small `forecast` table live.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app import db

DOW_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
_CACHE: dict = {}


def _hist() -> pd.DataFrame:
    key = str(db.read_sql("SELECT MAX(business_date) m FROM sales_line")["m"].iloc[0])
    if _CACHE.get("key") != key:
        df = db.read_sql("SELECT business_date, brand, store, daypart, category, "
                         "menu_item, qty, net, comp, void_qty FROM sales_line")
        df["business_date"] = pd.to_datetime(df["business_date"])
        for c in ("qty", "net", "comp", "void_qty"):
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df["dow"] = df["business_date"].dt.dayofweek
        _CACHE.clear()
        _CACHE.update(key=key, df=df, last=df["business_date"].max())
    return _CACHE["df"]


def _fc() -> pd.DataFrame:
    df = db.read_sql("SELECT brand, store, daypart, menu_item_id, target_date, "
                     "expected_units, expected_net, backend FROM forecast")
    for c in ("expected_units", "expected_net"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


# ---- chart aggregates --------------------------------------------------------
def chart_dow(days: int = 120) -> list[dict]:
    df = _hist()
    lo = _CACHE["last"] - pd.Timedelta(days=days - 1)
    g = df[df["business_date"] >= lo].groupby("dow")["net"].sum()
    base = g.get(0, g.mean())
    return [{"dow": DOW_NAMES[d], "net": round(float(g.get(d, 0)), 0),
             "index": round(float(g.get(d, 0) / base), 2)} for d in range(7)]


def chart_daypart() -> list[dict]:
    df = _fc()
    g = df.groupby("daypart").agg(net=("expected_net", "sum"), units=("expected_units", "sum"))
    order = {"breakfast": 0, "lunch": 1, "dinner": 2, "late": 3}
    rows = [{"daypart": dp, "net": round(float(r.net), 0), "units": round(float(r.units), 0)}
            for dp, r in g.iterrows()]
    return sorted(rows, key=lambda x: order.get(x["daypart"], 9))


def chart_category() -> list[dict]:
    df = _fc()
    menu = db.read_sql("SELECT menu_item_id, category FROM menu_item")   # category lives on the menu dim
    df = df.merge(menu, on="menu_item_id", how="left")
    g = df.groupby("category")["expected_net"].sum().sort_values(ascending=False)
    return [{"category": c, "net": round(float(v), 0)} for c, v in g.items()]


def chart_top_items(n: int = 10) -> list[dict]:
    df = _fc()
    menu = db.read_sql("SELECT menu_item_id, name FROM menu_item").set_index("menu_item_id")["name"]
    g = df.groupby("menu_item_id").agg(net=("expected_net", "sum"), units=("expected_units", "sum"))
    g = g.sort_values("net", ascending=False).head(n)
    return [{"menu_item_id": str(i), "menu_item": str(menu.get(i, i)),
             "net": round(float(r.net), 0), "units": round(float(r.units), 0)}
            for i, r in g.iterrows()]


def chart_stores() -> list[dict]:
    df = _fc()
    g = df.groupby("store")["expected_net"].sum().sort_values(ascending=False)
    return [{"store": s, "net": round(float(v), 0)} for s, v in g.items()]


def chart_trend(hist_days: int = 90) -> dict:
    """Chain-wide daily net revenue: recent history + forecast continuation."""
    df = _hist()
    lo = _CACHE["last"] - pd.Timedelta(days=hist_days - 1)
    h = df[df["business_date"] >= lo].groupby("business_date")["net"].sum().reset_index()
    hist = [{"date": d.strftime("%Y-%m-%d"), "net": round(float(v), 0)}
            for d, v in zip(h["business_date"], h["net"])]
    fc = _fc()
    fg = fc.groupby("target_date")["expected_net"].sum().reset_index()
    fcast = [{"date": str(d), "net": round(float(v), 0)}
             for d, v in zip(fg["target_date"], fg["expected_net"])]
    return {"history": hist, "forecast": fcast}


# ---- narrative summary -------------------------------------------------------
def summary(lang: str = "en") -> dict:
    df = _hist()
    fc = _fc()
    last = _CACHE["last"]
    backend = str(fc["backend"].iloc[0]) if len(fc) else "n/a"
    horizon_days = fc["target_date"].nunique() if len(fc) else 0

    fc_total = float(fc["expected_net"].sum())
    fc_daily = fc_total / max(horizon_days, 1)
    hist_days = (last - df["business_date"].min()).days + 1
    hist_daily = float(df["net"].sum()) / max(hist_days, 1)
    growth = (fc_daily / hist_daily - 1) if hist_daily else 0

    dow = chart_dow()
    busiest = max(dow, key=lambda x: x["net"])
    weekend = sum(x["net"] for x in dow if x["dow"] in ("Fri", "Sat", "Sun"))
    dow_total = sum(x["net"] for x in dow) or 1
    dp = chart_daypart()
    top_dp = max(dp, key=lambda x: x["net"]) if dp else {"daypart": "-", "net": 0}
    dp_total = sum(x["net"] for x in dp) or 1
    items = chart_top_items(1)
    top_item = items[0] if items else {"menu_item": "-", "net": 0}

    an = db.read_sql("SELECT type, store, description, score FROM anomaly ORDER BY score DESC") \
        if db.table_exists("anomaly") else pd.DataFrame()
    top_anom = an.iloc[0].to_dict() if len(an) else None
    buy = db.read_sql("SELECT sku, reorder_qty, order_cost FROM buying_suggestion WHERE reorder_qty>0") \
        if db.table_exists("buying_suggestion") else pd.DataFrame()
    reorder_cost = float(pd.to_numeric(buy["order_cost"], errors="coerce").sum()) if len(buy) else 0

    dp_name = top_dp["daypart"]
    if lang == "vi":
        _dp = {"lunch": "Trưa", "dinner": "Tối", "late": "Đêm", "breakfast": "Sáng"}.get(dp_name, dp_name)
        insights = [
            f"Dự báo **₫{fc_total:,.0f}** doanh thu thuần trong {horizon_days} ngày tới "
            f"(~₫{fc_daily:,.0f}/ngày), {growth:+.0%} so với trung bình lịch sử — qua mô hình {backend}.",
            f"**{busiest['dow']}** là ngày đông nhất ({busiest['index']:.2f}× Thứ Hai); "
            f"cuối tuần (T6–CN) chiếm **{weekend/dow_total:.0%}** doanh thu tuần.",
            f"**{_dp}** là buổi lớn nhất, chiếm **{top_dp['net']/dp_total:.0%}** doanh thu dự báo.",
            f"Món dự báo cao nhất: **{top_item['menu_item']}** (₫{top_item['net']:,.0f}).",
        ]
        if top_anom:
            insights.append(f"**{len(an)} bất thường** được gắn cờ; nghiêm trọng nhất — {top_anom['description']}.")
        if reorder_cost:
            insights.append(f"Bổ sung kho: **₫{reorder_cost:,.0f}** trên **{len(buy)} SKU** cần đặt lại.")
    else:
        insights = [
            f"Forecast **₫{fc_total:,.0f}** net revenue over the next {horizon_days} days "
            f"(~₫{fc_daily:,.0f}/day), {growth:+.0%} vs the historical daily average — via the {backend} model.",
            f"**{busiest['dow']}** is the busiest day ({busiest['index']:.2f}× Monday); "
            f"the weekend (Fri–Sun) drives **{weekend/dow_total:.0%}** of weekly revenue.",
            f"**{dp_name.title()}** is the largest daypart at **{top_dp['net']/dp_total:.0%}** of forecast sales.",
            f"Top forecast item: **{top_item['menu_item']}** (₫{top_item['net']:,.0f}).",
        ]
        if top_anom:
            insights.append(f"**{len(an)} anomalies** flagged; most severe — {top_anom['description']}.")
        if reorder_cost:
            insights.append(f"Replenishment: **₫{reorder_cost:,.0f}** across **{len(buy)} SKUs** to reorder.")

    return {
        "backend": backend, "run_through": str(last.date()), "horizon_days": int(horizon_days),
        "kpis": {
            "forecast_net_total": round(fc_total, 0),
            "forecast_net_daily": round(fc_daily, 0),
            "history_net_daily": round(hist_daily, 0),
            "growth_vs_history": round(growth, 3),
            "weekend_share": round(weekend / dow_total, 3),
            "busiest_day": busiest["dow"],
            "top_daypart": top_dp["daypart"],
            "top_item": top_item["menu_item"],
            "anomalies": int(len(an)),
            "reorder_cost": round(reorder_cost, 0),
            "stores": int(df["store"].nunique()),
            "menu_items": int(df["menu_item"].nunique()),
        },
        "insights": insights,
    }
