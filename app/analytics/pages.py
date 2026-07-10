"""Chain-wide rollups for the Stores / Anomalies / Inventory nav pages."""
from __future__ import annotations

import pandas as pd

from app import db
from app.analytics import summary as S


def stores_overview() -> list[dict]:
    fc = S._fc()
    menu = db.read_sql("SELECT menu_item_id, name FROM menu_item").set_index("menu_item_id")["name"]
    dim = db.read_sql("SELECT store, brand, region FROM store").set_index("store")
    net = fc.groupby("store")["expected_net"].sum()
    it = fc.groupby(["store", "menu_item_id"])["expected_net"].sum().reset_index()
    top = it.sort_values("expected_net", ascending=False).drop_duplicates("store").set_index("store")["menu_item_id"]
    an = (db.read_sql("SELECT store, COUNT(*) n FROM anomaly GROUP BY store").set_index("store")["n"].to_dict()
          if db.table_exists("anomaly") else {})
    buy = (db.read_sql("SELECT store, SUM(order_cost) c FROM buying_suggestion WHERE reorder_qty>0 GROUP BY store")
           .set_index("store")["c"].to_dict() if db.table_exists("buying_suggestion") else {})
    rows = []
    for st in net.index:
        rows.append({
            "store": st, "region": str(dim.loc[st, "region"]) if st in dim.index else "",
            "forecast_net": round(float(net.get(st, 0)), 0),
            "anomalies": int(an.get(st, 0)),
            "reorder_cost": round(float(buy.get(st, 0) or 0), 0),
            "top_item": str(menu.get(top.get(st, ""), top.get(st, ""))),
        })
    return sorted(rows, key=lambda r: r["forecast_net"], reverse=True)


def buying_summary() -> dict:
    if not db.table_exists("buying_suggestion"):
        return {"totals": {}, "by_sku": [], "by_store": []}
    b = db.read_sql("SELECT * FROM buying_suggestion WHERE reorder_qty>0")
    for c in ("reorder_qty", "order_cost"):
        b[c] = pd.to_numeric(b[c], errors="coerce")
    by_sku = (b.groupby(["sku", "uom"]).agg(qty=("reorder_qty", "sum"), cost=("order_cost", "sum"),
                                            stores=("store", "nunique")).reset_index()
              .sort_values("cost", ascending=False))
    by_store = b.groupby("store")["order_cost"].sum().sort_values(ascending=False).reset_index()
    return {
        "totals": {"cost": round(float(b["order_cost"].sum()), 0), "skus": int(b["sku"].nunique()),
                   "stores": int(b["store"].nunique()), "lines": int(len(b))},
        "by_sku": [{"label": r.sku, "value": round(float(r.cost), 0), "qty": round(float(r.qty), 1),
                    "uom": r.uom, "stores": int(r.stores)} for r in by_sku.itertuples(index=False)],
        "by_store": [{"label": r.store, "value": round(float(r.order_cost), 0)}
                     for r in by_store.itertuples(index=False)],
    }


def sss(period_days: int = 90) -> dict:
    """Same-store sales: comparable-store YoY growth (stores open >1yr) — this period
    vs the same period a year ago. New stores are reported separately (not comparable)."""
    s = db.read_sql("SELECT store, business_date, net FROM sales_line")
    s["business_date"] = pd.to_datetime(s["business_date"])
    s["net"] = pd.to_numeric(s["net"], errors="coerce")
    st = db.read_sql("SELECT store, region, opened FROM store").set_index("store")
    last = s["business_date"].max()
    cur_lo, cur_hi = last - pd.Timedelta(days=period_days - 1), last
    pri_lo, pri_hi = cur_lo - pd.Timedelta(days=365), cur_hi - pd.Timedelta(days=365)
    comp, new = [], []
    tot_cur = tot_pri = 0.0
    for store, g in s.groupby("store"):
        opened = pd.Timestamp(str(st.loc[store, "opened"])) if store in st.index else None
        cur = float(g[(g.business_date >= cur_lo) & (g.business_date <= cur_hi)]["net"].sum())
        pri = float(g[(g.business_date >= pri_lo) & (g.business_date <= pri_hi)]["net"].sum())
        comparable = opened is not None and opened <= pri_lo   # open for the whole prior window
        region = str(st.loc[store, "region"]) if store in st.index else ""
        if comparable and pri > 0:
            comp.append({"store": store, "region": region, "current": round(cur, 0), "prior": round(pri, 0),
                         "growth_pct": round((cur / pri - 1) * 100, 1)})
            tot_cur += cur; tot_pri += pri
        else:
            new.append({"store": store, "region": region, "current": round(cur, 0),
                        "opened": str(st.loc[store, "opened"]) if store in st.index else None})
    comp.sort(key=lambda r: r["growth_pct"], reverse=True)
    return {"period_days": period_days, "through": str(last.date()),
            "chain_sss_pct": round((tot_cur / tot_pri - 1) * 100, 1) if tot_pri else None,
            "comparable": comp, "new_stores": new}


def thaw_all(limit: int = 200) -> list[dict]:
    if not db.table_exists("prep_plan"):
        return []
    p = db.read_sql("SELECT store, service_date, daypart, sku, raw_qty_needed, uom, thaw_hours, "
                    "pull_from_freezer_at FROM prep_plan WHERE frozen AND pull_from_freezer_at IS NOT NULL")
    if p.empty:
        return []
    p["raw_qty_needed"] = pd.to_numeric(p["raw_qty_needed"], errors="coerce")
    g = (p.groupby(["store", "service_date", "daypart", "sku", "uom", "thaw_hours", "pull_from_freezer_at"],
                   as_index=False)["raw_qty_needed"].sum()
         .rename(columns={"raw_qty_needed": "qty"}))
    g["pull_from_freezer_at"] = g["pull_from_freezer_at"].astype(str)
    g = g.sort_values("pull_from_freezer_at").head(limit)
    return g.to_dict("records")


def channel_mix(days: int = 90) -> dict:
    """Recent net-revenue share by sales channel + delivery-partner split."""
    if not db.table_exists("sales_channel"):
        return {"channels": [], "partners": []}
    c = db.read_sql("SELECT business_date, channel, delivery_partner, net, qty FROM sales_channel")
    c["business_date"] = pd.to_datetime(c["business_date"])
    c["delivery_partner"] = c["delivery_partner"].fillna("").astype(str)
    lo = c["business_date"].max() - pd.Timedelta(days=days)
    c = c[c["business_date"] >= lo]
    order = {"dine_in": 0, "kiosk": 1, "delivery": 2, "app": 3}
    ch = (c.groupby("channel", as_index=False)
            .agg(net=("net", "sum"), qty=("qty", "sum")))
    ch["label"] = ch["channel"].str.replace("_", "-")
    ch = ch.sort_values("channel", key=lambda s: s.map(order))
    d = c[c["channel"] == "delivery"]
    pr = (d.groupby("delivery_partner", as_index=False).agg(net=("net", "sum"))
          .sort_values("net", ascending=False))
    return {
        "channels": [{"channel": r.channel, "label": r.label, "net": round(float(r.net), 2),
                      "qty": int(r.qty)} for r in ch.itertuples(index=False)],
        "partners": [{"partner": r.delivery_partner, "net": round(float(r.net), 2)}
                     for r in pr.itertuples(index=False) if r.delivery_partner],
        "days": days,
    }
