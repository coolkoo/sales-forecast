"""Shared LLM client — powers the AI executive report and natural-language Ask.

Config lives in the `llm_config` table (provider / model / api_key / base_url),
editable from Reports → "Configure LLM" or Settings → "LLM / AI endpoint". Supports
**Anthropic** and **any OpenAI-compatible** endpoint (OpenAI, Azure, self-hosted).

Nothing here is mocked: when configured, callers hit the real API; when not (or on
error) they fall back to their built-in non-LLM path and say so. `complete()` raises a
RuntimeError carrying the API's own error message, so failures are visible, not silent.
"""
from __future__ import annotations

from sqlalchemy import Column, MetaData, String, Table, text

from app import db

_MD = MetaData()
_cfg = Table("llm_config", _MD, Column("key", String, primary_key=True), Column("value", String))
KEYS = ["provider", "model", "api_key", "base_url"]
DEFAULTS = {"provider": "none", "model": "claude-sonnet-5", "api_key": "", "base_url": ""}


def ensure():
    _MD.create_all(db.engine(), tables=[_cfg], checkfirst=True)


def config() -> dict:
    ensure()
    raw = {r["key"]: r["value"] for r in db.read_sql("SELECT key, value FROM llm_config").to_dict("records")}
    out = {k: raw.get(k, DEFAULTS[k]) for k in KEYS}
    out["configured"] = bool(out["provider"] != "none" and out["api_key"])
    return out


def set_config(values: dict) -> dict:
    ensure()
    with db.engine().begin() as cx:
        for k, v in (values or {}).items():
            if k not in KEYS:
                continue
            cx.execute(text("DELETE FROM llm_config WHERE key=:k"), {"k": k})
            cx.execute(_cfg.insert(), {"key": k, "value": str(v)})
    return config()


def configured() -> bool:
    return config()["configured"]


def _call(messages: list, system: str | None, max_tokens: int, cfg: dict) -> str:
    """Core call: a full messages array → text. Raises RuntimeError with the API's message."""
    import requests
    if not cfg["configured"]:
        raise RuntimeError("no LLM configured")
    prov = cfg["provider"]
    model = cfg["model"] or ("claude-sonnet-5" if prov == "anthropic" else "gpt-4o-mini")
    try:
        if prov == "anthropic":
            body = {"model": model, "max_tokens": max_tokens, "messages": messages}
            if system:
                body["system"] = system
            r = requests.post("https://api.anthropic.com/v1/messages",
                              headers={"x-api-key": cfg["api_key"], "anthropic-version": "2023-06-01",
                                       "content-type": "application/json"}, json=body, timeout=90)
        else:
            base = (cfg["base_url"] or "https://api.openai.com/v1").rstrip("/")
            msgs = ([{"role": "system", "content": system}] if system else []) + messages
            r = requests.post(base + "/chat/completions",
                              headers={"Authorization": "Bearer " + cfg["api_key"],
                                       "content-type": "application/json"},
                              json={"model": model, "max_tokens": max_tokens, "messages": msgs}, timeout=90)
    except Exception as ex:
        raise RuntimeError(f"connection error: {ex}")
    if r.status_code >= 400:
        try:
            msg = (r.json().get("error", {}) or {}).get("message") or r.text[:300]
        except Exception:
            msg = r.text[:300]
        raise RuntimeError(f"{prov} API {r.status_code}: {msg}")
    try:
        data = r.json()
        if prov == "anthropic":
            out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text").strip()
        else:
            out = (data["choices"][0]["message"]["content"] or "").strip()
    except Exception as ex:
        raise RuntimeError(f"unexpected {prov} response: {ex}")
    if not out:
        raise RuntimeError(f"{prov} returned empty content")
    return out


def complete(prompt: str, system: str | None = None, max_tokens: int = 1600,
             cfg: dict | None = None) -> str:
    """Single-turn completion."""
    return _call([{"role": "user", "content": prompt}], system, max_tokens, cfg or config())


def chat(messages: list, system: str | None = None, max_tokens: int = 1200,
         cfg: dict | None = None) -> str:
    """Multi-turn chat: `messages` is [{role: user|assistant, content}]."""
    return _call(messages, system, max_tokens, cfg or config())


def health() -> dict:
    """Config summary + a live one-token ping, so the UI can prove it's really connected."""
    cfg = config()
    info = {"provider": cfg["provider"], "model": cfg["model"],
            "base_url": cfg["base_url"], "configured": cfg["configured"]}
    if not cfg["configured"]:
        info["status"] = "not_configured"
        info["detail"] = "No LLM set — the built-in analyst / rule engine is used."
        return info
    try:
        sample = complete("Reply with exactly: OK", max_tokens=12, cfg=cfg)
        info["status"] = "connected"
        info["detail"] = f"Live response: {sample[:60]}"
    except Exception as ex:
        info["status"] = "error"
        info["detail"] = str(ex)[:240]
    return info
