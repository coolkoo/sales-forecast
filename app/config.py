"""Central configuration — env-driven, no hard dependencies.

Local dev defaults to SQLite + the seasonal fallback forecaster so the whole
pipeline runs with zero infrastructure. On the server (192.168.50.85) the same
code runs against Postgres with the real Chronos-2 backend on GPU — everything is
switched by environment variables (see deploy/.env.example).
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"


def _load_env(path: Path) -> None:
    """Minimal .env loader (mirrors traderific's — no extra deps)."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())


_load_env(ROOT / ".env")
_load_env(ROOT / "deploy" / ".env")


def _b(key: str, default: bool) -> bool:
    return os.environ.get(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


class Config:
    # --- storage ---
    DATABASE_URL = os.environ.get("SF_DATABASE_URL", f"sqlite:///{ROOT / 'sales_forecast.db'}")

    # --- forecasting ---
    # backend: "chronos" (real Chronos-2, needs torch+GPU) or "seasonal" (zero-dep fallback)
    FORECAST_BACKEND = os.environ.get("SF_FORECAST_BACKEND", "seasonal")
    CHRONOS_MODEL_ID = os.environ.get("SF_CHRONOS_MODEL_ID", "amazon/chronos-2")
    CHRONOS_DEVICE = os.environ.get("SF_CHRONOS_DEVICE", "cuda")   # "cuda" | "cuda:0" | "cpu"
    FORECAST_HORIZON = int(os.environ.get("SF_FORECAST_HORIZON", "14"))   # days
    FORECAST_LOOKBACK = int(os.environ.get("SF_FORECAST_LOOKBACK", "365"))  # days of history fed
    USE_COVARIATES = _b("SF_USE_COVARIATES", True)   # restaurant demand is covariate-driven
    MIN_HISTORY_DAYS = int(os.environ.get("SF_MIN_HISTORY_DAYS", "90"))

    # --- anomaly detection ---
    ANOMALY_BAND = float(os.environ.get("SF_ANOMALY_BAND", "0.90"))   # forecast quantile band (p05..p95 -> 0.90)
    ANOMALY_LOOKBACK = int(os.environ.get("SF_ANOMALY_LOOKBACK", "45"))  # days scored for residual anomalies
    VOID_COMP_Z = float(os.environ.get("SF_VOID_COMP_Z", "3.0"))       # z-threshold for void/comp spikes
    INVENTORY_VARIANCE_PCT = float(os.environ.get("SF_INVENTORY_VARIANCE_PCT", "0.20"))  # |variance| flag (injected 25-45%; noise ~4%)

    # --- inventory / prep ---
    PAR_WEEKS = float(os.environ.get("SF_PAR_WEEKS", "1.5"))           # par = this many weeks of demand
    SERVICE_LEVEL_Z = float(os.environ.get("SF_SERVICE_LEVEL_Z", "1.28"))  # safety stock ~90% service

    # --- monitoring cadence ---
    # Intra-day anomaly-detection interval (success metric #3: detect < 2h). The
    # scheduler re-scans the latest data every this-many minutes; forecasts still
    # refresh nightly. 60 min => detection lands well inside the 2-hour target.
    DETECT_INTERVAL_MIN = int(os.environ.get("SF_DETECT_INTERVAL_MIN", "60"))
    # Transparent, adjustable model behind the analyst-time-saved metric (#4).
    MANUAL_MINUTES_PER_INVESTIGATION = int(os.environ.get("SF_MANUAL_MIN_PER_INVESTIGATION", "35"))

    # --- service ---
    API_HOST = os.environ.get("SF_API_HOST", "0.0.0.0")
    API_PORT = int(os.environ.get("SF_API_PORT", "8900"))
    MCP_PORT = int(os.environ.get("SF_MCP_PORT", "8901"))
    MCP_TOKEN = os.environ.get("SF_MCP_TOKEN", "")     # bearer token for the MCP endpoint (set on server)
    NODE_TOKEN = os.environ.get("SF_NODE_TOKEN", "")   # token store-node health agents present to /api/monitor/report (empty = accept)
    # Demo mode: present a server-side virtual agent for stores without a real one, so
    # remediation dispatches through the real command channel (Remediating→Resolved) and
    # auto-confirms. Set SF_DEMO_AGENT=0 in production once real store agents are deployed.
    DEMO_AGENT = _b("SF_DEMO_AGENT", True)

    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


CFG = Config()
