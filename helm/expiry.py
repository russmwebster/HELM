"""Settlement value for expired option legs. HELM-138 / W74 (s93).

An expired contract quotes nothing, ever, so any marking path that chases
live quotes goes permanently silent on it. That is the W74 shape: the EQT
diagonal's front leg expired 2026-07-24 and every later check wrote the
position a GOOD row with pnl_unrealized NULL -- six slots, deterministic,
invisible to every reader that trusts GOOD.

The honest mark for an expired option is its settlement intrinsic: the
underlying's official close on expiry day against the strike. That number
is a fact fixed at expiry -- it never changes afterward, so it is safe to
use as a standing mark for as long as the leg stays in the book.

DB-free (the lc_screen / vol_read pattern). The only I/O is a yfinance
daily-history read, memoized per (ticker, expiration) for the process.
Returns None whenever the close cannot be established -- callers must
treat None as "unmarkable" and never invent a value (HELM-095 discipline).
"""

from datetime import date, timedelta
from typing import Optional

# (ticker, YYYY-MM-DD) -> close price or None. Process-lifetime cache: the
# snapshot agent marks ~60 positions per run and must not re-fetch history
# for every leg of every position of the same name.
_close_cache: dict = {}


def expiry_close(ticker: str, expiration: str) -> Optional[float]:
    """Official underlying close on expiry day, or None if unknowable."""
    key = (ticker, (expiration or "")[:10])
    if key in _close_cache:
        return _close_cache[key]
    px = None
    try:
        exp = date.fromisoformat(key[1])
        if exp <= date.today():
            import yfinance as yf
            h = yf.Ticker(ticker).history(
                start=exp.isoformat(),
                end=(exp + timedelta(days=1)).isoformat())
            if h is not None and len(h) > 0:
                px = round(float(h["Close"].iloc[0]), 4)
    except Exception:
        px = None
    _close_cache[key] = px
    return px


def settlement_intrinsic(ticker: str, option_type: str, strike,
                         expiration: str) -> Optional[float]:
    """Settled per-share value of an EXPIRED option, or None if unknowable.

    max(0, S - K) for calls, max(0, K - S) for puts, S = expiry-day close.
    Only answers for genuinely expired contracts (expiration strictly before
    today): a contract on or after its expiry date is still quoting, and no
    caller may accidentally replace a live quote with intrinsic.
    """
    try:
        exp = date.fromisoformat((expiration or "")[:10])
    except Exception:
        return None
    if exp >= date.today():
        return None  # not expired yet (expiry day itself still quotes)
    px = expiry_close(ticker, expiration)
    if px is None or strike is None:
        return None
    ot = (option_type or "").upper()
    if ot == "CALL":
        return round(max(0.0, px - float(strike)), 2)
    if ot == "PUT":
        return round(max(0.0, float(strike) - px), 2)
    return None
