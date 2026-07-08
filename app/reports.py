"""Reports — runnable, parameterised report templates (table + CSV + print).

The near-term deliverable: reports the team can generate now. Date bounds are
computed in Python and passed as bind params (ISO-string compare) so the SQL is
portable across SQLite/Postgres with no dialect branches. The same warehouse
views are also exposed to PowerBI (app/feeds.py) for future BI connection.
"""
from __future__ import annotations

import io

import pandas as pd

from app import db


def _last_date() -> str:
    return str(db.read_sql("SELECT MAX(business_date) m FROM sales_line")["m"].iloc[0])[:10]


def _bounds(p: dict) -> tuple[str, str]:
    last = _last_date()
    dto = (p.get("date_to") or last)[:10]
    if p.get("date_from"):
        dfrom = p["date_from"][:10]
    else:
        dfrom = str((pd.Timestamp(last) - pd.Timedelta(days=89)).date())
    return dfrom, dto


def _sales_where(p: dict, alias: str = "") -> tuple[str, dict]:
    a = (alias + ".") if alias else ""
    dfrom, dto = _bounds(p)
    conds = [f"{a}business_date >= :dfrom", f"{a}business_date <= :dto"]
    bind = {"dfrom": dfrom, "dto": dto}
    if p.get("store"):
        conds.append(f"{a}store = :store"); bind["store"] = p["store"]
    if p.get("category"):
        conds.append(f"{a}category = :cat"); bind["cat"] = p["category"]
    return " WHERE " + " AND ".join(conds), bind


# name -> spec. params = which filters the UI shows. builder returns (sql, bind) or callable.
def _r_sales_summary(p):
    w, b = _sales_where(p, "sl")
    return ("SELECT sl.store, s.region, SUM(sl.qty) units, SUM(sl.net) net_vnd "
            f"FROM sales_line sl JOIN store s ON sl.store=s.store{w} "
            "GROUP BY sl.store, s.region ORDER BY net_vnd DESC"), b

def _r_sales_category(p):
    w, b = _sales_where(p)
    return f"SELECT category, SUM(qty) units, SUM(net) net_vnd FROM sales_line{w} GROUP BY category ORDER BY net_vnd DESC", b

def _r_sales_daypart(p):
    w, b = _sales_where(p)
    return f"SELECT daypart, SUM(qty) units, SUM(net) net_vnd FROM sales_line{w} GROUP BY daypart ORDER BY net_vnd DESC", b

def _r_item_perf(p):
    w, b = _sales_where(p)
    return (f"SELECT menu_item, category, SUM(qty) units, SUM(net) net_vnd FROM sales_line{w} "
            "GROUP BY menu_item, category ORDER BY net_vnd DESC"), b

def _r_daily_sales(p):
    w, b = _sales_where(p)
    return f"SELECT business_date, SUM(qty) units, SUM(net) net_vnd FROM sales_line{w} GROUP BY business_date ORDER BY business_date", b

def _r_void_comp(p):
    w, b = _sales_where(p, "")
    return (f"SELECT store, SUM(void_qty) voids, ROUND(SUM(comp)) comp_vnd, SUM(net) net_vnd "
            f"FROM sales_line{w} GROUP BY store ORDER BY comp_vnd DESC"), b

def _r_store_perf(p):
    w, b = _sales_where(p, "sl")
    return ("SELECT sl.store, s.region, SUM(sl.qty) units, SUM(sl.net) net_vnd, "
            "ROUND(AVG(sl.net)) avg_line_vnd FROM sales_line sl JOIN store s ON sl.store=s.store"
            f"{w} GROUP BY sl.store, s.region ORDER BY net_vnd DESC"), b

