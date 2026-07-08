#!/usr/bin/env python3
"""Synthetic multi-brand restaurant POS/ERP dataset generator.

Produces a realistic ~2-year dataset modelled on Oracle Simphony (POS) + SAP
(ERP) for validating the sales-forecast platform through every phase:

  Phase 1  raw Simphony check-detail  -> conform -> canonical sales_line
  Phase 2  canonical sales_line       -> Chronos-2 forecasting (recoverable signal)
  Phase 3  labelled anomalies         -> anomaly-detection scoring ground truth
  Phase 4  BOM + inventory + POs       -> buying / par / thaw-cook math

The generative model (config.py) embeds day-of-week & annual seasonality, daypart
curves, weather sensitivity, holidays and scheduled promos, then injects a set of
*labelled* anomalies so detection can be scored against known truth.

Deterministic (fixed seed + fixed END_DATE) => byte-stable reruns.

Usage:  python3 generate_pos_data.py
Outputs under ../data/{canonical,raw_simphony,ground_truth}/
"""
from __future__ import annotations

import datetime as dt
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

import config as C

RNG = np.random.default_rng(C.SEED)
OUT = Path(__file__).resolve().parent.parent / "data"
DIR_CANON = OUT / "canonical"
DIR_RAW = OUT / "raw_simphony"
DIR_GT = OUT / "ground_truth"
for d in (DIR_CANON, DIR_RAW, DIR_GT):
    d.mkdir(parents=True, exist_ok=True)

r2 = lambda a: np.round(np.asarray(a, float), 2)


# ---------------------------------------------------------------------------
# Calendar + weather
# ---------------------------------------------------------------------------
def build_calendar():
    dates = pd.date_range(C.START_DATE, C.END_DATE, freq="D")
    df = pd.DataFrame({"business_date": dates})
    df["day_index"] = np.arange(len(df))
    df["dow"] = df["business_date"].dt.dayofweek
    df["doy"] = df["business_date"].dt.dayofyear
    df["is_weekend"] = df["dow"] >= 5
    df["fiscal_period"] = df["business_date"].dt.strftime("%Y-%m")
    hol = {d: v["name"] for d, v in C.HOLIDAYS.items()}
    df["holiday"] = df["business_date"].dt.date.map(hol).fillna("")
    return df


def build_weather(cal):
    """Per-region daily temp/precip series + a lightly-noised forecast (known-future)."""
    rows = []
    doy = cal["doy"].to_numpy()
    for region, (mean_t, amp, rain_base) in C.REGION_CLIMATE.items():
        # coldest ~ mid-Jan, warmest ~ mid-Jul
        seasonal = mean_t - amp * np.cos(2 * np.pi * (doy - 15) / 365.0)
        temp = seasonal + RNG.normal(0, 4.0, len(cal))
        rain_p = np.clip(rain_base * (1 + 0.3 * np.sin(2 * np.pi * (doy - 60) / 365.0)), 0.03, 0.7)
        is_rain = (RNG.random(len(cal)) < rain_p).astype(int)
        fcst = temp + RNG.normal(0, 2.0, len(cal))     # what we'd know ahead of time
        rows.append(pd.DataFrame({
            "region": region, "business_date": cal["business_date"],
            "temp_f": r2(temp), "is_rain": is_rain, "forecast_temp_f": r2(fcst),
        }))
    return pd.concat(rows, ignore_index=True)


# ---------------------------------------------------------------------------
# Demand skeleton -> lambda -> Poisson
# ---------------------------------------------------------------------------
def daypart_fracs(fmt, item_dayparts):
    share = C.DAYPART_SHARE[fmt]
    w = {dp: share[dp] for dp in item_dayparts if share.get(dp, 0) > 0}
    tot = sum(w.values()) or 1.0
    return {dp: v / tot for dp, v in w.items()}


