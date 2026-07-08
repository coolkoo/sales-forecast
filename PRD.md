# Restaurant Sales Forecasting & Anomaly Detection Platform — PRD / Plan

**Status:** Draft v1 · **Owner:** Jason Koo · **Last updated:** 2026-07-08

## 1. Summary

An enterprise, multi-brand platform that ingests raw restaurant POS (**Oracle
Simphony**) and ERP (**SAP**) data into a cloud data lake, runs a full analytics
/ correlation suite over it, and produces **sales forecasts**, **anomaly
detection**, and **operational recommendations** — buying/replenishment, par
levels, and thaw/cook/prep scheduling — from a Chronos-2 forecasting engine.

We are not building from zero. Two existing projects are the load-bearing
building blocks:

| Building block | Repo | Role in this platform |
|---|---|---|
| **AURIX Engine** | `~/Documents/projects/aurix-engine` | Data lake + medallion ETL + analytics/semantic query layer |
| **Traderific / Sir Trades A Lot** | `~/Documents/projects/traderific` | Chronos-2 forecasting harness + covariates + backtest + calibration + LLM council + dashboard + MCP |

The fit is close to 1:1: **AURIX ingests, warehouses, and serves; Traderific
forecasts, validates, and presents.** The net-new work is restaurant-domain:
canonical schema, Simphony/SAP connectors, restaurant feature engineering,
anomaly logic, and the deterministic inventory/prep math.

## 2. Key decisions (locked)

- **Primary sources:** Oracle Micros **Simphony** (POS: sales lines, menu, labor,
  voids/discounts/comps) + **SAP** (ERP: inventory, purchase orders, BOM/recipes,
  finance).
- **Scale / grain:** **Enterprise, multi-brand.** Forecast at
  `brand × store × menu_item × day` with `daypart` as a fast-follow dimension.
- **Hosting:** **Cloud.** Managed object store + warehouse + GPU inference.
- **Multi-tenancy:** Reuse Traderific's proven per-tenant isolation model
  (encrypted vault, tenant-scoped queries, fail-closed) — tenant ≈ brand/org.

## 3. What we reuse vs. build

### Reuse from AURIX Engine (mostly as-is)
- Medallion pipeline on **Dagster** (bronze → silver → serving), auto-schema
  detection, schema evolution.
- Object storage (**MinIO → cloud S3-compatible**), **PostgreSQL** serving layer,
  **Qdrant** semantic search, **Ollama/LLM** for NL→SQL analytics.
- **MCP server** for natural-language querying of the warehouse.

### Reuse from Traderific (mostly as-is)
- `signals/chronos_engine.py` — generic **Chronos-2** wrapper: quantile forecasts
  → calibrated confidence + direction. Reads the exact
  `(item_id, timestamp, target, covariate…)` frame we'll produce.
- `signals/covariates.py` — **past** + **known-future** covariate builder, fully
  fail-safe. Becomes first-class here (see §5).
- **Backtest/hindcast harness** — per-series accuracy measurement.
- **Nightly self-calibration + rolling fine-tune** — tune confidence gates; option
  to fine-tune Chronos on our own history.
- **LLM council** — repurposed from trade-voting to **anomaly triage/explanation**.
- **Flask dashboard** + **MCP server** patterns — the surface layer.
- **Multi-tenant** vault + tenant-scoping — maps to multi-brand.

### Build net-new (restaurant domain)
1. Canonical restaurant schema (the silver/gold contract).
2. Simphony + SAP connectors / conform steps.
3. Restaurant covariates (daypart, DOW, holidays, weather, promos, events).
4. Anomaly detection (residual-based + statistical/structural).
5. Inventory/buying + thaw/cook/prep OR layer.
6. Cloud deployment mapping.

> **Carried-over learning:** Traderific's A/B test found covariates did *not*
> improve *stock* forecasting (Chronos uses them softly, added noise). Restaurant
> demand is the opposite — dominated by day-of-week, daypart, weather, holidays,
> and promotions. **Covariates are the core signal here, not an experiment**, so
> the fail-safe covariate builder is always-on and central.

## 4. Architecture

```
  Oracle Simphony (POS)        AURIX ENGINE (cloud data lake)          FORECAST ENGINE (from Traderific)
  SAP (ERP)                    ──────────────────────────            ──────────────────────────────────
  sales lines, menu,   ──▶  bronze → silver(conform) → serving  ──▶  Chronos-2 multi-series forecaster
  labor, voids;             + canonical restaurant schema             + restaurant covariates (always-on)
  inventory, POs, BOM       + Qdrant semantic layer                   + backtest / nightly calibration
        │                   + NL→SQL analytics suite                  + LLM council (triage/explain)
        │                              │                                          │
        │  raw drops / API             ▼                                          ▼
        └──────────────▶   Analytics / correlation  ◀───────────  Forecasts · anomalies · buying ·
                           suite (feature store)                   par levels · thaw/cook/prep
                                       │                                          │
                                       └──────────▶  Dashboard + MCP (ask/act from Claude) ◀────────┘
```

## 5. Canonical restaurant schema (the contract)

Everything downstream reads this shape; each source gets a thin **silver
"conform" step** that maps its native fields into it. Keyed by `brand` and
`store` for multi-brand isolation.

**Facts**
- `sales_line` — `brand, store, business_date, timestamp, daypart, menu_item_id,
  qty, gross, discount, comp, void, net, order_id, channel`
- `inventory_snapshot` — `brand, store, sku, on_hand_qty, uom, as_of` (SAP)
- `purchase_order` — `brand, store, sku, qty, uom, order_date, expected_date,
  lead_time_days, unit_cost` (SAP)
- `labor` — `brand, store, business_date, daypart, role, hours, cost` (Simphony)
- `waste_shrink` — `brand, store, sku, qty, uom, reason, as_of` (feedback loop)

