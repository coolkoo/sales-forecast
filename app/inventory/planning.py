"""Buying / replenishment planning (Phase 4).

Turns the forecast into SKU-level purchasing:
  forecast units --(BOM)--> raw SKU demand --> par + safety stock --> reorder qty

Safety stock uses the forecast's own uncertainty band (p95-p05) so noisier items
carry more buffer. Reorder fires when on-hand is below the reorder point
(lead-time demand + safety stock).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app import db
from app.config import CFG


def _bom() -> pd.DataFrame:
    bom = db.read_sql("SELECT * FROM recipe_bom")            # has sku, qty_per, uom, yield_factor
    ing = db.read_sql("SELECT * FROM ingredient").drop(columns=["uom"])   # uom already on bom
    m = bom.merge(ing, on="sku", how="left")
    for c in ("qty_per", "yield_factor", "unit_cost", "lead_time_days", "thaw_hours", "cook_yield"):
        m[c] = pd.to_numeric(m[c], errors="coerce")
    return m


def _latest_on_hand() -> pd.DataFrame:
    inv = db.read_sql("SELECT store, sku, on_hand_qty, as_of FROM inventory_snapshot")
    inv["as_of"] = pd.to_datetime(inv["as_of"])
    inv["on_hand_qty"] = pd.to_numeric(inv["on_hand_qty"], errors="coerce")
    idx = inv.groupby(["store", "sku"])["as_of"].idxmax()
    return inv.loc[idx, ["store", "sku", "on_hand_qty"]].reset_index(drop=True)


def run() -> dict:
    from app import settings
    par_weeks = settings.eff_float("par_weeks", CFG.PAR_WEEKS)
    svc_z = settings.eff_float("service_level_z", CFG.SERVICE_LEVEL_Z)
    fc = db.read_sql("SELECT brand, store, menu_item_id, target_date, expected_units, p05, p95 FROM forecast")
    if fc.empty:
        return {"error": "no forecast; run forecast first"}
    for c in ("expected_units", "p05", "p95"):
        fc[c] = pd.to_numeric(fc[c], errors="coerce")
    horizon_days = fc["target_date"].nunique()
    run_date = pd.to_datetime(fc["target_date"]).min().date()
    cover_date = pd.to_datetime(fc["target_date"]).max().date()

    bom = _bom()
    # explode forecast to SKU demand per store/day, carrying band width for safety stock
    fc = fc.merge(bom[["menu_item_id", "sku", "qty_per", "yield_factor"]], on="menu_item_id", how="inner")
    fc["raw"] = fc["expected_units"] * fc["qty_per"] / fc["yield_factor"].clip(lower=0.01)
    fc["band"] = (fc["p95"] - fc["p05"]).clip(lower=0) * fc["qty_per"] / fc["yield_factor"].clip(lower=0.01)
    # per store/sku/day totals, then aggregate stats
    daily = fc.groupby(["brand", "store", "sku", "target_date"], as_index=False).agg(
        raw=("raw", "sum"), band=("band", "sum"))
    agg = daily.groupby(["brand", "store", "sku"], as_index=False).agg(
        total_raw=("raw", "sum"), mean_daily=("raw", "mean"),
        daily_sigma=("band", lambda b: float(np.sqrt(np.mean(np.square(b / 3.29))))))

    ing = bom.drop_duplicates("sku").set_index("sku")
    onhand = _latest_on_hand().set_index(["store", "sku"])["on_hand_qty"]

    rows = []
    for r in agg.itertuples(index=False):
        info = ing.loc[r.sku]
        lead = float(info["lead_time_days"]) if pd.notna(info["lead_time_days"]) else 3.0
        cost = float(info["unit_cost"]) if pd.notna(info["unit_cost"]) else 0.0
        par = r.mean_daily * 7 * par_weeks
        safety = svc_z * r.daily_sigma * np.sqrt(max(lead, 1))
        reorder_point = r.mean_daily * lead + safety
        oh = float(onhand.get((r.store, r.sku), 0.0))
        need = max(0.0, par + safety - oh) if oh < reorder_point else 0.0
        rows.append(dict(
            run_date=run_date, brand=r.brand, store=r.store, sku=r.sku, uom=info["uom"],
            forecast_usage=round(r.total_raw, 1), on_hand=round(oh, 1),
            par_level=round(par, 1), safety_stock=round(float(safety), 1),
            reorder_qty=round(need, 1), lead_time_days=int(lead), unit_cost=round(cost, 2),
            order_cost=round(need * cost, 2), cover_date=cover_date))
    df = pd.DataFrame(rows)
    db.init_output_tables(drop=False)
    from sqlalchemy import text
    with db.engine().begin() as c:
        c.execute(text("DELETE FROM buying_suggestion"))
    n = db.write_df(df, "buying_suggestion")
    to_order = df[df["reorder_qty"] > 0]
    return {"skus_planned": int(len(df)), "rows_written": n, "horizon_days": int(horizon_days),
            "skus_to_reorder": int(len(to_order)),
            "total_order_cost": round(float(to_order["order_cost"].sum()), 2)}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
