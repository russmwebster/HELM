"""helm/legview.py -- W168. Which leg is a position's "primary", and what a
multi-expiry position's DTE means on a surface.

THE DEFECT THIS CLOSES. `check_cmd` took `primary = opt_legs[0]` from a
`SELECT * FROM legs` with no ORDER BY, so the primary -- and with it the check
row's `dte_now`, `delta` and `current_price` -- was whichever leg the writer
inserted first. Measured 2026-09-07 across all 31 open diagonals: 29 of 29 PAPER
reported the LONG leg, 2 of 2 REAL reported the SHORT leg, no exceptions. Paper
AA and real AA hold identical strikes and expiries and reported dte_now 105 and
42. Nobody chose that.

RUSS'S DECISION (2026-09-07): show BOTH legs on a surface, and key the exit-flag
decision point off the SHORT (front) leg -- the decision that is actually near.
`exit_flags` reads `checks.dte_now`, so ordering the legs front-first delivers
both halves with one change.

DELIBERATELY NARROW. `order_option_legs` is a NO-OP for any position whose option
legs share one expiration -- every vertical, condor, strangle, straddle and
single. It only orders when expirations differ, which is exactly the diagonal /
calendar / PMCC family. A wider reorder would have moved `primary` -- and with it
strike, direction, contracts and open_price -- on the whole book, which is not
what was asked for and not what was measured.

THE ENGINE IS UNAFFECTED EITHER WAY. `decision.evaluate` computes its own
min/max from the legs and manages a diagonal off the BACK leg by explicit design.
This is a question about what a surface SHOWS, not about what HELM does.
"""


def _exp(leg):
    e = leg.get("expiration") if isinstance(leg, dict) else leg["expiration"]
    return str(e)[:10] if e else ""


def is_multi_expiry(opt_legs):
    """True when the option legs do not all share one expiration."""
    return len({_exp(l) for l in opt_legs if _exp(l)}) > 1


def order_option_legs(opt_legs):
    """Front leg first, for multi-expiry positions ONLY.

    Returns the list unchanged -- same objects, same order -- when the legs share
    an expiration, so no single-expiry position can move. Ordering is by
    expiration, then SHORT before LONG, then strike, so it is total and stable
    rather than merely different from insertion order.
    """
    if not opt_legs or not is_multi_expiry(opt_legs):
        return opt_legs

    def key(l):
        d = (l.get("direction") if isinstance(l, dict) else l["direction"]) or ""
        s = (l.get("strike") if isinstance(l, dict) else l["strike"]) or 0
        return (_exp(l), 0 if d.upper() == "SHORT" else 1, s)

    return sorted(opt_legs, key=key)


def dte_pair(opt_legs, dte_fn):
    """(front_dte, back_dte) for a multi-expiry position, else (dte, None).

    `dte_fn` is passed in rather than imported so this module stays free of the
    CLI's date helpers and can be called from PG.
    """
    if not opt_legs:
        return (None, None)
    ds = [dte_fn(_exp(l)) for l in opt_legs if _exp(l)]
    ds = [d for d in ds if d is not None]
    if not ds:
        return (None, None)
    if not is_multi_expiry(opt_legs):
        return (min(ds), None)
    return (min(ds), max(ds))


def dte_label(opt_legs, dte_fn):
    """Display-ready: "42 / 105" for a multi-expiry position, "42" otherwise.

    The pair is the honest rendering -- W168. A single number on a diagonal is a
    claim about which leg matters, and that claim was previously made by
    insertion order.
    """
    front, back = dte_pair(opt_legs, dte_fn)
    if front is None:
        return ""
    if back is None:
        return str(front)
    return "%d / %d" % (front, back)
