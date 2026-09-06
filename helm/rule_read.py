"""W166 -- what the paper book's exit rule would do to THIS position, right now.

INFORMATION ONLY. On the real book Russ decides every exit (HELM-193); the
rules run the paper book. This module renders the rule's verdict on a REAL
position as a reading -- "the paper rule would close this now because ..." --
and acts on nothing. Anything reading as an instruction on a real-book
surface is a defect; the wording here is a report of what a different
process would do, with its reasons and its record.

ONE DOCTRINE, NOT A SECOND COPY. The verdict itself comes from the engine:
  - on the positions board, `check_one` has already run `decision.evaluate`
    and left `core_reason` + `arms` on the assessment -- `from_assessment`
    just reads them;
  - on the thesis card there is no live check, so `from_journal` reads the
    latest journaled check: for LONG_* the snapshot journals long_verdict's
    own arms record (`lc_arms_json`, HELM-101 s4) including `fired`; for the
    credit/calendar families it mirrors decision.evaluate's six-line branch
    using decision's OWN constants and settings lookup, and the s115 harness
    asserts the mirror agrees with the engine on the paper book's real closes.
This module never decides differently from the engine on purpose; if it ever
does, the engine is right and this file has a bug.

WHY THE TRACK RECORD RIDES ALONG. s115 measured what happens after a long
call peaks and gives back 20 points: the option regained its high in 5 of 15
cases, and every recovery came within days. A verdict shown without that is
an instruction dressed up as a fact.
"""
from helm import decision as D
from helm import long_exit as L

# Acting reasons, both families -- identical to paper_exit_agent.ACT_REASONS
# minus the retired v2 names that can no longer fire.
FIRES = ("PROFIT_TARGET", "DTE_MANAGE", "EXPIRY",
         "GIVE_BACK", "STOP_LOSS", "DTE_21", "DTE_7")

LABEL = {
    "PROFIT_TARGET": "target reached",
    "DTE_MANAGE": "calendar — inside the management window",
    "EXPIRY": "at expiry",
    "GIVE_BACK": "give-back off the peak",
    "STOP_LOSS": "stop",
    "DTE_21": "calendar — 21 DTE and not positive",
    "DTE_7": "calendar — 7 DTE hard close",
}

# s115: the measured record behind the long-side give-back line. Update when
# the study is re-run; the numbers are quoted on the card, so keep them true.
GIVE_BACK_RECORD = ("Measured 2026-09-06 over 15 long calls that peaked 10%+ "
                    "and then gave back 20 points: the option regained its "
                    "peak in 5, every one within a week; 4 went to zero; the "
                    "rest are still running below it.")


def _pct(v):
    return "—" if v is None else ("%+.0f%%" % (v * 100.0))


def explain(reason, strategy, pnl_pct=None, dte_now=None, arms=None,
            target_pct=None, dte_exit=None, book="REAL"):
    """Turn an engine verdict into a reading. pnl_pct is a FRACTION here
    (0.137 for +13.7%), matching long_exit's arms; the board and the card
    both convert before calling. Pure."""
    fires = reason in FIRES
    fam = D._family(strategy)
    lines = []
    arms = arms or {}
    gb = arms.get("give_back") or {}
    v3 = arms.get("v3") or {}
    if reason == "GIVE_BACK":
        hwm, floor, band = gb.get("hwm"), gb.get("floor"), gb.get("band", L.GIVE_BACK_BAND)
        lines.append("Peak %s of the debit, now %s. The trail closes %d points "
                     "below the peak, so the line was %s and the mark is under it."
                     % (_pct(hwm), _pct(pnl_pct), round((band or 0) * 100), _pct(floor)))
        if hwm is not None and hwm <= 0:
            lines.append("This position never had a profitable peak; the trail "
                         "still applies from its best mark, capped by the stop.")
        lines.append(GIVE_BACK_RECORD)
    elif reason == "STOP_LOSS":
        lines.append("Mark %s of the debit, through the %s stop."
                     % (_pct(pnl_pct), _pct(v3.get("stop", L.STOP_LOSS_PCT))))
    elif reason == "DTE_7":
        lines.append("%s days to expiry; the rule closes every long at %d DTE "
                     "regardless of sign, to avoid auto-exercise."
                     % (dte_now, v3.get("dte_hard", L.DTE_HARD)))
    elif reason == "DTE_21":
        lines.append("%s days to expiry at %s: the rule closes a long at %d DTE "
                     "only if it is not positive, and this one is not."
                     % (dte_now, _pct(pnl_pct), v3.get("dte_soft", L.DTE_SOFT)))
    elif reason == "PROFIT_TARGET":
        lines.append("%s of the %s captured against a %s target."
                     % (_pct(pnl_pct), "max profit" if fam == D.DEBIT_SPREAD_FAMILY else "credit",
                        _pct(target_pct)))
    elif reason == "DTE_MANAGE":
        lines.append("%s days to expiry, inside the %s-day management window "
                     "the premium-selling doctrine closes at, whatever the sign."
                     % (dte_now, dte_exit))
    elif reason == "EXPIRY":
        lines.append("Expiry has arrived.")
    else:
        # holding -- say what would have to happen, for the long families where
        # the arms record makes that cheap
        if fam == D.LONG_DEBIT_FAMILY and gb:
            lines.append("Holding. Peak %s, mark %s; the give-back line sits at %s "
                         "and the stop at %s."
                         % (_pct(gb.get("hwm")), _pct(pnl_pct), _pct(gb.get("floor")),
                            _pct(v3.get("stop", L.STOP_LOSS_PCT))))
        elif fam in (D.CREDIT_FAMILY, D.COVERED_FAMILY, D.DEBIT_SPREAD_FAMILY, D.DIAGONAL_FAMILY):
            lines.append("Holding. %s captured against a %s target; %s days to the "
                         "%s-day calendar." % (_pct(pnl_pct), _pct(target_pct), dte_now, dte_exit))
        else:
            lines.append("Holding.")
    return {"fires": fires, "reason": reason,
            "label": LABEL.get(reason, "hold"),
            "headline": ("The paper rule would CLOSE this now — %s." % LABEL[reason]) if fires
                        else "The paper rule would hold this.",
            "lines": lines,
            "doctrine": ("It would not close YOUR position: on the real book the rule "
                         "informs and never acts (HELM-193)."),
            "pnl_pct": pnl_pct, "dte": dte_now}


