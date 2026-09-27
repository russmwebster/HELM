"""HELM-101 step 4 -- the buy-side screen.

A dedicated screen for the long-call path, deliberately NOT a branch of
bias_to_strategy. The sell side and the buy side earn money in opposite ways --
selling harvests the variance premium, buying pays it -- so one function
flipping between them on technical direction had the roles inverted. That was
the whole argument for HELM-100's split; this module is the buy half.

Spec: claude/HELM-101-execution-addendum.md section 2, with the two parameters
it left open settled by Russ in s90:

  * G5 uses the ABSOLUTE HV252 ceiling. Hu & Jacobs is a level effect -- calls
    on high-vol underlyings return far less -- so a level test is the closer
    match, and it does not move between scans. The board-relative quintile is
    computed and logged on every row but NEVER acted on, the same arrangement
    long_exit uses for _dte_ratchet_floor: the log decides which was right.
  * The screen is NON-ROUTING in this ship. It records a verdict and a rank and
    books nothing. Paper routing is a separate, later switch.

W195 (Russ, 2026-09-27) -- lc-screen-v2. What changed from v1, and what did not:
  * G1 (bias >= +2 and the full MA stack) is NO LONGER A GATE. Bias and the
    stack are still measured and recorded on every row (gates['g1']), marked
    gate: false, so the log can still say what G1 would have done.
  * NEW market gate: the S&P 500 must be above its 200-day average, read at
    scan time and passed in by the caller (`market=`). Unknown fails CLOSED:
    no reading, no new longs.
  * Rank = 50% calmness + 50% cheapness, no RSI penalty. Calmness is 1 at
    HV252 <= 20%, 0 at 40% (the G5 ceiling), linear between. Cheapness is the
    unchanged vol_cheapness (IV / HV90). Trend quality and the RSI penalty
    are still computed and recorded as components; neither is scored.
  * A fixed rank BAR of 0.72 is part of the pass: a name clearing every gate
    but scoring under the bar is rejected 'below bar'. PROVISIONAL -- review
    after a month of paper results (~2026-10-27). Chosen from the 73-scan
    re-score and the per-day replay (_s123/w195_rescore.py,
    _s123/w195_replay_daily.py).
  * At most MAX_NEW_PER_DAY (2) new long calls a day, and one long call per
    name. Both are ROUTING limits, so they are enforced by the callers that
    route (paper generate; the scan footer's real suggestions), not here.
  G3, G4 and G5 are unchanged.

Nothing here reads or writes the database, imports rich, or touches the sell
side. It is a pure function of a list of scan rows, which is what makes it
testable against a real board without a broker.
"""

import json

# -- gate parameters ---------------------------------------------------------

G1_BIAS_MIN = 2          # >= +2 of 3 momentum votes, and the full MA stack
G3_RATIO_MAX = 0.90      # IV <= 0.90 x HV90 RAW - ORATS own denominator (HELM-132)
G4_CALENDAR_DAYS = 7     # 5 trading days before a print, spanning one weekend
G5_HV252_MAX = 40.0      # absolute underlying-vol ceiling, annualized %
G5_QUINTILE = 0.80       # logged alternative; never acted on

# ranking
W_VOL = 0.60             # vol cheapness dominates: it is the measurable edge
W_TREND = 0.40           # direction is necessary but weakly evidenced (7.3)
ADX_FLOOR = 15.0         # below this, trend strength contributes nothing
ADX_FULL = 35.0          # at or above this, it contributes in full
RSI_PENALTY_FROM = 70.0  # extension is a rank penalty, never a gate
RSI_PENALTY_FULL = 90.0
RSI_PENALTY_MAX = 0.10

# W195 (Russ, 2026-09-27) -- see module docstring.
RANK_BAR = 0.72          # PROVISIONAL: review after a month of paper results (~2026-10-27)
MAX_NEW_PER_DAY = 2      # routing limit, enforced by the callers that route
W_CALM = 0.50
W_CHEAP = 0.50
CALM_FULL_HV252 = 20.0   # calmness 1.0 at or below this HV252
CALM_ZERO_HV252 = 40.0   # calmness 0.0 at or above (= the G5 ceiling)
# W_VOL / W_TREND / the RSI constants above are v1's. Kept because the
# components they produce are still recorded; the v2 score uses none of them.

SCREEN_VERSION = 'lc-screen-v2 (W195)'

