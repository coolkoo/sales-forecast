# KFC Vietnam — Sales Forecasting & Analytics Platform

A production-shaped demand-forecasting, anomaly-detection and operations platform for a
multi-store restaurant chain (themed as **KFC Vietnam**). It ingests POS/ERP data into a
medallion warehouse, forecasts every `store × item × daypart` series with **Chronos-2** on
GPU, validates anomalies with a council, plans buying and prep, and surfaces everything
through a 9-page dashboard, natural-language querying, an AI report, and BI feeds — with
role-based access control and full English/Vietnamese localization.

---

## Highlights

- **Forecasting** — Chronos-2 multi-series (GPU) with a zero-dependency seasonal fallback;
  restaurant covariates: weather, day-of-week, **VN public holidays + retail driver days**
  (Children's/Women's/Teachers' Day, Mid-Autumn), a **multi-week Tết regime**, promos, and a
  **new-store maturation** (store-age) covariate.
- **Anomaly detection** — 5 types (POS outage, demand spike/drop, void/comp fraud, inventory
  variance), each with an **LLM/heuristic council** verdict + confidence, scored vs ground truth.
- **Operations** — BOM-driven buying / par / reorder POs, thaw-cook-prep scheduling,
  same-store-sales (comparable-store YoY).
- **Ask (NL→SQL)** — plain-English questions → safe read-only SQL + a semantic data catalog.
- **Backtest** — walk-forward calibration (MAE / MAPE / band coverage / skill vs naive).
- **Reports** — 12 parameterised report templates (CSV + print) + an **AI executive report**
  (LLM-optional, built-in analyst fallback), EN/VI.
- **PowerBI feeds** — OData v4 + CSV exports + read-only Postgres; connect existing BI, no migration.
- **Alerts** — route council-confirmed anomalies to teams via email (SMTP) / Teams-Slack webhook / log.
- **Sources** — connector registry (Simphony/SAP/Toast/Square/Weather/File) + upload → promote → re-forecast.
- **Data lineage** — bronze → silver → gold medallion asset graph with live counts + freshness.
- **RBAC** — login + roles (admin / manager / analyst / viewer), enforced server-side.
- **Localization** — full EN/VI toggle; **VND** throughout.
- **MCP** — drive it from Claude/any MCP client (read + confirm-gated act tools).

---

## Architecture

```
 POS / ERP                Medallion warehouse            Forecast engine (from traderific)
 Simphony · SAP   ──▶  bronze → silver → gold(PG)  ──▶  Chronos-2 multi-series (GPU) / seasonal
 Toast · Square        + conform (raw→canonical)         + restaurant covariates + backtest
 Weather · File        + canonical restaurant schema      + anomaly council
      │                        │                                    │
      ▼                        ▼                                    ▼
   Sources UI          Analytics / NL→SQL / lineage       Forecasts · anomalies · buying ·
   (connect/upload)                                        thaw-cook-prep · par · SSS
                                   │                                │
                                   └──▶  Dashboard (9 pages) · Reports · Alerts · MCP · PowerBI feeds
                                        with RBAC + EN/VI
```

The object store is the mounted `data/` volume (MinIO-ready); orchestration is a lightweight
pipeline runner + background job scheduler.

---

## Tech stack

| Layer | Tech |
|---|---|
| API / server | FastAPI + Uvicorn |
| Forecaster | Chronos-2 (`chronos-forecasting` + torch, GPU) · seasonal fallback |
| Warehouse | PostgreSQL (server) / SQLite (local) via SQLAlchemy Core |
| Dashboard | Single-file vanilla-JS SPA (self-contained SVG charts, no external deps) |
| MCP | FastMCP (read + act tools) |
| Deploy | Docker Compose (bundled Postgres) on a GPU host |

---

## Quick start (local — SQLite, seasonal backend, no infra)

```bash
pip install -r requirements.txt
python data_generator/generate_pos_data.py      # generate the synthetic KFC-VN dataset (~10s)
python -m app.pipeline                            # ingest → forecast → anomaly → buying → prep → alerts
uvicorn app.api.server:api --port 8900            # dashboard at http://localhost:8900/
python -m app.mcp_server                          # optional: MCP on :8901
```

The dataset is **not committed** (deterministic + large) — regenerate it with the generator.
Run a single pipeline stage with e.g. `python -m app.pipeline forecast`.

Sign in with a demo account (see **RBAC** below).

---

## Deploy (GPU server, Docker + Postgres)

```bash
cp deploy/.env.example deploy/.env      # set POSTGRES_PASSWORD, SF_MCP_TOKEN, etc.
./deploy/deploy.sh                       # rsync → host, docker compose up -d, health check
```

- Dashboard `http://<host>:8900/` · API `:8900/api/*` · MCP `:8901/mcp`
- Chronos-2 upgrade: set `SF_FORECAST_BACKEND=chronos`, `SF_CHRONOS_DEVICE=cuda:0`, add
  `requirements-forecast.txt` to the image + the GPU reservation in `deploy/docker-compose.yml`.
- Nightly refresh: `docker compose exec app python -m app.pipeline` (cron/systemd timer).

> ⚠️ **Security**: served over plain HTTP on the LAN — put a TLS reverse proxy in front for
> production. RBAC credentials otherwise travel in cleartext.

The KFC logo lives at `app/dashboard/kfc-logo.svg` (referenced via `/assets/kfc-logo.svg`).

---

## RBAC & roles

| Role | Permissions |
|---|---|
| **admin** | view · operate · admin (settings, LLM/alert config, **user management**) |
| **manager** | view · operate (run pipeline, sync sources, act tools) |
| **analyst** | view (analytics + reports) |
| **viewer** | view (read-only) |

Enforced by API middleware on every route; nav + controls gate on the frontend. Demo accounts
(password = username — **change in production** via Settings → Users & roles): `admin`,
`manager`, `analyst`, `viewer`.

---

## Reports & PowerBI

- **Reports page** — 12 templates (sales summary / by category / by daypart, menu-item &
  store performance, daily trend, void & comp, same-store sales, forecast, anomaly log, buying,
  prep) with date/store filters, CSV export, print; plus an AI executive report.
- **PowerBI** — connect to the OData v4 feed (`/odata/`), the CSV exports (`/api/export/<Entity>.csv`),
  or read-only Postgres. Get connection details on the Reports/Sources page (`/api/feeds/info`).
- **LLM** for the AI report/council is configurable in **Settings → LLM / AI endpoint**
  (Anthropic or any OpenAI-compatible URL); a built-in analyst is used otherwise.

---

## Project structure

```
app/
  api/server.py         FastAPI: JSON API, RBAC middleware, dashboard + asset routes
  auth.py               users, sessions, roles (RBAC)
  db.py                 SQLAlchemy Core layer (SQLite local / Postgres server)
  config.py settings.py env config + UI-editable runtime settings
  ingest/               load.py (dims+facts), conform.py (raw→canonical + validate)
  forecast/             features.py (covariates), backends.py (chronos+seasonal), service.py, backtest.py
  anomaly/detect.py     residual + structural detection, scored vs ground truth
  council.py            multi-judge anomaly validation (LLM-optional)
  inventory/            planning.py (buying/par), prep.py (thaw/cook)
  analytics/            summary, pages (stores/SSS/buying), detail (drill-downs)
  nlsql.py              NL→SQL + semantic catalog
  alerts.py             anomaly comms (email/webhook/log routing)
  feeds.py reports.py aireport.py   PowerBI feeds · report templates · AI report
  lake.py               medallion lineage
  jobs.py               background pipeline runner
  mcp_server.py         MCP tools (read + act)
  dashboard/index.html  the SPA (+ kfc-logo.svg)
  pipeline.py           orchestrates all stages
data_generator/         deterministic synthetic KFC-Vietnam dataset (config.py + generator)
deploy/                 Dockerfile, docker-compose.yml, .env.example, deploy.sh
PRD.md                  the plan
```

---

## Notes

- The dataset is **synthetic with known ground truth** on purpose (validates each phase);
  swap in real Simphony/SAP exports by pointing `app/ingest/conform.py` at them.
- pandas 3.0's `to_sql`/`read_sql` SQLAlchemy detection is unreliable, so all DB I/O goes
  through SQLAlchemy Core directly (`app/db.py`) — portable across SQLite/Postgres.
- Alerts (email/webhook), the AI report LLM mode, and direct PowerBI Postgres access all
  activate once you provide the respective credentials; everything works without them via
  log/heuristic/feed fallbacks.
