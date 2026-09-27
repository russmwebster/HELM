"""helm/risk_cap.py -- W160 ($5,000 of risk per trade, every strategy) and
W194 (long-premium sleeve <= 5% of account value).

Both are Russ's decisions from s122 (2026-09-26/27); see
claude/HELM-standing-rules.md (the project doc, not this repo) for the
record. Pure, mostly read-only computation -- nothing here writes anything,
and nothing here blocks the real book. HELM-193: on the real book HELM
informs, it never acts. A caller that uses `capped_contracts` to size a
SUGGESTION is still only suggesting; a caller that uses the *_view() reports
below is only flagging. Whether a rule ENFORCES what this module computes is
the caller's decision to make and name, per book (the paper book may enforce;
the real book may only flag -- ORIENTATION.md section 1).

W160 formula, one dollar figure already scaled by the contract count in
question (Russ, 2026-09-26):
    CSP                          : one-sigma move (IV-based) x contracts
    spreads / condors            : the position's own max_loss
    long call / long put         : debit paid
    diagonal / diagonal put/PMCC : the LONG leg's debit ALONE -- a sold-
                                    against short's rent is not netted out,
                                    same convention as the W194 sleeve below
A strategy with no decided formula (COVERED_CALL -- stock risk, not option
debit; undefined-risk SHORT_STRANGLE; LONG_STRADDLE; PERM) and no stored
max_loss returns None. An unmeasured risk must never read as a cheap one
(the same discipline as helm.cli.open_cmd.expected_move) -- callers must
treat None as "cannot flag this", never as "this is fine".

W194: the long-premium sleeve is the ORIGINAL COST of every OPEN long
option, never reduced by rent collected since. Per the s122 decision record
(archive, item 6) the sleeve is LONG_CALL + the diagonal family (DIAGONAL,
DIAGONAL_PUT, PMCC, counted at the long leg only) + index calls -- LONG_PUT
is deliberately not enumerated in the decision and is excluded here. This is
a reading of the record, not something Russ typed as a rule in those words;
flag it back to him if it's wrong. The index-call sub-cap (<= 1/3 of the
sleeve, W196) is NOT built here -- W196 is still "to analyse", not decided,
so this module reports the whole sleeve only.
"""
import math
import os
import sqlite3

RISK_CAP_PER_TRADE = 5000.0
SLEEVE_CAP_PCT = 0.05

LONG_SINGLE = ("LONG_CALL", "LONG_PUT")
DIAGONAL_FAMILY = ("DIAGONAL", "DIAGONAL_PUT", "PMCC")
SLEEVE_STRATEGIES = ("LONG_CALL",) + DIAGONAL_FAMILY  # LONG_PUT excluded -- see module docstring


# ---------------------------------------------------------------------------
# W160 -- per-trade risk
# ---------------------------------------------------------------------------

def csp_one_sigma_risk(spot, iv_pct, dte, contracts):
    """CSP risk = one-sigma move in dollars x contracts. IV-based (HELM-W12:
    297/303 coverage vs ATR's 39%, and matches the buy-side's own breakeven
    gate). `iv_pct` is a PERCENT (30.0, not 0.30) -- the entry_snapshots /
    checks / thesis.py convention. Returns None on any missing input."""
    try:
        if not spot or not iv_pct or not dte or not contracts:
            return None
        dte = float(dte)
        if dte <= 0:
            return None
        sigma = float(spot) * (float(iv_pct) / 100.0) * math.sqrt(dte / 365.0)
        return round(sigma * 100.0 * float(contracts), 2)
    except Exception:
        return None


def debit_risk(fill_price, contracts, multiplier=100):
    """Long call / long put / one diagonal leg: risk = what was paid for it."""
    try:
        if fill_price is None or contracts is None:
            return None
        return round(float(fill_price) * float(multiplier) * float(contracts), 2)
    except Exception:
        return None


def defined_risk_from_max_loss(max_loss):
    """Spread / condor: risk = the position's own stored max_loss, passed
    through rather than re-derived (HELM standing rule: 'a number that
    appears in three places was derived once')."""
    try:
        return round(float(max_loss), 2) if max_loss is not None else None
    except Exception:
        return None