# W70 / HELM-125 (s91): how far INSIDE the G3 gate a name must sit before it may
# ROUTE. The board verdict is unchanged -- the screen still publishes pass/fail
# against G3_RATIO_MAX for every name on every scan. Only routing needs the
# margin, because only routing spends money.
#
# Why a margin. G3 decides this screen: on the 2026-07-27 15:24 scan it failed
# 64 of 67 names and was the sole cause for 31, and it is knife-edge -- GE sits
# at 0.899 against a 0.900 gate, having crossed from 0.902 with implied vol
# unchanged. ORATS, which publishes screens on this same measure, answers this
# same problem with a buffer rather than with repetition: iv30d/orFcst20d < 0.85
# to buy and > 1.15 to sell, a deliberate dead zone so names on the line do not
# flip the signal. Their iv30d/orHv90d < 0.9 scan is where G3_RATIO_MAX itself
# comes from - and as of HELM-132 the denominator matches it too. It did
# not used to: the ratio divided raw IV by ex-earnings HV90, a mixed form
# ORATS never publishes, which moved the gate with earnings-cycle phase
# rather than with option cheapness.
#
# What this replaced. s91 first shipped CONFIRM_SCANS = 2 -- pass on two
# consecutive scans. Measurement killed it: the ratio steps rather than jitters
# (GE returned 0.899 on three scans across 17 live minutes), so a second scan
# carries no new information, and the guard's strength depended on how far apart
# Russ happened to scan rather than on the market.
#
# Not hysteresis. There is no stay-in half: routing is an entry decision taken
# once, the (ticker, strategy) paper guard prevents re-entry, and nothing
# un-books on the ratio. Only the stricter arm threshold can bite.
#
# TIER B -- convention. Measured: the gate is knife-edge, and 0.01 / 0.02 / 0.03
# each removed all three flips in the 7-batch sample. Not measured: that 0.01 is
# the right width. ORATS's precedent is for having a buffer, not for this size
# of one. The calibration log settles it.
#
# The routing test lives in helm/cli/_paper_generate.py so this module stays
# DB-free.
ROUTE_MARGIN = 0.01


def _num(v):
    """float(v) or None. Scan rows carry None for anything unmeasured, and a
    gate that cannot be measured must not silently read as passed."""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f else None      # reject NaN


def earn_flag(row, ratio):
    """Informational earnings-proximity flag. Gates nothing (HELM-133).

    Set only while the last print still sits inside the trailing 90d HV
    window, because that is exactly when the ex-earnings twin diverges from
    the gate. Measured 2026-07-27: median divergence +0.0736 at 0-30d since
    print, +0.0121 once it ages out. The flag reports the divergence rather
    than merely noting the print, so it can be read as a number.
    """
    since = _num(row.get("earn_days_since"))
    inwin = _num(row.get("earn_in_hv90_window"))
    rx = _num(row.get("iv_hv90_ratio_xearn"))
    if since is None or not inwin:
        return None
    if rx is None or ratio is None:
        return "reported %dd ago" % int(since)
    return "reported %dd ago; ex-earn ratio %.3f (%+.3f)" % (
        int(since), rx, rx - ratio)


def vol_cheapness(ratio):
    """0..1 from the buffered IV / HV90 ratio (raw HV90 - see HELM-132).

    1.0 at 0.70 or below, 0.0 at the 0.90 gate and above. The scale is
    calibrated across the passing band, so a rejected name scores near zero
    rather than going negative -- its score is still recorded, because the
    calibration period needs the losers' inputs as much as the winners'.
    """
    r = _num(ratio)
    if r is None:
        return None
    if r <= 0.70:
        return 1.0
    if r >= G3_RATIO_MAX:
        return 0.0
    return round((G3_RATIO_MAX - r) / (G3_RATIO_MAX - 0.70), 4)


def calmness(hv252):
    """0..1 from the underlying's own one-year realised vol (W195).

    1.0 at HV252 <= 20%, 0.0 at 40% (the G5 ceiling) and above, linear
    between. The level effect Hu & Jacobs describe: calls on calm names
    return more. None when unmeasured -- never scored as calm.
    """
    h = _num(hv252)
    if h is None:
        return None
    if h <= CALM_FULL_HV252:
        return 1.0
    if h >= CALM_ZERO_HV252:
        return 0.0
    return round((CALM_ZERO_HV252 - h) / (CALM_ZERO_HV252 - CALM_FULL_HV252), 4)


