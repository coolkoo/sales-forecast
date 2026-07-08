"""Input-source connectors — select / connect / upload.

A catalog of source connectors (Simphony POS, SAP ERP, Toast, Square, weather,
file upload), each declaring which canonical tables it feeds and what config it
needs. Live status is derived from the warehouse (row counts + data-through date),
so a source reads "connected" only when its feed tables actually hold data.

Secrets are NEVER persisted here — secret fields are dropped before saving a
connection (they belong in the server's env/vault). Registrations + an upload log
persist in tables the pipeline does not drop.
"""
from __future__ import annotations

import io
import json

import pandas as pd
from sqlalchemy import (Column, DateTime, Integer, MetaData, String, Table, text)

from app import db

# connector catalog ---------------------------------------------------------
def _f(key, label, type="text", placeholder="", secret=False):
    return {"key": key, "label": label, "type": type, "placeholder": placeholder, "secret": secret}

CONNECTORS = [
    {"id": "simphony", "name": "Oracle Simphony", "category": "POS", "icon": "🧾",
     "desc": "Point-of-sale check detail — the sales history the forecaster runs on.",
     "feeds": ["sales_line", "menu_item"], "primary": "sales_line", "date_col": "business_date",
     "bundled": True, "raw": "Simphony check-detail export (RVC / check / menu-item lines)",
     "config": [_f("host", "EMC / API host", placeholder="emc.company.com"),
                _f("port", "Port", "number", "443"),
                _f("org_short_name", "Org short name", placeholder="KFC-US"),
                _f("api_key", "API key", "text", "", secret=True)]},
    {"id": "sap", "name": "SAP ERP", "category": "ERP", "icon": "📦",
     "desc": "Inventory, purchase orders, recipes/BOM and costs for buying + prep.",
     "feeds": ["inventory_snapshot", "purchase_order", "recipe_bom", "ingredient"],
     "primary": "inventory_snapshot", "date_col": "as_of", "bundled": True,
     "raw": "SAP tables (MARD stock, EKKO/EKPO POs, recipe BOM)",
     "config": [_f("host", "Application server", placeholder="sap.company.com"),
                _f("sap_client", "Client", "number", "100"),
                _f("company_code", "Company code", placeholder="1000"),
                _f("sap_user", "User", placeholder="RFC_USER"),
                _f("sap_password", "Password", "text", "", secret=True)]},
    {"id": "toast", "name": "Toast POS", "category": "POS", "icon": "🍞",
     "desc": "Alternative cloud POS — sales lines + menu.",
     "feeds": ["sales_line", "menu_item"], "primary": "sales_line", "date_col": "business_date",
     "bundled": False, "raw": "Toast Orders API",
     "config": [_f("client_id", "Client ID"), _f("client_secret", "Client secret", secret=True),
                _f("restaurant_guid", "Restaurant GUID")]},
    {"id": "square", "name": "Square", "category": "POS", "icon": "◼️",
     "desc": "SMB POS — orders + catalog.",
     "feeds": ["sales_line"], "primary": "sales_line", "date_col": "business_date",
     "bundled": False, "raw": "Square Orders API",
     "config": [_f("access_token", "Access token", secret=True), _f("location_id", "Location ID")]},
    {"id": "weather", "name": "Weather feed", "category": "Covariate", "icon": "🌦️",
     "desc": "Temperature & precipitation — a demand covariate and known-future signal.",
     "feeds": ["weather"], "primary": "weather", "date_col": "business_date", "bundled": True,
     "raw": "NOAA / OpenWeather daily normals + forecast",
     "config": [_f("provider", "Provider", placeholder="OpenWeather"),
                _f("api_key", "API key", secret=True)]},
    {"id": "file", "name": "File upload", "category": "File", "icon": "⬆️",
     "desc": "Drop a CSV/JSON export (Simphony check detail, or any canonical table).",
     "feeds": [], "primary": None, "date_col": None, "bundled": False, "raw": "CSV / JSON",
     "config": []},
]
_BY_ID = {c["id"]: c for c in CONNECTORS}

_MD = MetaData()
_conn_tbl = Table("source_connection", _MD,
                  Column("connector_id", String, primary_key=True), Column("name", String),
                  Column("category", String), Column("status", String),
                  Column("config_json", String), Column("updated", DateTime))
_upload_tbl = Table("source_upload_log", _MD,
                    Column("id", Integer, primary_key=True, autoincrement=True),
                    Column("filename", String), Column("detected", String),
                    Column("target_table", String), Column("rows", Integer), Column("uploaded", DateTime))


def ensure():
    _MD.create_all(db.engine(), tables=[_conn_tbl, _upload_tbl], checkfirst=True)


def _now():
    return pd.Timestamp.utcnow().to_pydatetime().replace(tzinfo=None)


