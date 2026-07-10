"""Natural-language → SQL analytics + a semantic data catalog.

Answers plain-English questions over the warehouse with a safe, read-only query
builder (intent detection + entity extraction), and ranks tables/columns by
relevance to a query (a lightweight semantic catalog — the Qdrant-equivalent).

Guardrails: only SELECT, only whitelisted tables, always LIMIT-bounded. An LLM
provider can be wired in later (settings) to translate free-form questions; the
built-in intent engine works with zero dependencies.
"""
from __future__ import annotations

import re

from app import db

# --- data catalog (table + column descriptions) ---------------------------
CATALOG = {
    "sales_line": {"desc": "Daily sales by store, daypart and menu item — units and money.",
                   "columns": {"brand": "brand code", "store": "store id", "business_date": "date",
                               "daypart": "lunch/dinner/late", "menu_item_id": "item id",
                               "menu_item": "item name", "category": "menu category",
                               "qty": "units sold", "net": "net revenue", "gross": "gross revenue",
                               "discount": "discount $", "comp": "comped $", "void_qty": "voided units"}},
    "forecast": {"desc": "Forecast per store/item/daypart/day — p05/p50/p95 and expected net.",
                 "columns": {"store": "store", "menu_item_id": "item", "daypart": "daypart",
                             "target_date": "forecast date", "p05": "low", "p50": "expected",
                             "p95": "high", "expected_units": "expected units", "expected_net": "expected $"}},
    "anomaly": {"desc": "Detected anomalies — outages, spikes, drops, fraud, inventory variance.",
                "columns": {"type": "anomaly type", "store": "store", "target": "sku/daypart",
                            "start_date": "from", "end_date": "to", "score": "severity", "description": "detail"}},
    "buying_suggestion": {"desc": "Suggested purchase-order reorders by store and SKU.",
                          "columns": {"store": "store", "sku": "ingredient", "reorder_qty": "order qty",
                                      "on_hand": "on hand", "order_cost": "cost", "lead_time_days": "lead time"}},
    "inventory_snapshot": {"desc": "On-hand vs theoretical inventory by store and SKU.",
                           "columns": {"store": "store", "sku": "ingredient", "on_hand_qty": "on hand",
                                       "variance_pct": "variance %", "as_of": "date"}},
    "menu_item": {"desc": "Menu catalog — item, category, price.",
                  "columns": {"menu_item_id": "id", "name": "name", "category": "category", "price": "price"}},
    "store": {"desc": "Store directory — brand, region, location.",
              "columns": {"store": "id", "brand": "brand", "region": "region"}},
    "prep_plan": {"desc": "Prep + thaw plan for the next service day.",
                  "columns": {"store": "store", "daypart": "daypart", "menu_item": "item",
                              "prep_units": "prep qty", "sku": "ingredient", "pull_from_freezer_at": "thaw pull time"}},
}
WHITELIST = set(CATALOG)

_STOP = set("the a an of for by in on to and or is are what which show me list top give get how "
            "many much this that all with per from".split())


def _tok(s: str):
    return [w for w in re.findall(r"[a-z0-9_]+", s.lower()) if w not in _STOP]


def search_catalog(q: str, k: int = 5) -> list[dict]:
    """Rank tables by token overlap between the query and table/column text (semantic-ish)."""
    qt = set(_tok(q))
    out = []
    for t, meta in CATALOG.items():
        doc = t + " " + meta["desc"] + " " + " ".join(meta["columns"]) + " " + " ".join(meta["columns"].values())
        dt = set(_tok(doc))
        overlap = len(qt & dt)
        score = overlap / (len(qt) + 1e-6)
        out.append({"table": t, "desc": meta["desc"], "score": round(score, 3), "hits": overlap})
    return sorted(out, key=lambda r: r["hits"], reverse=True)[:k]


# --- entity extraction -----------------------------------------------------
def _entities(q: str) -> dict:
    ql = q.lower()
    e = {}
    m = re.search(r"kfc-?\s?(\d{3,4})", ql)
    if m:
        e["store"] = "KFC-" + m.group(1).zfill(4)
    for dp in ("breakfast", "lunch", "dinner", "late"):
        if dp in ql:
            e["daypart"] = dp
    d = re.search(r"(20\d{2}-\d{2}-\d{2})", q)
    if d:
        e["date"] = d.group(1)
    for cat in ("chicken", "side", "sandwich", "beverage", "dessert", "entree"):
        if cat in ql:
            e["category"] = cat
    m = re.search(r"top\s+(\d+)", ql)
    e["n"] = int(m.group(1)) if m else 10
    return e