def trade_risk_dollars(strategy, *, contracts=None, fill_price=None,
                        max_loss=None, spot=None, iv_pct=None, dte=None,
                        long_fill_price=None, multiplier=100):
    """Route a strategy to its W160 formula.

    Returns (risk_dollars_or_None, formula_label). `formula_label` is always
    a short human string, even on a None result, so a caller can say WHY it
    could not flag rather than silently doing nothing.
    """
    strategy = (strategy or "").upper()
    if strategy == "CSP":
        return csp_one_sigma_risk(spot, iv_pct, dte, contracts), "one-sigma move x contracts"
    if strategy in LONG_SINGLE:
        return debit_risk(fill_price, contracts, multiplier), "debit"
    if strategy in DIAGONAL_FAMILY:
        lfp = long_fill_price if long_fill_price is not None else fill_price
        return debit_risk(lfp, contracts, multiplier), "long leg's debit"
    if max_loss is not None:
        return defined_risk_from_max_loss(max_loss), "max loss"
    return None, "no W160 formula for %s (and no stored max_loss)" % strategy


def capped_contracts(risk_per_contract, *, cap_dollars=RISK_CAP_PER_TRADE,
                      portfolio_value=None, risk_pct=None, ceiling=20):
    """The number of contracts $5,000/trade allows, and separately the
    number the existing risk_pct-of-portfolio ceiling allows (W160: 'the 5%
    cash ceiling stays behind it') -- returns the TIGHTER of the two.

    Returns (max_contracts, binding) where binding is 'risk_cap',
    'cash_ceiling', or None (risk_per_contract unusable). max_contracts is
    FLOORED, never rounded up into more risk than decided, and can be 0 --
    callers decide what 0 means for their strategy (s122: a CSP that comes
    out at 0 is not taken as a CSP at all; other strategies may still choose
    to floor a 0 up to 1 and say so, matching pre-W160 behaviour).
    """
    if not risk_per_contract or risk_per_contract <= 0:
        return None, None
    candidates = [(cap_dollars / risk_per_contract, "risk_cap")]
    if portfolio_value and risk_pct:
        candidates.append(((portfolio_value * risk_pct) / risk_per_contract, "cash_ceiling"))
    n, binding = min(candidates, key=lambda t: t[0])
    n_int = int(n)
    if ceiling is not None and n_int > ceiling:
        return ceiling, "ceiling"      # the sanity limit bound, not the cap
    return max(n_int, 0), binding


# ---------------------------------------------------------------------------
# W201 -- a 0 is a decline for these; everything else is floored AND SAYS SO
# ---------------------------------------------------------------------------
# Russ, 2026-09-27, after the s123 build: a long call or diagonal whose ONE
# contract already costs more than $5,000 is declined, the same as a CSP.
# s123 floored every non-CSP 0 up to 1 and labelled it "sized by the $5,000
# cap", which let a $16,095 LLY-style one-lot through looking compliant.
#   declined at 0 : CSP (W160), LONG_CALL and the diagonal family (W201)
#   floored to 1  : LONG_PUT (W201 did not decide it -- behaviour unchanged),
#                   spreads, condors, straddles -- and the note says "floored",
#                   never "sized by the cap".
DECLINE_AT_ZERO = ("CSP", "LONG_CALL") + DIAGONAL_FAMILY


class OverCapRefusal(Exception):
    """Raised by a PAPER booker that refuses an over-cap trade, so the batch
    records -- and logs -- a named reason rather than 'no viable real-chain
    contract'. str() is the logged reason: "refused by W160: ..." for a CSP,
    "refused by W201: ..." for a long call or diagonal."""

    def __init__(self, rule, strategy, amount, detail=None, message=None):
        self.rule, self.strategy, self.amount = rule, (strategy or "").upper(), amount
        self.detail = detail or (over_cap_text(strategy, amount) if amount is not None else "")
        # `message` replaces the whole reason when the cap could not run at all
        # (Russ, 2026-09-27: "refused: no IV, cap couldn't run").
        super().__init__(message or "refused by %s: %s" % (rule, self.detail))


# W195 real-book flags (see real_long_call_flags). They decline a real long
# call through the same 0-and-override path as W160/W201, so the binding
# names which rule said no.
W195_SLEEVE = "w195_sleeve"
W195_HELD = "w195_held"


def rule_for(strategy, binding=None):
    """Which decision declines this strategy (W160 / W201), or, for the W195
    real-book flags, which rule raised the flag."""
    if binding == W195_SLEEVE:
        return "W194"
    if binding == W195_HELD:
        return "W195"
    return "W160" if (strategy or "").upper() == "CSP" else "W201"


