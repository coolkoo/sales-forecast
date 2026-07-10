"""AI-generated intelligent report — an executive narrative over the warehouse.

Assembles the key facts (KPIs, driver days, anomalies + council verdicts, SSS,
buying) and writes an executive report. If an LLM is configured (Reports → AI
settings: Anthropic or any OpenAI-compatible endpoint) it writes the narrative;
otherwise a built-in analyst-style generator produces a solid report with zero
dependencies. EN + VI.
"""
from __future__ import annotations

import json

from app import db, llm

# LLM config now lives in the shared client (app/llm.py); re-exported so the
# /api/report/ai/config endpoints keep working unchanged.
config = llm.config
set_config = llm.set_config


def _facts() -> dict:
    from app.analytics import summary as S, pages
    sm = S.summary(); k = sm["kpis"]
    an = db.read_sql("SELECT type, store, score, council_verdict, council_confidence, description "
                     "FROM anomaly WHERE council_verdict='confirmed' ORDER BY score DESC LIMIT 6").to_dict("records") \
        if db.table_exists("anomaly") else []
    sss = pages.sss()
    buy = db.read_sql("SELECT sku, SUM(order_cost) cost FROM buying_suggestion WHERE reorder_qty>0 "
                      "GROUP BY sku ORDER BY cost DESC LIMIT 5").to_dict("records") if db.table_exists("buying_suggestion") else []
    cat = S.chart_category()[:6]
    return {"kpis": k, "run_through": sm["run_through"], "backend": sm["backend"],
            "anomalies": an, "sss": {"chain": sss["chain_sss_pct"], "comparable": len(sss["comparable"]),
                                     "new_stores": [n["store"] for n in sss["new_stores"]]},
            "top_reorders": buy, "category_mix": cat}


def _vnd(v):
    v = float(v or 0)
    return f"₫{v/1e9:.2f} tỷ" if abs(v) >= 1e9 else f"₫{v/1e6:.0f} tr" if abs(v) >= 1e6 else f"₫{v:,.0f}"