def _q(sql, p=None):
    df = db.read_sql(sql, p or {})
    for c in df.columns:
        if df[c].dtype.kind in ("M", "m"):
            df[c] = df[c].astype(str)
        elif df[c].dtype.kind == "f":          # round floats here (portable — SQL ROUND(float,n) errors on Postgres)
            df[c] = df[c].round(0)
    return {"columns": list(df.columns), "rows": df.head(200).to_dict("records"), "sql": sql}


# --- LLM path: translate ANY question into one safe, read-only SELECT ------
# Sensitive tables are never exposed to the query generator.
_DENY = {"app_user", "app_session", "llm_config", "source_connection", "alert_config",
         "source_upload_log"}
_FORBIDDEN = re.compile(r"\b(insert|update|delete|drop|alter|create|attach|detach|pragma|truncate|"
                        r"grant|revoke|vacuum|copy|merge|call|exec|execute|into)\b", re.I)


def _safe_schema():
    """Introspect the live DB → (allowed table set, compact schema text) minus sensitive tables."""
    from sqlalchemy import inspect
    insp = inspect(db.engine())
    allowed, lines = set(), []
    for t in sorted(insp.get_table_names()):
        if t in _DENY or t.startswith(("pg_", "sql_", "sqlite_")):
            continue
        try:
            cols = [f"{c['name']}:{str(c['type']).split('(')[0].lower()}" for c in insp.get_columns(t)]
        except Exception:
            continue
        allowed.add(t)
        desc = CATALOG.get(t, {}).get("desc", "")
        lines.append(f"- {t}({', '.join(cols)})" + (f" — {desc}" if desc else ""))
    return allowed, "\n".join(lines)


def _safe_sql(raw: str, allowed: set) -> str:
    """Sanitise + validate LLM-generated SQL: single read-only SELECT over allowed tables only."""
    s = re.sub(r"^```[a-zA-Z]*\s*", "", raw.strip()).replace("```", "").strip()
    s = s.split(";")[0].strip()                          # first statement only
    low = s.lower()
    if not low.startswith("select"):
        raise ValueError("generated query was not a SELECT")
    if _FORBIDDEN.search(low):
        raise ValueError("only read-only SELECT is permitted")
    refs = set(re.findall(r"\b(?:from|join)\s+([a-zA-Z_][a-zA-Z0-9_.]*)", low))
    if not refs:
        raise ValueError("no table referenced")
    for tbl in refs:
        base = tbl.split(".")[-1]
        if base not in allowed or tbl.startswith(("pg_", "information_schema", "sqlite_")):
            raise ValueError(f"table not permitted: {tbl}")
    if not re.search(r"\blimit\b", low):     # word-boundary: the model may put LIMIT on its own line
        s = s.rstrip("; ") + " LIMIT 500"
    return s


def _llm_answer(q: str, cfg: dict) -> dict:
    from app import llm
    allowed, schema = _safe_schema()
    dialect = db.engine().dialect.name    # 'postgresql' | 'sqlite'
    system = (
        "You are a SQL analyst for a KFC Vietnam sales-forecasting & operations warehouse. "
        f"Translate the user's question into ONE read-only SQL SELECT for a **{dialect}** database. "
        "Use ONLY the tables and columns in the schema below (shown as name:type) — you may JOIN, GROUP BY, "
        "aggregate, and use subqueries to answer data and correlation questions. Do NOT use CTEs, multiple "
        "statements, or any write/DDL. Always add a LIMIT (<=500). Return ONLY the SQL — no prose, no markdown.\n"
        "IMPORTANT: date-like columns typed 'varchar' (e.g. sales_line.business_date, weather.business_date) hold "
        "text 'YYYY-MM-DD'; when comparing them to real date columns (e.g. forecast.target_date) CAST to match, "
        "and derive day-of-week/weekend from the `calendar` table rather than DB-specific date functions.\n\n"
        "SCHEMA:\n" + schema)
    sql = _safe_sql(llm.complete(q, system=system, max_tokens=500, cfg=cfg), allowed)
    r = _q(sql)
    r.update({"intent": "llm", "engine": f"{cfg['provider']}:{cfg['model']}",
              "answer": f"Answered by {cfg['provider']} · {cfg['model']}"})
    return r