def override_line(strategy, binding, n):
    """What the CLI prints when Russ types a count over a decline."""
    b = binding or ""
    if b == W195_SLEEVE:
        what = "with the real long-premium sleeve at or over 5%"
    elif b == W195_HELD:
        what = "in a name that already has an open long call"
    else:
        what = "over " + ("the 5% cash ceiling" if b.startswith("cash_ceiling")
                          else "the $5,000 cap")
    return "%d contract(s) %s (%s) -- your call, recorded as typed" % (
        n, what, rule_for(strategy, binding))


def override_note(strategy, binding, n, size_note):
    """What is WRITTEN to positions.notes when a declined trade is booked by
    override (Russ, 2026-09-27). Searchable: notes LIKE '%OVERRIDE (W%'.
    A cap (W160/W201/W194) is 'OVER-CAP OVERRIDE'; W195's one-per-name is
    not a cap, so it says 'RULE OVERRIDE'."""
    prefix = "RULE OVERRIDE" if binding == W195_HELD else "OVER-CAP OVERRIDE"
    return "%s (%s): %s; booked %d contract(s) as typed" % (
        prefix, rule_for(strategy, binding), size_note, n)


def _money(x):
    return "$" + format(int(round(float(x))), ",d")


def one_contract_over_cap(amount):
    """True when ONE contract's W160 figure is already over $5,000. Strictly
    greater: a contract of exactly $5,000 fits."""
    return amount is not None and float(amount) > RISK_CAP_PER_TRADE


def over_cap_text(strategy, amount, binding="risk_cap"):
    """'one contract is $X, over the $5,000 cap' -- the wording Russ asked for,
    with the measure named where it is not a plain debit."""
    strategy = (strategy or "").upper()
    if binding and binding.startswith("cash_ceiling"):
        what, limit = " (collateral)", "the 5% cash ceiling"
    else:
        limit = "the $5,000 cap"
        what = (" (one-sigma move)" if strategy == "CSP"
                else " (long leg)" if strategy in DIAGONAL_FAMILY
                else "" if strategy in LONG_SINGLE
                else " (max loss)")
    return "one contract%s is %s, over %s" % (what, _money(amount), limit)


_LIMIT_LABEL = {
    "risk_cap": "the $5,000/trade risk cap (W160)",
    "cash_ceiling": "the 5% cash ceiling",
    "cash_ceiling_only_no_iv": "the 5% cash ceiling (IV, DTE or spot missing, so the $5,000 check could not run)",
    "ceiling": "the 20-contract sanity limit",
}


def size_decision(strategy, raw_n, binding, amount):
    """Turn a raw contract count into what to suggest, and say honestly why.

    raw_n   : the floored count the binding limit allows (may be 0)
    binding : which limit produced raw_n (see _LIMIT_LABEL)
    amount  : the per-contract dollars that limit was measured on -- one-sigma
              for a CSP's cap, collateral for a cash ceiling, else the debit /
              long-leg debit / max loss
    Returns (n, binding, note). n == 0 is a decline. A 1 that came from the
    floor carries binding 'floored' and a note that says so.
    """
    strategy = (strategy or "").upper()
    if raw_n is None:
        return 1, None, None
    if raw_n >= 1:
        return raw_n, binding, "sized to %d by %s" % (raw_n, _LIMIT_LABEL.get(binding, binding))
    over = over_cap_text(strategy, amount, binding)
    if strategy == "CSP":
        return 0, binding, "%s -- not taken as a CSP (W160)" % over
    if strategy in DECLINE_AT_ZERO:
        return 0, binding, "%s (W201)" % over
    undecided = ("long puts" if strategy == "LONG_PUT" else strategy)
    return 1, "floored", ("floored to 1, NOT sized by the cap: %s. The one-contract "
                          "decline is not decided for %s, so it still floors to 1"
                          % (over, undecided))


# ---------------------------------------------------------------------------
# DB-reading reports -- READ-ONLY (W34 discipline: a ro:// connection, never
# a writable one; nothing here is a gate, both are display for a human).
# ---------------------------------------------------------------------------