def build_demand(cal, weather):
    store_meta = {s[0]: {"brand": s[1], "region": s[2], "scale": s[6], "growth": s[7], "opened": s[8]}
                  for s in C.STORES}
    wx = {(r.region, pd.Timestamp(r.business_date)): (r.temp_f, r.is_rain)
          for r in weather.itertuples(index=False)}
    dates = cal["business_date"].to_numpy()
    dow = cal["dow"].to_numpy()
    doy = cal["doy"].to_numpy()
    didx = cal["day_index"].to_numpy()
    n = len(cal)
    annual = 1 + 0.07 * np.sin(2 * np.pi * (doy - 120) / 365.0)

    blocks = []
    for store_id, sm in store_meta.items():
        brand = sm["brand"]
        fmt = C.BRANDS[brand]["format"]
        region = sm["region"]
        temp = np.array([wx[(region, pd.Timestamp(d))][0] for d in dates])
        rain = np.array([wx[(region, pd.Timestamp(d))][1] for d in dates])
        dowf = np.array(C.DOW_FACTOR[fmt])[dow]
        growth = 1 + sm["growth"] * (didx / 365.0)
        hol_mult = np.array([C.HOLIDAYS.get(pd.Timestamp(d).date(), {}).get(fmt, 1.0)
                             for d in dates])
        # new-store maturation: sales ramp from ~55% to 100% over the first year;
        # zero before the store opened (so new stores have shorter history).
        opened = pd.Timestamp(sm["opened"])
        age = np.array([(pd.Timestamp(d) - opened).days for d in dates], float)
        maturity = np.where(age < 0, 0.0, np.clip(0.55 + 0.45 * np.clip(age, 0, 365) / 365.0, 0.0, 1.0))
        for it in C.MENU[brand]:
            iid, name, cat, price, dparts, base_pop, (tb, rb), _bom = it
            fr = daypart_fracs(fmt, dparts)
            wmult = np.clip((1 + tb * (temp - 65) / 10.0) * (1 + rb * rain), 0.4, 2.2)
            for dp, frac in fr.items():
                lam = (base_pop * sm["scale"] * frac * growth * dowf
                       * annual * wmult * hol_mult * maturity)
                blocks.append(pd.DataFrame({
                    "brand": brand, "store": store_id, "business_date": dates,
                    "daypart": dp, "menu_item_id": iid, "menu_item": name,
                    "category": cat, "unit_price": price, "lam": lam,
                    "day_index": didx,
                }))
    df = pd.concat(blocks, ignore_index=True)

    # scheduled promos: lift demand + record discount (known-future signal)
    df["promo_discount_pct"] = 0.0
    df["promo_flag"] = False
    dcol = df["business_date"].to_numpy()
    for scope, target, item_id, start, end, disc, lift in C.PROMOS:
        m = (df["menu_item_id"] == item_id).to_numpy().copy()
        m = m & (dcol >= np.datetime64(start)) & (dcol <= np.datetime64(end))
        scope_m = (df["store"] == target).to_numpy() if scope == "store" else (df["brand"] == target).to_numpy()
        m = m & scope_m
        df.loc[m, "lam"] *= lift
        df.loc[m, "promo_discount_pct"] = disc
        df.loc[m, "promo_flag"] = True

    df["qty_base"] = RNG.poisson(np.clip(df["lam"].to_numpy(), 0, None))
    return df