def _infer_viz(question: str, columns: list, rows: list) -> dict:
    """Suggest a chart for the result: honours an explicit 'pie/bar/line' in the question,
    else infers from the shape (categorical→bar, date series→line, small set + 'pie'→pie)."""
    if not rows or not columns:
        return {"type": "none"}
    ql = (question or "").lower()

    def _isnum(c):
        vals = [r.get(c) for r in rows[:25] if r.get(c) is not None]
        return bool(vals) and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals)

    num = [c for c in columns if _isnum(c)]
    if not num:
        return {"type": "none"}
    txt = [c for c in columns if c not in num]
    date_col = next((c for c in columns if re.search(r"date|month|target_date|business_date|_at$|(^|_)day($|_)",
                                                     c.lower())), None)
    if re.search(r"unit|qty|quantity|sold|count", ql):
        value = next((c for c in num if re.search(r"unit|qty|count|n_", c.lower())), num[0])
    else:
        value = next((c for c in num if re.search(r"net|rev|sales|amount|cost|value|total|price", c.lower())), num[-1])

    if re.search(r"\bpie\b|donut", ql):
        want = "pie"
    elif re.search(r"\bline\b|\btrend\b|over time|\bby day\b|\bdaily\b|time series|timeline", ql):
        want = "line"
    elif re.search(r"\bbar\b|\bcolumn\b|chart|graph|plot|visuali", ql):
        want = "bar"
    else:
        want = None

    if date_col and (want == "line" or want is None) and len(rows) >= 3:
        return {"type": "line", "x": date_col, "value": value}
    if want == "line":          # asked for a line but there's no time axis → fall back to bars
        want = "bar"
    label = txt[0] if txt else (date_col or columns[0])
    if want == "pie" and 1 < len(rows) <= 8:
        return {"type": "pie", "label": label, "value": value}
    if want in ("bar", "pie") or (want is None and txt and 1 < len(rows) <= 25):
        return {"type": "bar", "label": label, "value": value}
    return {"type": "none"}


def _dispatch(q: str) -> dict:
    from app import llm
    try:
        cfg = llm.config()
    except Exception:
        cfg = {"configured": False}
    if q and cfg.get("configured"):
        try:
            return _llm_answer(q, cfg)
        except Exception as ex:
            fb = _rule_answer(q)
            fb["llm_note"] = f"LLM path fell back to the rule engine: {str(ex)[:140]}"
            return fb
    return _rule_answer(q)


def answer(question: str) -> dict:
    """LLM-first when configured (answers arbitrary data/correlation questions), with the
    deterministic rule engine as fallback — then attaches a suggested chart (viz)."""
    q = (question or "").strip()
    res = _dispatch(q)
    try:
        if isinstance(res, dict) and res.get("rows") and res.get("intent") not in ("unknown", "error"):
            res["viz"] = _infer_viz(q, res.get("columns") or list(res["rows"][0].keys()), res["rows"])
    except Exception:
        pass
    return res


