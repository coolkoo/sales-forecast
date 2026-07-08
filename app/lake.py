"""Medallion lineage — the bronze → silver → gold asset graph.

Documents the data-lake layers with live row counts + freshness so the pipeline's
lineage is inspectable (the value Dagster/AURIX provide). The object store is the
mounted `data/` volume (bronze = raw Simphony exports; silver = conformed
canonical; gold = forecast/anomaly/plan outputs) — MinIO-ready if we later swap
the filesystem for S3.
"""
from __future__ import annotations

from app import db
from app.config import DATA_DIR

# asset -> (layer, source-or-upstream, human label)
ASSETS = [
    ("check_detail (raw)", "bronze", "Simphony", "Raw POS check-detail export"),
    ("sales_line", "silver", "conform ← bronze", "Conformed canonical sales"),
    ("menu_item", "silver", "SAP/menu", "Menu catalog"),
    ("recipe_bom", "silver", "SAP", "Recipe bill-of-materials"),
    ("ingredient", "silver", "SAP", "Ingredient master + costs"),
    ("inventory_snapshot", "silver", "SAP", "On-hand inventory"),
    ("purchase_order", "silver", "SAP", "Purchase orders"),
    ("weather", "silver", "Weather feed", "Weather covariate"),
    ("forecast", "gold", "Chronos-2 ← silver", "Forecasts (p05/p50/p95)"),
    ("anomaly", "gold", "detector ← silver", "Anomalies + council verdicts"),
    ("buying_suggestion", "gold", "planner ← forecast+BOM", "Reorder suggestions"),
    ("prep_plan", "gold", "planner ← forecast+BOM", "Thaw / cook / prep plan"),
]


def _rows(t: str) -> int:
    return int(db.read_sql(f"SELECT COUNT(*) c FROM {t}")["c"].iloc[0]) if db.table_exists(t) else 0


def _fresh(t: str):
    for col in ("business_date", "as_of", "target_date", "run_date", "detected_on", "order_date"):
        try:
            if db.table_exists(t):
                v = db.read_sql(f"SELECT MAX({col}) m FROM {t}")["m"]
                if len(v) and v.iloc[0] is not None:
                    return str(v.iloc[0])[:10]
        except Exception:
            continue
    return None


def lineage() -> dict:
    layers = {"bronze": [], "silver": [], "gold": []}
    for asset, layer, src, label in ASSETS:
        tbl = "check_detail_last60d" if asset.startswith("check_detail") else asset
        if asset.startswith("check_detail"):
            path = DATA_DIR / "raw_simphony" / "check_detail_last60d.csv"
            rows = sum(1 for _ in path.open()) - 1 if path.exists() else 0
            fresh = None
        else:
            rows = _rows(tbl); fresh = _fresh(tbl)
        layers[layer].append({"asset": asset, "table": tbl, "source": src, "label": label,
                              "rows": rows, "fresh": fresh})
    return {"layers": layers, "object_store": "filesystem (data/) · MinIO-ready",
            "orchestration": "pipeline runner + job scheduler"}
