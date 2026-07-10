"""FastAPI service — JSON API + the dashboard.

Run:  uvicorn app.api.server:api --host 0.0.0.0 --port 8900
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from app import db
from app.config import CFG

api = FastAPI(title="sales-forecast", version="0.1.0")
DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard" / "index.html"

# --- RBAC: enforce auth + role on every API route (open list bypasses) -------
_OPEN = {"/", "/api/health", "/api/auth/login", "/api/auth/signup", "/api/auth/me", "/api/auth/logout",
         "/favicon.ico", "/api/monitor/report", "/api/monitor/commands", "/api/monitor/command_result"}
# ^ the /monitor/report|commands|command_result routes are the store-node agent channel (node-token guarded)
_OPEN_PREFIX = ("/assets/", "/odata", "/api/export/")   # read-only BI feeds + static assets
_ADMIN_WRITE = ("/api/settings", "/api/alerts/config", "/api/report/ai/config")


@api.middleware("http")
async def rbac(request: Request, call_next):
    path, method = request.url.path, request.method
    if path in _OPEN or path.startswith(_OPEN_PREFIX) or not path.startswith(("/api", "/odata")):
        return await call_next(request)
    from app import auth
    user = auth.user_from_token(request.cookies.get("sf_session"))
    if not user:
        return JSONResponse({"error": "authentication required"}, status_code=401)
    if path.startswith("/api/auth/users"):
        perm = "admin"
    elif method == "GET":
        perm = "view"
    elif any(path.startswith(a) for a in _ADMIN_WRITE):
        perm = "admin"
    else:
        perm = "operate"
    if perm not in user["permissions"]:
        return JSONResponse({"error": "forbidden", "need": perm, "role": user["role"]}, status_code=403)
    request.state.user = user
    return await call_next(request)


class LoginReq(BaseModel):
    username: str
    password: str


@api.post("/api/auth/login")
def auth_login(req: LoginReq, response: Response):
    from app import auth
    status, tok = auth.authenticate(req.username, req.password)
    if status != "ok":
        code = 403 if status == "pending" else 401
        msg = "Account pending admin approval" if status == "pending" else "Invalid username or password"
        return JSONResponse({"error": msg, "status": status}, status_code=code)
    response.set_cookie("sf_session", tok, httponly=True, samesite="lax", max_age=43200, path="/")
    return {"ok": True, "user": auth.user_from_token(tok)}


@api.post("/api/auth/signup")
def auth_signup(req: LoginReq):
    from app import auth
    return auth.signup(req.username, req.password)


@api.post("/api/auth/logout")
def auth_logout(request: Request, response: Response):
    from app import auth
    auth.logout(request.cookies.get("sf_session"))
    response.delete_cookie("sf_session", path="/")
    return {"ok": True}


@api.get("/api/auth/me")
def auth_me(request: Request):
    from app import auth
    u = auth.user_from_token(request.cookies.get("sf_session"))
    return {"authenticated": bool(u), **(u or {})}


@api.get("/api/auth/users")
def auth_users():
    from app import auth
    return auth.list_users()


class UserReq(BaseModel):
    username: str
    role: str
    password: str = ""
    store: str = ""
    active: bool = True


@api.post("/api/auth/users")
def auth_users_upsert(req: UserReq):
    from app import auth
    return auth.upsert_user(req.username, req.role, req.password or None, req.store or None, req.active)


@api.post("/api/auth/users/delete")
def auth_users_delete(req: dict):
    from app import auth
    return auth.delete_user(req.get("username", ""))


def _rows(sql: str, params: dict | None = None):
    import math
    df = db.read_sql(sql, params)
    for c in df.columns:
        if df[c].dtype.kind in ("M", "m"):
            df[c] = df[c].astype(str)
    recs = df.to_dict("records")
    for r in recs:   # NaN/inf are not JSON-compliant
        for k, v in r.items():
            if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
                r[k] = None
    return recs


@api.get("/api/health")
def health():
    ok = db.table_exists("forecast")
    from app import scheduler
    return {"status": "ok" if ok else "empty", "backend": CFG.FORECAST_BACKEND,
            "database": "postgres" if not CFG.is_sqlite() else "sqlite",
            "detect_interval_min": CFG.DETECT_INTERVAL_MIN,
            "time_to_detect_hours": round(CFG.DETECT_INTERVAL_MIN / 60.0, 2),
            "last_detect_run": scheduler.last_run()}


@api.get("/api/summary")
def summary():
    def count(t):
        return int(db.read_sql(f"SELECT COUNT(*) c FROM {t}")["c"].iloc[0]) if db.table_exists(t) else 0
    run_date = None
    if db.table_exists("forecast"):
        r = db.read_sql("SELECT MAX(run_date) d FROM forecast")["d"]
        run_date = str(r.iloc[0]) if len(r) else None
    ano = _rows("SELECT type, COUNT(*) n FROM anomaly GROUP BY type") if db.table_exists("anomaly") else []
    return {
        "run_date": run_date, "backend": CFG.FORECAST_BACKEND,
        "counts": {t: count(t) for t in ["sales_line", "forecast", "anomaly",
                                         "buying_suggestion", "prep_plan"]},
        "anomaly_by_type": {r["type"]: r["n"] for r in ano},
    }


@api.get("/api/stores")
def stores():
    return _rows("SELECT store, brand, brand_name, region, format FROM store ORDER BY store")


@api.get("/api/items")
def items(store: str | None = None):
    if store:
        return _rows("SELECT DISTINCT menu_item_id FROM forecast WHERE store=:s ORDER BY menu_item_id",
                     {"s": store})
    return _rows("SELECT menu_item_id, brand, name, category, price FROM menu_item ORDER BY menu_item_id")


@api.get("/api/forecast")
def forecast(store: str = Query(...), daypart: str | None = None, item: str | None = None,
             days: int = 14):
    sql = ("SELECT brand, store, daypart, menu_item_id, target_date, horizon_day, "
           "p05, p50, p95, expected_units, expected_net, backend FROM forecast WHERE store=:s")
    p = {"s": store}
    if daypart:
        sql += " AND daypart=:d"; p["d"] = daypart
    if item:
        sql += " AND menu_item_id=:i"; p["i"] = item
    sql += " AND horizon_day<=:n ORDER BY menu_item_id, daypart, target_date"
    p["n"] = days
    return _rows(sql, p)


@api.get("/api/forecast/store_daily")
def store_daily(store: str = Query(...)):
    """Total forecast net revenue per day for a store, with a p05-p95 band (headline chart)."""
    df = db.read_sql("SELECT target_date, expected_units, expected_net, p05, p95 "
                     "FROM forecast WHERE store=:s", {"s": store})
    if df.empty:
        return []
    for c in ("expected_units", "expected_net", "p05", "p95"):
        df[c] = df[c].astype(float)
    price = df["expected_net"] / df["expected_units"].clip(lower=1e-9)
    df["lo"] = df["p05"] * price
    df["hi"] = df["p95"] * price
    g = df.groupby("target_date", as_index=False).agg(net=("expected_net", "sum"),
                                                      lo=("lo", "sum"), hi=("hi", "sum"))
    g["target_date"] = g["target_date"].astype(str)
    return g.round(2).to_dict("records")


@api.get("/api/anomalies")
def anomalies(type: str | None = None):
    sql = "SELECT * FROM anomaly"
    p = {}
    if type:
        sql += " WHERE type=:t"; p["t"] = type
    sql += " ORDER BY score DESC"
    rows = _rows(sql, p)
    import json as _json
    from app import feedback
    fb = feedback.for_anomalies()
    for r in rows if isinstance(rows, list) else []:
        try:
            r["drivers"] = _json.loads(r["drivers"]) if r.get("drivers") else []
        except Exception:
            r["drivers"] = []
        v = fb.get(r.get("anomaly_id"))
        r["feedback"] = v["verdict"] if v else None
    return rows


@api.post("/api/anomalies/feedback")
def anomaly_feedback(body: dict = Body(...)):
    from app import feedback
    return feedback.record(str(body.get("anomaly_id", "")), str(body.get("verdict", "")),
                           str(body.get("note", "")), str(body.get("reviewer", "")))


@api.get("/api/buying")
def buying(store: str | None = None, reorder_only: bool = True):
    sql = "SELECT * FROM buying_suggestion"
    conds, p = [], {}
    if store:
        conds.append("store=:s"); p["s"] = store
    if reorder_only:
        conds.append("reorder_qty > 0")
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    sql += " ORDER BY order_cost DESC"
    return _rows(sql, p)


@api.get("/api/prep")
def prep(store: str = Query(...), daypart: str | None = None):
    sql = "SELECT * FROM prep_plan WHERE store=:s"
    p = {"s": store}
    if daypart:
        sql += " AND daypart=:d"; p["d"] = daypart
    sql += " ORDER BY daypart, pull_from_freezer_at"
    return _rows(sql, p)


@api.get("/api/prep/thaw")
def thaw(store: str = Query(...)):
    """Frozen-pull schedule for a store (the thaw board)."""
    return _rows("SELECT service_date, daypart, sku, SUM(raw_qty_needed) qty, uom, thaw_hours, "
                 "pull_from_freezer_at FROM prep_plan WHERE store=:s AND frozen "
                 "AND pull_from_freezer_at IS NOT NULL "
                 "GROUP BY service_date, daypart, sku, uom, thaw_hours, pull_from_freezer_at "
                 "ORDER BY pull_from_freezer_at", {"s": store})


@api.get("/api/analytics/summary")
def analytics_summary(lang: str = "en"):
    from app.analytics import summary as A
    return A.summary(lang)


# ---- alerts ----
@api.get("/api/alerts")
def alerts_get():
    from app import alerts
    return {"config": alerts.config(), "routing": alerts.ROUTING, "recent": alerts.recent()}


@api.post("/api/alerts/config")
def alerts_config(values: dict):
    from app import alerts
    return alerts.set_config(values)


@api.post("/api/alerts/test")
def alerts_test():
    from app import alerts
    return alerts.test()


@api.post("/api/alerts/dispatch")
def alerts_dispatch():
    from app import alerts
    return alerts.dispatch(force=True)


# ---- reports ----
@api.get("/api/reports")
def reports_list():
    from app import reports
    return reports.list_reports()


def _rp(date_from, date_to, store, category):
    return {"date_from": date_from, "date_to": date_to, "store": store, "category": category}


@api.get("/api/report/ai")
def report_ai(lang: str = "en"):
    from app import aireport
    return aireport.generate(lang)


@api.get("/api/report/ai/config")
def report_ai_config():
    from app import aireport
    return aireport.config()


@api.post("/api/report/ai/config")
def report_ai_set(values: dict):
    from app import aireport
    return aireport.set_config(values)


@api.get("/api/llm/health")
def llm_health(request: Request):
    # Live one-token ping so the UI can prove the LLM is really connected (admin only).
    user = getattr(request.state, "user", None)
    if not user or "admin" not in user.get("permissions", []):
        return JSONResponse({"error": "forbidden"}, status_code=403)
    from app import llm
    return llm.health()


@api.get("/api/report/{name}.csv")
def report_csv(name: str, date_from: str = "", date_to: str = "", store: str = "", category: str = ""):
    from app import reports
    from fastapi.responses import Response
    c = reports.csv(name, _rp(date_from, date_to, store, category))
    if c is None:
        return JSONResponse({"error": "unknown report"}, status_code=404)
    return Response(content=c, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@api.get("/api/report/{name}")
def report_run(name: str, date_from: str = "", date_to: str = "", store: str = "", category: str = ""):
    from app import reports
    return reports.run(name, _rp(date_from, date_to, store, category))


# ---- PowerBI feeds (CSV + OData v4) ----
@api.get("/api/feeds/info")
def feeds_info(request: Request):
    from app import feeds
    base = str(request.base_url).rstrip("/")
    return feeds.info(base)


@api.get("/api/export/{name}.csv")
def export_csv(name: str):
    from app import feeds
    from fastapi.responses import Response
    c = feeds.csv(name)
    if c is None:
        return JSONResponse({"error": "unknown view"}, status_code=404)
    return Response(content=c, media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="{name}.csv"'})


@api.get("/odata/")
def odata_root(request: Request):
    from app import feeds
    return feeds.odata_service(str(request.base_url).rstrip("/") + "/odata")


@api.get("/odata/$metadata")
def odata_meta():
    from app import feeds
    from fastapi.responses import Response
    return Response(content=feeds.odata_metadata(), media_type="application/xml")


@api.get("/odata/{name}")
def odata_entity(name: str, request: Request):
    from app import feeds
    r = feeds.odata_entity(name, str(request.base_url).rstrip("/") + "/odata")
    if r is None:
        return JSONResponse({"error": "unknown entity"}, status_code=404)
    return r


@api.get("/api/analytics/dow")
def analytics_dow():
    from app.analytics import summary as A
    return A.chart_dow()


@api.get("/api/analytics/daypart")
def analytics_daypart():
    from app.analytics import summary as A
    return A.chart_daypart()


@api.get("/api/analytics/category")
def analytics_category():
    from app.analytics import summary as A
    return A.chart_category()


@api.get("/api/analytics/top_items")
def analytics_top_items(n: int = 10):
    from app.analytics import summary as A
    return A.chart_top_items(n)


@api.get("/api/analytics/stores")
def analytics_stores():
    from app.analytics import summary as A
    return A.chart_stores()


@api.get("/api/analytics/trend")
def analytics_trend(hist_days: int = 90):
    from app.analytics import summary as A
    return A.chart_trend(hist_days)


@api.get("/api/anomaly_detail")
def anomaly_detail(anomaly_id: str = Query(...)):
    from app.analytics import detail
    return detail.anomaly_detail(anomaly_id)


@api.get("/api/analytics/breakdown")
def analytics_breakdown(by: str = "menu_item", store: str = "", daypart: str = "",
                        category: str = "", menu_item_id: str = ""):
    from app.analytics import detail
    return detail.breakdown(by, store, daypart, category, menu_item_id)


@api.get("/api/analytics/dow_detail")
def analytics_dow_detail(dow: int = Query(...)):
    from app.analytics import detail
    return detail.dow_detail(dow)


@api.get("/api/analytics/day_detail")
def analytics_day_detail(date: str = Query(...)):
    from app.analytics import detail
    return detail.day_detail(date)


class AskReq(BaseModel):
    q: str


@api.post("/api/ask")
def ask(req: AskReq):
    from app import nlsql
    return nlsql.answer(req.q)


@api.post("/api/ask/chat")
def ask_chat(body: dict = Body(...)):
    # Conversational analyst: body = {messages: [{role, content}, ...]}
    from app import nlsql
    return nlsql.chat(body.get("messages") or [])


@api.get("/api/catalog/search")
def catalog_search(q: str = ""):
    from app import nlsql
    return {"results": nlsql.search_catalog(q), "catalog": [{"table": t, "desc": m["desc"]} for t, m in nlsql.CATALOG.items()]}


@api.get("/api/backtest")
def backtest(horizon: int = 14):
    from app.forecast import backtest as bt
    return bt.run(horizon)


@api.get("/api/backtest/store_daily")
def backtest_store_daily(horizon: int = 14):
    from app.forecast import backtest as bt
    return bt.run_store_daily(horizon)


@api.get("/api/metrics/roi")
def metrics_roi():
    from app import metrics
    return metrics.roi()


@api.get("/api/metrics/scorecard")
def metrics_scorecard():
    from app import metrics
    return metrics.scorecard()


# ---- Store-node health monitoring + remediation (/api/monitor/*) ----
@api.get("/api/monitor/fleet")
def monitor_fleet():
    from app import health
    return health.fleet()


@api.get("/api/monitor/store")
def monitor_store(store: str = Query(...)):
    from app import health
    return health.store_detail(store)


@api.get("/api/monitor/events")
def monitor_events(limit: int = 40):
    from app import health
    return health.events(int(limit))


@api.post("/api/monitor/remediate")
def monitor_remediate(request: Request, body: dict = Body(...)):
    from app import health
    user = getattr(request.state, "user", None)
    actor = user["username"] if user else "operator"
    return health.remediate(str(body.get("store", "")), str(body.get("service", "")),
                            str(body.get("action", "")), actor=actor)


def _node_auth(request: Request) -> bool:
    if not CFG.NODE_TOKEN:
        return True
    tok = request.headers.get("x-node-token") or request.headers.get("authorization", "").replace("Bearer ", "")
    return tok == CFG.NODE_TOKEN


@api.post("/api/monitor/report")
def monitor_report(request: Request, body: dict = Body(...)):
    # Store-node agent listener. Token-guarded (open route); accepts a service-status batch.
    if not _node_auth(request):
        return JSONResponse({"error": "invalid node token"}, status_code=401)
    from app import health
    return health.report(body, ip=(request.client.host if request.client else ""))


@api.get("/api/monitor/commands")
def monitor_commands(request: Request, store: str = Query(...)):
    # Agent polls for pending remediation commands (claims them).
    if not _node_auth(request):
        return JSONResponse({"error": "invalid node token"}, status_code=401)
    from app import health
    return health.pending_commands(store)


@api.post("/api/monitor/command_result")
def monitor_command_result(request: Request, body: dict = Body(...)):
    # Agent reports the outcome of a dispatched remediation command.
    if not _node_auth(request):
        return JSONResponse({"error": "invalid node token"}, status_code=401)
    from app import health
    return health.command_result(int(body.get("command_id", 0)), str(body.get("status", "")),
                                 str(body.get("result", "")), actor="agent")


@api.on_event("startup")
def _start_demo_agent():
    # Demo mode: a server-side virtual agent registers presence and auto-confirms dispatched
    # remediation commands, so operators see the real Remediating→Resolved flow without a
    # physical store node. No-op when SF_DEMO_AGENT=0 (real agents deployed).
    if not CFG.DEMO_AGENT:
        return
    import threading
    import time as _t

    def loop():
        from app import health
        while True:
            try:
                health.register_virtual_agents()
                health.process_virtual_commands()
            except Exception:
                pass
            _t.sleep(3)
    threading.Thread(target=loop, daemon=True, name="demo-agent").start()


@api.get("/api/forecast/hindcast")
def forecast_hindcast(store: str = Query(...), item: str = Query(...), daypart: str = "", days: int = 14):
    from app.forecast import backtest as bt
    return bt.hindcast_series(store, item, daypart, int(days))


@api.get("/api/lineage")
def lineage():
    from app import lake
    return lake.lineage()


class SyncReq(BaseModel):
    connector_id: str


@api.post("/api/sources/sync")
def sources_sync(req: SyncReq):
    from app.sources import registry
    return registry.sync(req.connector_id)


@api.get("/api/stores/overview")
def stores_overview():
    from app.analytics import pages
    return pages.stores_overview()


@api.get("/api/analytics/buying_summary")
def buying_summary():
    from app.analytics import pages
    return pages.buying_summary()


@api.get("/api/prep/thaw_all")
def thaw_all():
    from app.analytics import pages
    return pages.thaw_all()


@api.get("/api/analytics/channel_mix")
def analytics_channel_mix(days: int = 90):
    from app.analytics import pages
    return pages.channel_mix(days)


@api.get("/api/analytics/sss")
def sss():
    from app.analytics import pages
    return pages.sss()


@api.get("/api/sources")
def sources():
    from app.sources import registry
    return registry.list_sources()


class ConnectReq(BaseModel):
    connector_id: str
    name: str = ""
    config: dict = {}


@api.post("/api/sources/connect")
def sources_connect(req: ConnectReq):
    from app.sources import registry
    return registry.connect(req.connector_id, req.name, req.config)


@api.post("/api/sources/upload")
async def sources_upload(request: Request, filename: str = Query(...)):
    from app.sources import registry
    content = await request.body()
    return registry.ingest_upload(filename, content)


class PromoteReq(BaseModel):
    target_table: str


@api.post("/api/sources/promote")
def sources_promote(req: PromoteReq):
    from app.sources import registry
    return registry.promote(req.target_table)


@api.get("/api/settings")
def get_settings():
    from app import settings
    return settings.get_all()


@api.post("/api/settings")
def save_settings(values: dict):
    from app import settings
    return settings.set_many(values)


@api.post("/api/pipeline/run")
def pipeline_run():
    from app import jobs
    return jobs.run_async()


@api.get("/api/pipeline/status")
def pipeline_status():
    from app import jobs
    return jobs.status()


@api.get("/assets/{fname}")
def asset(fname: str):
    # serve static brand assets (logo, etc.) from the dashboard dir; extension-whitelisted, no traversal
    if "/" in fname or "\\" in fname or not fname.rsplit(".", 1)[-1].lower() in ("svg", "png", "jpg", "jpeg", "ico", "webp"):
        return JSONResponse({"error": "not allowed"}, status_code=404)
    p = DASHBOARD.parent / fname
    if not p.exists():
        return JSONResponse({"error": "not found"}, status_code=404)
    media = {"svg": "image/svg+xml", "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
             "ico": "image/x-icon", "webp": "image/webp"}[fname.rsplit(".", 1)[-1].lower()]
    return FileResponse(str(p), media_type=media)


@api.get("/")
def dashboard():
    if DASHBOARD.exists():
        # no-cache: browsers always revalidate the single-file SPA (ETag → 304 when
        # unchanged), so a redeploy is picked up on the next load, never served stale.
        return FileResponse(str(DASHBOARD), headers={"Cache-Control": "no-cache, must-revalidate"})
    return JSONResponse({"error": "dashboard not found"}, status_code=404)