# ---------------------------------------------------------------------------
# Anomaly injection (labelled)
# ---------------------------------------------------------------------------
def inject_anomalies(df):
    stores = [s[0] for s in C.STORES]
    all_dates = pd.to_datetime(sorted(df["business_date"].unique()))
    # keep some anomalies inside the trailing check-detail window so they surface at check grain too
    recent_lo = pd.Timestamp(C.END_DATE) - pd.Timedelta(days=C.CHECK_DETAIL_DAYS - 5)

    df["qty"] = df["qty_base"].astype(float)
    df["void_rate"] = 0.006
    df["comp_rate"] = 0.008
    dcol = df["business_date"].to_numpy()
    scol = df["store"].to_numpy()
    dpcol = df["daypart"].to_numpy()
    labels = []
    aid = 0

    def pick_date(lo=None, hi=None):
        pool = all_dates
        if lo is not None:
            pool = pool[pool >= lo]
        if hi is not None:
            pool = pool[pool <= hi]
        return pool[RNG.integers(len(pool))]

    # 1) POS outages: a store loses a daypart (or full day) -> zero sales
    for i in range(6):
        st = stores[RNG.integers(len(stores))]
        day = pick_date(hi=all_dates[-3])
        if i < 3:  # some in the recent window
            day = pick_date(lo=recent_lo, hi=all_dates[-2])
        dparts = C.BRANDS[st.split("-")[0]]["dayparts"]
        dp = dparts[RNG.integers(len(dparts))]
        m = (scol == st) & (dcol == np.datetime64(day)) & (dpcol == dp)
        expected = float(df.loc[m, "qty"].sum())
        df.loc[m, "qty"] = 0.0
        aid += 1
        labels.append(dict(anomaly_id=f"A{aid:03d}", type="POS_OUTAGE", brand=st.split("-")[0],
                           store=st, target=dp, daypart=dp, start_date=day.date(), end_date=day.date(),
                           magnitude=0.0, expected_units=round(expected, 1), observed_units=0.0,
                           description=f"POS/register outage — {dp} sales lost at {st}"))

    # 2) Local-event demand spikes: whole store-day lifts 2.5-4x
    for i in range(8):
        st = stores[RNG.integers(len(stores))]
        day = pick_date(lo=recent_lo) if i < 3 else pick_date()
        fac = round(float(RNG.uniform(2.5, 4.0)), 2)
        m = (scol == st) & (dcol == np.datetime64(day))
        expected = float(df.loc[m, "qty"].sum())
        df.loc[m, "qty"] *= fac
        aid += 1
        labels.append(dict(anomaly_id=f"A{aid:03d}", type="DEMAND_SPIKE", brand=st.split("-")[0],
                           store=st, target="ALL", daypart="ALL", start_date=day.date(), end_date=day.date(),
                           magnitude=fac, expected_units=round(expected, 1),
                           observed_units=round(expected * fac, 1),
                           description=f"Local event demand spike x{fac} at {st}"))

    # 3) Sustained demand drops: road closure / construction, 6-10 day window
    for _ in range(4):
        st = stores[RNG.integers(len(stores))]
        start = pick_date(hi=all_dates[-15])
        span = int(RNG.integers(6, 11))
        end = start + pd.Timedelta(days=span - 1)
        fac = round(float(RNG.uniform(0.4, 0.6)), 2)
        m = (scol == st) & (dcol >= np.datetime64(start)) & (dcol <= np.datetime64(end))
        expected = float(df.loc[m, "qty"].sum())
        df.loc[m, "qty"] *= fac
        aid += 1
        labels.append(dict(anomaly_id=f"A{aid:03d}", type="DEMAND_DROP", brand=st.split("-")[0],
                           store=st, target="ALL", daypart="ALL", start_date=start.date(), end_date=end.date(),
                           magnitude=fac, expected_units=round(expected, 1),
                           observed_units=round(expected * fac, 1),
                           description=f"Sustained demand drop x{fac} over {span}d at {st} (construction/closure)"))

    # 4) Void/comp fraud: elevated void & comp rate at a store over a window
    for i in range(4):
        st = stores[RNG.integers(len(stores))]
        start = pick_date(lo=recent_lo, hi=all_dates[-4]) if i < 2 else pick_date(hi=all_dates[-8])
        span = int(RNG.integers(5, 12))
        end = start + pd.Timedelta(days=span - 1)
        vr = round(float(RNG.uniform(0.05, 0.09)), 3)
        cr = round(float(RNG.uniform(0.10, 0.16)), 3)
        m = (scol == st) & (dcol >= np.datetime64(start)) & (dcol <= np.datetime64(end))
        df.loc[m, "void_rate"] = vr
        df.loc[m, "comp_rate"] = cr
        aid += 1
        labels.append(dict(anomaly_id=f"A{aid:03d}", type="VOID_COMP_FRAUD", brand=st.split("-")[0],
                           store=st, target="ALL", daypart="ALL", start_date=start.date(), end_date=end.date(),
                           magnitude=cr, expected_units=None, observed_units=None,
                           description=f"Elevated voids ({vr:.0%}) & comps ({cr:.0%}) over {span}d at {st}"))

    return df, labels


