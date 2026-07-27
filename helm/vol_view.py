"""Realized-vol context for the open board (s90, after the MU BeCS read).

The board showed IV 94 and IVR 82 and nothing else, so it read as rich vol. It
was not: MU's 30-day realized was 106.6 against 94.5 implied, i.e. the premium
being sold was priced BELOW what the stock was actually doing. IV Rank answers
"rich against its own past year"; only IV-versus-realized answers "rich against
what is happening now", and the two disagreed by twelve points on a live board.
Neither number was wrong. Only one of them was on screen.

Two decisions settled by Russ when this was built:

  * DTE-MATCHED, and labelled. A 25-day contract is judged against 30-day
    realized; beyond ~45 days it is judged against 90-day ex-earnings realized.
    Judging three months of implied against one month of realized is the
    mismatch that made the HELM-101 draft's VRP-on-HV30 gate adversely selected
    (design doc section 7.2), and the label means the comparison can never be
    silently mismatched.

  * A header line for the underlying AND a per-row ratio. HV is a property of
    the name, so it belongs above the table; but skew and term structure mean
    each contract prices vol differently, so the ratio belongs on the row.

Source is hybrid, the same shape as long_exit.current_context: the day's scan
row when there is one, a live computation otherwise. A same-day board therefore
reads exactly the numbers the buy-side screen used, and an out-of-hours or
unscanned name still gets a real answer rather than a blank. Which one you got
is always stated -- W56's rule, that a blank reads as current.
"""

from datetime import datetime

# DTE at or below this compares against 30-day realized; above it, 90-day
# ex-earnings. The boundary is the point where a month of realized stops being
# a fair yardstick for the life of the contract.
DTE_SHORT_MAX = 45

# A scan row older than this is not "today" -- same constant and same reasoning
# as long_exit.CTX_MAX_AGE_DAYS. See W65: this is measured in whole days, which
# is more generous than it looks.
MAX_AGE_DAYS = 1


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None


def from_signals(conn, ticker):
    """The day's scan row for one ticker, or None when there isn't a fresh one."""
    try:
        row = conn.execute(
            'SELECT generated_at, spot_price, iv_current, iv_rank, iv_percentile, '
            'hv_30, hv_90, hv_90_ex_earn, hv_90_source, hv_252 '
            'FROM signals WHERE ticker = ? ORDER BY generated_at DESC LIMIT 1',
            (str(ticker).upper(),)).fetchone()
    except Exception:
        return None
    if not row:
        return None
    try:
        age = (datetime.now() - datetime.fromisoformat(str(row[0]))).days
    except Exception:
        age = 999
    return {
        'source': 'scan', 'asof': row[0], 'age_days': age,
        'spot': _num(row[1]), 'iv': _num(row[2]),
        'iv_rank': _num(row[3]), 'iv_percentile': _num(row[4]),
        'hv_30': _num(row[5]), 'hv_90': _num(row[6]),
        'hv_90_ex_earn': _num(row[7]), 'hv_90_source': row[8],
        'hv_252': _num(row[9]),
    }


def compute_live(ticker, conn=None):
    """Compute HV directly from price history. Returns None if unavailable.

    Uses the same hv_from_closes / hv_ex_earnings the scan uses, so a live
    reading and a scanned one are the same measurement taken at different
    times -- not two different definitions wearing one label.
    """
    try:
        import yfinance as yf
        from helm import hv_earnings as H
    except Exception:
        return None
    try:
        hist = yf.Ticker(str(ticker).upper()).history(period='1y')
        close = hist['Close'].dropna()
        if len(close) < 40:
            return None
    except Exception:
        return None
    dates = []
    if conn is not None:
        try:
            dates = H.cached_dates(conn, str(ticker).upper()) or []
        except Exception:
            dates = []
    out = {'source': 'live', 'asof': datetime.now().isoformat(), 'age_days': 0,
           'iv': None, 'iv_rank': None, 'iv_percentile': None,
           'spot': float(close.iloc[-1])}
    try:
        out['hv_30'] = H.hv_from_closes(close, 30)
        out['hv_90'] = H.hv_from_closes(close, 90)
        out['hv_252'] = H.hv_from_closes(close, 252)
        hv90x, src = H.hv_ex_earnings(close, dates, 90)
        out['hv_90_ex_earn'] = hv90x
        out['hv_90_source'] = src
    except Exception:
        return None
    return out


