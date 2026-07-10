"""Anomaly detection (Phase 3).

Two families, both leaning on signal the platform already computes:

  Residual anomalies  — actuals vs an expected day-of-week x level model with an
    empirical band.  Catches POS_OUTAGE (actual 0 vs expected), DEMAND_SPIKE
    (far above band) and DEMAND_DROP (sustained below band).
  Structural anomalies — VOID_COMP_FRAUD (comp% spikes vs a store's rolling
    baseline) and INVENTORY_VARIANCE (counted vs theoretical on-hand).

Known holidays are excluded from residual detection (they are seasonal, not
anomalous — the platform knows the calendar).  `score()` grades detections
against the injected ground truth.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app import db
from app.config import CFG
from app.forecast.features import FeatureBuilder


def _expected_panel(fb: FeatureBuilder, lookback: int) -> pd.DataFrame:
    """Per series (store,daypart,item) daily actual + expected + sigma over the recent window."""
    rows = []
    cutoff = fb.last_date - pd.Timedelta(days=lookback - 1)
    for s in fb.series_iter():
        y = s.hist["y"].astype(float)
        idx = y.index
        dow = idx.dayofweek
        overall = max(y.mean(), 1e-6)
        prof = np.array([max(y[dow == d].mean() / overall, 1e-3) if (dow == d).any() else 1.0
                         for d in range(7)])
        deseas = y.values / prof[dow]
        # level for day t uses info up to t-1 (shift) -> limits leakage from the anomaly itself.
        # A stickier span keeps "normal" from chasing a sustained drop, so drops stay detectable.
        level = pd.Series(deseas, index=idx).ewm(span=45, adjust=False).mean().shift(1)
        expected = (level.values * prof[dow])
        resid = y.values - expected
        sigma = np.nanstd(resid[~np.isnan(resid)][-120:]) if np.isfinite(resid).any() else overall
        sigma = max(float(sigma), 0.5)
        sub = pd.DataFrame({"store": s.store, "brand": s.brand, "daypart": s.daypart,
                            "menu_item_id": s.menu_item_id, "business_date": idx,
                            "y": y.values, "expected": expected, "sigma": sigma})
        rows.append(sub[sub["business_date"] >= cutoff])
    panel = pd.concat(rows, ignore_index=True)
    panel = panel.dropna(subset=["expected"])
    # attach holiday flag
    cal = fb.cal[["business_date", "holiday_flag"]]
    return panel.merge(cal, on="business_date", how="left").fillna({"holiday_flag": 0})


def detect_residual(panel: pd.DataFrame) -> list[dict]:
    out = []
    non_hol = panel[panel["holiday_flag"] == 0]

    # -- POS_OUTAGE: a store-daypart-day with zero sales but a meaningful expectation --
    dp = non_hol.groupby(["brand", "store", "daypart", "business_date"], as_index=False).agg(
        y=("y", "sum"), expected=("expected", "sum"))
    outages = dp[(dp["y"] == 0) & (dp["expected"] >= 15)]
    for r in outages.itertuples(index=False):
        out.append(dict(type="POS_OUTAGE", brand=r.brand, store=r.store, target=r.daypart,
                        daypart=r.daypart, start_date=r.business_date, end_date=r.business_date,
                        severity=0.0, expected=round(r.expected, 1), observed=0.0,
                        score=round(r.expected, 1),
                        description=f"No {r.daypart} sales at {r.store} (expected ~{r.expected:.0f} units) — possible register/POS outage"))

    # -- store-day aggregate for spikes & drops --
    sd = non_hol.groupby(["brand", "store", "business_date"], as_index=False).agg(
        y=("y", "sum"), expected=("expected", "sum"),
        var=("sigma", lambda s: float(np.sum(np.square(s)))))
    sd["sigma"] = np.sqrt(sd["var"]).clip(lower=1.0)
    sd["z"] = (sd["y"] - sd["expected"]) / sd["sigma"]
    sd["ratio"] = sd["y"] / sd["expected"].clip(lower=1e-6)

    spikes = sd[(sd["ratio"] >= 1.6) & (sd["z"] >= 4)]
    for r in spikes.itertuples(index=False):
        out.append(dict(type="DEMAND_SPIKE", brand=r.brand, store=r.store, target="ALL",
                        daypart="ALL", start_date=r.business_date, end_date=r.business_date,
                        severity=round(r.ratio, 2), expected=round(r.expected, 1),
                        observed=round(r.y, 1), score=round(float(r.z), 1),
                        description=f"Demand spike x{r.ratio:.1f} at {r.store} ({r.y:.0f} vs ~{r.expected:.0f} expected)"))

    # -- sustained drops: group consecutive low-ratio days per store (>=2 days).
    # single-day dips are usually a POS outage's store-day shadow, not a demand drop.
    drops = sd[(sd["ratio"] <= 0.65) & (sd["z"] <= -2.5)].sort_values(["store", "business_date"])
    for store, g in drops.groupby("store"):
        g = g.sort_values("business_date")
        run = []
        prev = None
        for r in g.itertuples(index=False):
            if prev is not None and (r.business_date - prev).days > 1:
                if len(run) >= 2:
                    out.append(_drop_event(run))
                run = []
            run.append(r)
            prev = r.business_date
        if len(run) >= 2:
            out.append(_drop_event(run))
    return out


def _drop_event(run) -> dict:
    r0, r1 = run[0], run[-1]
    exp = sum(r.expected for r in run)
    obs = sum(r.y for r in run)
    return dict(type="DEMAND_DROP", brand=r0.brand, store=r0.store, target="ALL", daypart="ALL",
                start_date=r0.business_date, end_date=r1.business_date,
                severity=round(obs / max(exp, 1e-6), 2), expected=round(exp, 1), observed=round(obs, 1),
                score=round(len(run) + (1 - obs / max(exp, 1e-6)) * 5, 1),
                description=f"Sustained demand drop over {len(run)}d at {r0.store} ({obs:.0f} vs ~{exp:.0f} expected)")


def detect_fraud(fb: FeatureBuilder, lookback: int) -> list[dict]:
    s = fb.sales.copy()
    sd = s.groupby(["brand", "store", "business_date"], as_index=False).agg(
        comp=("comp", "sum"), net=("net", "sum"), voids=("void_qty", "sum"), qty=("qty", "sum"))
    sd["comp_pct"] = sd["comp"] / sd["net"].clip(lower=1)
    out = []
    for (brand, store), g in sd.groupby(["brand", "store"]):
        g = g.sort_values("business_date").reset_index(drop=True)
        base = g["comp_pct"].rolling(60, min_periods=20).median().shift(1)
        mad = (g["comp_pct"] - base).abs().rolling(60, min_periods=20).median().shift(1)
        thresh = (base + CFG.VOID_COMP_Z * mad.clip(lower=0.003)).fillna(0.05)
        g["flag"] = (g["comp_pct"] > thresh) & (g["comp_pct"] > 0.04)
        recent = g[g["business_date"] >= fb.last_date - pd.Timedelta(days=lookback - 1)]
        flagged = recent[recent["flag"]]
        if flagged.empty:
            continue
        run = []
        prev = None
        for r in flagged.itertuples(index=False):
            if prev is not None and (r.business_date - prev).days > 2:
                out.append(_fraud_event(brand, store, run))
                run = []
            run.append(r)
            prev = r.business_date
        if run:
            out.append(_fraud_event(brand, store, run))
    return out


def _fraud_event(brand, store, run) -> dict:
    r0, r1 = run[0], run[-1]
    cp = np.mean([r.comp_pct for r in run])
    return dict(type="VOID_COMP_FRAUD", brand=brand, store=store, target="ALL", daypart="ALL",
                start_date=r0.business_date, end_date=r1.business_date, severity=round(float(cp), 3),
                expected=None, observed=round(float(cp), 3), score=round(float(cp) * 100, 1),
                description=f"Elevated comps ~{cp:.0%} of net over {len(run)}d at {store} — possible void/comp abuse")


def _channel_expected(y: pd.Series):
    """DOW-profile x EWMA-level expected + sigma for a single daily channel/partner series."""
    idx = y.index
    dow = idx.dayofweek
    overall = max(y.mean(), 1e-6)
    prof = np.array([max(y[dow == d].mean() / overall, 1e-3) if (dow == d).any() else 1.0
                     for d in range(7)])
    deseas = y.values / prof[dow]
    level = pd.Series(deseas, index=idx).ewm(span=45, adjust=False).mean().shift(1)
    expected = level.values * prof[dow]
    resid = y.values - expected
    sigma = np.nanstd(resid[~np.isnan(resid)][-120:]) if np.isfinite(resid).any() else overall
    return expected, max(float(sigma), 1.0)


def _channel_event(store, kind, k, run) -> dict:
    r0, r1 = run[0], run[-1]
    exp = sum(r.expected for r in run)
    obs = sum(r.y for r in run)
    label = "delivery partner" if kind == "delivery_partner" else "channel"
    return dict(type="CHANNEL_OUTAGE", brand=store.split("-")[0], store=store, target=k, daypart="ALL",
                start_date=r0.business_date.date(), end_date=r1.business_date.date(),
                severity=round(obs / max(exp, 1e-6), 2), expected=round(exp, 1), observed=round(obs, 1),
                score=round(exp - obs, 1),
                description=f"{k} {label} outage at {store} — {obs:.0f} vs ~{exp:.0f} expected over {len(run)}d")


def detect_channel(lookback: int) -> list[dict]:
    """Channel- and delivery-partner-level outages/drops (e.g. a GrabFood outage or an
    app-ordering failure) — invisible at store-total grain but material per channel."""
    if not db.table_exists("sales_channel"):
        return []
    c = db.read_sql("SELECT store, business_date, channel, delivery_partner, qty FROM sales_channel")
    if c.empty:
        return []
    c["business_date"] = pd.to_datetime(c["business_date"])
    c["delivery_partner"] = c["delivery_partner"].fillna("").astype(str)
    last = c["business_date"].max()
    win_lo = last - pd.Timedelta(days=lookback - 1)
    grains = [("channel", c.assign(k=c["channel"])),
              ("delivery_partner", c[c["delivery_partner"] != ""].assign(k=c["delivery_partner"]))]
    partner_ev, channel_ev = [], []
    for kind, cc in grains:
        daily = cc.groupby(["store", "k", "business_date"], as_index=False)["qty"].sum()
        for (store, k), g in daily.groupby(["store", "k"]):
            g = g.sort_values("business_date")
            idx = pd.date_range(g["business_date"].min(), last, freq="D")
            y = g.set_index("business_date")["qty"].reindex(idx, fill_value=0).astype(float)
            if len(y) < 60 or y.mean() < 5:
                continue
            expected, _sigma = _channel_expected(y)
            df = pd.DataFrame({"business_date": idx, "y": y.values, "expected": expected})
            df = df[df["business_date"] >= win_lo].dropna(subset=["expected"])
            hit = df[(df["y"] / df["expected"].clip(lower=1e-6) <= 0.4) & (df["expected"] >= 20)]
            hit = hit.sort_values("business_date")
            run, prev, events = [], None, (partner_ev if kind == "delivery_partner" else channel_ev)
            for r in hit.itertuples(index=False):
                if prev is not None and (r.business_date - prev).days > 1:
                    events.append(_channel_event(store, kind, k, run)); run = []
                run.append(r); prev = r.business_date
            if run:
                events.append(_channel_event(store, kind, k, run))
    # de-dupe: a partner outage also shows at its channel grain — keep the specific
    # partner event and drop the overlapping channel-grain event for the same store.
    def overlaps(a, b):
        return (a["store"] == b["store"] and a["start_date"] <= b["end_date"]
                and b["start_date"] <= a["end_date"])
    kept_channel = [ce for ce in channel_ev if not any(overlaps(ce, pe) for pe in partner_ev)]
    return partner_ev + kept_channel


def detect_inventory(fb: FeatureBuilder) -> list[dict]:
    inv = db.read_sql("SELECT * FROM inventory_snapshot")
    inv["variance_pct"] = pd.to_numeric(inv["variance_pct"], errors="coerce")
    bad = inv[inv["variance_pct"].abs() >= CFG.INVENTORY_VARIANCE_PCT]
    out = []
    for r in bad.itertuples(index=False):
        out.append(dict(type="INVENTORY_VARIANCE", brand=r.brand, store=r.store, target=r.sku,
                        daypart=None, start_date=pd.to_datetime(r.as_of).date(),
                        end_date=pd.to_datetime(r.as_of).date(), severity=round(float(r.variance_pct), 3),
                        expected=round(float(r.theoretical_on_hand), 1), observed=round(float(r.on_hand_qty), 1),
                        score=round(abs(float(r.variance_pct)) * 100, 1),
                        description=f"Inventory variance {r.variance_pct:+.0%} on {r.sku} at {r.store} (counted vs theoretical)"))
    return out


def run(lookback: int | None = None) -> dict:
    lookback = lookback or CFG.ANOMALY_LOOKBACK
    fb = FeatureBuilder()
    panel = _expected_panel(fb, lookback)
    detected = (detect_residual(panel) + detect_fraud(fb, lookback)
                + detect_inventory(fb) + detect_channel(lookback))
    run_date = fb.last_date.date()
    for i, d in enumerate(detected):
        d["anomaly_id"] = f"D{i+1:04d}"
        d["detected_on"] = run_date
    from app import council
    council.review_all(detected)   # multi-judge verdict + confidence + explanation
    from app.anomaly import drivers
    drivers.attach(detected)       # ≥3 contributing factors per anomaly (metric #5)
    df = pd.DataFrame(detected)
    if not df.empty:
        df = df.sort_values("score", ascending=False)
    db.init_output_tables(drop=False)
    from sqlalchemy import text
    with db.engine().begin() as c:
        c.execute(text("DELETE FROM anomaly"))
    n = db.write_df(df, "anomaly")
    summary = {"detected": n, "by_type": df["type"].value_counts().to_dict() if not df.empty else {}}
    summary["scoring"] = score(df, lookback)
    return summary


def score(detected: pd.DataFrame, lookback: int) -> dict:
    """Grade detections vs injected ground truth (recent window only)."""
    if not db.table_exists("anomaly_ground_truth"):
        return {}
    gt = db.read_sql("SELECT * FROM anomaly_ground_truth")
    for c in ("start_date", "end_date"):
        gt[c] = pd.to_datetime(gt[c])
    last = pd.to_datetime(db.read_sql("SELECT MAX(business_date) m FROM sales_line")["m"].iloc[0])
    win_lo = last - pd.Timedelta(days=lookback - 1)
    # inventory/fraud GT can be anywhere; residual GT only detectable in-window
    result = {}
    for typ in ["POS_OUTAGE", "DEMAND_SPIKE", "DEMAND_DROP", "VOID_COMP_FRAUD",
                "INVENTORY_VARIANCE", "CHANNEL_OUTAGE"]:
        g = gt[gt["type"] == typ].copy()
        if typ in ("POS_OUTAGE", "DEMAND_SPIKE", "DEMAND_DROP", "CHANNEL_OUTAGE"):
            g = g[g["end_date"] >= win_lo]
        d = detected[detected["type"] == typ] if not detected.empty else detected
        matched_gt, matched_det = set(), set()
        for gi, gr in g.iterrows():
            for di, dr in d.iterrows():
                if dr["store"] != gr["store"]:
                    continue
                ds, de = pd.to_datetime(dr["start_date"]), pd.to_datetime(dr["end_date"])
                if de >= gr["start_date"] and ds <= gr["end_date"]:
                    if typ == "POS_OUTAGE" and dr["daypart"] not in (gr["daypart"], "ALL"):
                        continue
                    if typ == "INVENTORY_VARIANCE" and dr["target"] != gr["target"]:
                        continue
                    matched_gt.add(gi)
                    matched_det.add(di)
        tp = len(matched_gt)
        fp = (len(d) - len(matched_det)) if not d.empty else 0
        fn = len(g) - tp
        result[typ] = {
            "truth": int(len(g)), "detected": int(len(d)), "true_pos": int(tp),
            "false_pos": int(max(fp, 0)),
            "recall": round(tp / len(g), 2) if len(g) else None,
            "precision": round(tp / (tp + max(fp, 0)), 2) if (tp + max(fp, 0)) else None,
        }
    return result


if __name__ == "__main__":
    import json
    print(json.dumps(run(), indent=2, default=str))
