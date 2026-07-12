"""
helm/vol_context.py — volatility-context entry features (HELM-081).

Read-only IBKR fetch of hv_30d and option skew for an entry snapshot, plus a
best-effort backfill that writes them onto a freshly-booked position's
entry_snapshots row(s). Validated against IBKR frozen data 2026-07-12.

  * hv_30d  -> reqHistoricalData(whatToShow='HISTORICAL_VOLATILITY')
  * skew    -> reqMktData(opt,'106') + modelGreeks.impliedVol at +/-wing_pct

Design notes (HELM-081): fixed +/-7% OTM wings (25-delta risk-reversal is the
alt convention); a thin wing can return no modelGreeks so skew_value may be
None even when hv_30d populates; ~7s of IB calls per name, so backfill runs
AFTER booking and never blocks a trade.
"""
from __future__ import annotations
import math
import datetime as dt


def _num(v):
    try:
        if v is None:
            return None
        f = float(v)
        return None if math.isnan(f) or f in (-1.0, 0.0) else round(f, 4)
    except Exception:
        return None


def vol_context(ib, ticker, spot=None, expiry=None, strikes=None,
                wing_pct=0.07, settle=4):
    """Read-only IBKR volatility-context features. Keys: hv_30d, skew_put_iv,
    skew_call_iv, skew_value, skew_wing_pct, expiry, put_strike, call_strike.
    Never raises; missing -> None. Submits no orders."""
    from ib_insync import Stock, Option
    try:
        from helm.ibkr import to_ibkr_symbol
        sym = to_ibkr_symbol(ticker)
    except Exception:
        sym = ticker
    out = {'hv_30d': None, 'skew_put_iv': None, 'skew_call_iv': None,
           'skew_value': None, 'skew_wing_pct': wing_pct, 'expiry': expiry,
           'put_strike': None, 'call_strike': None}
    try:
        stk = Stock(sym, 'SMART', 'USD')
        ib.qualifyContracts(stk)
    except Exception:
        return out
    if spot is None:
        try:
            t = ib.reqMktData(stk, '', False, False)
            ib.sleep(2)
            spot = _num(t.marketPrice()) or _num(t.close)
        except Exception:
            pass
    try:
        hv = ib.reqHistoricalData(stk, endDateTime='', durationStr='1 M',
            barSizeSetting='1 day', whatToShow='HISTORICAL_VOLATILITY',
            useRTH=True, formatDate=1, keepUpToDate=False)
        out['hv_30d'] = _num(hv[-1].close) if hv else None
    except Exception:
        pass
    try:
        if strikes is None or expiry is None:
            cid = getattr(stk, 'conId', 0) or 0
            params = ib.reqSecDefOptParams(sym, '', 'STK', cid)
            ib.sleep(1)
            exps = sorted({e for p in params for e in p.expirations})
            strikes = sorted({float(s) for p in params for s in p.strikes})
            today = dt.date.today()
            def _dte(e):
                return (dt.date(int(e[:4]), int(e[4:6]), int(e[6:8])) - today).days
            expiry = expiry or next((e for e in exps if _dte(e) >= 10),
                                    exps[0] if exps else None)
        out['expiry'] = expiry
        if spot and expiry and strikes:
            def _near(x):
                return min(strikes, key=lambda s: abs(s - x))
            kp = _near(spot * (1 - wing_pct)); kc = _near(spot * (1 + wing_pct))
            put = Option(sym, expiry, kp, 'P', 'SMART')
            call = Option(sym, expiry, kc, 'C', 'SMART')
            ib.qualifyContracts(put, call)
            tp = ib.reqMktData(put, '106', False, False)
            tc = ib.reqMktData(call, '106', False, False)
            ib.sleep(settle)
            piv = _num(getattr(tp.modelGreeks, 'impliedVol', None)) if tp.modelGreeks else None
            civ = _num(getattr(tc.modelGreeks, 'impliedVol', None)) if tc.modelGreeks else None
            out.update({'put_strike': kp, 'call_strike': kc,
                        'skew_put_iv': piv, 'skew_call_iv': civ,
                        'skew_value': (round(piv - civ, 4)
                                       if piv is not None and civ is not None else None)})
    except Exception:
        pass
    return out


def backfill_entry_vol(position_id, ticker, ib=None, spot=None, expiry=None,
                       wing_pct=0.07, settle=4):
    """Best-effort: fetch vol_context and write hv_30d + skew onto the
    position's entry snapshot(s). Never raises; returns the written dict or
    None. Runs just after a position is booked (HELM-081)."""
    try:
        if ib is None:
            from helm.ibkr import get_ib
            ib = get_ib(readonly=True)
        vc = vol_context(ib, ticker, spot=spot, expiry=expiry,
                         wing_pct=wing_pct, settle=settle)
        if not any(vc.get(k) is not None for k in
                   ('hv_30d', 'skew_put_iv', 'skew_call_iv', 'skew_value')):
            return None
        from helm.db import transaction
        with transaction() as conn:
            conn.execute(
                "UPDATE entry_snapshots SET hv_30d = ?, skew_put_iv = ?, "
                "skew_call_iv = ?, skew_value = ? WHERE position_id = ?",
                (vc.get('hv_30d'), vc.get('skew_put_iv'), vc.get('skew_call_iv'),
                 vc.get('skew_value'), position_id))
        return vc
    except Exception:
        return None