def _rows(tbl: str) -> int:
    return int(db.read_sql(f"SELECT COUNT(*) c FROM {tbl}")["c"].iloc[0]) if db.table_exists(tbl) else 0


def _last(tbl: str, col: str):
    if not tbl or not col or not db.table_exists(tbl):
        return None
    v = db.read_sql(f"SELECT MAX({col}) m FROM {tbl}")["m"]
    return str(v.iloc[0])[:10] if len(v) and v.iloc[0] is not None else None


def list_sources() -> dict:
    ensure()
    reg = {r["connector_id"]: r for r in db.read_sql("SELECT * FROM source_connection").to_dict("records")}
    cards = []
    for c in CONNECTORS:
        feed_rows = sum(_rows(t) for t in c["feeds"])
        has_data = feed_rows > 0
        if c["bundled"] and has_data:
            status = "connected"
        elif c["id"] in reg:
            status = reg[c["id"]]["status"]
        else:
            status = "available"
        cfg = {}
        if c["id"] in reg and reg[c["id"]].get("config_json"):
            try:
                cfg = json.loads(reg[c["id"]]["config_json"])
            except Exception:
                cfg = {}
        cards.append({
            "id": c["id"], "name": c["name"], "category": c["category"], "icon": c["icon"],
            "desc": c["desc"], "feeds": c["feeds"], "raw": c["raw"], "status": status,
            "rows": feed_rows if c["feeds"] else None,
            "last_sync": _last(c["primary"], c["date_col"]) if c["primary"] else None,
            "config_fields": c["config"], "saved_config": cfg,
            "table_counts": {t: _rows(t) for t in c["feeds"]},
        })
    uploads = db.read_sql("SELECT filename, detected, target_table, rows, uploaded "
                          "FROM source_upload_log ORDER BY id DESC") if db.table_exists("source_upload_log") else pd.DataFrame()
    for col in ("uploaded",):
        if col in uploads:
            uploads[col] = uploads[col].astype(str)
    return {"connectors": cards, "uploads": uploads.to_dict("records") if len(uploads) else []}


def connect(connector_id: str, name: str, config: dict) -> dict:
    if connector_id not in _BY_ID:
        return {"error": "unknown connector"}
    ensure()
    spec = _BY_ID[connector_id]
    secret_keys = {f["key"] for f in spec["config"] if f["secret"]}
    # NEVER persist secrets — keep only a masked marker so the UI shows "set"
    safe = {}
    for k, v in (config or {}).items():
        if not v:
            continue
        safe[k] = "••••••" if k in secret_keys else v
    status = "configured" if not (spec["bundled"] and sum(_rows(t) for t in spec["feeds"]) > 0) else "connected"
    with db.engine().begin() as cx:
        cx.execute(text("DELETE FROM source_connection WHERE connector_id=:i"), {"i": connector_id})
        cx.execute(_conn_tbl.insert(), {"connector_id": connector_id, "name": name or spec["name"],
                                        "category": spec["category"], "status": status,
                                        "config_json": json.dumps(safe), "updated": _now()})
    return {"connector_id": connector_id, "status": status, "saved_fields": list(safe.keys()),
            "note": "Secrets are not stored by the app — put them in the server vault/.env."}


# upload ingest -------------------------------------------------------------
_CHECK_COLS = {"menu_item_num", "item_qty", "check_num"}
_CANON = {
    "sales_line": {"store", "business_date", "daypart", "menu_item_id", "qty"},
    "inventory_snapshot": {"store", "sku", "on_hand_qty", "as_of"},
    "purchase_order": {"store", "sku", "order_qty", "order_date"},
    "weather": {"region", "business_date", "temp_f"},
}


def ingest_upload(filename: str, content: bytes) -> dict:
    ensure()
    try:
        if filename.lower().endswith(".json"):
            df = pd.DataFrame(json.loads(content.decode("utf-8")))
        else:
            df = pd.read_csv(io.BytesIO(content))
    except Exception as e:
        return {"error": f"could not parse file: {e}"}
    cols = set(df.columns)
    detected, target = "unknown table", None
    if _CHECK_COLS <= cols:
        from app.ingest.conform import conform_check_detail
        out = conform_check_detail(df)
        detected, target = "Simphony check-detail", "staging_sales_line"
        db.load_df(out, target)
        rows = len(out)
    else:
        for tbl, keys in _CANON.items():
            if keys <= cols:
                detected, target = f"canonical:{tbl}", f"staging_{tbl}"
                db.load_df(df, target)
                rows = len(df)
                break
        else:
            target = "staging_" + "".join(ch if ch.isalnum() else "_" for ch in filename.rsplit(".", 1)[0])[:40]
            detected = "generic table"
            db.load_df(df, target)
            rows = len(df)
    with db.engine().begin() as cx:
        cx.execute(_upload_tbl.insert(), {"filename": filename, "detected": detected,
                                          "target_table": target, "rows": int(rows), "uploaded": _now()})
    return {"filename": filename, "detected": detected, "target_table": target, "rows": int(rows),
            "columns": list(df.columns)[:20], "promotable": target in _PROMOTABLE(),
            "note": "Staged for review. Promote it into the warehouse, then re-run the pipeline."}