def trend_quality(bias, adx):
    """0..1 from directional bias, modulated by trend strength.

    ADX enters as a RANK INPUT, not a gate (design doc section 7.3: ADX is
    symmetric by construction, and practitioners read a high reading as
    exhaustion at least as often as confirmation). It can halve a name's trend
    term; it can never exclude one. Bias carries the sign and the weight.
    """
    b = _num(bias)
    if b is None:
        return None
    base = min(abs(b), 3.0) / 3.0
    a = _num(adx)
    if a is None:
        strength = 0.5                # unmeasured strength is neither rewarded
    else:                             # nor punished
        span = ADX_FULL - ADX_FLOOR
        strength = min(max((a - ADX_FLOOR) / span, 0.0), 1.0)
    return round(base * (0.5 + 0.5 * strength), 4)


def rsi_penalty(rsi):
    """Up to RSI_PENALTY_MAX off the rank score for an extended name.

    A penalty and not a gate, but note WHY: the design draft justified this by
    'winners keep winning', and section 7.3 weakened that badly (documented
    momentum in the largest size decile is indistinguishable from zero). The
    surviving reason is mundane -- RSI is noisy and should not be allowed to
    refuse a trade on its own.
    """
    r = _num(rsi)
    if r is None or r <= RSI_PENALTY_FROM:
        return 0.0
    span = RSI_PENALTY_FULL - RSI_PENALTY_FROM
    frac = min((r - RSI_PENALTY_FROM) / span, 1.0)
    return round(frac * RSI_PENALTY_MAX, 4)


# -- the gates ---------------------------------------------------------------