# ---------------------------------------------------------------------------
# Money columns -> canonical sales_line
# ---------------------------------------------------------------------------
def finalize_sales(df):
    qty = df["qty"].to_numpy()
    price = df["unit_price"].to_numpy()
    gross = qty * price
    discount = gross * df["promo_discount_pct"].to_numpy()
    comp = (gross - discount) * df["comp_rate"].to_numpy()
    void_qty = np.floor(qty * df["void_rate"].to_numpy() + RNG.random(len(df)))
    net = gross - discount - comp
    out = pd.DataFrame({
        "brand": df["brand"], "store": df["store"],
        "business_date": df["business_date"].dt.date.astype(str),
        "daypart": df["daypart"], "menu_item_id": df["menu_item_id"],
        "menu_item": df["menu_item"], "category": df["category"],
        "qty": qty.astype(int), "unit_price": r2(price), "gross": r2(gross),
        "discount": r2(discount), "comp": r2(comp), "void_qty": void_qty.astype(int),
        "net": r2(net), "promo_flag": df["promo_flag"].astype(bool),
    })
    # drop zero-activity rows only where genuinely closed (holiday closures) to keep file lean
    out = out[(out["qty"] > 0) | (out["net"] != 0)].reset_index(drop=True)
    return out


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------
def write_dimensions():
    stores = [dict(store=s[0], brand=s[1], brand_name=C.BRANDS[s[1]]["name"],
                   format=C.BRANDS[s[1]]["format"], region=s[2], timezone=s[3],
                   lat=s[4], lon=s[5], opened=s[8]) for s in C.STORES]
    menu, bom = [], []
    for brand, items in C.MENU.items():
        for iid, name, cat, price, dparts, base_pop, (tb, rb), recipe in items:
            menu.append(dict(menu_item_id=iid, brand=brand, name=name, category=cat,
                             price=price, dayparts=dparts))
            for sku, qty_per in recipe:
                ing = C.INGREDIENTS[sku]
                bom.append(dict(menu_item_id=iid, sku=sku, qty_per=qty_per, uom=ing[1],
                                yield_factor=ing[6]))
    ingredients = [dict(sku=k, unit_cost=v[0], uom=v[1], shelf_life_days=v[2],
                        lead_time_days=v[3], frozen=v[4], thaw_hours=v[5], cook_yield=v[6])
                   for k, v in C.INGREDIENTS.items()]
    promos = [dict(scope=p[0], target=p[1], menu_item_id=p[2], start_date=str(p[3]),
                   end_date=str(p[4]), discount_pct=p[5], expected_lift=p[6]) for p in C.PROMOS]
    for name, obj in [("store", stores), ("menu_item", menu), ("recipe_bom", bom),
                      ("ingredient", ingredients), ("promo_event", promos)]:
        (DIR_CANON / f"{name}.json").write_text(json.dumps(obj, indent=2))
    return {"stores": len(stores), "menu_items": len(menu), "bom_lines": len(bom),
            "ingredients": len(ingredients), "promos": len(promos)}


# ---------------------------------------------------------------------------
# Inventory + purchase orders (SAP side) with inventory-variance anomalies
# ---------------------------------------------------------------------------
def build_inventory(sales, labels):
    bom = {}
    for brand, items in C.MENU.items():
        for iid, *_rest, recipe in items:
            bom[iid] = recipe
    # weekly theoretical SKU usage per store
    s = sales.copy()
    s["business_date"] = pd.to_datetime(s["business_date"])
    s["week"] = s["business_date"].dt.to_period("W").dt.start_time
    rows = []
    for (store, week), g in s.groupby(["store", "week"], sort=True):
        usage = {}
        for iid, q in zip(g["menu_item_id"].to_numpy(), g["qty"].to_numpy()):
            for sku, qty_per in bom.get(iid, []):
                usage[sku] = usage.get(sku, 0.0) + q * qty_per
        for sku, u in usage.items():
            rows.append((store, store.split("-")[0], week, sku, round(u, 1)))
    usage_df = pd.DataFrame(rows, columns=["store", "brand", "week", "sku", "theoretical_usage"])

    # on-hand = par (2 weeks usage) - actual usage + noise; PO reorders next week's need
    inv_rows, po_rows = [], []
    variance_targets = []  # (store, sku, week) to shrink for anomalies
    aid = len(labels)
    weeks = sorted(usage_df["week"].unique())
    for _ in range(5):
        row = usage_df.sample(1, random_state=int(RNG.integers(1e9))).iloc[0]
        variance_targets.append((row["store"], row["sku"], row["week"]))
    vt = set((a, b, pd.Timestamp(c)) for a, b, c in variance_targets)

    for r in usage_df.itertuples(index=False):
        cost = C.INGREDIENTS[r.sku][0]
        lead = C.INGREDIENTS[r.sku][3]
        par = r.theoretical_usage * 2.0
        theoretical_onhand = max(par - r.theoretical_usage, 0)
        counted = theoretical_onhand * (1 + RNG.normal(0, 0.04))
        is_var = (r.store, r.sku, pd.Timestamp(r.week)) in vt
        if is_var:
            shrink = round(float(RNG.uniform(0.25, 0.45)), 2)
            counted = theoretical_onhand * (1 - shrink)
            aid += 1
            labels.append(dict(anomaly_id=f"A{aid:03d}", type="INVENTORY_VARIANCE", brand=r.brand,
                               store=r.store, target=r.sku, daypart=None,
                               start_date=pd.Timestamp(r.week).date(), end_date=pd.Timestamp(r.week).date(),
                               magnitude=shrink, expected_units=round(theoretical_onhand, 1),
                               observed_units=round(counted, 1),
                               description=f"Inventory shrink {shrink:.0%} on {r.sku} at {r.store} (theft/waste/miscount)"))
        inv_rows.append(dict(brand=r.brand, store=r.store, sku=r.sku,
                             on_hand_qty=round(max(counted, 0), 1), uom=C.INGREDIENTS[r.sku][1],
                             theoretical_usage_wk=r.theoretical_usage,
                             theoretical_on_hand=round(theoretical_onhand, 1),
                             variance_pct=round((counted - theoretical_onhand) / (theoretical_onhand + 1e-9), 3),
                             as_of=str(pd.Timestamp(r.week).date())))
        order_qty = round(r.theoretical_usage * 1.1, 1)
        order_date = pd.Timestamp(r.week)
        po_rows.append(dict(brand=r.brand, store=r.store, sku=r.sku, order_qty=order_qty,
                            uom=C.INGREDIENTS[r.sku][1], unit_cost=cost,
                            order_date=str(order_date.date()),
                            expected_date=str((order_date + pd.Timedelta(days=lead)).date()),
                            lead_time_days=lead))
    return pd.DataFrame(inv_rows), pd.DataFrame(po_rows), labels


