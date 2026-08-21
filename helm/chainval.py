"""helm/chainval.py - coercions for raw option-chain rows.

One concern: broker and yfinance chain rows carry NaN in numeric columns on
strikes that are otherwise quoted. The idiom int(row.get(col, 0) or 0) does
NOT defend against that -- float('nan') is truthy, so `nan or 0` evaluates to
nan and int(nan) raises ValueError.

W145 (s108): that raise escaped evaluate_diagonals entirely, because opt_rows'
try/except wraps only the chain fetch. `helm open BX DIAGONAL` died on it, as
did 7 of the 30 names the scan has ever routed to DIAGONAL -- 48% of the 103
routings. It is the only evaluator that walks 60-120 DTE back months, which is
where yfinance leaves open interest NaN on otherwise-quoted strikes.

Nothing here rounds, scales or judges. NaN and unparseable become 0 -- the
value the calling code already intended.
"""


def oi_int(value, default: int = 0) -> int:
    """Open interest / volume as an int. NaN, None and junk become `default`."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f:          # NaN is the only value not equal to itself
        return default
    return int(f)
