"""Effective entry bands for SELL-side structures (HELM-135 / W71).

Two sets of numbers describe the same entry, and they disagree:

  strategy_settings          STRATEGY_CONFIG (open_cmd)
  CSP    delta 0.20-0.30     delta 0.15-0.40
         dte   30-45         dte   21-50
  BeCS   delta 0.15-0.25     delta 0.15-0.40
         dte   30-45         dte   21-56
  IC     delta 0.10-0.20     delta 0.15-0.40

The stored numbers sit inside published convention (15-30 delta, 30-45 DTE).
The code numbers sit outside it, and the code is what runs -- so a contract at
0.40 delta and 56 DTE is offered as qualified while the settings screen displays
0.25 and 45 as the preference. That is W71.

This is not hypothetical. The same shape has already cost money once: 42 closed
condors at a 10% win rate and -$6,107, while the same structure entered to spec
won 73%, because STRATEGY_CONFIG's dte_min and strategy_settings'
dte_exit_threshold drifted apart (see claude/HELM-condor-loss-root-cause.md and
_entry_dte_floor below it, which fixed that specific pair). This module
generalises the fix to the entry bands themselves.

THE RULE: the effective band is the INTERSECTION of the two -- the tighter of
each edge wins. Consequences, all deliberate:

  * The effective band is always a SUBSET of the code band. This can never
    loosen an entry, only narrow one, so it cannot introduce a trade that the
    current code would have refused.
  * It therefore does NOT touch entry_iv_rank_min, where the relationship runs
    the other way: settings say 20-40 while the scan gates at 50 (HELM-105
    deliberately raised the bear-call floor from the seeded 30). Applying the
    stored value there would LOOSEN a considered decision. Vol gating stays
    where it is.
  * A missing or unreadable settings row leaves the code band untouched. A
    guard that cannot read its own rule must not invent one -- the same posture
    _manage_threshold takes.

SELL SIDE ONLY, by Russ's instruction. The buy-side structures keep their code
bands verbatim.
"""

SELL_SIDE = (
    "CSP",
    "COVERED_CALL",
    "BULL_PUT_SPREAD",
    "BEAR_CALL_SPREAD",
    "IRON_CONDOR",
)

_SETTINGS_CACHE = {}

_FIELDS = (
    ("delta_min", "entry_delta_min", "lo"),
    ("delta_max", "entry_delta_max", "hi"),
    ("dte_min", "entry_dte_min", "lo"),
    ("dte_max", "entry_dte_max", "hi"),
)


def tighter(code_val, stored_val, edge):
    """The more conservative of two edges. None on either side yields the other.

    edge == 'lo' -> a floor, so the LARGER value is tighter.
    edge == 'hi' -> a ceiling, so the SMALLER value is tighter.
    """
    if stored_val is None:
        return code_val
    if code_val is None:
        return stored_val
    try:
        c, s = float(code_val), float(stored_val)
    except (TypeError, ValueError):
        return code_val
    return (max(c, s) if edge == "lo" else min(c, s))


def stored_entry_bands(strategy, conn_factory=None):
    """Read entry bands from strategy_settings. Returns {} on any failure."""
    if strategy in _SETTINGS_CACHE:
        return _SETTINGS_CACHE[strategy]
    out = {}
    try:
        if conn_factory is None:
            from helm.db import get_conn as conn_factory
        conn = conn_factory()
        try:
            row = conn.execute(
                "SELECT entry_delta_min, entry_delta_max, entry_dte_min, entry_dte_max "
                "FROM strategy_settings WHERE strategy = ? "
                "ORDER BY is_default DESC LIMIT 1", (strategy,)).fetchone()
            if row:
                for i, key in enumerate(
                        ("entry_delta_min", "entry_delta_max",
                         "entry_dte_min", "entry_dte_max")):
                    if row[i] is not None:
                        out[key] = row[i]
        finally:
            conn.close()
    except Exception:
        out = {}
    _SETTINGS_CACHE[strategy] = out
    return out


def effective_bands(strategy, config, stored=None):
    """Return (bands, notes) -- the code config narrowed by stored settings.

    bands is a dict with delta_min/delta_max/dte_min/dte_max. notes lists every
    edge that actually moved, so the caller can say WHY a contract was refused
    rather than just refusing it.
    """
    bands = {k: config.get(k) for k, _, _ in _FIELDS}
    notes = []
    if strategy not in SELL_SIDE:
        return bands, notes
    if stored is None:
        stored = stored_entry_bands(strategy)
    if not stored:
        return bands, notes
    for code_key, stored_key, edge in _FIELDS:
        cur = config.get(code_key)
        new = tighter(cur, stored.get(stored_key), edge)
        if new is not None and cur is not None and float(new) != float(cur):
            notes.append("%s %s -> %s (settings)" % (code_key, cur, new))
        # DTE is a whole number of days; settings store it as REAL.
        if new is not None and code_key.startswith('dte'):
            new = int(new)
        bands[code_key] = new
    return bands, notes