# ---------------------------------------------------------------------------
# Raw Simphony check-detail for the trailing window
# ---------------------------------------------------------------------------
DAYPART_HOURS = {"breakfast": (7, 11), "lunch": (11, 15), "dinner": (17, 22), "late": (22, 24)}
ORDER_TYPES = ["Dine In", "Take Out", "To Go"]


def build_check_detail(sales, df_full):
    lo = (pd.Timestamp(C.END_DATE) - pd.Timedelta(days=C.CHECK_DETAIL_DAYS - 1)).date()
    s = sales[pd.to_datetime(sales["business_date"]).dt.date >= lo].copy()
    # bring void/comp rates back for line-level flagging
    rates = df_full[["store", "business_date", "daypart", "menu_item_id", "void_rate", "comp_rate"]].copy()
    rates["business_date"] = rates["business_date"].dt.date.astype(str)
    s = s.merge(rates, on=["store", "business_date", "daypart", "menu_item_id"], how="left")

    emp_by_store = {st[0]: [f"{st[0]}-E{n:02d}" for n in range(1, 7)] for st in C.STORES}
    recs = []
    for (store, bdate, daypart), g in s.groupby(["store", "business_date", "daypart"], sort=True):
        total_units = int(g["qty"].sum())
        if total_units <= 0:
            continue
        n_checks = max(1, round(total_units / 2.3))
        emps = emp_by_store[store]
        h0, h1 = DAYPART_HOURS[daypart]
        rvc = C.RVC_CODE[daypart]
        fmt = C.BRANDS[store.split("-")[0]]["format"]
        # per-check metadata
        chk_emp = RNG.choice(emps, n_checks)
        chk_station = RNG.integers(1, 5, n_checks)
        otype_p = [0.35, 0.30, 0.35] if fmt == "cafe" else [0.62, 0.23, 0.15]
        chk_otype = RNG.choice(ORDER_TYPES, n_checks, p=otype_p)
        chk_party = np.clip(1 + RNG.poisson(1.2, n_checks), 1, 8)
        base_min = int((pd.Timestamp(bdate) - pd.Timestamp(bdate).normalize()).total_seconds())  # 0
        chk_minute = RNG.integers(h0 * 60, h1 * 60, n_checks)
        chk_num = np.arange(1, n_checks + 1)
        # explode item rows into lines assigned to random checks
        for row in g.itertuples(index=False):
            q = int(row.qty)
            vr = float(row.void_rate) if not math.isnan(row.void_rate) else 0.006
            cr = float(row.comp_rate) if not math.isnan(row.comp_rate) else 0.008
            while q > 0:
                lq = int(min(q, RNG.choice([1, 1, 2, 2, 3])))
                q -= lq
                ci = int(RNG.integers(n_checks))
                minute = int(chk_minute[ci])
                ts = pd.Timestamp(bdate) + pd.Timedelta(minutes=minute)
                void = int(RNG.random() < vr)
                comp = int(RNG.random() < cr)
                ext = round(lq * row.unit_price, 2)
                disc = round(ext * (0.15 if row.promo_flag else 0.0), 2)
                recs.append((
                    store, store.split("-")[0], rvc, str(bdate), int(chk_num[ci]),
                    str(chk_emp[ci]), int(chk_station[ci]), str(chk_otype[ci]), int(chk_party[ci]),
                    ts.strftime("%Y-%m-%d %H:%M:%S"), daypart, row.menu_item_id, row.menu_item,
                    lq, round(row.unit_price, 2), ext, disc, void, comp,
                ))
    cols = ["store", "brand", "rvc", "business_date", "check_num", "employee_id", "station",
            "order_type", "guest_count", "trans_datetime", "daypart", "menu_item_num",
            "menu_item_name", "item_qty", "unit_price", "extended_price", "discount_amt",
            "void_flag", "comp_flag"]
    return pd.DataFrame(recs, columns=cols)


