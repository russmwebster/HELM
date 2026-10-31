#!/usr/bin/env python3
"""W163 (s113) -- the chain bypass for the diagonal pin. s111's pin path only
matched against the <=4 SCREENED candidates, so a real fill whose pair was not
on today's board was still refused -- the exact gap W163 was raised for (AA's
$55C/$47C was logged by script for this reason). Now, when all four pin flags
are given and no candidate matches, the two named contracts are fetched
straight from the chain (W97's bypass, for two legs), booked through the
canonical writer with origin MANUAL_PIN, and the position's notes disclose
the out-of-band entry permanently (W132/W164's lesson). A contract absent
from the chain still REFUSES (W7). Anchored replaces; aborts on any miss.
Backups: helm/cli/diagonal.py.bak-s113-* and helm/cli/open_cmd.py.bak-s113-*."""
import io, os, sys, time, py_compile, shutil

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
D = os.path.join(ROOT, "helm", "cli", "diagonal.py")
O = os.path.join(ROOT, "helm", "cli", "open_cmd.py")
STAMP = time.strftime("%Y%m%d-%H%M%S")

def rd(p): return io.open(p, encoding="utf-8").read()
def once(src, a, label):
    n = src.count(a)
    if n != 1: sys.exit("ABORT: anchor %s x%d" % (label, n))

d = rd(D); o = rd(O)

# ---- diagonal.py -------------------------------------------------------
A_EVAL = 'def evaluate_diagonal(ticker: str, config: dict = None) -> tuple:'
once(d, A_EVAL, "evaluate_diagonal def")
NEW_FN = '''def _chain_num(x):
    """yfinance leaves NaN in bid/ask/IV/lastPrice on quoted strikes (W145's
    lesson: `or 0` does not defend against NaN). 0.0 for None/NaN/junk."""
    try:
        v = float(x)
        return v if v == v else 0.0
    except (TypeError, ValueError):
        return 0.0


def pin_diagonal_from_chain(ticker: str, short_strike, short_exp, long_strike,
                            long_exp, spot: float = None) -> tuple:
    """W163 (s113) -- W97's bypass for two legs: fetch the two NAMED call
    contracts straight from the chain, so a real fill the screen would never
    have proposed can still be RECORDED. Returns (spot, candidate) in the
    nested shape _confirm_diagonal consumes, flagged pinned_out_of_band.
    Refuses (RuntimeError, nothing booked) when an expiry or strike is not on
    the chain -- it names the contract, never substitutes (W7). Delta is the
    same BS-from-IV the screen uses (chains carry no greeks); mid falls back
    to lastPrice when there is no two-sided quote (pre-market, illiquid) and
    says so -- it is only the prompt default; the trader types the fill."""
    import math
    from datetime import date, datetime
    import yfinance as yf
    from scipy.stats import norm
    from helm.chainval import oi_int
    tk = yf.Ticker(ticker)
    if not spot:
        spot = getattr(tk.fast_info, "last_price", None)
        if not spot:
            h = tk.history(period="5d")
            spot = float(h["Close"].iloc[-1]) if not h.empty else None
    if not spot:
        raise RuntimeError(f"Pin refused: could not determine spot for {ticker}. Nothing was booked.")
    today = date.today()
    expiries = list(tk.options or [])

    def leg(strike, exp, role):
        exp = str(exp)[:10]
        try:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
        except ValueError:
            raise RuntimeError(f"Pin refused: {role} expiry {exp!r} is not YYYY-MM-DD. Nothing was booked.")
        if exp not in expiries:
            raise RuntimeError(f"Pin refused: {ticker} has no {exp} expiry on the chain "
                               f"({role} leg). Nothing was booked.")
        try:
            df = tk.option_chain(exp).calls
        except Exception as e:
            raise RuntimeError(f"Pin refused: chain fetch failed for {ticker} {exp}: {e}. Nothing was booked.")
        want = float(strike)
        rows = df[(df["strike"] - want).abs() < 0.01]
        if rows.empty:
            raise RuntimeError(f"Pin refused: no ${want:g} CALL at {exp} on {ticker}'s chain "
                               f"({role} leg). Nothing was booked.")
        r = rows.iloc[0]
        bid, ask, last = _chain_num(r.get("bid")), _chain_num(r.get("ask")), _chain_num(r.get("lastPrice"))
        two_sided = bid > 0 and ask > 0
        mid = round((bid + ask) / 2, 2) if two_sided else round(last, 2)
        iv = round(_chain_num(r.get("impliedVolatility")) * 100, 1)
        delta = None
        try:
            v = iv / 100.0
            if v > 0 and dte > 0 and want > 0:
                T = dte / 365.0
                d1 = (math.log(spot / want) + (0.045 + 0.5 * v * v) * T) / (v * math.sqrt(T))
                delta = round(float(norm.cdf(d1)), 3)
        except Exception:
            delta = None
        return {"expiration": exp, "dte": dte, "strike": want, "mid": mid, "delta": delta,
                "iv": iv, "oi": oi_int(r.get("openInterest")),
                "mid_source": "bid/ask" if two_sided else "last"}

    s = leg(short_strike, short_exp, "short")
    l = leg(long_strike, long_exp, "long")
    net_debit = round(l["mid"] - s["mid"], 2)
    return spot, {
        "short": s, "long": l,
        "net_debit": net_debit,
        "net_debit_total": round(net_debit * 100, 2),
        "breakeven": round(s["strike"] + net_debit, 2),
        "max_profit_approx": round(s["mid"] * 100, 2),
        "spot": spot,
        "pinned_out_of_band": True,
    }


def evaluate_diagonal(ticker: str, config: dict = None, allow_empty: bool = False) -> tuple:'''
d = d.replace(A_EVAL, NEW_FN)