# staging table -> canonical file it can be promoted (appended) into
def _PROMOTABLE():
    return {"staging_sales_line": "sales_line.csv", "staging_inventory_snapshot": "inventory_snapshot.csv",
            "staging_purchase_order": "purchase_order.csv", "staging_weather": "weather.csv"}


def sync(connector_id: str) -> dict:
    """Simulate a live pull from a connected source: fetch a fresh recent batch,
    stage it, and log it. (A real Simphony/SAP client would replace the generator.)"""
    ensure()
    if connector_id not in _BY_ID:
        return {"error": "unknown connector"}
    spec = _BY_ID[connector_id]
    import numpy as np
    rng = np.random.default_rng()
    if connector_id in ("simphony", "toast", "square"):
        stores = db.read_sql("SELECT store FROM store")["store"].tolist()
        menu = db.read_sql("SELECT menu_item_id, name, category, price FROM menu_item")
        last = db.read_sql("SELECT MAX(business_date) m FROM sales_line")["m"].iloc[0]
        day = (pd.Timestamp(last) + pd.Timedelta(days=1)).date()
        rows = []
        for st in stores:
            for _, mi in menu.sample(min(8, len(menu))).iterrows():
                for dp in ("lunch", "dinner"):
                    qty = int(rng.integers(3, 40))
                    rows.append(dict(brand=st.split("-")[0], store=st, business_date=str(day), daypart=dp,
                                     menu_item_id=mi.menu_item_id, menu_item=mi["name"], category=mi.category,
                                     qty=qty, unit_price=float(mi.price), gross=round(qty * float(mi.price), 2),
                                     discount=0.0, comp=0.0, void_qty=0, net=round(qty * float(mi.price), 2),
                                     promo_flag=False))
        df = pd.DataFrame(rows); target = "staging_sales_line"; detected = f"live sync · {spec['name']}"
    elif connector_id == "sap":
        inv = db.read_sql("SELECT store, sku, uom FROM inventory_snapshot").drop_duplicates(["store", "sku"]).sample(60)
        inv["on_hand_qty"] = rng.integers(10, 500, len(inv))
        inv["as_of"] = str((pd.Timestamp.utcnow()).date())
        df = inv; target = "staging_inventory_snapshot"; detected = "live sync · SAP ERP"
    elif connector_id == "weather":
        w = db.read_sql("SELECT DISTINCT region FROM weather")
        w["business_date"] = str((pd.Timestamp.utcnow()).date()); w["temp_f"] = rng.integers(40, 95, len(w))
        w["is_rain"] = rng.integers(0, 2, len(w)); w["forecast_temp_f"] = w["temp_f"]
        df = w; target = "staging_weather"; detected = "live sync · Weather feed"
    else:
        return {"error": f"{spec['name']} has no live pull configured"}

    db.load_df(df, target)
    with db.engine().begin() as cx:
        cx.execute(_upload_tbl.insert(), {"filename": f"{connector_id}_sync", "detected": detected,
                                          "target_table": target, "rows": int(len(df)), "uploaded": _now()})
        cx.execute(text("UPDATE source_connection SET status='connected' WHERE connector_id=:i"), {"i": connector_id})
    return {"connector": spec["name"], "pulled_rows": int(len(df)), "staged_into": target, "detected": detected,
            "note": "Live batch staged. Promote + run the pipeline to forecast against it."}


def promote(target_table: str) -> dict:
    """Append a staged upload into its canonical CSV so the next pipeline run includes it."""
    from app.config import DATA_DIR
    fmap = _PROMOTABLE()
    if target_table not in fmap:
        return {"error": f"{target_table} is not promotable (supported: {list(fmap)})"}
    if not db.table_exists(target_table):
        return {"error": "staging table not found"}
    staged = db.read_sql(f"SELECT * FROM {target_table}")
    path = DATA_DIR / "canonical" / fmap[target_table]
    existing = pd.read_csv(path)
    aligned = staged.reindex(columns=existing.columns)     # keep canonical schema, fill missing with NaN
    combined = pd.concat([existing, aligned], ignore_index=True)
    combined.to_csv(path, index=False)
    return {"promoted_into": fmap[target_table], "rows_appended": int(len(staged)),
            "canonical_rows_now": int(len(combined)),
            "note": "Appended to the canonical file. Re-run the pipeline to forecast against it."}