# --------------------------------------------------------------------------- #
def _heuristic(f: dict, lang: str) -> list[dict]:
    k = f["kpis"]; vi = lang == "vi"
    top_an = f["anomalies"][:3]
    an_lines = "\n".join(f"• {a['type'].replace('_',' ')} @ {a['store']} — {a['description']} "
                         f"(council {int((a['council_confidence'] or 0)*100)}%)" for a in top_an) or ("• Không có bất thường nghiêm trọng." if vi else "• No severe anomalies.")
    cat = ", ".join(f"{c['category']} {_vnd(c['net'])}" for c in f["category_mix"][:4])
    reorder = ", ".join(f"{b['sku']} ({_vnd(b['cost'])})" for b in f["top_reorders"][:4])
    if vi:
        return [
            {"heading": "Tóm tắt điều hành", "body":
             f"Dự báo doanh thu {_vnd(k['forecast_net_total'])} trong 14 ngày tới (dữ liệu đến {f['run_through']}, "
             f"~{_vnd(k['forecast_net_daily'])}/ngày, {k['growth_vs_history']:+.0%} so với lịch sử), qua mô hình {f['backend']}. "
             f"Doanh thu cùng cửa hàng (YoY) {f['sss']['chain']:+}% trên {f['sss']['comparable']} cửa hàng đủ điều kiện."},
            {"heading": "Triển vọng nhu cầu", "body":
             f"{k['busiest_day']} là ngày đông nhất; buổi {k['top_daypart']} lớn nhất. Món dẫn đầu: {k['top_item']}. "
             f"Cơ cấu nhóm món: {cat}. Lưu ý các ngày cao điểm bán lẻ VN (Quốc tế Thiếu nhi, Phụ nữ, Trung Thu) và quy luật Tết nhiều tuần."},
            {"heading": "Rủi ro & bất thường", "body": f"{k['anomalies']} bất thường được gắn cờ. Nghiêm trọng nhất:\n{an_lines}"},
            {"heading": "Kho & mua hàng", "body":
             f"Đề xuất đặt lại {_vnd(k['reorder_cost'])} trên {k.get('menu_items','')} mặt hàng. SKU lớn nhất: {reorder}."},
            {"heading": "Khuyến nghị", "body":
             f"1) Tăng ca cuối tuần & {k['busiest_day']}. 2) Ưu tiên đặt lại {reorder.split(',')[0] if reorder else 'gà tươi & dầu chiên'}. "
             f"3) Điều tra cửa hàng có gian lận hủy/tặng. 4) Cửa hàng mới ({', '.join(f['sss']['new_stores']) or 'n/a'}) đang tăng trưởng — theo dõi đường cong trưởng thành."},
        ]
    return [
        {"heading": "Executive summary", "body":
         f"Forecast revenue of {_vnd(k['forecast_net_total'])} over the next 14 days "
         f"(~{_vnd(k['forecast_net_daily'])}/day, {k['growth_vs_history']:+.0%} vs history) via the {f['backend']} model, "
         f"data through {f['run_through']}. Same-store sales are {f['sss']['chain']:+}% YoY across {f['sss']['comparable']} comparable stores."},
        {"heading": "Demand outlook", "body":
         f"{k['busiest_day']} is the busiest day and {k['top_daypart']} the largest daypart; the lead item is {k['top_item']}. "
         f"Category mix: {cat}. Watch the VN retail driver days (Children's / Women's Day, Mid-Autumn) and the multi-week Tết regime."},
        {"heading": "Risks & anomalies", "body": f"{k['anomalies']} anomalies flagged. Most severe:\n{an_lines}"},
        {"heading": "Inventory & buying", "body":
         f"Suggested reorders total {_vnd(k['reorder_cost'])}. Largest SKUs: {reorder}."},
        {"heading": "Recommendations", "body":
         f"1) Staff up weekends and {k['busiest_day']}. 2) Expedite the largest reorders ({reorder.split(',')[0] if reorder else 'fresh chicken & fryer oil'}). "
         f"3) Investigate stores flagged for void/comp abuse. 4) New stores ({', '.join(f['sss']['new_stores']) or 'none'}) are ramping — track the maturation curve."},
    ]


def _parse_sections(txt: str) -> list[dict]:
    sections, cur = [], None
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("#"):
            if cur:
                sections.append(cur)
            cur = {"heading": s.lstrip("# ").strip(), "body": ""}
        elif cur is not None:
            cur["body"] += line + "\n"
    if cur:
        sections.append(cur)
    return sections or [{"heading": "Report", "body": txt}]


def generate(lang: str = "en") -> dict:
    f = _facts()
    cfg = config()
    model_used = "built-in analyst"
    if cfg["configured"]:
        lng = "Vietnamese" if lang == "vi" else "English"
        prompt = (f"You are a retail analytics director for KFC Vietnam. Using ONLY these facts (JSON), "
                  f"write a concise executive report in {lng} with Markdown '## ' section headings: "
                  f"Executive summary, Demand outlook, Risks & anomalies, Inventory & buying, Recommendations. "
                  f"Use VND (₫). Be specific and actionable.\n\nFACTS:\n{json.dumps(f, default=str)}")
        try:
            sections = _parse_sections(llm.complete(prompt, max_tokens=1600, cfg=cfg))
            model_used = f"{cfg['provider']}:{cfg['model']}"
        except Exception as e:
            sections = _heuristic(f, lang)
            model_used = f"built-in analyst (LLM error: {str(e)[:50]})"
    else:
        sections = _heuristic(f, lang)
    return {"title": "AI executive report" if lang == "en" else "Báo cáo điều hành AI",
            "generated_for": f["run_through"], "model": model_used, "sections": sections}