def _thresholds(pos):
    s = D._settings(pos.get("account_id"), pos.get("strategy")) if isinstance(pos, dict) else {}
    pt = s.get("profit_target_pct") or D.DEFAULT_PROFIT_TARGET
    pt = pt if pt <= 1 else pt / 100.0
    de = s.get("dte_exit_threshold") or D.DEFAULT_DTE_EXIT
    return pt, de


def from_assessment(a, pos):
    """Board path: check_one already ran the engine. Read, don't recompute."""
    pos = dict(pos) if not isinstance(pos, dict) else pos
    pt, de = _thresholds(pos)
    pnl = a.get("pnl_pct")
    pnl = (pnl / 100.0) if pnl is not None else None   # check_one's pnl_pct is a PERCENT
    prim = a.get("primary_leg") or {}
    dte_now = None
    try:
        from helm.decision import dte as _dte
        dtes = [_dte(l.get("expiration")) for l in (a.get("legs") or []) if l.get("expiration")]
        dtes = [d for d in dtes if d is not None]
        dte_now = min(dtes) if dtes else None
    except Exception:
        pass
    return explain(a.get("core_reason"), pos.get("strategy"), pnl, dte_now,
                   arms=a.get("arms"), target_pct=pt, dte_exit=de, book=pos.get("book"))


def from_journal(pos, legs, latest_check):
    """Card path: no live check. LONG_* reads long_verdict's journaled arms;
    the other families mirror decision.evaluate's branch on the latest mark.
    Returns None when there is no GOOD check to read."""
    import json
    if not latest_check:
        return None
    pos = dict(pos); strategy = pos.get("strategy"); fam = D._family(strategy)
    pt, de = _thresholds(pos)
    pct = latest_check.get("pnl_pct")
    pct = (pct / 100.0) if pct is not None else None          # checks store %
    dte_now = latest_check.get("dte_now")
    arms = None
    if fam == D.LONG_DEBIT_FAMILY:
        raw = latest_check.get("lc_arms_json")
        try:
            arms = json.loads(raw) if raw else None
        except Exception:
            arms = None
        reason = (arms or {}).get("fired")
        if arms is None:
            return None
    else:
        # mirror of decision.evaluate for the non-long families, verified
        # against the paper book's closes in _s115/verify_w166.py
        # DTE as of the CHECK, not as of today -- the same call works live
        # (the check is today's) and in replay (the check is historical).
        from datetime import date as _date
        try:
            asof = _date.fromisoformat((latest_check.get("checked_at") or "")[:10])
        except Exception:
            asof = _date.today()
        def _dte(exp):
            try:
                return (_date.fromisoformat(str(exp)[:10]) - asof).days
            except Exception:
                return None
        dtes = [_dte(l.get("expiration")) for l in legs if l.get("expiration")]
        dtes = [d for d in dtes if d is not None]
        dte_min = min(dtes) if dtes else dte_now
        dte_cal = (max(dtes) if dtes else dte_now) if fam == D.DIAGONAL_FAMILY else dte_min
        reason = None
        if fam == D.DEBIT_SPREAD_FAMILY:
            mp = pos.get("max_profit"); u = latest_check.get("pnl_unrealized")
            if mp and u is not None and (u / mp) >= pt:
                reason = "PROFIT_TARGET"
        elif fam in (D.CREDIT_FAMILY, D.COVERED_FAMILY):
            if pct is not None and pct >= pt:
                reason = "PROFIT_TARGET"
        if reason is None and dte_cal is not None:
            if dte_cal <= 0:
                reason = "EXPIRY"
            elif dte_cal <= de:
                reason = "DTE_MANAGE"
        dte_now = dte_min
    out = explain(reason, strategy, pct, dte_now, arms=arms, target_pct=pt,
                  dte_exit=de, book=pos.get("book"))
    out["as_of"] = (latest_check.get("checked_at") or "")[:16]
    return out