A_RAISE = '''    if not flat:
        raise RuntimeError(explain_diagonal_miss(_trace, core_cfg))
    return spot, [_reshape_diagonal(r, spot) for r in flat]'''
once(d, A_RAISE, "empty-raise")
d = d.replace(A_RAISE, '''    if not flat:
        if allow_empty:
            # W163 (s113): a pinned caller does not need the screen to have
            # found anything -- the pin names its own pair from the chain.
            return spot, []
        raise RuntimeError(explain_diagonal_miss(_trace, core_cfg))
    return spot, [_reshape_diagonal(r, spot) for r in flat]''')

A_DOC = '''      --strike K --expiry YYYY-MM-DD          the SHORT leg
      --long-strike K --long-expiry YYYY-MM-DD  the LONG leg
      --expect-contracts N --expect-net X      refuse on mismatch (W84)
    A pin that matches no candidate REFUSES rather than substituting (W7).
    """'''
once(d, A_DOC, "docstring")
d = d.replace(A_DOC, '''      --strike K --expiry YYYY-MM-DD          the SHORT leg
      --long-strike K --long-expiry YYYY-MM-DD  the LONG leg
      --expect-contracts N --expect-net X      refuse on mismatch (W84)
    A pin that matches a screened candidate books it as SELL_SCREEN. A full
    four-flag pin that matches NO candidate is fetched straight from the chain
    (s113 -- W97's bypass for two legs) and booked as MANUAL_PIN with the
    out-of-band entry written into the position's notes; a contract that is
    not on the chain REFUSES rather than substituting (W7).
    """''')

A_REFUSE = '''        if d is None:
            console.print("[red]  Pin refused:[/red] no candidate matches short "
                          f"${pin_ks} {pin_es}"
                          + (f" / long ${pin_kl} {pin_el}" if pin_kl else "")
                          + ". The chain moved; nothing was booked.")
            for cand in diagonals[:5]:
                s0, l0 = cand["short"], cand["long"]
                console.print(f"    [dim]candidate: short ${s0['strike']:.0f} {s0['expiration']}"
                              f" / long ${l0['strike']:.0f} {l0['expiration']}[/dim]")
            return
'''
once(d, A_REFUSE, "refusal block")
d = d.replace(A_REFUSE, '''        if d is None and (pin_kl is None or pin_el is None):
            console.print("[red]  Pin refused:[/red] no screened candidate matches short "
                          f"${pin_ks} {pin_es}, and a chain pin needs all four of "
                          "--strike --expiry --long-strike --long-expiry. Nothing was booked.")
            for cand in diagonals[:5]:
                s0, l0 = cand["short"], cand["long"]
                console.print(f"    [dim]candidate: short ${s0['strike']:.0f} {s0['expiration']}"
                              f" / long ${l0['strike']:.0f} {l0['expiration']}[/dim]")
            return
        if d is None:
            # W163 (s113): not one of today's screened pairs -- fetch the
            # named contracts from the chain instead of refusing. The screen
            # did not propose this; the record will say so (MANUAL_PIN + notes).
            try:
                _spot, d = pin_diagonal_from_chain(ticker, pin_ks, pin_es, pin_kl, pin_el, spot=spot)
            except RuntimeError as e:
                console.print(f"[red]  {e}[/red]")
                return
            spot = spot or _spot
            s0, l0 = d["short"], d["long"]
            console.print("[yellow]  Pinned from the chain, OUT OF BAND:[/yellow] not one of "
                          f"today's {len(diagonals)} screened candidate(s). Booked as MANUAL_PIN; "
                          "the position's notes will say so.")
            for role, x in (("short", s0), ("long", l0)):
                dl = f"{x['delta']:.2f}" if x.get("delta") is not None else "n/a"
                console.print(f"    [dim]{role}: ${x['strike']:.0f} CALL {x['expiration']} "
                              f"({x['dte']}d)  delta {dl}  mid ${x['mid']:.2f} ({x['mid_source']})"
                              f"  OI {x.get('oi', 0)}[/dim]")
''')