def vol_view(ticker, conn=None, iv_hint=None):
    """The vol context for one underlying, hybrid-sourced and always labelled.

    iv_hint lets a caller supply the underlying IV it already has (the open
    board knows it from the chain) so the ratio does not depend on a scan row
    having been written today.

    Never raises. A board must render even when every source is unavailable --
    and when it is, this says so rather than returning zeros.
    """
    view = None
    if conn is not None:
        v = from_signals(conn, ticker)
        if v and v.get('age_days') is not None and v['age_days'] <= MAX_AGE_DAYS \
                and v.get('hv_30') is not None:
            view = v
    if view is None:
        view = compute_live(ticker, conn=conn)
    if view is None:
        # last resort: a stale scan row, clearly marked, beats nothing at all
        view = from_signals(conn, ticker) if conn is not None else None
        if view is not None:
            view['source'] = 'scan (stale)'
    if view is None:
        return {'ticker': str(ticker).upper(), 'source': None, 'available': False}
    view['ticker'] = str(ticker).upper()
    view['available'] = True
    if iv_hint is not None and _num(iv_hint) is not None:
        view['iv'] = _num(iv_hint)
    return view


def hv_for_dte(view, dte):
    """(hv, label) matched to the contract's life, or (None, None).

    The label is returned, not inferred by the caller, so every surface that
    renders this agrees about which window it showed.
    """
    if not view or not view.get('available'):
        return None, None
    d = _num(dte)
    if d is not None and d > DTE_SHORT_MAX:
        hv = view.get('hv_90_ex_earn') or view.get('hv_90')
        if hv is None:
            return None, None
        label = 'HV90ex' if view.get('hv_90_source') == 'dates' else 'HV90'
        return hv, label
    hv = view.get('hv_30')
    return (hv, 'HV30') if hv is not None else (None, None)


def iv_hv_ratio(iv, view, dte):
    """Contract IV over DTE-matched realized. Below 1.00 means the premium is
    priced under what the underlying has actually been doing."""
    i = _num(iv)
    hv, _label = hv_for_dte(view, dte)
    if i is None or not hv:
        return None
    return round(i / hv, 3)


def header_line(view, dte=None):
    """One rich-markup line of vol context for the top of an open board.

    Colour carries one thing only: whether the premium is priced above or below
    realized. Green at or over 1.10, yellow under 1.00 -- because under 1.00 a
    credit strategy is selling vol cheaper than the stock is moving, which is
    the case that should stop you, and it is invisible on IV Rank alone.
    """
    if not view or not view.get('available'):
        return '[yellow]vol context unavailable — no scan row and no price history[/yellow]'
    bits = []
    iv = view.get('iv')
    if iv is not None:
        bits.append('IV %.1f' % iv)
    hv, label = hv_for_dte(view, dte if dte is not None else 30)
    if hv is not None:
        bits.append('%s %.1f' % (label, hv))
    r = iv_hv_ratio(iv, view, dte if dte is not None else 30)
    if r is not None:
        if r >= 1.10:
            tag = '[green]IV/HV %.2f — implied above realized[/green]' % r
        elif r < 1.00:
            tag = ('[yellow]IV/HV %.2f — implied BELOW realized[/yellow]' % r)
        else:
            bits.append('IV/HV %.2f' % r)
            tag = None
        if tag:
            bits.append(tag)
    if view.get('hv_252') is not None:
        bits.append('HV252 %.0f' % view['hv_252'])
    if view.get('iv_rank') is not None:
        ivp = view.get('iv_percentile')
        bits.append('IVR %.0f%s' % (view['iv_rank'],
                                    ('/IVP %.0f' % ivp) if ivp is not None else ''))
    src = view.get('source') or '?'
    age = view.get('age_days')
    if src == 'scan' and age:
        src = 'scan %dd old' % age
    bits.append('[dim](%s)[/dim]' % src)
    return '[dim]vol:[/dim] ' + '  ·  '.join(bits)