def evaluate_gates(row, hv252_quintile=None, market=None):
    """Run the gates over one scan row. Returns (gates, failures).

    W195: G1 is recorded, not gated. `market` is the caller's scan-time S&P
    reading, {'above': bool|None, 'spx', 'sma200', 'asof', 'source', 'error'};
    None or above=None fails the market gate CLOSED. The rank bar is applied
    in screen(), because it needs the score.

    gates is the full record -- every gate's inputs, threshold and verdict,
    plus the logged-not-acted quintile alternative. It is written to
    signals.lc_gates_json on every name, passing or not, because a screen that
    only explains its survivors cannot be argued with. HELM-110's lesson: the
    open layer recorded a precise decline reason per contract and no surface
    read it, so an emptied board pointed the trader at the wrong constraint.

    failures is the ordered list of gate labels that failed. Empty means the
    name is a candidate.
    """
    fails = []

    bias = _num(row.get('bias_score'))
    if bias is None:
        bias = _num(row.get('auto_bias_score'))
    spot = _num(row.get('spot_price')) or _num(row.get('price'))
    s50 = _num(row.get('sma_50'))
    s200 = _num(row.get('sma_200'))

    # G1 -- direction. Bias AND the full stack: the stack requirement is not
    # redundant, it drops a strong bounce inside a downtrend (ABT in the
    # original dry run: bias +3 with price below its own SMA200).
    # W195: RECORDED, NOT GATED. Measured exactly as v1 did, so the log can
    # still say what G1 would have done; it no longer adds a failure.
    g1_bias_ok = bias is not None and bias >= G1_BIAS_MIN
    g1_stack_ok = (spot is not None and s50 is not None and s200 is not None
                   and spot > s50 > s200)

    # W195 market gate -- S&P 500 above its 200-day, read at scan time by the
    # caller. Fails closed: an unread index is no new longs, never a pass.
    m = market or {}
    mkt_above = m.get('above')
    if mkt_above is None:
        fails.append('S&P unknown')
    elif not mkt_above:
        fails.append('S&P below 200d')

    # G3 -- vol not expensive, with a buffer. This replaces BOTH the old
    # IVR < 35 and the design draft's VRP <= 0 on HV30, which section 7.2
    # showed was adversely selected: a 30-day window fires hardest on names
    # that have just had a vol spike, which is Hu & Jacobs' worst decile.
    # HELM-132: the denominator is RAW HV90, matching ORATS iv30d / orHv90d,
    # which is where 0.90 came from. It used to be hv_90_ex_earn against the
    # same raw numerator -- a mixed form that moved the gate with earnings
    # cycle phase. The ex-earnings twin survives as information (HELM-133).
    ratio = _num(row.get('iv_hv90_ratio'))
    g3_ok = ratio is not None and ratio <= G3_RATIO_MAX
    if not g3_ok:
        fails.append('G3 vol' if ratio is not None else 'G3 vol unknown')

    # G4 -- earnings placement, and this gate FAILS CLOSED.
    # Deliberately the inverse of the sell side: a seller wants to be short
    # into the IV ramp and harvest the crush, a buyer entering there pays the
    # ramp and eats it. A print later inside the 90-180 window is fine -- that
    # is the convexity being paid for.
    # An unknown or stale date is a refusal, not a pass, matching gate_longs
    # on the buy side. W25 has twelve watchlist dates already in the past;
    # reading those as "no earnings risk" is exactly the failure mode.
    d2e = row.get('days_to_earnings')
    d2e = int(d2e) if isinstance(d2e, (int, float)) else None
    if d2e is None:
        fails.append('G4 earnings unknown')
        g4_state = 'unknown'
    elif d2e < 0:
        fails.append('G4 earnings stale')
        g4_state = 'stale'
    elif d2e <= G4_CALENDAR_DAYS:
        fails.append('G4 earnings ramp')
        g4_state = 'ramp'
    else:
        g4_state = 'clear'

    # G5 -- underlying-vol ceiling, absolute. Settled s90; the board-relative
    # quintile is computed alongside and never acted on.
    hv252 = _num(row.get('hv_252'))
    g5_ok = hv252 is not None and hv252 < G5_HV252_MAX
    if not g5_ok:
        fails.append('G5 vol ceiling' if hv252 is not None
                     else 'G5 hv252 unknown')
    # The logged alternative is None-when-unknown, never False. A board too
    # small to have a quintile (or a name with no HV252) must not record
    # "the alternative disagreed" -- that is a measurement that did not happen,
    # and folding it into a boolean is how an absent number starts looking like
    # a negative verdict. Same reason G4 distinguishes unknown from clear.
    q = _num(hv252_quintile)
    if hv252 is None or q is None:
        g5_alt_ok = None
    else:
        g5_alt_ok = hv252 < q

    gates = {
        'version': SCREEN_VERSION,
        'mkt': {'above': mkt_above, 'spx': m.get('spx'), 'sma200': m.get('sma200'),
                'asof': m.get('asof'), 'source': m.get('source'),
                'error': m.get('error'), 'ok': bool(mkt_above),
                'note': 'S&P 500 above its 200-day at scan time; fails closed'},
        'g1': {'bias': bias, 'bias_min': G1_BIAS_MIN, 'bias_ok': g1_bias_ok,
               'spot': spot, 'sma_50': s50, 'sma_200': s200,
               'stack_ok': g1_stack_ok,
               'ok': bool(g1_bias_ok and g1_stack_ok), 'gate': False,
               'note': 'recorded, not gated (W195)'},
        'g3': {'iv_hv90_ratio': ratio, 'max': G3_RATIO_MAX, 'ok': g3_ok,
               'hv_90': _num(row.get('hv_90')),          # the gate denominator
               'hv_90_ex_earn': _num(row.get('hv_90_ex_earn')),
               'hv_90_source': row.get('hv_90_source'),
               # HELM-133 -- informational, gates nothing.
               'ratio_xearn': _num(row.get('iv_hv90_ratio_xearn')),
               'earn_days_since': _num(row.get('earn_days_since')),
               'earn_in_hv90_window': _num(row.get('earn_in_hv90_window')),
               'earn_flag': earn_flag(row, ratio)},
        'g4': {'days_to_earnings': d2e, 'window_calendar_days': G4_CALENDAR_DAYS,
               'state': g4_state, 'ok': g4_state == 'clear',
               'note': 'calendar-day proxy for 5 trading days; fails closed'},
        'g5': {'hv_252': hv252, 'max': G5_HV252_MAX, 'ok': g5_ok,
               'alt_quintile': q, 'alt_ok': g5_alt_ok,
               'alt_agrees': None if g5_alt_ok is None else (g5_ok == g5_alt_ok),
               'note': 'absolute ceiling acted on; quintile logged only'},
        'rank_inputs': {'adx': _num(row.get('adx')),
                        'rsi_14': _num(row.get('rsi_14')),
                        'obv_trend': row.get('obv_trend')},
    }
    return gates, fails


