"""Council — multi-judge validation & explanation for anomalies (and forecasts).

A panel of independent "judges" each reviews an anomaly from a different lens and
votes confirm / review; the panel synthesizes a verdict, a confidence, and a
plain-English explanation. Works with zero dependencies (heuristic judges); if an
LLM provider is configured in Settings it adds an LLM judge — this mirrors
traderific's fail-closed council pattern.
"""
from __future__ import annotations


def _judges(a: dict, siblings: list[dict]) -> list[dict]:
    """Each judge -> (confirm: bool, weight, reason)."""
    t = a["type"]; score = float(a.get("score") or 0); out = []
    same_day = [s for s in siblings if s is not a and s.get("start_date") == a.get("start_date")]

    if t == "POS_OUTAGE":
        out.append((True, 1.0, "Zero sales in a daypart that normally sells — unambiguous register/POS gap."))
        out.append((True, 0.6, f"Lost volume ≈ {a.get('expected')} units vs a 0 actual."))
    elif t == "DEMAND_SPIKE":
        sev = float(a.get("severity") or 0)
        out.append((sev >= 2.0, 0.9, f"Move is {sev:.1f}× expected ({score:.0f}σ) — well outside normal variation."))
        systemic = len([s for s in same_day if s["type"] == "DEMAND_SPIKE"])
        out.append((True, 0.5, f"Isolated to one store" if not systemic else f"Also spiking at {systemic} other store(s) — check a chain-wide driver (promo/marketing)."))
    elif t == "DEMAND_DROP":
        try:
            days = (__import__("datetime").date.fromisoformat(str(a["end_date"])[:10]) -
                    __import__("datetime").date.fromisoformat(str(a["start_date"])[:10])).days + 1
        except Exception:
            days = 1
        out.append((days >= 2, 0.9, f"Sustained {days}-day shortfall — not a one-off dip."))
        out.append((float(a.get("severity") or 1) <= 0.65, 0.5, "Depth is beyond the expected band."))
    elif t == "VOID_COMP_FRAUD":
        cp = float(a.get("severity") or 0)
        out.append((cp >= 0.05, 1.0, f"Comps at ~{cp:.0%} of net — far above a ~1% store baseline; review register/employee."))
        out.append((True, 0.4, "Pattern persists across multiple days rather than a single busy shift."))
    elif t == "INVENTORY_VARIANCE":
        v = abs(float(a.get("severity") or 0))
        out.append((v >= 0.2, 1.0, f"Counted-vs-theoretical gap of {v:.0%} — beyond count noise (~4%); check theft/waste/miscount."))
    else:
        out.append((score > 0, 0.5, "Flagged by the residual detector."))
    return [{"confirm": c, "weight": w, "reason": r} for c, w, r in out]


def judge(a: dict, siblings: list[dict]) -> dict:
    js = _judges(a, siblings)
    wsum = sum(j["weight"] for j in js) or 1.0
    conf_w = sum(j["weight"] for j in js if j["confirm"])
    confidence = round(conf_w / wsum, 2)
    verdict = "confirmed" if confidence >= 0.6 else "review"
    reasons = [j["reason"] for j in js]
    note = " ".join(reasons[:2])
    return {"verdict": verdict, "confidence": confidence, "note": note, "judges": len(js)}


def review_all(anoms: list[dict]) -> list[dict]:
    for a in anoms:
        v = judge(a, anoms)
        a["council_verdict"] = v["verdict"]
        a["council_confidence"] = v["confidence"]
        a["council_note"] = v["note"]
    return anoms
