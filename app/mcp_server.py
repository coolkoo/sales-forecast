"""MCP server — drive the platform from Claude (or any MCP client).

Read-only tools over the same warehouse the dashboard uses: forecasts, anomalies,
buying suggestions, and the thaw/prep plan. Mirrors traderific's hosted-HTTP MCP
pattern (bearer token via env, streamable-http transport).

Run:  python -m app.mcp_server        (HTTP transport on SF_MCP_PORT)
Wire: claude mcp add --transport http salesforecast http://HOST:8901/mcp \
        --header "Authorization: Bearer $SF_MCP_TOKEN"
"""
from __future__ import annotations

import json

from app import db
from app.config import CFG

try:
    from mcp.server.fastmcp import FastMCP
except ImportError:
    raise SystemExit("The 'mcp' package is required: pip install mcp")

mcp = FastMCP("salesforecast", host="0.0.0.0", port=CFG.MCP_PORT)


def _q(sql, params=None):
    df = db.read_sql(sql, params or {})
    for c in df.columns:
        if df[c].dtype.kind in ("M", "m"):
            df[c] = df[c].astype(str)
    return df.to_dict("records")


@mcp.tool()
def list_stores() -> str:
    """List all stores (id, brand, region)."""
    return json.dumps(_q("SELECT store, brand, brand_name, region, format FROM store ORDER BY store"))


@mcp.tool()
def pipeline_status() -> str:
    """Summary of the latest pipeline run: row counts and anomaly counts by type."""
    def c(t):
        return int(db.read_sql(f"SELECT COUNT(*) c FROM {t}")["c"].iloc[0]) if db.table_exists(t) else 0
    run = db.read_sql("SELECT MAX(run_date) d FROM forecast")["d"]
    return json.dumps({
        "run_date": str(run.iloc[0]) if len(run) else None,
        "backend": CFG.FORECAST_BACKEND,
        "rows": {t: c(t) for t in ["sales_line", "forecast", "anomaly", "buying_suggestion", "prep_plan"]},
        "anomalies_by_type": _q("SELECT type, COUNT(*) n FROM anomaly GROUP BY type"),
    })


@mcp.tool()
def forecast(store: str, item: str = "", daypart: str = "", days: int = 7) -> str:
    """Forecast for a store. Optionally filter by menu_item_id and daypart. Returns
    per-day p05/p50/p95 units and expected net revenue."""
    sql = ("SELECT daypart, menu_item_id, target_date, p05, p50, p95, expected_units, expected_net "
           "FROM forecast WHERE store=:s")
    p = {"s": store}
    if item:
        sql += " AND menu_item_id=:i"; p["i"] = item
    if daypart:
        sql += " AND daypart=:d"; p["d"] = daypart
    sql += " AND horizon_day<=:n ORDER BY menu_item_id, daypart, target_date"
    p["n"] = int(days)
    return json.dumps(_q(sql, p))


@mcp.tool()
def top_anomalies(n: int = 15, type: str = "") -> str:
    """Highest-ranked detected anomalies. Optionally filter by type (POS_OUTAGE,
    DEMAND_SPIKE, DEMAND_DROP, VOID_COMP_FRAUD, INVENTORY_VARIANCE)."""
    sql = "SELECT type, store, target, start_date, end_date, score, description FROM anomaly"
    p = {}
    if type:
        sql += " WHERE type=:t"; p["t"] = type
    sql += " ORDER BY score DESC LIMIT :n"
    p["n"] = int(n)
    return json.dumps(_q(sql, p))


@mcp.tool()
def buying_plan(store: str = "", reorder_only: bool = True) -> str:
    """Suggested purchase-order reorders (SKU, qty, cost, lead time). Filter by store."""
    sql = "SELECT store, sku, reorder_qty, uom, on_hand, par_level, lead_time_days, order_cost FROM buying_suggestion"
    conds, p = [], {}
    if store:
        conds.append("store=:s"); p["s"] = store
    if reorder_only:
        conds.append("reorder_qty > 0")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY order_cost DESC LIMIT 200"
    return json.dumps(_q(sql, p))


