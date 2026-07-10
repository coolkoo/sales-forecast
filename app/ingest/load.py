"""Load canonical + dimension data into the warehouse.

In production these tables are the *serving layer* AURIX materialises from the
medallion pipeline. Here we load the generated canonical files directly so the
forecasting/anomaly/inventory layers have a stable contract to read.
"""
from __future__ import annotations

import json

import pandas as pd

from app import db
from app.config import DATA_DIR

CANON = DATA_DIR / "canonical"
GT = DATA_DIR / "ground_truth"


def _load_json(name: str) -> list[dict]:
    return json.loads((CANON / name).read_text())


def load_all() -> dict[str, int]:
    counts: dict[str, int] = {}

    # facts / time series (CSV)
    for tbl, fname in [("sales_line", "sales_line.csv"), ("calendar", "calendar.csv"),
                       ("weather", "weather.csv"), ("inventory_snapshot", "inventory_snapshot.csv"),
                       ("purchase_order", "purchase_order.csv"), ("sales_channel", "sales_channel.csv")]:
        if not (CANON / fname).exists():
            continue
        df = pd.read_csv(CANON / fname)
        if tbl == "sales_channel":
            df["delivery_partner"] = df["delivery_partner"].fillna("")
        counts[tbl] = db.load_df(df, tbl)

    # dimensions (JSON)
    menu = pd.DataFrame(_load_json("menu_item.json"))
    menu["dayparts"] = menu["dayparts"].apply(lambda x: ",".join(x))   # flatten list for SQL
    counts["menu_item"] = db.load_df(menu, "menu_item")
    for tbl, fname in [("recipe_bom", "recipe_bom.json"), ("ingredient", "ingredient.json"),
                       ("store", "store.json"), ("promo_event", "promo_event.json")]:
        counts[tbl] = db.load_df(pd.DataFrame(_load_json(fname)), tbl)

    # ground truth (for anomaly scoring; not a production table)
    if (GT / "anomalies.csv").exists():
        counts["anomaly_ground_truth"] = db.load_df(pd.read_csv(GT / "anomalies.csv"),
                                                    "anomaly_ground_truth")
    return counts


if __name__ == "__main__":
    print(json.dumps(load_all(), indent=2))
