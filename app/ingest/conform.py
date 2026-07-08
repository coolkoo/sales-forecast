"""Raw Simphony check-detail  ->  canonical sales_line  (the Phase-0/1 conform seam).

This is the transform to run against real Oracle Simphony exports: it takes
transaction-grain check-detail rows and aggregates them to the canonical
`brand x store x business_date x daypart x menu_item` grain the platform reads.

`validate()` proves the transform is correct against the generated ground truth:
conformed unit counts must match the canonical `sales_line` for the overlap
window (the synthetic check-detail is exploded from those exact quantities).
"""
from __future__ import annotations

import pandas as pd

from app.config import DATA_DIR

RAW = DATA_DIR / "raw_simphony"
CANON = DATA_DIR / "canonical"


def conform_check_detail(raw: pd.DataFrame) -> pd.DataFrame:
    """Aggregate Simphony check-detail lines into canonical sales_line rows."""
    r = raw.copy()
    r["gross"] = r["extended_price"]
    r["comp_amt"] = r["extended_price"] * r["comp_flag"]
    r["void_units"] = r["item_qty"] * r["void_flag"]
    g = r.groupby(["brand", "store", "business_date", "daypart", "menu_item_num"], as_index=False).agg(
        menu_item=("menu_item_name", "first"),
        qty=("item_qty", "sum"),
        gross=("gross", "sum"),
        discount=("discount_amt", "sum"),
        comp=("comp_amt", "sum"),
        void_qty=("void_units", "sum"),
    )
    g = g.rename(columns={"menu_item_num": "menu_item_id"})
    g["net"] = (g["gross"] - g["discount"] - g["comp"]).round(2)
    for c in ("gross", "discount", "comp"):
        g[c] = g[c].round(2)
    return g


def run() -> pd.DataFrame:
    raw = pd.read_csv(RAW / "check_detail_last60d.csv")
    return conform_check_detail(raw)


def validate(tol: float = 1e-6) -> dict:
    """Check conformed units == canonical units on the overlap window."""
    conf = run()
    canon = pd.read_csv(CANON / "sales_line.csv")
    keys = ["store", "business_date", "daypart", "menu_item_id"]
    a = conf.groupby(keys, as_index=False)["qty"].sum().rename(columns={"qty": "qty_conf"})
    b = canon.groupby(keys, as_index=False)["qty"].sum().rename(columns={"qty": "qty_canon"})
    m = a.merge(b, on=keys, how="inner")
    m["diff"] = (m["qty_conf"] - m["qty_canon"]).abs()
    max_diff = float(m["diff"].max()) if len(m) else 0.0
    return {
        "overlap_rows": int(len(m)),
        "max_unit_diff": max_diff,
        "matches": bool(max_diff <= tol),
        "conformed_rows": int(len(conf)),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(validate(), indent=2))
