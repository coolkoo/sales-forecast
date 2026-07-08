# Synthetic Simphony + SAP test dataset

A realistic, **fully-labelled** multi-brand restaurant dataset for validating the
sales-forecast platform through every phase. It is synthetic (we can't use real
proprietary POS data) but that's a feature: because we control the generative
model, we know exactly what the forecaster should recover and what anomaly
detection should catch — so every phase can be **scored against ground truth**.

## Regenerate

```bash
cd data_generator
python3 generate_pos_data.py        # deterministic (~8s); numpy + pandas only
```

Deterministic: fixed seed + fixed `END_DATE` ⇒ byte-stable reruns. Edit
`config.py` to change brands, menus, seasonality, promos, or holidays.

## What's in it

- **2 brands / 7 stores / 32 menu items**, 2 years daily at **daypart** grain
  (2024-07-01 → 2026-06-30). ~146k canonical rows, ~5.9M units, ~$44M net.
- Brands: `UPT` Uptown Grill (casual dining, all dayparts) and `BNB` Bean & Bagel
  (cafe, breakfast/lunch).

### Embedded signal (Phase 2 should recover this)
- **Day-of-week** curves (dining peaks Fri/Sat ≈ 1.6–1.8× Mon; cafe is weekday-heavy)
- **Annual** seasonality + per-store **growth trend**
- **Daypart** share per item
- **Weather**: temp & rain sensitivity per item (soup↓ / iced-tea↑ with temp — verified corr −0.50 / +0.74)
- **Holidays**: closures (Thanksgiving/Christmas) and spikes (Valentine's, Mother's Day, Super Bowl)
- **12 scheduled promos** with known lift (brand- and store-scoped)

### Labelled anomalies (Phase 3 scores detection against these)
`ground_truth/anomalies.csv` — 27 injected events, each with store, target,
date range, magnitude, expected vs observed, and a description:
| type | n | signature |
|---|---|---|
| `DEMAND_SPIKE` | 8 | store-day 2.5–4× (local event) — 6.6–12.6σ outliers |
| `POS_OUTAGE` | 6 | a store daypart → 0 units |
| `DEMAND_DROP` | 4 | 6–10 day sustained 0.4–0.6× (closure/construction) |
| `VOID_COMP_FRAUD` | 4 | comp% jumps ~1% → 12–18% over a window |
| `INVENTORY_VARIANCE` | 5 | SKU shrink −33% to −44% vs ±4% baseline |

Some anomalies are placed inside the trailing 60-day window so they also surface
at check grain. **Normal seasonal swings are not anomalies** — that's the point.

## Outputs

```
data/
  raw_simphony/          # Phase 1: raw POS as it arrives, pre-conform
    check_detail_last60d.csv   # Simphony check-detail (transaction grain), 60d, ~305k lines
    check_detail_sample.json   # one store-day, pretty-printed, to eyeball the shape
  canonical/             # Phase 2/4: the conformed contract everything reads
    sales_line.csv             # PRIMARY fact: brand×store×date×daypart×item + money
    menu_item.json  recipe_bom.json  ingredient.json  store.json
    calendar.csv  weather.csv  promo_event.json         # dimensions / covariates
    inventory_snapshot.csv  purchase_order.csv          # SAP side
  ground_truth/
    anomalies.csv              # labelled anomalies for scoring
    manifest.json              # counts, params, per-phase validation notes
```

### Raw → canonical (the Phase 0/1 conform step to build)
`check_detail_last60d.csv` is the **Simphony shape** (rvc, check_num, employee,
station, order_type, guest_count, menu_item_num, item_qty, void/comp flags…).
Aggregating it by `store × business_date × daypart × menu_item_num` and summing
qty/money reproduces the canonical `sales_line` rows for that window — that's the
conform transform to implement against real exports.

## Phase mapping

- **Phase 1** — drop `raw_simphony/` into AURIX; build the conform step; confirm
  analytics answers "sales by item × daypart × store".
- **Phase 2** — feed `sales_line` (+ `weather`/`calendar`/`promo_event` as
  covariates) to Chronos-2; measure how well it recovers the embedded signal.
- **Phase 3** — run anomaly detection; score precision/recall vs `anomalies.csv`.
- **Phase 4** — use `recipe_bom` + `ingredient` (frozen/thaw_hours/cook_yield) +
  `inventory_snapshot` + `purchase_order` for BOM explosion, par/reorder, and
  thaw/cook scheduling.

## Notes / limits (v1)
- Labor and check-level tender/tips are omitted (not needed for forecast/anomaly
  validation); easy to add in `config.py` later.
- Check-detail is emitted for the trailing 60 days only (keeps it ~36MB); the full
  2 years lives at daypart grain in `sales_line.csv`.
- Store-scoped and seasonal promos show diluted lift when measured across all
  stores/seasons — expected; the known-future promo covariate carries the scope.
