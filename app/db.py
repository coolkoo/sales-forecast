"""Database layer — SQLAlchemy Core, portable across SQLite (local) and Postgres (server).

Canonical/dimension tables are created by bulk-loading DataFrames (schema inferred,
matching AURIX's auto-schema philosophy). The analytics *output* tables (forecast,
anomaly, buying_suggestion, prep_plan) are declared explicitly so downstream
readers have a stable contract.
"""
from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import pandas as pd
from sqlalchemy import (Boolean, Column, Date, DateTime, Float, Integer,
                        MetaData, String, Table, create_engine, text)
from sqlalchemy.engine import Engine

from app.config import CFG

# NOTE: pandas 3.0's to_sql/read_sql SQLAlchemy detection is broken in this env, so
# all DB I/O goes through SQLAlchemy Core directly (also cleaner + portable to PG).

META = MetaData()

# --- output tables (stable contract) --------------------------------------
forecast = Table(
    "forecast", META,
    Column("brand", String), Column("store", String), Column("daypart", String),
    Column("menu_item_id", String), Column("target_date", Date),
    Column("run_date", Date), Column("horizon_day", Integer),
    Column("p05", Float), Column("p50", Float), Column("p95", Float),
    Column("expected_units", Float), Column("expected_net", Float),
    Column("backend", String),
)

anomaly = Table(
    "anomaly", META,
    Column("anomaly_id", String), Column("detected_on", Date),
    Column("type", String), Column("brand", String), Column("store", String),
    Column("target", String), Column("daypart", String),
    Column("start_date", Date), Column("end_date", Date),
    Column("severity", Float), Column("expected", Float), Column("observed", Float),
    Column("score", Float), Column("description", String),
    Column("council_verdict", String), Column("council_confidence", Float), Column("council_note", String),
)

buying_suggestion = Table(
    "buying_suggestion", META,
    Column("run_date", Date), Column("brand", String), Column("store", String),
    Column("sku", String), Column("uom", String),
    Column("forecast_usage", Float), Column("on_hand", Float),
    Column("par_level", Float), Column("safety_stock", Float),
    Column("reorder_qty", Float), Column("lead_time_days", Integer),
    Column("unit_cost", Float), Column("order_cost", Float), Column("cover_date", Date),
)

prep_plan = Table(
    "prep_plan", META,
    Column("run_date", Date), Column("service_date", Date),
    Column("brand", String), Column("store", String), Column("daypart", String),
    Column("menu_item_id", String), Column("menu_item", String),
    Column("forecast_units", Float), Column("prep_units", Float),
    Column("sku", String), Column("raw_qty_needed", Float), Column("uom", String),
    Column("frozen", Boolean), Column("thaw_hours", Float),
    Column("pull_from_freezer_at", DateTime), Column("cook_yield", Float),
)

OUTPUT_TABLES = [forecast, anomaly, buying_suggestion, prep_plan]


@lru_cache(maxsize=1)
def engine() -> Engine:
    kw = {"future": True}
    if CFG.is_sqlite():
        kw["connect_args"] = {"check_same_thread": False}
    return create_engine(CFG.DATABASE_URL, **kw)


def init_output_tables(drop: bool = True) -> None:
    eng = engine()
    if drop:
        META.drop_all(eng, tables=OUTPUT_TABLES)
    META.create_all(eng, tables=OUTPUT_TABLES)


def _coerce(v):
    """numpy/pandas scalar -> native python (drivers reject numpy types / NaN)."""
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if isinstance(v, (np.integer,)):
        return int(v)
    if isinstance(v, (np.floating,)):
        return None if math.isnan(float(v)) else float(v)
    if isinstance(v, (np.bool_,)):
        return bool(v)
    if v is pd.NaT:
        return None
    return v


def _sqla_type(dtype):
    if pd.api.types.is_bool_dtype(dtype):
        return Boolean
    if pd.api.types.is_integer_dtype(dtype):
        return Integer
    if pd.api.types.is_float_dtype(dtype):
        return Float
    return String


def load_df(df: pd.DataFrame, table: str, if_exists: str = "replace") -> int:
    """Create (schema inferred from dtypes) and bulk-load a DataFrame as a table."""
    md = MetaData()
    tbl = Table(table, md, *[Column(str(c), _sqla_type(dt)) for c, dt in df.dtypes.items()])
    eng = engine()
    with eng.begin() as conn:
        if if_exists == "replace":
            tbl.drop(conn, checkfirst=True)
        tbl.create(conn, checkfirst=True)
        records = [{k: _coerce(v) for k, v in row.items()} for row in df.to_dict("records")]
        if records:
            conn.execute(tbl.insert(), records)
    return len(df)


def write_df(df: pd.DataFrame, table: str) -> int:
    """Append rows to an existing declared output table."""
    if df.empty:
        return 0
    tbl = META.tables[table]
    records = [{k: _coerce(v) for k, v in row.items()} for row in df.to_dict("records")]
    with engine().begin() as conn:
        conn.execute(tbl.insert(), records)
    return len(df)


def read_sql(sql: str, params: dict | None = None) -> pd.DataFrame:
    with engine().connect() as c:
        res = c.execute(text(sql), params or {})
        cols = list(res.keys())
        return pd.DataFrame(res.fetchall(), columns=cols)


def table_exists(name: str) -> bool:
    from sqlalchemy import inspect
    return name in inspect(engine()).get_table_names()