def _conn(db=None):
    if db is None:
        from helm.config import DB_PATH
        db = str(DB_PATH)
    c = sqlite3.connect('file:' + os.path.abspath(db) + '?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    return c


def _open_long_leg(conn, position_id):
    row = conn.execute(
        "SELECT * FROM legs WHERE position_id=? AND status='OPEN' AND direction='LONG' "
        "ORDER BY id LIMIT 1", (position_id,)).fetchone()
    return dict(row) if row else None


def _entry_snapshot(conn, position_id):
    row = conn.execute(
        "SELECT * FROM entry_snapshots WHERE position_id=? LIMIT 1", (position_id,)
    ).fetchone()
    return dict(row) if row else None


def position_risk(conn, position):
    """W160 risk for one OPEN position, read from what was already stored at
    entry (never re-fetched live -- this reports what was booked, not what
    the market does today). Returns a dict; `risk` is None when unmeasured.
    """
    p = dict(position)
    strategy = (p.get("strategy") or "").upper()
    contracts = p.get("total_contracts") or 1
    out = {"position_id": p["id"], "ticker": p["ticker"], "strategy": strategy,
           "contracts": contracts, "risk": None, "formula": None}
    if strategy == "CSP":
        es = _entry_snapshot(conn, p["id"])
        leg = _open_long_leg(conn, p["id"])  # CSP's own leg is SHORT; kept for shape only
        if es:
            risk, formula = trade_risk_dollars(
                strategy, contracts=contracts, spot=es.get("spot_price"),
                iv_pct=es.get("iv_current"), dte=es.get("dte") or p.get("entry_dte"))
        else:
            risk, formula = None, "no entry_snapshot for this position"
    elif strategy in LONG_SINGLE:
        leg = _open_long_leg(conn, p["id"])
        if leg:
            risk, formula = trade_risk_dollars(
                strategy, contracts=leg.get("contracts") or contracts,
                fill_price=leg.get("open_price"), multiplier=leg.get("multiplier") or 100)
        else:
            risk, formula = None, "no open long leg"
    elif strategy in DIAGONAL_FAMILY:
        leg = _open_long_leg(conn, p["id"])
        if leg:
            risk, formula = trade_risk_dollars(
                strategy, contracts=leg.get("contracts") or contracts,
                long_fill_price=leg.get("open_price"), multiplier=leg.get("multiplier") or 100)
        else:
            risk, formula = None, "no open long leg"
    else:
        risk, formula = trade_risk_dollars(strategy, max_loss=p.get("max_loss"))
    out["risk"], out["formula"] = risk, formula
    out["breach"] = (risk is not None and risk > RISK_CAP_PER_TRADE)
    return out


def risk_view(book="REAL", db=None):
    """Every OPEN position in `book`, its W160 risk, and whether it breaches
    the $5,000 cap. GATES NOTHING -- for the board / `helm risk` to display.
    Never raises: an exception here must not break a page that shows it.
    """
    try:
        conn = _conn(db)
    except Exception as exc:
        return {"error": str(exc), "book": book, "positions": [], "breaches": []}
    try:
        rows = conn.execute(
            "SELECT * FROM positions WHERE book=? AND status='OPEN'", (book,)
        ).fetchall()
        out = [position_risk(conn, r) for r in rows]
    except Exception as exc:
        return {"error": str(exc), "book": book, "positions": [], "breaches": []}
    finally:
        conn.close()
    breaches = [r for r in out if r["breach"]]
    unmeasured = [r for r in out if r["risk"] is None]
    return {"book": book, "cap": RISK_CAP_PER_TRADE, "positions": out,
            "breaches": breaches, "unmeasured": unmeasured}


# ---------------------------------------------------------------------------
# W194 -- long-premium sleeve
# ---------------------------------------------------------------------------

def sleeve_positions(conn, book="REAL"):
    """Every OPEN long-premium position counted toward the W194 sleeve, with
    the dollars each contributes (the long leg's cost; rent never nets it
    down). See module docstring for exactly which strategies are counted."""
    placeholders = ",".join("?" for _ in SLEEVE_STRATEGIES)
    rows = conn.execute(
        "SELECT * FROM positions WHERE book=? AND status='OPEN' AND strategy IN (%s)"
        % placeholders, (book,) + SLEEVE_STRATEGIES).fetchall()
    out = []
    for r in rows:
        p = dict(r)
        leg = _open_long_leg(conn, p["id"])
        if not leg:
            continue
        cost = debit_risk(leg.get("open_price"), leg.get("contracts"), leg.get("multiplier") or 100)
        if cost is None:
            continue
        out.append({"position_id": p["id"], "ticker": p["ticker"],
                     "strategy": p["strategy"], "cost": cost})
    return out


def sleeve_view(account_id, book="REAL", db=None):
    """Sleeve value, % of account value, and whether it is over the W194
    cap. GATES NOTHING -- new-long callers use `over_cap` to FLAG, per
    s122: 'while over the cap, no new longs; nothing is force-closed.'
    """
    try:
        conn = _conn(db)
    except Exception as exc:
        return {"error": str(exc), "value": None, "pct": None, "over_cap": None}
    try:
        positions = sleeve_positions(conn, book)
        acct = conn.execute(
            "SELECT portfolio_value, buying_power FROM accounts WHERE id=?",
            (account_id,)).fetchone()
    except Exception as exc:
        return {"error": str(exc), "value": None, "pct": None, "over_cap": None}
    finally:
        conn.close()
    value = round(sum(p["cost"] for p in positions), 2)
    account_value = (acct["portfolio_value"] or acct["buying_power"] or 0) if acct else 0
    pct = round(100.0 * value / account_value, 2) if account_value else None
    over_cap = (pct is not None and pct >= SLEEVE_CAP_PCT * 100.0)
    return {"book": book, "value": value, "account_value": account_value or None,
            "pct": pct, "cap_pct": SLEEVE_CAP_PCT * 100.0, "over_cap": over_cap,
            "positions": positions}


# ---------------------------------------------------------------------------
# W195 -- the long-call screen's limits (Russ, 2026-09-27)
#   REAL : a FLAG. No real long-call suggestion while the real sleeve is at or
#          over 5%, or while the name already has an open long call (a call
#          diagonal's long leg counts). Declines through the W201 path, so a
#          typed count overrides and the override is recorded.
#   PAPER: ENFORCED, and the paper sleeve counts ONLY long calls the W195
#          screen itself booked (Russ chose option (a): the paper test
#          measures the screen; paper's diagonals would measure the diagonal
#          sleeve instead -- they alone held 10.6% of the account on
#          2026-09-27, W199).
# ---------------------------------------------------------------------------

# A put diagonal's long leg is a put: not "a long call in the name".
HELD_LONG_CALL_STRATEGIES = ("LONG_CALL", "DIAGONAL", "PMCC")


def held_long_names(book="REAL", db=None, strategies=HELD_LONG_CALL_STRATEGIES):
    """{ticker: strategy} for every OPEN position in `book` holding a long call."""
    conn = _conn(db)
    try:
        rows = conn.execute(
            "SELECT ticker, strategy FROM positions WHERE book=? AND status='OPEN' "
            "AND strategy IN (%s)" % ",".join("?" for _ in strategies),
            (book,) + tuple(strategies)).fetchall()
    finally:
        conn.close()
    return {r["ticker"].upper(): r["strategy"] for r in rows}


def real_long_call_flags(ticker, account_id, db=None):
    """[(binding, text), ...] -- the W195 reasons HELM will not SUGGEST a
    real long call on `ticker`. Empty = no flag. Read-only; flags, never acts.

    Fails CLOSED on the sleeve: a sleeve that cannot be measured is a flag
    that says so, never a silent pass (an unmeasured risk must not read as a
    small one)."""
    flags = []
    sv = sleeve_view(account_id, "REAL", db=db)
    if sv.get("pct") is None:
        flags.append((W195_SLEEVE, "the real long-premium sleeve could not be "
                      "measured (%s) -- no long-call suggestion without it (W194)"
                      % (sv.get("error") or "no account value")))
    elif sv.get("over_cap"):
        flags.append((W195_SLEEVE, "the real long-premium sleeve is %.1f%% of the "
                      "account, at or over the 5%% cap -- no new long calls while it "
                      "is (W194)" % sv["pct"]))
    try:
        held = held_long_names("REAL", db=db)
    except Exception as exc:
        held, flags = {}, flags + [(W195_HELD, "open long calls could not be read "
                                    "(%s) -- one long call per name unchecked (W195)" % exc)]
    t = (ticker or "").upper()
    if t in held:
        flags.append((W195_HELD, "%s already has an open %s -- one long call per "
                      "name (W195)" % (t, held[t])))
    return flags


def apply_w195_real_flags(strategy, ticker, account_id, decision, db=None):
    """Fold the W195 real flags into a (n, binding, note) sizing decision.

    LONG_CALL only. A flagged name becomes n = 0 (declined, override by
    typed count) with the flag's binding; a trade W201 already declined keeps
    W201 as its binding and gains the flags in its note."""
    n, binding, note = decision
    if (strategy or "").upper() != "LONG_CALL" or not ticker:
        return decision
    flags = real_long_call_flags(ticker, account_id, db=db)
    if not flags:
        return decision
    text = "; ".join(t for _, t in flags)
    if n is not None and n <= 0:
        return n, binding, ("%s; also %s" % (note, text)) if note else text
    return 0, flags[0][0], text


W195_ORIGIN = "LC_SCREEN"
W195_VERSION_LIKE = '%"version": "lc-screen-v2%'


def w195_paper_sleeve(account_id, db=None):
    """The PAPER sleeve W195 is capped on: OPEN paper long calls the v2 screen
    booked (origin LC_SCREEN, originating signal scored by lc-screen-v2).
    Same shape as sleeve_view. v1 LC_SCREEN long calls and paper diagonals are
    NOT counted -- Russ's option (a), 2026-09-27."""
    try:
        conn = _conn(db)
    except Exception as exc:
        return {"error": str(exc), "value": None, "pct": None, "over_cap": None}
    try:
        ids = {r[0] for r in conn.execute(
            "SELECT p.id FROM positions p JOIN signals s ON s.id = p.signal_id "
            "WHERE p.book='PAPER' AND p.status='OPEN' AND p.strategy='LONG_CALL' "
            "AND p.origin_screen=? AND s.lc_gates_json LIKE ?",
            (W195_ORIGIN, W195_VERSION_LIKE))}
        positions = [p for p in sleeve_positions(conn, "PAPER") if p["position_id"] in ids]
        acct = conn.execute("SELECT portfolio_value, buying_power FROM accounts WHERE id=?",
                            (account_id,)).fetchone()
    except Exception as exc:
        return {"error": str(exc), "value": None, "pct": None, "over_cap": None}
    finally:
        conn.close()
    value = round(sum(p["cost"] for p in positions), 2)
    account_value = (acct["portfolio_value"] or acct["buying_power"] or 0) if acct else 0
    pct = round(100.0 * value / account_value, 2) if account_value else None
    return {"book": "PAPER", "scope": "W195 long calls only", "value": value,
            "account_value": account_value or None, "pct": pct,
            "cap_pct": SLEEVE_CAP_PCT * 100.0,
            "over_cap": (pct is not None and pct >= SLEEVE_CAP_PCT * 100.0),
            "positions": positions}


def position_long_cost(position_id, db=None):
    """The open long leg's cost for one position (the sleeve's measure), or None."""
    conn = _conn(db)
    try:
        leg = _open_long_leg(conn, position_id)
    finally:
        conn.close()
    if not leg:
        return None
    return debit_risk(leg.get("open_price"), leg.get("contracts"), leg.get("multiplier") or 100)


# ---------------------------------------------------------------------------
# Self-test -- no network, no writes. `python3 -m helm.risk_cap --selftest`
# ---------------------------------------------------------------------------

def _selftest():
    import math as _m
    failures = []

    _selftest.count = 0

    def check(label, got, want, tol=0.01):
        _selftest.count += 1
        ok = (abs(got - want) <= tol
              if isinstance(want, float) and isinstance(got, (int, float))
              else got == want)
        if not ok:
            failures.append("%s: got %r want %r" % (label, got, want))

    # CSP one-sigma: spot=100, iv=30%, dte=30 -> 100*0.30*sqrt(30/365)*100*2
    r = csp_one_sigma_risk(100, 30, 30, 2)
    check("csp_one_sigma_risk basic", r, 100 * 0.30 * _m.sqrt(30 / 365.0) * 100 * 2)
    check("csp_one_sigma_risk missing iv", csp_one_sigma_risk(100, None, 30, 2), None)
    check("csp_one_sigma_risk zero dte", csp_one_sigma_risk(100, 30, 0, 2), None)

    check("debit_risk basic", debit_risk(5.00, 3), 1500.0)
    check("debit_risk missing", debit_risk(None, 3), None)

    check("max_loss passthrough", defined_risk_from_max_loss(1234.5), 1234.5)
    check("max_loss None", defined_risk_from_max_loss(None), None)

    r, f = trade_risk_dollars("CSP", contracts=2, spot=100, iv_pct=30, dte=30)
    check("route CSP", round(r, 2), round(100 * 0.30 * _m.sqrt(30 / 365.0) * 100 * 2, 2))
    r, f = trade_risk_dollars("LONG_CALL", contracts=3, fill_price=5.00)
    check("route LONG_CALL", r, 1500.0)
    r, f = trade_risk_dollars("DIAGONAL", contracts=1, fill_price=20.0, long_fill_price=18.0)
    check("route DIAGONAL uses long leg not net fill_price", r, 1800.0)
    r, f = trade_risk_dollars("IRON_CONDOR", max_loss=900.0)
    check("route IRON_CONDOR via max_loss", r, 900.0)
    r, f = trade_risk_dollars("COVERED_CALL")
    check("route COVERED_CALL unmeasured", r, None)

    # capped_contracts: risk cap tighter than cash ceiling
    n, binding = capped_contracts(1000, portfolio_value=1_000_000, risk_pct=0.05)
    check("capped_contracts risk_cap binds", n, 5)  # 5000/1000
    check("capped_contracts risk_cap label", binding, "risk_cap")
    # cash ceiling tighter (small account)
    n, binding = capped_contracts(1000, portfolio_value=20_000, risk_pct=0.05)
    check("capped_contracts cash_ceiling binds", n, 1)  # (20000*0.05)/1000 = 1
    check("capped_contracts cash_ceiling label", binding, "cash_ceiling")
    # sub-one-contract CSP -- s122's "not taken as a CSP" case
    n, binding = capped_contracts(6000, portfolio_value=1_000_000, risk_pct=0.05)
    check("capped_contracts floors to 0 under cap", n, 0)
    # ceiling of 20 respected -- and labelled as the ceiling, not the cap
    n, binding = capped_contracts(1, portfolio_value=1_000_000, risk_pct=0.05, ceiling=20)
    check("capped_contracts respects ceiling", n, 20)
    check("capped_contracts ceiling label", binding, "ceiling")

    # --- W201 edges: ONE contract already over the cap ----------------------
    # LLY-style $16,095 one-lot long call: declined, never floored to 1
    n, binding = capped_contracts(16095, portfolio_value=688175.53, risk_pct=0.05)
    check("W201 LLY raw count", n, 0)
    n, b, note = size_decision("LONG_CALL", n, binding, 16095)
    check("W201 LLY long call declined", n, 0)
    check("W201 LLY note", note, "one contract is $16,095, over the $5,000 cap (W201)")
    # diagonal: the long leg over the cap -> declined
    n, b, note = size_decision("DIAGONAL", 0, "risk_cap", 7660)
    check("W201 diagonal declined", n, 0)
    check("W201 diagonal note", note,
          "one contract (long leg) is $7,660, over the $5,000 cap (W201)")
    for st in ("PMCC", "DIAGONAL_PUT"):
        check("W201 %s declined" % st, size_decision(st, 0, "risk_cap", 9000)[0], 0)
    # boundary: exactly $5,000 fits, a cent over does not
    check("W201 $5,000 exactly fits", capped_contracts(5000)[0], 1)
    check("W201 $5,000.01 does not", capped_contracts(5000.01)[0], 0)
    check("W201 one_contract_over_cap 5000", one_contract_over_cap(5000), False)
    check("W201 one_contract_over_cap 5000.01", one_contract_over_cap(5000.01), True)
    # long put: not decided -> floored to 1, and the note must not claim the cap sized it
    n, b, note = size_decision("LONG_PUT", 0, "risk_cap", 16095)
    check("W201 long put still floors", n, 1)
    check("W201 long put binding", b, "floored")
    check("W201 long put note honest", note.startswith("floored to 1, NOT sized by the cap"), True)
    # spreads: same -- floored, labelled floored
    n, b, note = size_decision("BULL_PUT_SPREAD", 0, "risk_cap", 6200)
    check("W201 spread floors", (n, b), (1, "floored"))
    check("W201 spread note says max loss", "(max loss) is $6,200" in note, True)
    # a real 1 from the cap IS labelled by the cap
    n, b, note = size_decision("LONG_CALL", 1, "risk_cap", 4800)
    check("W201 genuine 1 by cap", note, "sized to 1 by the $5,000/trade risk cap (W160)")
    # no floored 1 anywhere says "sized"
    for st in ("LONG_PUT", "BULL_PUT_SPREAD", "IRON_CONDOR", "LONG_STRADDLE"):
        _n, _b, _note = size_decision(st, 0, "risk_cap", 9999)
        check("W201 %s floored note never 'sized to'" % st, _note.startswith("sized"), False)
    # bad input
    n, binding = capped_contracts(0)
    check("capped_contracts zero risk", n, None)

    # --- W160 extension (Russ, 2026-09-27): CSP override + paper refusal text
    e = OverCapRefusal("W160", "CSP", 12034)
    check("paper CSP refusal text", str(e),
          "refused by W160: one contract (one-sigma move) is $12,034, over the $5,000 cap")
    check("paper LC refusal text", str(OverCapRefusal("W201", "LONG_CALL", 16150)),
          "refused by W201: one contract is $16,150, over the $5,000 cap")
    e = OverCapRefusal("W160", "CSP", None, message="refused: no IV, cap couldn't run")
    check("paper CSP no-IV refusal text", (str(e), e.rule, e.amount),
          ("refused: no IV, cap couldn't run", "W160", None))
    check("rule_for CSP / LC / DIAGONAL", (rule_for("CSP"), rule_for("LONG_CALL"),
                                            rule_for("DIAGONAL")), ("W160", "W201", "W201"))
    n, b, note = size_decision("CSP", 0, "risk_cap", 12034)
    check("override line CSP", override_line("CSP", b, 1),
          "1 contract(s) over the $5,000 cap (W160) -- your call, recorded as typed")
    check("override line CSP cash", override_line("CSP", "cash_ceiling", 2),
          "2 contract(s) over the 5% cash ceiling (W160) -- your call, recorded as typed")
    check("override note CSP", override_note("CSP", b, 1, note),
          "OVER-CAP OVERRIDE (W160): one contract (one-sigma move) is $12,034, over the "
          "$5,000 cap -- not taken as a CSP (W160); booked 1 contract(s) as typed")

    # --- W195 real-book flags: wording + fold (pure; the DB reads are verified
    # on a copy by _s123/verify_w195.py)
    check("W195 rule_for sleeve / held", (rule_for("LONG_CALL", W195_SLEEVE),
                                          rule_for("LONG_CALL", W195_HELD)), ("W194", "W195"))
    check("W195 override line sleeve", override_line("LONG_CALL", W195_SLEEVE, 2),
          "2 contract(s) with the real long-premium sleeve at or over 5% (W194) -- "
          "your call, recorded as typed")
    check("W195 override line held", override_line("LONG_CALL", W195_HELD, 1),
          "1 contract(s) in a name that already has an open long call (W195) -- "
          "your call, recorded as typed")
    check("W195 override note held", override_note("LONG_CALL", W195_HELD, 1, "KO held"),
          "RULE OVERRIDE (W195): KO held; booked 1 contract(s) as typed")
    check("W195 override note sleeve", override_note("LONG_CALL", W195_SLEEVE, 1, "x")[:22],
          "OVER-CAP OVERRIDE (W19")
    _saved = globals()["real_long_call_flags"]
    try:
        globals()["real_long_call_flags"] = lambda t, a, db=None: [(W195_SLEEVE, "sleeve 8.7%")]
        check("W195 fold: sized trade declined", apply_w195_real_flags(
            "LONG_CALL", "KO", "a", (3, "risk_cap", "sized to 3 by x")),
            (0, W195_SLEEVE, "sleeve 8.7%"))
        check("W195 fold: W201 decline keeps W201", apply_w195_real_flags(
            "LONG_CALL", "LLY", "a", (0, "risk_cap", "one contract is $16,095 (W201)")),
            (0, "risk_cap", "one contract is $16,095 (W201); also sleeve 8.7%"))
        check("W195 fold: diagonal untouched", apply_w195_real_flags(
            "DIAGONAL", "KO", "a", (2, "risk_cap", "n")), (2, "risk_cap", "n"))
        globals()["real_long_call_flags"] = lambda t, a, db=None: []
        check("W195 fold: no flags untouched", apply_w195_real_flags(
            "LONG_CALL", "KO", "a", (2, "risk_cap", "n")), (2, "risk_cap", "n"))
    finally:
        globals()["real_long_call_flags"] = _saved

    if failures:
        print("FAIL (%d):" % len(failures))
        for f in failures:
            print("  " + f)
        return 1
    print("PASS -- %d checks ok, no network, no writes" % _selftest.count)
    return 0


if __name__ == "__main__":
    import sys
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    print(__doc__)
