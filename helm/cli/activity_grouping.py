"""
HELM-038 Gap 2 — activity-import spread grouping (capability layer).

Pure, dependency-free inference: takes the `new_opens` transaction list that
`helm activity` builds (opens with no matching HELM position) and groups legs
that belong to the same multi-leg position, inferring the strategy from leg
structure so an imported spread is written as ONE multi-leg position instead of
fragmenting into misclassified single-leg rows (e.g. a bear call spread's short
leg landing as a COVERED_CALL with unbounded-looking risk).

Scope (v1 — mirrors Gap 1's `confirm_spread`/`confirm_condor`):
    credit verticals  BEAR_CALL_SPREAD, BULL_PUT_SPREAD
    IRON_CONDOR
Deferred (fall through to the existing single-leg path):
    debit verticals (bull call / bear put), diagonals (differing expirations),
    any bucket that doesn't match a recognized template.

No DB, no console, no HELM imports — the wiring patch (separate step) consumes
`classify_group`'s output and calls open_multileg_with_snapshot per recognized
group, falling through to import_stage4_position for the leftovers.

Each input tx is the dict produced by activity_cmd parse:
    {ticker, opt_type ('PUT'|'CALL'), strike (float), expiration (str),
     direction ('SHORT'|'LONG'), contracts (int), price (float per-share fill),
     date (str), ...}
"""

from collections import defaultdict


# ---- grouping ------------------------------------------------------------

def group_key(tx):
    """Legs of one spread share ticker, expiration, trade date and size.
    Diagonals differ in expiration -> different key -> never grouped (deferred)."""
    return (tx["ticker"], tx["expiration"], tx["date"], int(tx["contracts"]))


def group_new_opens(new_opens):
    """Bucket new_opens by group_key, preserving first-seen order.
    Returns list of buckets (each a list of tx dicts)."""
    buckets = defaultdict(list)
    order = []
    for tx in new_opens:
        k = group_key(tx)
        if k not in buckets:
            order.append(k)
        buckets[k].append(tx)
    return [buckets[k] for k in order]


# ---- structure inference -------------------------------------------------

def _leg_dict(tx):
    """Shape a tx into the legs-list dict open_multileg_with_snapshot expects.
    No greeks at import time; the writer fetches a partial snapshot as proxy."""
    return {
        "direction": tx["direction"],
        "opt_type": tx["opt_type"],
        "strike": float(tx["strike"]),
        "expiration": tx["expiration"],
        "fill_price": float(tx["price"]),
    }


def _vertical(short_tx, long_tx, contracts):
    """Build (strategy, legs, position_fields) for a 2-leg credit vertical,
    or None if it's a debit spread / not a recognized credit vertical."""
    opt_type = short_tx["opt_type"]           # both legs same opt_type here
    ss = float(short_tx["strike"])
    ls = float(long_tx["strike"])
    net_credit = float(short_tx["price"]) - float(long_tx["price"])   # per-share
    if net_credit <= 0:
        return None                            # debit spread -> defer (Gap-1 parity)

    if opt_type == "CALL" and ss < ls:
        strategy = "BEAR_CALL_SPREAD"
        breakeven = {"breakeven_high": round(ss + net_credit, 2)}
    elif opt_type == "PUT" and ss > ls:
        strategy = "BULL_PUT_SPREAD"
        breakeven = {"breakeven_low": round(ss - net_credit, 2)}
    else:
        return None                            # bull-call / bear-put debit -> defer

    width = abs(ls - ss)
    pf = {
        "spread_width": round(width, 2),
        "max_profit": round(net_credit * 100 * contracts, 2),
        "max_loss": round((width - net_credit) * 100 * contracts, 2),
        "credit_to_width_ratio": round(net_credit / width, 4) if width else None,
    }
    pf.update(breakeven)
    legs = [_leg_dict(short_tx), _leg_dict(long_tx)]
    return strategy, legs, pf


