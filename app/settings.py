"""Runtime settings — UI-editable overrides on top of env config.

Env (config.py) provides the defaults; a small `app_setting` key/value table
overrides them at run time so the Settings page can change the forecast backend,
horizon, and tuning knobs without a redeploy. The pipeline stages read effective
values from here.
"""
from __future__ import annotations

from sqlalchemy import Column, MetaData, String, Table, text

from app import db
from app.config import CFG

_MD = MetaData()
_tbl = Table("app_setting", _MD, Column("key", String, primary_key=True), Column("value", String))

# key -> (label, type, options, default)
SPEC = {
    "forecast_backend":  ("Forecast backend", "select", ["seasonal", "chronos"], lambda: CFG.FORECAST_BACKEND),
    "forecast_horizon":  ("Forecast horizon (days)", "number", None, lambda: CFG.FORECAST_HORIZON),
    "anomaly_lookback":  ("Anomaly lookback (days · 0 = full history)", "number", None, lambda: 0),
    "par_weeks":         ("Par level (weeks of demand)", "number", None, lambda: CFG.PAR_WEEKS),
    "service_level_z":   ("Safety-stock z (service level)", "number", None, lambda: CFG.SERVICE_LEVEL_Z),
}


def ensure():
    _MD.create_all(db.engine(), tables=[_tbl], checkfirst=True)


def _raw() -> dict:
    ensure()
    return {r["key"]: r["value"] for r in db.read_sql("SELECT key, value FROM app_setting").to_dict("records")}


def effective(key: str):
    val = _raw().get(key)
    if val is not None:
        return val
    d = SPEC[key][3]()
    return d


def eff_int(key: str, default: int) -> int:
    try:
        return int(float(effective(key)))
    except Exception:
        return default


def eff_float(key: str, default: float) -> float:
    try:
        return float(effective(key))
    except Exception:
        return default


def eff_str(key: str, default: str) -> str:
    v = effective(key)
    return str(v) if v is not None else default


def get_all() -> dict:
    raw = _raw()
    fields = []
    for k, (label, typ, opts, dfl) in SPEC.items():
        fields.append({"key": k, "label": label, "type": typ, "options": opts,
                       "value": raw.get(k, str(dfl())), "is_default": k not in raw})
    return {"fields": fields}


def set_many(values: dict) -> dict:
    ensure()
    with db.engine().begin() as cx:
        for k, v in (values or {}).items():
            if k not in SPEC:
                continue
            cx.execute(text("DELETE FROM app_setting WHERE key=:k"), {"k": k})
            if v not in (None, ""):
                cx.execute(_tbl.insert(), {"key": k, "value": str(v)})
    return get_all()
