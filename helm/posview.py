"""helm/posview.py -- W181. Which display GROUP a position belongs to, and what
that group is called. Shared by the CLI board (`helm check`) and the PG board.

THE DEFECT THIS CLOSES. Two copies of this map existed, in two repositories:
`helm/cli/check_cmd.py` and `~/Projects/helm-pg/helm_engine.py`. They were
byte-identical, neither referenced the other, and NEITHER HAD A DIAGONAL BRANCH
-- so every diagonal on both surfaces fell through to "Other" and rendered with
the generic row: ticker, strategy, DTE, earnings, spot, kept%, p&l, credit.
No strikes, no buffer, no sense of which leg is which.

Measured 2026-09-23: the diagonal is the LARGEST strategy on both books -- 8 of
19 real, 37 of 63 paper -- and the only one without a panel. The structure HELM
holds most of is the one it displayed worst.

THE SHARPER POINT, and the reason this is a module rather than a two-line patch
in each repo: PG's `_family_of()` already routes "diagonal" to
`evaluate_diagonals` on the OPEN side (W149, s108) -- so the board could open a
diagonal correctly and then could not display it, from two functions 300 lines
apart in the same file. A rule duplicated across repos drifts, and this is what
the drift cost.

THIS IS THE DISPLAY TAXONOMY ONLY. HELM has two other family maps and neither
is affected:
  * `helm/decision.py::_family` -- the RULES family (CREDIT / DIAGONAL /
    LONG_DEBIT / ...). It has had DIAGONAL_FAMILY throughout; the engine has
    always known. Read by rule_read.py and exit_considered.py.
  * `helm/cli/scan_cmd.py::_strategy_family` -- the CONVICTION family
    (buy / range / sell), for scoring a candidate.
Changing what a surface SHOWS is not changing what HELM DOES (standing rule).

W34 NOTE. PG importing this couples it to the live engine. That coupling is
already there -- helm_engine.py imports from helm.config, helm.strategies,
helm.ownership, helm.models.iv_history, helm.cli.scan_cmd and
helm.cli.check_cmd at seven call sites -- and W34 records that PG's read-only
stance is a convention rather than a design. One more import of a pure,
side-effect-free map does not widen that, and it removes a rule that was being
maintained twice. Precedent: helm/legview.py (W168, s117), created for exactly
this reason.
"""

# Canonical render order. "OTHER" is last and is the catch-all.
FAMILY_ORDER = ["CSP", "CREDIT_SPREAD", "IC", "DIAGONAL", "LONG_CALL", "OTHER"]

# family -> (long label, short code)
FAMILY_META = {
    "CSP":           ("Cash-secured puts", "CSP"),
    "CREDIT_SPREAD": ("Credit spreads",    "BCS"),   # suffix refined per members
    "IC":            ("Iron condors",      "IC"),
    "DIAGONAL":      ("Diagonals",         "DIAG"),
    "LONG_CALL":     ("Long calls",        "LC"),
    "OTHER":         ("Other",             "--"),
}


def family(strat):
    """Display group for a strategy name. Unknown -> OTHER, never an error."""
    s = (strat or "").upper()
    if s in ("CSP", "CASH_SECURED_PUT"):
        return "CSP"
    if s in ("BEAR_CALL_SPREAD", "BULL_PUT_SPREAD"):
        return "CREDIT_SPREAD"
    if s == "IRON_CONDOR":
        return "IC"
    # W181: DIAGONAL_PUT and PMCC share the diagonal's shape -- a long leg with a
    # shorter-dated short sold against it -- and the same panel answers for them.
    # They match decision.py's DIAGONAL_FAMILY membership, deliberately.
    if s in ("DIAGONAL", "DIAGONAL_PUT", "PMCC"):
        return "DIAGONAL"
    if s == "LONG_CALL":
        return "LONG_CALL"
    return "OTHER"


# ---- diagonal-specific reads ----------------------------------------------
# A diagonal is a long call with a SEQUENCE of shorts sold against it (Russ,
# 2026-09-21; W180 §4a). These answer "where is this one in that sequence".

def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _leg_get(leg, k):
    return leg.get(k) if isinstance(leg, dict) else leg[k]


