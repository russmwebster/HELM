"""Decision-capture (policy v0): persist scan candidates into the signals table.

Additive helper invoked from scan after the candidate set is built. Captures
HELM's read on every scanned name (road-not-taken included) so the decision
ledger accumulates. russ_* / spec_* fields stay at their defaults (PENDING) and
are resolved later by reconcile.
"""
from __future__ import annotations

import dataclasses
from datetime import datetime

from helm.models.signal import Signal
from helm.db import get_conn  # HELM-EARN-SIGNAL-v1
from helm.earnings import days_until, earnings_warning

_PASSTHROUGH = ("iv_current", "iv_rank", "ema_20", "sma_50", "sma_200",
                "rsi_14", "atr_14", "price_vs_52wk_pct")


def _bias_dir(score):
    if not isinstance(score, (int, float)):
        return "NEUTRAL"
    if score > 0:
        return "BULLISH"
    if score < 0:
        return "BEARISH"
    return "NEUTRAL"


_FIT_MAP = {
    "HIGH": "STRONG", "STRONG": "STRONG",
    "GOOD": "GOOD",
    "MODERATE": "MODERATE", "MEDIUM": "MODERATE",
    "LOW": "WEAK", "WEAK": "WEAK",
}


def _fit_grade(conviction):
    if conviction is None:
        return None
    return _FIT_MAP.get(str(conviction).strip().upper())


def persist_scan_signals(results, policy_version="v0", generated_at=None):
    """Persist each scanned candidate as a Signal row. Returns (saved, skipped)."""
    if not results:
        return (0, 0)
    fields = {f.name for f in dataclasses.fields(Signal)}
    # HELM-EARN-SIGNAL-v1: cache watchlist next_earnings for the scanned names
    try:
        _earn_rows = get_conn().execute("SELECT ticker, next_earnings FROM watchlist").fetchall()
        _earn_map = {r["ticker"]: r["next_earnings"] for r in _earn_rows}
    except Exception:
        _earn_map = {}
    ts = generated_at or datetime.now().isoformat()
    saved = 0
    skipped = 0
    for res in results:
        if not res or res.get("error") or not res.get("ticker"):
            skipped += 1
            continue
        score = res.get("bias_score")
        bias = _bias_dir(score)
        factors = res.get("bias_factors")
        reasoning = "; ".join(factors) if isinstance(factors, list) else (factors or None)
        recs = [{
            "strategy": res.get("strategy"),
            "fit": _fit_grade(res.get("conviction")),
            "conviction": res.get("conviction"),
            "rationale": res.get("strategy_rationale"),
        }]
        _ed = _earn_map.get(res["ticker"])
        _dte = days_until(_ed, ts)
        payload = {
            "spot_price": res.get("price"),
            "iv_percentile": res.get("iv_pct"),
            "auto_bias": bias,
            "auto_bias_score": score,
            "auto_bias_reasoning": reasoning,
            "helm_policy_version": policy_version,
            "earnings_date": _ed,
            "days_to_earnings": _dte,
            "earnings_warning": earnings_warning(_dte),
        }
        for k in _PASSTHROUGH:
            if k in res:
                payload[k] = res.get(k)
        payload = {k: v for k, v in payload.items() if k in fields}
        try:
            Signal.create(ticker=res["ticker"], confirmed_bias=bias,
                          recommendations=recs, generated_at=ts, **payload)
            saved += 1
        except Exception:
            skipped += 1
    return (saved, skipped)

