"""Canonical strategy list for HELM.

Single source of truth for the valid strategy tokens. Imported by the Python
layer (position.py today; future onboarding/menus should import here too). The
SQL CHECK constraints on positions.strategy and strategy_settings.strategy
enumerate the same set but cannot import this module; verify_strategy_checks()
reconciles them so DB drift surfaces as a loud (non-fatal) warning.
"""
from __future__ import annotations

STRATEGIES = (
    'CSP', 'COVERED_CALL', 'LONG_CALL', 'LONG_PUT', 'LONG_STRADDLE', 'PERM',
    'BULL_PUT_SPREAD', 'BEAR_CALL_SPREAD', 'IRON_CONDOR',
    'BEAR_PUT_SPREAD', 'BULL_CALL_SPREAD', 'LONG_CONDOR',
    'DIAGONAL', 'PMCC', 'DIAGONAL_PUT', 'SHORT_STRANGLE', 'JADE_LIZARD',
)

# Short strategy codes -- single source shared by `helm scan` (display) and
# `helm open` (input). Direction prefix: B = bull, Be = bear.
STRATEGY_CODES = {
    'CSP': 'CSP', 'COVERED_CALL': 'CC', 'LONG_CALL': 'LC', 'LONG_PUT': 'LP',
    'LONG_STRADDLE': 'STDL', 'PERM': 'PERM',
    'BULL_PUT_SPREAD': 'BPS', 'BEAR_CALL_SPREAD': 'BeCS', 'IRON_CONDOR': 'IC',
    'BEAR_PUT_SPREAD': 'BePS', 'BULL_CALL_SPREAD': 'BCS', 'LONG_CONDOR': 'LCDR',
    'DIAGONAL': 'DIAG', 'PMCC': 'PMCC', 'DIAGONAL_PUT': 'DGP',
    'SHORT_STRANGLE': 'STRG', 'JADE_LIZARD': 'JADE',
}

_CODE_TO_STRATEGY = {code.upper(): strat for strat, code in STRATEGY_CODES.items()}


def resolve_strategy(token):
    """Map a short code (case-insensitive) to its canonical strategy token.

    Canonical tokens pass through unchanged (uppercased). Shared by
    `helm scan` (display) and `helm open` (input) so they cannot drift.
    """
    t = (token or "").strip().upper()
    return _CODE_TO_STRATEGY.get(t, t)


def _check_tokens(sql):
    import re
    m = re.search(r"CHECK\s*\(\s*strategy\s+IN\s*\(", sql or "", re.I)
    if not m:
        return set()
    i = m.end() - 1
    depth = 0
    j = i
    while j < len(sql):
        if sql[j] == "(":
            depth += 1
        elif sql[j] == ")":
            depth -= 1
            if depth == 0:
                break
        j += 1
    return set(re.findall(r"'([A-Z][A-Z_]+)'", sql[i:j + 1]))


def verify_strategy_checks(conn=None):
    """Reconcile the SQL strategy CHECKs against STRATEGIES (non-fatal).

    Returns {'ok': bool, 'tables': {name: {'missing': [...], 'extra': [...]}}}.
    missing = in STRATEGIES but not the CHECK (widen the CHECK);
    extra   = in the CHECK but not STRATEGIES (add to the tuple).
    """
    close = False
    if conn is None:
        from helm.db import get_conn
        conn = get_conn()
        close = True
    want = set(STRATEGIES)
    out = {"ok": True, "tables": {}}
    try:
        for tbl in ("positions", "strategy_settings"):
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                (tbl,),
            ).fetchone()
            have = _check_tokens(row[0] if row else "")
            missing = sorted(want - have)
            extra = sorted(have - want)
            out["tables"][tbl] = {"missing": missing, "extra": extra}
            if missing or extra:
                out["ok"] = False
    finally:
        if close:
            conn.close()
    return out