def diagonal_state(legs):
    """SHORT ON | LONG ONLY | CLOSED -- W180 §4a's three states, read from the legs.

    LONG ONLY is reached by a bought-back short (W180 step 3) or by expiry and
    settlement (W131/W177); the two are the same state and this does not
    distinguish them.
    """
    opt = [l for l in (legs or []) if (_leg_get(l, "option_type") or "") not in ("", "STOCK", None)]
    open_ = [l for l in opt if str(_leg_get(l, "status") or "").upper() == "OPEN"]
    if not open_:
        return "CLOSED"
    if any(str(_leg_get(l, "direction") or "").upper() == "SHORT" for l in open_):
        return "SHORT ON"
    return "LONG ONLY"


def open_short_leg(legs):
    """The live short leg, or None in LONG ONLY. Front-most if several."""
    c = [l for l in (legs or [])
         if str(_leg_get(l, "direction") or "").upper() == "SHORT"
         and str(_leg_get(l, "status") or "").upper() == "OPEN"
         and (_leg_get(l, "option_type") or "") not in ("", "STOCK", None)]
    if not c:
        return None
    return sorted(c, key=lambda l: str(_leg_get(l, "expiration") or "9999"))[0]


def open_long_leg(legs):
    """The live long leg -- the asset. Back-most if several."""
    c = [l for l in (legs or [])
         if str(_leg_get(l, "direction") or "").upper() == "LONG"
         and str(_leg_get(l, "status") or "").upper() == "OPEN"
         and (_leg_get(l, "option_type") or "") not in ("", "STOCK", None)]
    if not c:
        return None
    return sorted(c, key=lambda l: str(_leg_get(l, "expiration") or ""), reverse=True)[0]


def captured_pct(short_leg, mark):
    """Percent of the short's premium captured so far: (open - mark) / open.

    The number W180's harvest rule acts on (flags at 25% and 50% on the real
    book). None when there is no live short or no mark -- never a guess.
    Negative when the short has moved against you, which is the breach case.
    """
    if short_leg is None or mark is None:
        return None
    op = _num(_leg_get(short_leg, "open_price"))
    m = _num(mark)
    if not op or m is None:
        return None
    return (op - m) / op * 100.0


def rent_collected(legs):
    """Realized P&L of every short sold against this long and since closed.

    Counts CLOSED short legs only -- bought back (W180 step 3) or settled at
    expiry (W131/W177). A short still open contributes nothing: it is not
    realized. Returns 0.0 when none have closed, never None, because "no shorts
    closed yet" is a fact rather than a missing reading.
    """
    total = 0.0
    for l in legs or []:
        if str(_leg_get(l, "direction") or "").upper() != "SHORT":
            continue
        if str(_leg_get(l, "status") or "").upper() != "CLOSED":
            continue
        op, cp = _num(_leg_get(l, "open_price")), _num(_leg_get(l, "close_price"))
        if op is None or cp is None:
            continue
        n = _num(_leg_get(l, "contracts")) or 0
        mult = _num(_leg_get(l, "multiplier")) or 100
        total += (op - cp) * n * mult
    return round(total, 2)


def effective_basis(legs):
    """What the long has cost NET of every short sold against it.

    long debit - rent collected. The number that says whether the rent has paid
    for the asset yet (W180 §4a); when it crosses zero the long is free.
    None when there is no open long leg to have a basis.

    NOT what the v3 rules measure against -- those use the long's ORIGINAL
    debit (Russ, 2026-09-22), because a stop that widens with every harvest is
    a cap in reverse. This is the number the trader looks at, not the one the
    rules use.
    """
    lg = open_long_leg(legs)
    if lg is None:
        return None
    op = _num(_leg_get(lg, "open_price"))
    if op is None:
        return None
    n = _num(_leg_get(lg, "contracts")) or 0
    mult = _num(_leg_get(lg, "multiplier")) or 100
    return round(op * n * mult - rent_collected(legs), 2)


def shorts_sold(legs):
    """How many shorts have been sold against this long, closed + open."""
    return sum(1 for l in (legs or [])
               if str(_leg_get(l, "direction") or "").upper() == "SHORT"
               and (_leg_get(l, "option_type") or "") not in ("", "STOCK", None))