@mcp.tool()
def ask(question: str) -> str:
    """Ask a plain-English analytics question over the warehouse (NL→SQL). E.g.
    'top items by revenue', 'anomalies at KFC-0533', 'reorders for KFC-0421'."""
    from app import nlsql
    return json.dumps(nlsql.answer(question))


@mcp.tool()
def backtest(horizon: int = 14) -> str:
    """Model accuracy: walk-forward backtest — MAE, MAPE, prediction-band coverage, skill vs naive."""
    from app.forecast import backtest as bt
    return json.dumps(bt.run(int(horizon)))


# ---- ACT tools (write-back; require confirm=true) -----------------------------
def _act_table():
    from sqlalchemy import Column, DateTime, MetaData, String, Table
    md = MetaData()
    t = Table("action_log", md, Column("ts", DateTime), Column("action", String),
              Column("target", String), Column("note", String))
    md.create_all(db.engine(), tables=[t], checkfirst=True)
    return t


def _log_action(action, target, note=""):
    import datetime
    t = _act_table()
    with db.engine().begin() as c:
        c.execute(t.insert(), {"ts": datetime.datetime.utcnow(), "action": action,
                               "target": target, "note": note})


@mcp.tool()
def acknowledge_anomaly(anomaly_id: str, note: str = "", confirm: bool = False) -> str:
    """ACT: acknowledge/triage an anomaly (write-back). Requires confirm=true."""
    if not confirm:
        a = _q("SELECT type, store, description FROM anomaly WHERE anomaly_id=:a", {"a": anomaly_id})
        return json.dumps({"preview": a, "note": "Set confirm=true to acknowledge."})
    _log_action("acknowledge_anomaly", anomaly_id, note)
    return json.dumps({"ok": True, "acknowledged": anomaly_id})


@mcp.tool()
def approve_reorder(store: str, sku: str, confirm: bool = False) -> str:
    """ACT: approve a suggested purchase-order reorder (write-back). Requires confirm=true."""
    row = _q("SELECT store, sku, reorder_qty, order_cost FROM buying_suggestion WHERE store=:s AND sku=:k",
             {"s": store, "k": sku})
    if not confirm:
        return json.dumps({"preview": row, "note": "Set confirm=true to approve this reorder."})
    _log_action("approve_reorder", f"{store}/{sku}", json.dumps(row[0] if row else {}))
    return json.dumps({"ok": True, "approved": {"store": store, "sku": sku}})


@mcp.tool()
def set_forecast_backend(backend: str, confirm: bool = False) -> str:
    """ACT: switch the forecast backend ('chronos' or 'seasonal'). Requires confirm=true.
    Run the pipeline afterward to apply."""
    if backend not in ("chronos", "seasonal"):
        return json.dumps({"error": "backend must be 'chronos' or 'seasonal'"})
    if not confirm:
        return json.dumps({"preview": {"backend": backend}, "note": "Set confirm=true to change."})
    from app import settings
    settings.set_many({"forecast_backend": backend})
    _log_action("set_forecast_backend", backend)
    return json.dumps({"ok": True, "backend": backend, "note": "Run the pipeline to apply."})


@mcp.tool()
def run_pipeline(confirm: bool = False) -> str:
    """ACT: trigger a full pipeline re-run (ingest→forecast→anomaly→plan). Requires confirm=true."""
    if not confirm:
        return json.dumps({"note": "Set confirm=true to start a re-run (~1–2 min on Chronos)."})
    from app import jobs
    return json.dumps(jobs.run_async())


@mcp.tool()
def prep_plan(store: str, daypart: str = "") -> str:
    """Prep plan for the next service day: how much to prep, and the frozen thaw
    (pull-from-freezer) schedule."""
    sql = ("SELECT service_date, daypart, menu_item, prep_units, sku, raw_qty_needed, uom, "
           "frozen, thaw_hours, pull_from_freezer_at FROM prep_plan WHERE store=:s")
    p = {"s": store}
    if daypart:
        sql += " AND daypart=:d"; p["d"] = daypart
    sql += " ORDER BY daypart, pull_from_freezer_at LIMIT 500"
    return json.dumps(_q(sql, p))


if __name__ == "__main__":
    mcp.run(transport="streamable-http")
