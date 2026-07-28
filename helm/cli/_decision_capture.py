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
                "rsi_14", "atr_14", "price_vs_52wk_pct",
                'hv_30', 'vrp', 'vrp_ratio', 'vol_bucket',  # HELM-090 p1
                'adx', 'plus_di', 'minus_di', 'obv_trend',  # HELM-103
                'hv_30_ex_earn', 'hv_30_source',              # s90
                'hv_90', 'hv_90_ex_earn', 'hv_90_source', 'hv_252',
                'iv_hv90_ratio', 'lc_screen_pass', 'lc_screen_rank',
                'lc_screen_reject', 'lc_rank_score', 'lc_gates_json',  # HELM-101
                'iv_hv90_ratio_xearn', 'earn_days_since',
                'earn_in_hv90_window',   # HELM-133 - logged, never gated
                'strategy_shadow')       # HELM-136 - route the sell gate overrode


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


def attach_days_to_earnings(results, generated_at=None):
    """Write days_to_earnings onto each scan row, from the watchlist cache.

    persist_scan_signals has always computed this on its way into the signals
    table. HELM-101 G4 needs it EARLIER -- at screen time, before persistence --
    so it is lifted into a helper both paths use rather than computed twice from
    the same source. That is the same collapse W9 applied to the pulse
    arithmetic and W11 applied to the earnings date: two copies of one fact
    disagree eventually, and here they would disagree about whether a name is
    inside its earnings ramp.

    Returns the number of rows annotated. Never raises: a scan must not fail
    because an earnings-cache read did.
    """
    if not results:
        return 0
    try:
        rows = get_conn().execute(
            "SELECT ticker, next_earnings FROM watchlist").fetchall()
        earn = {r["ticker"]: r["next_earnings"] for r in rows}
    except Exception:
        earn = {}
    ts = generated_at or datetime.now().isoformat()
    n = 0
    for res in results:
        if not isinstance(res, dict) or not res.get("ticker"):
            continue
        try:
            res["days_to_earnings"] = days_until(earn.get(res["ticker"]), ts)
            n += 1
        except Exception:
            res.setdefault("days_to_earnings", None)
    return n


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
        # HELM-101 step 4: prefer the value attach_days_to_earnings()
        # already wrote, so the screen and the stored row cannot disagree
        # about the same fact. Falls back to computing it, so this path is
        # unchanged for any caller that does not attach first.
        _dte = res.get("days_to_earnings")
        if _dte is None:
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

