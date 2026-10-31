"""s116 - the diagonal's post-expiry marking path. Three changes, one theme:
a settled leg is a FACT the book already recorded, not a quote to re-derive.

1. helm/expiry.py  : + settled_mark(leg, ticker) -- legs.close_price first,
                     settlement_intrinsic() only as the fallback.
2. check_cmd       : primary leg prefers a leg that still QUOTES (a dead
                     primary makes opt_source non-live -> dq PARTIAL -> the
                     whole check is discarded); non-primary expired legs mark
                     via settled_mark.
3. _persist_real_leg_marks : write the legs that HAVE a mark, stamp the set
                     PARTIAL when it is not fully live, and say what was
                     skipped -- instead of returning bare and losing every
                     per-leg row for the rest of the position's life (W144).
4. paper_exit_agent.leg_mid : same settled_mark, so an acting close cannot
                     price a leg differently from the way the book settled it.

Dry-run by default; --apply writes, with .bak-s116-* backups + readback.
"""
import re, sys, shutil, datetime, py_compile, os

ROOT = os.environ.get("HELM_ROOT", os.path.expanduser("~/Projects/helm"))
APPLY = "--apply" in sys.argv
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

EXPIRY_ADD = '''

# ── s116: the book's own settled price ──────────────────────────────────────

def _leg_get(leg, key):
    """Read a field off a leg row (dict) or a Leg model. Both callers exist."""
    if isinstance(leg, dict):
        return leg.get(key)
    return getattr(leg, key, None)


def settled_mark(leg, ticker=None):
    """Standing mark for an EXPIRED leg: the price the book settled it at.

    Two paths valued a dead leg and they disagreed. `helm settle` (W131)
    writes legs.close_price from the last GOOD expiry-day MARK; this module's
    settlement_intrinsic() recomputes it from yfinance's official CLOSE. On
    BX-DIAGONAL-20260729-7A1722 that is 8.28 against 7.39 -- both constants,
    so the gap never closes: every check since the settle reported the
    position $89 better than its own book. The settled record wins.

    Intrinsic stays the fallback for a leg nothing has settled yet, and None
    still means unmarkable (HELM-095: never invent a value).
    """
    cp = _leg_get(leg, "close_price")
    if cp is not None:
        try:
            return round(float(cp), 4)
        except (TypeError, ValueError):
            pass
    return settlement_intrinsic(ticker, _leg_get(leg, "option_type"),
                                _leg_get(leg, "strike"),
                                _leg_get(leg, "expiration"))
'''

PRIMARY_OLD = '''    opt_legs = [l for l in legs if l.get("option_type") not in (None, "STOCK")]
    primary = opt_legs[0] if opt_legs else None

    # Fetch underlying price'''

PRIMARY_NEW = '''    opt_legs = [l for l in legs if l.get("option_type") not in (None, "STOCK")]
    # s116: the primary leg must be one that still QUOTES. The primary sets
    # opt_source, and save_check persists only when "live" is in it -- so a
    # position whose primary leg has EXPIRED is not journaled at all, which is
    # a diagonal's normal lifecycle rather than an anomaly. Nothing chose the
    # primary before this: "SELECT * FROM legs" has no ORDER BY, so it was the
    # insertion order. The PAPER diagonals happen to be written long-leg-first
    # and kept marking through their front-leg expiry; the two REAL ones are
    # written short-leg-first and would have gone silent on 2026-10-16.
    # Live-leg order is otherwise untouched -- this only moves the primary when
    # the leg that would have been chosen is dead.
    _dead = lambda l: (l.get("expiration") is not None
                       and (dte(l["expiration"]) is not None)
                       and dte(l["expiration"]) < 0)
    _quoting = [l for l in opt_legs if not _dead(l)]
    primary = (_quoting or opt_legs)[0] if opt_legs else None

    # Fetch underlying price'''

MID_OLD = '''                from helm.expiry import settlement_intrinsic
                _q = {}
                _mid = settlement_intrinsic(ticker, _lg["option_type"],
                                            _lg["strike"], _lg["expiration"])
                _leg_live = False'''

MID_NEW = '''                from helm.expiry import settled_mark
                _q = {}
                # s116: the settled close_price the book recorded, when it has
                # one; intrinsic only as the fallback. See expiry.settled_mark.
                _mid = settled_mark(_lg, ticker)
                _leg_live = False'''

PERSIST_OLD = '''    import uuid as _uuid
    from datetime import datetime
    if not leg_marks_by_id:
        return
    for _m in leg_marks_by_id:
        if (not _m.get("leg_id") or _m.get("current_price") is None
                or not _m.get("is_live")):
            return
    _now = datetime.now().isoformat()
    _seen = set()
    for _m in leg_marks_by_id:
        _lid = _m["leg_id"]
        if _lid in _seen:
            continue
        _seen.add(_lid)'''