def _condor(puts, calls, contracts):
    """Build (strategy, legs, position_fields) for an iron condor from
    2 puts + 2 calls, or None if the structure isn't a valid credit condor."""
    sp = next((t for t in puts if t["direction"] == "SHORT"), None)
    lp = next((t for t in puts if t["direction"] == "LONG"), None)
    sc = next((t for t in calls if t["direction"] == "SHORT"), None)
    lc = next((t for t in calls if t["direction"] == "LONG"), None)
    if not (sp and lp and sc and lc):
        return None                            # need one short+one long each side

    sp_k, lp_k = float(sp["strike"]), float(lp["strike"])
    sc_k, lc_k = float(sc["strike"]), float(lc["strike"])
    # put spread sits below (short put > long put); call spread above (short call < long call);
    # the short strikes straddle the body (short put < short call).
    if not (sp_k > lp_k and sc_k < lc_k and sp_k < sc_k):
        return None

    net_credit = ((float(sp["price"]) - float(lp["price"]))
                  + (float(sc["price"]) - float(lc["price"])))
    if net_credit <= 0:
        return None

    put_width = sp_k - lp_k
    call_width = lc_k - sc_k
    width = max(put_width, call_width)         # max-risk side governs max_loss
    pf = {
        "spread_width": round(width, 2),
        "max_profit": round(net_credit * 100 * contracts, 2),
        "max_loss": round((width - net_credit) * 100 * contracts, 2),
        "credit_to_width_ratio": round(net_credit / width, 4) if width else None,
        "breakeven_low": round(sp_k - net_credit, 2),
        "breakeven_high": round(sc_k + net_credit, 2),
    }
    legs = [_leg_dict(sp), _leg_dict(lp), _leg_dict(sc), _leg_dict(lc)]
    return "IRON_CONDOR", legs, pf


def classify_group(group):
    """Infer a v1 multi-leg strategy from a bucket of tx dicts.

    Returns a dict {strategy, legs, position_fields, contracts, ticker,
    expiration, source_txs} for a recognized credit vertical or iron condor,
    or None to signal 'fall through to the single-leg import path'."""
    if len(group) < 2:
        return None                            # single leg -> existing path

    contracts = int(group[0]["contracts"])
    puts = [t for t in group if t["opt_type"] == "PUT"]
    calls = [t for t in group if t["opt_type"] == "CALL"]
    shorts = [t for t in group if t["direction"] == "SHORT"]
    longs = [t for t in group if t["direction"] == "LONG"]

    result = None
    if len(group) == 2 and len(shorts) == 1 and len(longs) == 1:
        if len(calls) == 2:
            result = _vertical(shorts[0], longs[0], contracts)
        elif len(puts) == 2:
            result = _vertical(shorts[0], longs[0], contracts)
        # mixed put+call 2-leg (strangle/synthetic) -> None (defer)
    elif len(group) == 4 and len(puts) == 2 and len(calls) == 2:
        result = _condor(puts, calls, contracts)
    # any other shape (3 legs, same-direction pairs, 4 same-type) -> None

    if result is None:
        return None
    strategy, legs, pf = result
    return {
        "strategy": strategy,
        "legs": legs,
        "position_fields": pf,
        "contracts": contracts,
        "ticker": group[0]["ticker"],
        "expiration": group[0]["expiration"],
        "source_txs": group,
    }


def partition_new_opens(new_opens):
    """Top-level convenience: split new_opens into (spreads, singles).

    spreads: list of classify_group dicts (recognized v1 multi-leg positions)
    singles: list of tx dicts to keep on the existing single-leg import path
             (unrecognized buckets are flattened back to individual txs)"""
    spreads, singles = [], []
    for bucket in group_new_opens(new_opens):
        c = classify_group(bucket)
        if c is None:
            singles.extend(bucket)
        else:
            spreads.append(c)
    return spreads, singles
