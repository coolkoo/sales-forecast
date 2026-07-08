# Architecture

How the platform is put together: the data flow from POS to plan, the medallion
warehouse, the forecast engine, and the runtime services. Every diagram below is
Mermaid and renders on GitHub.

## 1. System context

```mermaid
flowchart TB
  subgraph sources["Data sources"]
    POS["POS — Oracle Simphony / Toast / Square"]
    ERP["ERP — SAP (menu, BOM, inventory, POs)"]
    WX["Weather feed"]
    FILE["Manual CSV/JSON upload"]
  end

  subgraph platform["Sales-Forecast platform"]
    LAKE[("Medallion warehouse<br/>bronze · silver · gold")]
    PIPE["Pipeline<br/>ingest → forecast → anomaly → inventory → prep → alerts"]
    API["FastAPI service :8900<br/>dashboard · /api · /odata · auth"]
    MCP["MCP server :8901<br/>agent tools"]
  end

  subgraph consumers["Consumers"]
    UI["Analysts & managers<br/>(dashboard SPA)"]
    BI["PowerBI / Excel<br/>(OData + CSV)"]
    AGENT["AI agents<br/>(MCP)"]
    NOTIFY["Email · Teams · Slack"]
  end

  POS & ERP & WX & FILE --> LAKE
  LAKE --> PIPE --> LAKE
  LAKE --> API
  API --> UI
  API --> BI
  MCP --> AGENT
  PIPE --> NOTIFY
  LAKE --> MCP
```

## 2. The medallion warehouse

Data is refined in three layers. Bronze is raw as‑received; silver is the conformed
canonical model; gold is the analytics/plan output the app reads. This is the same
bronze→silver→gold pattern used across the data platform, stored today as tables in
SQLite/Postgres over a mounted `data/` volume (MinIO‑ready if object storage is added).

```mermaid
flowchart LR
  subgraph bronze["🥉 Bronze — raw"]
    B1["check_detail (raw)<br/>Simphony export"]
  end
  subgraph silver["🥈 Silver — conformed"]
    S1["sales_line"]
    S2["menu_item"]
    S3["recipe_bom"]
    S4["ingredient"]
    S5["inventory_snapshot"]
    S6["purchase_order"]
    S7["weather"]
  end
  subgraph gold["🥇 Gold — business-ready"]
    G1["forecast<br/>p05/p50/p95"]
    G2["anomaly<br/>+ council verdicts"]
    G3["buying_suggestion"]
    G4["prep_plan"]
  end

  B1 -->|conform| S1
  S1 & S7 --> G1
  S1 --> G2
  G1 & S3 & S4 & S5 --> G3
  G1 & S3 --> G4
```

The lineage graph (layer, source, freshness, row counts) is exposed live at
`/api/lineage` and rendered on the dashboard's **Data lineage** page.

## 3. The batch pipeline

`python -m app.pipeline` runs the stages in order. `run_all()` first drops & rebuilds
the gold output tables, so every run is a clean, reproducible rebuild. Any single stage
can be run alone (`python -m app.pipeline forecast`).

```mermaid
flowchart LR
  I["ingest<br/>load + conform<br/>bronze → silver"] --> F["forecast<br/>Chronos-2 / seasonal<br/>→ gold.forecast"]
  F --> A["anomaly<br/>residual + structural<br/>+ council → gold.anomaly"]
  A --> V["inventory<br/>par + reorder<br/>→ gold.buying_suggestion"]
  V --> P["prep<br/>thaw/cook<br/>→ gold.prep_plan"]
  P --> AL["alerts<br/>dispatch if enabled<br/>email/Teams/Slack"]
```

| Stage | Module | Produces |
|-------|--------|----------|
| ingest | `app.ingest.load` + `conform` | silver tables + a validation report |
| forecast | `app.forecast.service` | `forecast` (p05/p50/p95 per store×item×daypart) |
| anomaly | `app.anomaly.detect` + `app.council` | `anomaly` with type + council verdict/confidence |
| inventory | `app.inventory.planning` | `buying_suggestion` (par, reorder point, qty, cost) |
| prep | `app.inventory.prep` | `prep_plan` (thaw/cook lead‑time plan) |
| alerts | `app.alerts` | notifications (only if a rule is enabled) |

## 4. The forecast engine (pluggable backend)

The forecaster is swappable behind one contract: given history + future covariates,
return `p05/p50/p95` per series. `SF_FORECAST_BACKEND` selects the implementation, so
the exact same pipeline, charts, backtest, and anomaly band work on either.