A_PF = '''        "origin_screen": "SELL_SCREEN",                # routed candidate, trader-confirmed'''
once(d, A_PF, "origin field")
d = d.replace(A_PF, '''        # W97's rule: a pinned-from-chain booking is not a screen
        # recommendation; W19 must not credit the screen with it.
        "origin_screen": "MANUAL_PIN" if d.get("pinned_out_of_band") else "SELL_SCREEN",''')

A_NOTES = '''        notes=f"Diagonal: short ${s['strike']:.0f} {s['expiration']} / "
              f"long ${l['strike']:.0f} {l['expiration']} -- fills confirmed at entry")'''
once(d, A_NOTES, "notes")
d = d.replace(A_NOTES, '''        notes=f"Diagonal: short ${s['strike']:.0f} {s['expiration']} / "
              f"long ${l['strike']:.0f} {l['expiration']} -- fills confirmed at entry"
              + (" -- PINNED OUT OF BAND (s113/W163): not a screened candidate; "
                 f"short delta {s.get('delta')} {s.get('dte')}d, long delta {l.get('delta')} "
                 f"{l.get('dte')}d at entry" if d.get("pinned_out_of_band") else ""))''')

A_EMPTYTBL = '''    console.print(tbl)
    console.print(f"  [dim]Net debit per share = long mid - short credit (x100 per contract)[/dim]")'''
once(d, A_EMPTYTBL, "table print")
d = d.replace(A_EMPTYTBL, '''    if not diagonals:
        console.print("  [dim]No screened candidates today (pinned caller -- the pin names its own pair).[/dim]")
    console.print(tbl)
    console.print(f"  [dim]Net debit per share = long mid - short credit (x100 per contract)[/dim]")''')

# ---- open_cmd.py -------------------------------------------------------
A_DISP = '''    if is_diagonal:
        try:
            from helm.cli.diagonal import evaluate_diagonal, display_diagonal
            spot_d, diagonals = evaluate_diagonal(ticker)'''
once(o, A_DISP, "open_cmd diagonal dispatch")
o = o.replace(A_DISP, '''    if is_diagonal:
        try:
            from helm.cli.diagonal import evaluate_diagonal, display_diagonal
            # W163 (s113): a full pin names its own pair from the chain, so an
            # empty screen must not end the command before the pin is read.
            _diag_pinned = (pin_strike is not None and pin_expiry is not None
                            and "--long-strike" in args and "--long-expiry" in args)
            spot_d, diagonals = evaluate_diagonal(ticker, allow_empty=_diag_pinned)''')

# ---- write, compile, readback ------------------------------------------
for p, new in ((D, d), (O, o)):
    bak = p + ".bak-s113-" + STAMP
    shutil.copy2(p, bak)
    io.open(p, "w", encoding="utf-8").write(new)
    try:
        py_compile.compile(p, doraise=True)
    except Exception as e:
        shutil.copy2(bak, p)
        sys.exit("ABORT: py_compile failed on %s, restored: %s" % (p, e))
    print("wrote", p, len(new), "bytes; backup", os.path.basename(bak))

chk = rd(D)
for needle, n in (("def pin_diagonal_from_chain(", 1), ("pinned_out_of_band", 5),
                  ("allow_empty", 3), ("PINNED OUT OF BAND", 1)):
    c = chk.count(needle)
    print("readback %-32s x%d %s" % (needle, c, "OK" if c == n else "MISMATCH (expected %d)" % n))
print("readback open_cmd allow_empty x%d" % rd(O).count("allow_empty=_diag_pinned"))