**Dimensions**
- `menu_item` — `menu_item_id, brand, name, category, price`
- `recipe_bom` — `menu_item_id → {sku, qty_per, uom, yield_factor}` (SAP recipes)
- `store` — `store, brand, region, timezone, lat/lon, format`
- `calendar` — date, DOW, holiday flags, fiscal period
- `weather` — `store/region, date, temp, precip, forecast` (external; known-future)
- `promo_event` — `brand, store, date_range, type, price_change, local_event`

## 6. Forecasting approach

- **Grain:** `brand × store × menu_item × day` (add `daypart` in phase 2b). Each
  series is one Chronos `item_id`; Chronos-2 is strong **zero-shot across many
  related series**, which fits enterprise multi-brand well.
- **Target:** `net` units (and optionally `net revenue`) per series per period.
- **Covariates** (restaurant-tuned rebuild of `covariates.py`):
  - *Past:* recent trend, rolling mean/vol, price, promo intensity, prior waste.
  - *Known-future:* daypart & day-of-week seasonality (sin/cos), holiday flags,
    **weather forecast**, scheduled promos/price changes, local events.
- **Output:** per series, a calibrated quantile band (p10…p90) + median + a
  confidence gate — reused directly from `chronos_engine.py`.
- **Validation:** Traderific's hindcast harness measures error per item/store;
  nightly calibration tunes the confidence gate; optional rolling fine-tune on our
  own history if zero-shot underperforms on long-tail items.

## 7. Anomaly detection

Two layers, both leaning on forecaster output we already produce:

1. **Forecast-residual anomalies** — actuals outside the Chronos **p10–p90** band
   flag over/under-performance per store/item/daypart. Free once forecasting runs.
2. **Statistical / structural anomalies** — void/discount/comp spikes, item-mix
   shifts, register/employee-level outliers (waste & fraud signals), inventory
   variance vs. theoretical usage (BOM-implied vs. counted).

The **LLM council** is repurposed to **triage & explain** flagged anomalies
("Store 4 lunch dipped — matches the road-closure event on `promo_event`") rather
than to vote on trades. Fail-closed thresholds are calibrated, not hardcoded.

## 8. Inventory, buying & prep (deterministic OR layer)

Fed by forecasts + `recipe_bom` + SAP inventory/lead-times. Rules, not ML:

- **Demand → ingredient explosion (BOM):** forecast menu-item demand → required
  SKU quantities via recipe yields.
- **Par levels & reorder points:** from forecast demand, on-hand, lead time, and
  shelf life → **suggested purchase orders** back to SAP.
- **Thaw / cook / prep scheduling:** forecast demand **by daypart** → required
  prepared quantity → back-schedule **thaw** (protein thaw curves) and
  **cook/yield** times so prep is ready on time with minimal over-prep.
- **Waste/shrink loop:** actual waste feeds both replenishment tuning and the
  anomaly layer.

## 9. Cloud deployment mapping

| AURIX/Traderific local component | Cloud target |
|---|---|
| MinIO (object store) | S3-compatible bucket |
| PostgreSQL serving | Managed Postgres (or warehouse: Snowflake/BigQuery) |
| Qdrant | Managed vector DB |
| Dagster (local) | Dagster Cloud / containerized orchestrator |
| Ollama (NL→SQL / council) | Hosted LLM (Claude for council/triage; local OSS optional) |
| Chronos-2 inference | Cloud **GPU** inference service (2 GB VRAM suffices for live) |
| Flask dashboard + MCP | Containerized service behind TLS + per-tenant tokens |

Reuse Traderific's security posture (per-tenant encrypted vault, tenant-scoped
queries, TLS/reverse-proxy) — it already targets a multi-tenant hosted model.

## 10. Phased roadmap

- **Phase 0 — Schema & connectors.** Lock canonical schema; write Simphony + SAP
  conform steps. *Exit:* one brand's history lands in serving tables in canonical shape.
- **Phase 1 — Ingestion + analytics.** AURIX in cloud; NL/analytics answers
  "sales by item × daypart × store." *Exit:* correlation suite live on real data.
- **Phase 2 — Forecast service.** Standalone Chronos-2 service reads serving tables,
  forecasts `store × item × day`; restaurant covariates; hindcast + calibration.
  *(2b: add daypart grain.)* *Exit:* measured accuracy per item/store.
- **Phase 3 — Anomaly detection.** Residual + structural layers; council triage.
  *Exit:* anomaly feed with explanations.
- **Phase 4 — Inventory/buying/prep.** BOM explosion, par/reorder, thaw/cook
  scheduling, waste loop. *Exit:* suggested POs + daily prep/thaw plan.
- **Phase 5 — Surface.** Dashboard + MCP for forecasts, anomalies, and
  buying/prep recommendations; "ask/act from Claude." *Exit:* usable ops product.

## 11. Immediate next steps

1. Confirm canonical schema field list (§5) against real Simphony + SAP exports.
2. Scaffold canonical schema + the **Simphony** conform step (first source with
   sales history — the forecasting target).
3. **Prove the seam:** extract a minimal standalone forecast service from
   `chronos_engine.py` that reads one AURIX serving table and forecasts one store's
   top items end-to-end.

## 12. Open questions / risks

- Access & format of real Simphony + SAP exports (API vs. flat dumps) — drives
  connector effort.
- Long-tail / new menu items with little history — may need fine-tuning or
  hierarchical pooling across stores/brands.
- Daypart-grain data availability (timestamped sales lines) for prep scheduling.
- Weather-forecast provider + local-events data sourcing for known-future covariates.
- Cloud GPU cost vs. batch cadence (nightly forecast vs. intraday re-forecast).