```mermaid
flowchart TB
  REQ["service.run()<br/>per store × item × daypart series"] --> SW{"SF_FORECAST_BACKEND"}
  SW -->|chronos| CH["Chronos2Pipeline<br/>amazon/chronos-2 on GPU<br/>(requirements-forecast.txt)"]
  SW -->|seasonal| SE["SeasonalBackend<br/>DOW profile × EWMA level<br/>(zero dependencies)"]
  CH --> OUT["p05 / p50 / p95"]
  SE --> OUT
  COV["Covariates: weather · DOW · VN holidays<br/>Tết regime · promos · store_age maturation"] --> REQ
```

- **Chronos‑2** — the production model (GPU). Pinned to an idle GPU on the server to
  avoid contention with other workloads (see [deployment.md](deployment.md#gpu-selection)).
- **Seasonal** — the always‑available fallback: a day‑of‑week profile scaled by an EWMA
  level. No torch, no GPU; used for local dev and as a safety net.
- **Backtest / hindcast** (`app.forecast.backtest`) holds out the last *N* days, forecasts
  them, and scores MAE / MAPE / band‑coverage / skill‑vs‑naive / bias — this powers the
  Forecast page's "Backtest vs actual" overlay.

## 5. Anomaly detection + council

```mermaid
flowchart LR
  S["silver.sales_line"] --> R["Residual detector<br/>actual vs DOW×EWMA expected"]
  S --> ST["Structural detector<br/>level shifts"]
  S --> FR["Void/comp z-score<br/>(fraud signal)"]
  S --> INV["Inventory variance<br/>vs on-hand"]
  R & ST & FR & INV --> C["Council<br/>multiple independent judges vote"]
  C --> G["gold.anomaly<br/>type · verdict · confidence"]
```

A candidate anomaly is only surfaced with high confidence when the **council** (a panel
of independent checks, optionally LLM‑assisted) agrees. Types: spike, drop, outage,
fraud, inventory.

## 6. Runtime services & request flow

```mermaid
flowchart TB
  subgraph container["app container (uvicorn)"]
    ENT["entrypoint.sh"]
    ENT -->|background| PIPE["python -m app.pipeline"]
    ENT --> MCP["app.mcp_server :8901"]
    ENT --> API["app.api.server:api :8900"]
    API --> MW["RBAC middleware<br/>auth + role → permission"]
    MW --> ROUTES["/ dashboard · /api/* · /odata · /api/export"]
  end
  ROUTES --> DB[("SQLAlchemy Core<br/>SQLite / Postgres")]
  MCP --> DB
  PIPE --> DB
```

- **One FastAPI app** serves the SPA (`/`), the JSON API (`/api/*`), the PowerBI feeds
  (`/odata`, `/api/export/*`), and the auth endpoints.
- **RBAC middleware** runs on every request: it resolves the session cookie to a user,
  then enforces the role→permission map (open routes and static assets are exempt).
- **Database access** is always through `app/db.py` (SQLAlchemy Core). pandas'
  `to_sql`/`read_sql` are bypassed on purpose — their SQLAlchemy detection is broken in
  this environment — so all reads/writes go through the thin `db.read_sql` / `db.write_df`
  wrappers, which work identically on SQLite and Postgres.

## 7. Key design decisions

| Decision | Why |
|----------|-----|
| **Env‑switched backends** (SQLite/Postgres, seasonal/Chronos) | One codebase runs on a laptop and on the GPU server unchanged. |
| **Pluggable forecaster behind a p05/p50/p95 contract** | Chronos‑2 and the fallback are interchangeable; downstream never changes. |
| **Rebuild‑from‑scratch pipeline** | Deterministic, reproducible gold tables; no incremental‑state drift. |
| **Single‑file vanilla‑JS SPA** (no build step, self‑contained SVG charts) | Zero front‑end toolchain; served straight from disk; trivial to deploy. |
| **SQLAlchemy Core, pandas bypassed for I/O** | Works around a pandas 3.0 I/O bug; portable across SQLite/Postgres. |
| **Bind‑mounted code + data** | A dashboard/code change is an rsync away — no image rebuild for content. |

## Related docs

- Ports, hosts, and data ingress/egress → [networking.md](networking.md)
- How to deploy this to the server → [deployment.md](deployment.md)
- How to use each surface → [usage.md](usage.md)