def rank_score(row):
    """W195: 50% calmness + 50% cheapness, no RSI penalty. None if either
    input is unmeasured -- an unscoreable name can never clear the bar.

    The weights and every component are recorded in the gates blob on every
    row, so a later pass can re-weight from stored inputs.
    """
    v = vol_cheapness(row.get('iv_hv90_ratio'))
    c = calmness(row.get('hv_252'))
    if v is None or c is None:
        return None
    return round(W_CALM * c + W_CHEAP * v, 4)


def hv252_quintile(rows):
    """The board's 80th-percentile HV252, or None when too few names carry one.

    Logged, not acted on. Needs the whole board, which is why the screen is a
    board-level pass rather than a per-name call inside the scan loop.
    """
    vals = sorted(v for v in (_num(r.get('hv_252')) for r in rows) if v is not None)
    if len(vals) < 5:
        return None
    idx = min(int(len(vals) * G5_QUINTILE), len(vals) - 1)
    return round(vals[idx], 4)


def screen(rows, market=None):
    """Annotate a whole scan board in place and return the survivors, ranked.

    W195: `market` is the scan-time S&P reading (see evaluate_gates); a pass
    now also needs lc_rank_score >= RANK_BAR. Survivors are every name that
    passed -- the 2-a-day and one-per-name limits belong to the router.

    Writes five fields onto every row, passing or not:
      lc_screen_pass    1 / 0
      lc_screen_rank    1-based among survivors, None otherwise
      lc_screen_reject  comma-joined failing gates, None when it passed
      lc_rank_score     the W195 50/50 calmness/cheapness score
      lc_gates_json     the full gate record, including the logged alternative

    ROUTING IS NOT TOUCHED. This never sets row['strategy'] and never books
    anything: the addendum's dual-book routing is a separate switch, and until
    it is thrown the screen's only job is to be visible and to be recorded so
    the calibration period has something to score.
    """
    rows = list(rows or [])
    q = hv252_quintile(rows)

    survivors = []
    for r in rows:
        gates, fails = evaluate_gates(r, hv252_quintile=q, market=market)
        score = rank_score(r)
        # The bar is checked on every row and recorded on every row, but only
        # NAMED as the failure when every gate passed -- a G3 reject scores
        # under the bar by construction, and listing it twice would bury the
        # histogram's one job (which gate emptied the board).
        bar_ok = score is not None and score >= RANK_BAR
        if not fails and not bar_ok:
            fails.append('below bar')
        gates['score'] = score
        gates['bar'] = {'bar': RANK_BAR, 'score': score, 'ok': bar_ok,
                        'note': 'provisional; review ~2026-10-27 (W195)'}
        gates['weights'] = {'calm': W_CALM, 'cheap': W_CHEAP}
        gates['components'] = {
            'calmness': calmness(r.get('hv_252')),
            'vol_cheapness': vol_cheapness(r.get('iv_hv90_ratio')),
            # v1 components -- recorded, not scored (W195).
            'trend_quality': trend_quality(
                r.get('bias_score') if r.get('bias_score') is not None
                else r.get('auto_bias_score'), r.get('adx')),
            'rsi_penalty': rsi_penalty(r.get('rsi_14')),
        }
        r['lc_screen_pass'] = 0 if fails else 1
        r['lc_screen_reject'] = ', '.join(fails) if fails else None
        r['lc_rank_score'] = score
        r['lc_screen_rank'] = None
        try:
            r['lc_gates_json'] = json.dumps(gates, default=str)
        except Exception:
            r['lc_gates_json'] = None
        if not fails:
            survivors.append(r)

    # A survivor with no score still ranks -- last, and visibly. Dropping it
    # would make an unscoreable candidate indistinguishable from a rejected
    # one, which is the distinction this whole module exists to keep.
    survivors.sort(key=lambda r: (r.get('lc_rank_score') is None,
                                  -(r.get('lc_rank_score') or 0.0),
                                  str(r.get('ticker') or '')))
    for i, r in enumerate(survivors, 1):
        r['lc_screen_rank'] = i
    return survivors


def summarize(rows):
    """One line per outcome for the scan's footer, and the reject histogram.

    Returns (n_pass, n_total, Counter of failing gates). A gate that rejects
    the entire board is the single most useful thing this screen can tell you,
    and it is invisible if only survivors are reported.
    """
    from collections import Counter
    rows = list(rows or [])
    hist = Counter()
    for r in rows:
        rej = r.get('lc_screen_reject')
        if rej:
            for part in rej.split(', '):
                hist[part] += 1
    n_pass = sum(1 for r in rows if r.get('lc_screen_pass'))
    return n_pass, len(rows), hist
