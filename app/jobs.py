"""Background pipeline runner — lets the UI trigger a re-run and poll status.

Runs `pipeline.run_all()` on a daemon thread (single in-flight run), tracking
state in a module global. Single-process uvicorn, so the global is the source of
truth; status is polled by the Settings / Sources pages.
"""
from __future__ import annotations

import threading
import time

_LOCK = threading.Lock()
_STATE = {"state": "idle", "started": None, "finished": None, "summary": None, "error": None}


def status() -> dict:
    return dict(_STATE)


def _compact(summary: dict) -> dict:
    """Trim run_all()'s per-stage output to headline numbers for the UI."""
    out = {}
    fc = summary.get("forecast", {})
    an = summary.get("anomaly", {})
    inv = summary.get("inventory", {})
    out["backend"] = fc.get("backend")
    out["series_forecast"] = fc.get("series_forecast")
    out["anomalies"] = an.get("detected")
    out["skus_to_reorder"] = inv.get("skus_to_reorder")
    out["seconds"] = round(sum(v.get("_seconds", 0) for v in summary.values() if isinstance(v, dict)), 1)
    return out


def _run():
    try:
        from app.pipeline import run_all
        summary = run_all()
        with _LOCK:
            _STATE.update(state="done", finished=time.strftime("%Y-%m-%d %H:%M:%S"),
                          summary=_compact(summary), error=None)
    except Exception as e:
        with _LOCK:
            _STATE.update(state="error", finished=time.strftime("%Y-%m-%d %H:%M:%S"), error=str(e))


def run_async() -> dict:
    with _LOCK:
        if _STATE["state"] == "running":
            return {"state": "running", "note": "already running"}
        _STATE.update(state="running", started=time.strftime("%Y-%m-%d %H:%M:%S"),
                      finished=None, summary=None, error=None)
    threading.Thread(target=_run, daemon=True).start()
    return {"state": "running"}