REPORTS = {
    "sales_summary":   {"title": "Sales summary by store", "params": ["date", "store"],
                        "desc": "Units and net revenue (VND) per store.", "fn": _r_sales_summary},
    "sales_category":  {"title": "Sales by category", "params": ["date", "store"],
                        "desc": "Revenue split by menu category.", "fn": _r_sales_category},
    "sales_daypart":   {"title": "Sales by daypart", "params": ["date", "store"],
                        "desc": "Revenue split by lunch / dinner / late.", "fn": _r_sales_daypart},
    "item_performance":{"title": "Menu-item performance", "params": ["date", "store"],
                        "desc": "Every item ranked by revenue.", "fn": _r_item_perf},
    "daily_sales":     {"title": "Daily sales trend", "params": ["date", "store"],
                        "desc": "Net revenue by day for the period.", "fn": _r_daily_sales},
    "void_comp":       {"title": "Void & comp report", "params": ["date", "store"],
                        "desc": "Voids and comps by store (loss prevention).", "fn": _r_void_comp},
    "store_performance":{"title": "Store performance", "params": ["date", "store"],
                        "desc": "Units, revenue and avg check line by store.", "fn": _r_store_perf},
    "same_store_sales":{"title": "Same-store sales (YoY)", "params": [], "kind": "sss",
                        "desc": "Comparable-store growth vs a year ago (excludes new stores)."},
    "forecast_next14": {"title": "Forecast by store (horizon)", "params": [],
                        "sql": "SELECT store, COUNT(DISTINCT target_date) days, ROUND(SUM(expected_units)) units, "
                               "ROUND(SUM(expected_net)) net_vnd FROM forecast GROUP BY store ORDER BY net_vnd DESC",
                        "desc": "Expected units and revenue per store over the forecast horizon."},
    "anomaly_log":     {"title": "Anomaly log (council)", "params": [],
                        "sql": "SELECT type, store, target, start_date, end_date, score, council_verdict, "
                               "council_confidence, description FROM anomaly ORDER BY score DESC",
                        "desc": "All anomalies with council verdict, confidence and detail."},
    "buying_plan":     {"title": "Buying plan — reorders", "params": [],
                        "sql": "SELECT store, sku, reorder_qty, uom, on_hand, order_cost cost_vnd, lead_time_days "
                               "FROM buying_suggestion WHERE reorder_qty>0 ORDER BY order_cost DESC",
                        "desc": "Suggested purchase-order reorders by store and SKU."},
    "thaw_plan":       {"title": "Prep / thaw plan", "params": [],
                        "sql": "SELECT store, service_date, daypart, sku, raw_qty_needed, uom, thaw_hours, "
                               "pull_from_freezer_at FROM prep_plan WHERE frozen ORDER BY pull_from_freezer_at",
                        "desc": "Freezer-pull schedule across stores (next service day)."},
}


def list_reports() -> list[dict]:
    return [{"name": k, "title": v["title"], "desc": v["desc"], "params": v.get("params", [])}
            for k, v in REPORTS.items()]


def run(name: str, params: dict | None = None) -> dict:
    if name not in REPORTS:
        return {"error": "unknown report"}
    spec = REPORTS[name]; params = params or {}
    if spec.get("kind") == "sss":
        from app.analytics import pages
        d = pages.sss()
        rows = [{"store": r["store"], "region": r["region"], "current_vnd": r["current"],
                 "prior_year_vnd": r["prior"], "yoy_pct": r["growth_pct"]} for r in d["comparable"]]
        cols = ["store", "region", "current_vnd", "prior_year_vnd", "yoy_pct"]
        return {"title": spec["title"], "desc": spec["desc"] + f"  Chain SSS: {d['chain_sss_pct']}%",
                "columns": cols, "rows": rows, "period": {"through": d["through"]}}
    if "fn" in spec:
        sql, bind = spec["fn"](params)
    else:
        sql, bind = spec["sql"], {}
    df = db.read_sql(sql, bind)
    for c in df.columns:
        if df[c].dtype.kind in ("M", "m"):
            df[c] = df[c].astype(str)
    period = {}
    if "date" in spec.get("params", []):
        f, t = _bounds(params); period = {"from": f, "to": t}
    return {"title": spec["title"], "desc": spec["desc"], "columns": list(df.columns),
            "rows": df.head(3000).to_dict("records"), "period": period}


def csv(name: str, params: dict | None = None) -> str | None:
    r = run(name, params)
    if "error" in r:
        return None
    buf = io.StringIO()
    pd.DataFrame(r["rows"], columns=r["columns"]).to_csv(buf, index=False)
    return buf.getvalue()