# ---------------------------------------------------------------------------
def main():
    print("Building calendar + weather ...")
    cal = build_calendar()
    weather = build_weather(cal)

    print("Generating demand (this is the recoverable signal) ...")
    demand = build_demand(cal, weather)
    demand, labels = inject_anomalies(demand)
    sales = finalize_sales(demand)

    print("Writing dimensions ...")
    dim_counts = write_dimensions()
    cal_out = cal.copy()
    cal_out["business_date"] = cal_out["business_date"].dt.date.astype(str)
    cal_out.drop(columns=["day_index"]).to_csv(DIR_CANON / "calendar.csv", index=False)
    w_out = weather.copy()
    w_out["business_date"] = w_out["business_date"].dt.date.astype(str)
    w_out.to_csv(DIR_CANON / "weather.csv", index=False)

    print("Writing canonical sales_line ...")
    sales.to_csv(DIR_CANON / "sales_line.csv", index=False)

    print("Building inventory + purchase orders (SAP) ...")
    inv, po, labels = build_inventory(sales, labels)
    inv.to_csv(DIR_CANON / "inventory_snapshot.csv", index=False)
    po.to_csv(DIR_CANON / "purchase_order.csv", index=False)

    print(f"Building Simphony check-detail (trailing {C.CHECK_DETAIL_DAYS}d) ...")
    checks = build_check_detail(sales, demand)
    checks.to_csv(DIR_RAW / "check_detail_last60d.csv", index=False)
    # pretty JSON sample: the most recent full business date at one store
    sample_day = checks["business_date"].max()
    sample = checks[(checks["business_date"] == sample_day) &
                    (checks["store"] == checks["store"].iloc[0])]
    (DIR_RAW / "check_detail_sample.json").write_text(
        json.dumps(sample.to_dict(orient="records"), indent=2))

    print("Writing ground truth ...")
    gt = pd.DataFrame(labels)
    gt.to_csv(DIR_GT / "anomalies.csv", index=False)

    manifest = {
        "generated_for": "sales-forecast platform phase validation",
        "seed": C.SEED,
        "date_range": [str(C.START_DATE), str(C.END_DATE)],
        "brands": {b: v["name"] for b, v in C.BRANDS.items()},
        "dimensions": dim_counts,
        "sales_line_rows": int(len(sales)),
        "sales_line_series": int(sales.groupby(["store", "menu_item_id", "daypart"]).ngroups),
        "total_units": int(sales["qty"].sum()),
        "total_net_revenue": round(float(sales["net"].sum()), 2),
        "check_detail_rows": int(len(checks)),
        "check_detail_days": C.CHECK_DETAIL_DAYS,
        "inventory_rows": int(len(inv)),
        "purchase_order_rows": int(len(po)),
        "anomalies": {
            "total": int(len(gt)),
            "by_type": gt["type"].value_counts().to_dict(),
        },
        "validation_notes": {
            "phase2_forecast": "sales_line is driven by DOW/annual seasonality, daypart curves, "
                               "weather (temp/rain) and promos — a good forecaster should recover these.",
            "phase3_anomaly": "ground_truth/anomalies.csv lists every injected event; score detection "
                              "precision/recall against it. Normal seasonal swings are NOT anomalies.",
            "phase4_inventory": "recipe_bom + ingredient + inventory_snapshot + purchase_order support "
                                "BOM explosion, par/reorder, and thaw/cook (frozen/thaw_hours/cook_yield).",
        },
    }
    (DIR_GT / "manifest.json").write_text(json.dumps(manifest, indent=2, default=str))

    print("\n=== DONE ===")
    print(json.dumps(manifest, indent=2, default=str))


if __name__ == "__main__":
    main()
