"""Thaw / cook / prep scheduling (Phase 4).

For a service date, turns the daypart forecast into a prep list: how many units to
prepare per item, the raw SKU quantities, and — for frozen ingredients — when to
pull them from the freezer so they're thawed in time for the daypart.

  pull_from_freezer_at = daypart_start - thaw_hours
  raw_needed           = prep_units * qty_per / cook_yield   (buy/prep enough edible)
"""
from __future__ import annotations

import pandas as pd

from app import db

# service-window start hour per daypart (local) — when the food must be ready
DAYPART_START_HOUR = {"breakfast": 7, "lunch": 11, "dinner": 17, "late": 22}
PREP_BUFFER = 1.10   # prep 10% over forecast to avoid running out


def run(service_date: str | None = None) -> dict:
    fc = db.read_sql("SELECT * FROM forecast ORDER BY target_date")
    if fc.empty:
        return {"error": "no forecast; run forecast first"}
    fc["expected_units"] = pd.to_numeric(fc["expected_units"], errors="coerce")
    if service_date is None:
        service_date = str(pd.to_datetime(fc["target_date"]).min().date())
    day = fc[fc["target_date"].astype(str) == service_date].copy()
    if day.empty:
        return {"error": f"no forecast for {service_date}"}

    menu = db.read_sql("SELECT menu_item_id, name FROM menu_item").set_index("menu_item_id")["name"]
    bom = db.read_sql("SELECT * FROM recipe_bom").merge(   # recipe_bom already carries uom
        db.read_sql("SELECT sku, frozen, thaw_hours, cook_yield FROM ingredient"), on="sku", how="left")
    for c in ("qty_per", "thaw_hours", "cook_yield"):
        bom[c] = pd.to_numeric(bom[c], errors="coerce")

    day["prep_units"] = (day["expected_units"] * PREP_BUFFER).round()
    rows = []
    for r in day.itertuples(index=False):
        recipe = bom[bom["menu_item_id"] == r.menu_item_id]
        start_hour = DAYPART_START_HOUR.get(r.daypart, 11)
        ready_at = pd.Timestamp(service_date) + pd.Timedelta(hours=start_hour)
        for b in recipe.itertuples(index=False):
            cy = b.cook_yield if pd.notna(b.cook_yield) and b.cook_yield > 0 else 1.0
            raw = r.prep_units * b.qty_per / cy
            frozen = bool(b.frozen)
            thaw_h = float(b.thaw_hours) if pd.notna(b.thaw_hours) else 0.0
            pull_at = (ready_at - pd.Timedelta(hours=thaw_h)) if (frozen and thaw_h > 0) else None
            rows.append(dict(
                run_date=pd.to_datetime(r.run_date).date(), service_date=pd.to_datetime(service_date).date(),
                brand=r.brand, store=r.store, daypart=r.daypart, menu_item_id=r.menu_item_id,
                menu_item=str(menu.get(r.menu_item_id, r.menu_item_id)),
                forecast_units=round(float(r.expected_units), 1), prep_units=float(r.prep_units),
                sku=b.sku, raw_qty_needed=round(float(raw), 2), uom=b.uom,
                frozen=frozen, thaw_hours=thaw_h,
                pull_from_freezer_at=pull_at.to_pydatetime() if pull_at is not None else None,
                cook_yield=cy))
    df = pd.DataFrame(rows)
    db.init_output_tables(drop=False)
    from sqlalchemy import text
    with db.engine().begin() as c:
        c.execute(text("DELETE FROM prep_plan"))
    n = db.write_df(df, "prep_plan")
    frozen_pulls = df[df["frozen"] & df["pull_from_freezer_at"].notna()]
    return {"service_date": service_date, "prep_rows": n,
            "items": int(day.groupby(["store", "daypart", "menu_item_id"]).ngroups),
            "frozen_thaw_tasks": int(len(frozen_pulls))}


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