# --- deterministic rule engine (fallback / zero-config) --------------------
def _rule_answer(question: str) -> dict:
    q = (question or "").strip()
    ql = q.lower()
    e = _entities(q)
    where, p = [], {}
    if e.get("store"):
        where.append("store = :store"); p["store"] = e["store"]
    if e.get("daypart"):
        where.append("daypart = :daypart"); p["daypart"] = e["daypart"]
    wsql = (" WHERE " + " AND ".join(where)) if where else ""

    try:
        # anomalies / problems / fraud
        if re.search(r"anomal|outage|fraud|spike|drop|problem|issue|variance", ql):
            w = " WHERE " + " AND ".join(where) if where else ""
            r = _q(f"SELECT type, store, target, start_date, end_date, score, description FROM anomaly{w} "
                   f"ORDER BY score DESC LIMIT {e['n']}", p)
            return {**r, "intent": "anomalies", "answer": f"Top {e['n']} anomalies" + (f" at {e['store']}" if e.get('store') else " (all stores)")}

        # reorder / buying / purchasing
        if re.search(r"reorder|buy|buying|purchase|order|restock|po\b", ql):
            w = (" WHERE " + " AND ".join(where + ["reorder_qty > 0"])) if where else " WHERE reorder_qty > 0"
            r = _q(f"SELECT store, sku, reorder_qty, uom, on_hand, order_cost, lead_time_days FROM buying_suggestion{w} "
                   f"ORDER BY order_cost DESC LIMIT {e['n']}", p)
            return {**r, "intent": "buying", "answer": "Suggested reorders" + (f" for {e['store']}" if e.get('store') else "")}

        # forecast
        if re.search(r"forecast|predict|expect|next \d+ day|will sell|projection", ql):
            w = " WHERE " + " AND ".join(where) if where else ""
            r = _q(f"SELECT store, menu_item_id, daypart, target_date, p50 AS expected_units, expected_net FROM forecast{w} "
                   f"ORDER BY target_date, store LIMIT 200", p)
            return {**r, "intent": "forecast", "answer": "Forecast" + (f" for {e['store']}" if e.get('store') else " (all stores)")}

        # thaw / prep
        if re.search(r"thaw|prep|freezer|defrost|cook", ql):
            w = " WHERE " + " AND ".join(where + ["frozen"]) if where else " WHERE frozen"
            r = _q(f"SELECT store, daypart, menu_item, sku, prep_units, pull_from_freezer_at FROM prep_plan{w} "
                   f"ORDER BY pull_from_freezer_at LIMIT {e['n']}", p)
            return {**r, "intent": "prep", "answer": "Prep / thaw plan"}

        # top items / best sellers  (sales or forecast)
        if re.search(r"top|best.?sell|most popular|biggest|highest", ql) and re.search(r"item|menu|product|sell|dish", ql):
            cat = " AND category = :cat" if e.get("category") else ""
            if e.get("category"):
                p["cat"] = e["category"]
            base = "forecast" if "forecast" in ql or "will" in ql else "sales_line"
            if base == "sales_line":
                w = " WHERE 1=1" + (" AND store=:store" if e.get("store") else "") + cat
                r = _q(f"SELECT menu_item, SUM(qty) units, SUM(net) net FROM sales_line{w} "
                       f"GROUP BY menu_item ORDER BY net DESC LIMIT {e['n']}", p)
            else:
                w = " WHERE 1=1" + (" AND store=:store" if e.get("store") else "")
                r = _q(f"SELECT menu_item_id, SUM(expected_net) net FROM forecast{w} "
                       f"GROUP BY menu_item_id ORDER BY net DESC LIMIT {e['n']}", p)
            return {**r, "intent": "top_items", "answer": f"Top {e['n']} items by revenue"}

        # sales by store / region / daypart / category
        if re.search(r"sales|revenue|net|units|sold", ql):
            if "region" in ql:
                r = _q("SELECT s.region, SUM(sl.net) net FROM sales_line sl JOIN store s ON sl.store=s.store "
                       "GROUP BY s.region ORDER BY net DESC")
                return {**r, "intent": "sales_by_region", "answer": "Sales by region"}
            if "daypart" in ql:
                r = _q(f"SELECT daypart, SUM(net) net, SUM(qty) units FROM sales_line{wsql} GROUP BY daypart ORDER BY net DESC", p)
                return {**r, "intent": "sales_by_daypart", "answer": "Sales by daypart"}
            if "category" in ql:
                r = _q(f"SELECT category, SUM(net) net FROM sales_line{wsql} GROUP BY category ORDER BY net DESC", p)
                return {**r, "intent": "sales_by_category", "answer": "Sales by category"}
            if e.get("date"):
                p["date"] = e["date"]
                r = _q(f"SELECT store, SUM(net) net, SUM(qty) units FROM sales_line WHERE business_date=:date "
                       f"GROUP BY store ORDER BY net DESC", p)
                return {**r, "intent": "sales_on_date", "answer": f"Sales on {e['date']}"}
            # default: by store
            r = _q(f"SELECT store, SUM(net) net, SUM(qty) units FROM sales_line{wsql} GROUP BY store ORDER BY net DESC", p)
            return {**r, "intent": "sales_by_store", "answer": "Sales by store"}

        # fallback: catalog search to guide the user
        cats = search_catalog(q)
        return {"intent": "unknown", "answer": "I couldn't map that to a query. Related data:",
                "columns": ["table", "what it holds", "relevance"],
                "rows": [{"table": c["table"], "what it holds": c["desc"], "relevance": c["score"]} for c in cats],
                "sql": None,
                "suggestions": ["top items by revenue", "sales by daypart", "anomalies at KFC-0533",
                                "reorders for KFC-0421", "forecast for KFC-0888", "sales by region"]}
    except Exception as ex:
        return {"intent": "error", "answer": f"Query failed: {ex}", "columns": [], "rows": [], "sql": None}
