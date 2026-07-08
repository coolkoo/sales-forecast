"""PowerBI interop — report views exposed as CSV exports and an OData v4 feed.

Their legacy PowerBI stays; this makes our forecasts/anomalies/sales consumable
without migration. PowerBI natively reads OData and CSV; a read-only Postgres
connection to these same views is the other supported path (see /api/feeds/info).
"""
from __future__ import annotations

import io

from app import db

# report-ready (denormalised) views over the warehouse
VIEWS = {
    "Forecast": "SELECT brand, store, daypart, menu_item_id, target_date, p05, p50, p95, "
                "expected_units, expected_net, backend FROM forecast",
    "Anomalies": "SELECT anomaly_id, type, store, target, start_date, end_date, score, "
                 "council_verdict, council_confidence, description FROM anomaly",
    "Buying": "SELECT run_date, brand, store, sku, reorder_qty, uom, on_hand, par_level, "
              "order_cost, lead_time_days FROM buying_suggestion WHERE reorder_qty > 0",
    "SalesDaily": "SELECT brand, store, business_date, daypart, menu_item_id, menu_item, "
                  "category, qty, net FROM sales_line",
    "Stores": "SELECT store, brand, region, opened FROM store",
}
_KEY = {"Forecast": None, "Anomalies": "anomaly_id", "Buying": None, "SalesDaily": None, "Stores": "store"}


def csv(name: str) -> str | None:
    if name not in VIEWS:
        return None
    df = db.read_sql(VIEWS[name] + (" LIMIT 200000" if name == "SalesDaily" else ""))
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue()


def odata_service(base: str) -> dict:
    return {"@odata.context": f"{base}/$metadata",
            "value": [{"name": n, "kind": "EntitySet", "url": n} for n in VIEWS]}


def odata_entity(name: str, base: str, top: int = 5000) -> dict | None:
    if name not in VIEWS:
        return None
    df = db.read_sql(VIEWS[name] + f" LIMIT {int(top)}")
    for c in df.columns:
        if df[c].dtype.kind in ("M", "m"):
            df[c] = df[c].astype(str)
    return {"@odata.context": f"{base}/$metadata#{name}", "value": df.to_dict("records")}


def odata_metadata() -> str:
    """Minimal EDMX so PowerBI's OData connector can discover the entity sets."""
    ns = "KFC"
    ents, sets = [], []
    for name, sql in VIEWS.items():
        # column names from a 0-row read
        cols = list(db.read_sql(sql + " LIMIT 0").columns)
        key = _KEY[name] or cols[0]
        props = "".join(f'<Property Name="{c}" Type="Edm.String"/>' for c in cols)
        ents.append(f'<EntityType Name="{name}"><Key><PropertyRef Name="{key}"/></Key>{props}</EntityType>')
        sets.append(f'<EntitySet Name="{name}" EntityType="{ns}.{name}"/>')
    return ('<?xml version="1.0" encoding="utf-8"?>'
            '<edmx:Edmx xmlns:edmx="http://docs.oasis-open.org/odata/ns/edmx" Version="4.0"><edmx:DataServices>'
            f'<Schema xmlns="http://docs.oasis-open.org/odata/ns/edm" Namespace="{ns}">'
            + "".join(ents) + f'<EntityContainer Name="Container">' + "".join(sets)
            + '</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>')


def info(base: str) -> dict:
    return {
        "odata_url": f"{base}/odata/",
        "entities": list(VIEWS),
        "csv_urls": {n: f"{base}/api/export/{n}.csv" for n in VIEWS},
        "postgres": {"note": "Or connect PowerBI directly (read-only) to these tables/views",
                     "host": "192.168.50.85", "port": 5432, "database": "salesforecast",
                     "tables": ["forecast", "anomaly", "buying_suggestion", "sales_line", "store"]},
        "how_to": "PowerBI → Get Data → OData feed → paste odata_url. Or Get Data → PostgreSQL.",
    }
