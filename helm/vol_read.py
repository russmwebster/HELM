"""HELM READ - the vol clauses (HELM-134 / W78).

The scan board already shows IV%, HV, VRP, IVR and IVP as columns. A prose
column that restates a number already on screen adds nothing, and until now the
READ's vol clause did exactly that: it printed the IV Rank and called it "good
premium", citing one column and ignoring three others.

Measured on the 2026-07-28 board, 67 names:

  * 35 rows carried "IVR NN - elevated, good premium".
  * 10 of those had a NEGATIVE VRP - the board's own columns said the premium
    was cheap while the sentence said it was rich. KO read IV 22.0 against
    HV 31.4 at IVR 85.
  * 17 of the 35 had an earnings print within 10 days, so for roughly half the
    sell board the elevated rank IS the event premium, not a standing edge.
  * 6 names had IVR and IVP diverging by 20+ points, which is the single-spike
    distortion IVP was added to detect - both numbers shown, the disagreement
    never stated.
  * 26 of 67 rows could not compute realized vol from known earnings dates
    (hv_30_source == 'dates-none'), so their HV and VRP are less trustworthy
    and nothing said so.

So this module answers what the columns cannot: WHY a level is what it is,
WHERE the measures disagree, and HOW MUCH to trust them. It states no verdict
and gates nothing - routing is untouched. Russ decides.

Deliberately DB-free and pure, like helm/lc_screen.py: it takes a scan result
dict and returns clauses. That is what makes it testable without a database.
"""

SELL_STRATEGIES = ("CSP", "IRON_CONDOR", "BEAR_CALL_SPREAD", "BULL_PUT_SPREAD")
BUY_STRATEGIES = ("LONG_CALL",)

EARN_NEAR_DAYS = 10      # a print this close is what the rank is pricing
DIVERGENCE_PTS = 20      # IVR vs IVP gap that implies a single-spike rank
IVR_RICH = 50.0
IVR_CHEAP = 25.0


def _num(v):
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def vol_read(row):
    """Leading clauses for the HELM READ column. Order is decision priority.

    Cause first, then disagreement, then confidence. A tick is used ONLY when
    the measures agree; where they conflict the clause says so rather than
    picking the flattering one.
    """
    out = []
    strategy = row.get("strategy")
    selling = strategy in SELL_STRATEGIES
    buying = strategy in BUY_STRATEGIES

    ivr = _num(row.get("iv_rank"))
    ivp = _num(row.get("iv_percentile"))
    if ivp is None:
        ivp = _num(row.get("iv_pct"))
    iv = _num(row.get("iv_current"))
    hv = _num(row.get("hv_30"))
    vrp = _num(row.get("vrp"))
    d2e = _num(row.get("days_to_earnings"))
    src = row.get("hv_30_source")

    # 1 - CAUSE. An elevated rank with a print days away is the event being
    # priced. Avoiding or deliberately structuring around a print inside the
    # trade is standard practice for premium sellers; the board should say
    # when that is the situation rather than leave it to the EARN column.
    if selling and ivr is not None and ivr >= IVR_RICH \
            and d2e is not None and 0 <= d2e <= EARN_NEAR_DAYS:
        if d2e <= 0:
            out.append("⚠ IVR %.0f - earnings TODAY; the rank is the print" % ivr)
        else:
            out.append("⚠ IVR %.0f - but earnings in %.0fd; the rank is the print, "
                       "not a standing edge" % (ivr, d2e))

    # 2 - DISAGREEMENT. IV Rank says rich-for-this-stock; VRP says rich against
    # what the stock is actually doing. They answer different questions and can
    # point opposite ways.
    if iv is not None and hv is not None and vrp is not None:
        if selling:
            if vrp < 0:
                out.append("⚠ IV %.0f vs HV %.0f - selling BELOW realized (VRP %+.1f)"
                           % (iv, hv, vrp))
            elif ivr is not None and ivr >= IVR_RICH:
                out.append("✓ IVR %.0f elevated and IV %.0f > HV %.0f - rich on both "
                           "tests (VRP %+.1f)" % (ivr, iv, hv, vrp))
            else:
                out.append("IV %.0f vs HV %.0f (VRP %+.1f)" % (iv, hv, vrp))
        elif buying:
            if vrp > 0:
                out.append("⚠ IV %.0f vs HV %.0f - paying ABOVE realized (VRP %+.1f)"
                           % (iv, hv, vrp))
            else:
                out.append("✓ IV %.0f vs HV %.0f - buying below realized (VRP %+.1f)"
                           % (iv, hv, vrp))

    # 2b - the level on its own, when there is no VRP to compare it against.
    if vrp is None and ivr is not None:
        if selling and ivr < IVR_CHEAP:
            out.append("⚠ Low IVR %.0f - cheap IV to sell" % ivr)
        elif selling and ivr >= IVR_RICH:
            out.append("IVR %.0f elevated - no VRP available to confirm" % ivr)
        elif buying and ivr > IVR_RICH:
            out.append("⚠ IVR %.0f - buying expensive options" % ivr)
        elif buying and ivr <= IVR_CHEAP:
            out.append("✓ IVR %.0f - low IV, cheap options" % ivr)

    # 3 - DIVERGENCE. Rank is distorted by one vol spike in the trailing year;
    # percentile is not. A wide gap means the rank is resting on an event.
    if ivr is not None and ivp is not None and abs(ivr - ivp) >= DIVERGENCE_PTS:
        out.append("⚠ IVR %.0f vs IVP %.0f - rank and percentile disagree by %.0f"
                   % (ivr, ivp, abs(ivr - ivp)))

    # 4 - CONFIDENCE. W69's source vocabulary, surfaced. 'dates-none' means the
    # ex-earnings trim had no earnings dates to work with, so HV - and every
    # number derived from it - is weaker than it looks.
    if src == "dates-none":
        out.append("HV from price history only, no earnings dates - VRP less certain")

    return out


def annotate(results):
    """Prepend the vol clauses to each row's bias_factors. Returns count changed.

    Runs as a post-pass because it needs two things fetch_technicals does not
    have at the point the old clause was written: VRP (computed later in the
    same function) and days_to_earnings (attached by a separate pass entirely).
    That ordering is why the old sentence could only ever cite IV Rank.
    """
    n = 0
    for r in results or []:
        if not isinstance(r, dict) or r.get("error"):
            continue
        clauses = vol_read(r)
        if not clauses:
            continue
        existing = r.get("bias_factors") or []
        if not isinstance(existing, list):
            existing = [str(existing)]
        r["bias_factors"] = clauses + existing
        n += 1
    return n