PERSIST_NEW = '''    import uuid as _uuid
    from datetime import datetime
    if not leg_marks_by_id:
        return
    # s116 (W144's stated fix shape): write the legs that HAVE a mark and label
    # the set, instead of returning bare. The all-or-nothing live gate was
    # written for a partly-unquotable position, where a consumer could compute
    # a confidently wrong net delta from three legs of four. It also fires on a
    # state that is not a fault at all -- a settled leg is never "live" -- so
    # BX-DIAGONAL-20260729-7A1722 has had ZERO leg_checks rows since its front
    # leg expired on 2026-08-28, silently, while every other open diagonal
    # carried 17-18 over the same six days. Both REAL diagonals reach that
    # state on 2026-10-16.
    #
    # A set is GOOD only when every leg is priced AND live -- unchanged, so
    # every reader that trusts GOOD sees exactly what it saw before. Anything
    # else is written PARTIAL: the truth is kept, and nobody can mistake it for
    # a whole one. A leg with no mark at all is still not written (HELM-095),
    # and the skip now SAYS so -- the gate used to discard its own evidence,
    # which is why characterising one position took an hour of queries.
    _priced = [_m for _m in leg_marks_by_id
               if _m.get("leg_id") and _m.get("current_price") is not None]
    if not _priced:
        return
    _unpriced = [_m.get("leg_id") for _m in leg_marks_by_id
                 if _m.get("leg_id") and _m.get("current_price") is None]
    _not_live = [_m.get("leg_id") for _m in _priced if not _m.get("is_live")]
    _dq = "GOOD" if (not _unpriced and not _not_live) else "PARTIAL"
    if _dq != "GOOD":
        # to the log, never stdout: this runs inside the snapshot agent and
        # inside `helm check`, and a stray line on stdout is a rendered claim
        # nobody asked for.
        logging.getLogger("helm.check").info(
            "leg_checks PARTIAL for %s: %d priced, unpriced=%s, not-live=%s",
            position_id, len(_priced), _unpriced or "none", _not_live or "none")
    _now = datetime.now().isoformat()
    _seen = set()
    for _m in _priced:
        _lid = _m["leg_id"]
        if _lid in _seen:
            continue
        _seen.add(_lid)'''

PERSIST_INS_OLD = '''            "current_bid, current_ask, current_price, delta, gamma, theta, vega, iv_current, greeks_source, data_quality, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'GOOD', ?)",'''
PERSIST_INS_NEW = '''            "current_bid, current_ask, current_price, delta, gamma, theta, vega, iv_current, greeks_source, data_quality, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",'''

PERSIST_ARGS_OLD = '''             check_id, position_id, _lid, _now, _m.get("bid"), _m.get("ask"), _m["current_price"], _m.get("delta"), _m.get("gamma"), _m.get("theta"), _m.get("vega"), _m.get("iv"), ("ibkr-live" if _m.get("delta") is not None else None), _now),'''
PERSIST_ARGS_NEW = '''             check_id, position_id, _lid, _now, _m.get("bid"), _m.get("ask"), _m["current_price"], _m.get("delta"), _m.get("gamma"), _m.get("theta"), _m.get("vega"), _m.get("iv"), ("ibkr-live" if _m.get("delta") is not None else None), _dq, _now),'''

AGENT_OLD = '''            from helm.expiry import settlement_intrinsic
            return settlement_intrinsic(
                getattr(tk, "ticker", None), leg.option_type, leg.strike, _exp)'''
AGENT_NEW = '''            from helm.expiry import settled_mark
            # s116: the book's settled close_price first, intrinsic as the
            # fallback -- an acting close must not price a leg differently
            # from the way `helm settle` recorded it.
            return settled_mark(leg, getattr(tk, "ticker", None))'''


EDITS = [
    ("helm/expiry.py",        [("__APPEND__", EXPIRY_ADD)]),
    ("helm/cli/check_cmd.py", [(PRIMARY_OLD, PRIMARY_NEW),
                               (MID_OLD, MID_NEW),
                               (PERSIST_OLD, PERSIST_NEW),
                               (PERSIST_INS_OLD, PERSIST_INS_NEW),
                               (PERSIST_ARGS_OLD, PERSIST_ARGS_NEW)]),
    ("paper_exit_agent.py",   [(AGENT_OLD, AGENT_NEW)]),
]

ok = True
for rel, edits in EDITS:
    path = os.path.join(ROOT, rel)
    src = open(path).read()
    new = src
    for old, repl in edits:
        if old == "__APPEND__":
            if "def settled_mark(" in new:
                print(f"  {rel}: settled_mark already present -- SKIP"); continue
            new = new.rstrip("\n") + "\n" + repl
            continue
        n = new.count(old)
        if n != 1:
            print(f"  !! {rel}: anchor matched {n} times, expected 1:\n     {old.splitlines()[0][:70]}")
            ok = False
            continue
        new = new.replace(old, repl)
    if new == src:
        print(f"  {rel}: no change")
        continue
    print(f"  {rel}: {len(src)} -> {len(new)} bytes")
    if APPLY and ok:
        shutil.copy2(path, f"{path}.bak-s116-{STAMP}")
        open(path, "w").write(new)
        py_compile.compile(path, doraise=True)
        back = open(path).read()
        assert back == new, f"readback mismatch on {rel}"
        print(f"     written, py_compile OK, readback identical ({len(back)} bytes)")

if not ok:
    print("\nANCHOR FAILURE -- nothing written for the failed file.")
    sys.exit(1)
print("\nDRY RUN (pass --apply to write)" if not APPLY else "\nAPPLIED.")
