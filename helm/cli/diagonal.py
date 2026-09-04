"""
helm open TICKER DIAGONAL

Diagonal spread (short near-term call / long back-month call).
Separate module imported by open_cmd.py.
"""
from datetime import date, datetime
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm
from rich import box

console = Console()

# ── Config defaults (all tunable) ─────────────────────────────────────────────
DIAGONAL_CONFIG = {
    "strategy":          "DIAGONAL",
    "option_type":       "CALL",
    "label":             "Diagonal Spread",
    "is_diagonal":       True,
    # Short leg
    "short_dte_min":     21,
    "short_dte_max":     45,
    "short_dte_sweet":   30,
    "short_delta_min":   0.30,
    "short_delta_max":   0.55,
    "short_delta_sweet": (0.38, 0.45),
    # Long leg
    "long_dte_min":      60,
    "long_dte_max":      120,
    "long_dte_sweet":    75,
    "long_delta_min":    0.55,
    "long_delta_max":    0.85,
    "long_delta_sweet":  (0.65, 0.75),
    # W147 (s108): max_debit_pct deliberately absent. It lived here as 0.75
    # while open_cmd.STRATEGY_CONFIG["DIAGONAL"] said 1.0, and evaluate_diagonal
    # passes core_cfg -- so 1.0 was always the number that ran and this copy had
    # never gated anything. W4's lesson: the copy you would naturally read was
    # not the copy that executed. STRATEGY_CONFIG is the single home.
}

PMCC_CONFIG = {
    "strategy":          "PMCC",
    "option_type":       "CALL",
    "label":             "Poor Man's Covered Call (PMCC)",
    # Short leg — OTM front-month call, rolled monthly
    "short_dte_min":     21,
    "short_dte_max":     45,
    "short_dte_sweet":   30,
    "short_delta_min":   0.20,
    "short_delta_max":   0.35,
    "short_delta_sweet": (0.25, 0.30),
    # Long leg — deep ITM LEAPS, held 1-2 years
    "long_dte_min":      150,
    "long_dte_max":      730,
    "long_dte_sweet":    365,
    "long_delta_min":    0.70,
    "long_delta_max":    0.90,
    "long_delta_sweet":  (0.75, 0.85),
    # W147 (s108): same as DIAGONAL above, and worse -- this said 0.30 against
    # an authoritative 1.0, so reading this file suggested PMCC caps its debit
    # at 30% of width when nothing ever enforced tighter than 100%.
}




def _score_delta(delta, sweet):
    mid = (sweet[0] + sweet[1]) / 2
    return 1.0 - abs(delta - mid) / max(mid, 0.01)


def _fetch_calls(tk, exp):
    try:
        chain = tk.option_chain(exp)
        df = chain.calls
        df = df[df["bid"] > 0].copy()
        df["mid"] = (df["bid"] + df["ask"]) / 2
        df["spread_pct"] = (df["ask"] - df["bid"]) / df["mid"].clip(lower=0.01)
        return df
    except Exception:
        return None


def _reshape_diagonal(r: dict, spot: float) -> dict:
    """Reshape one flat evaluate_diagonals row into the nested candidate shape
    that display_diagonal / _confirm_diagonal (and the _put variants) consume."""
    return {
        "short": {"expiration": r["short_exp"], "dte": r["short_dte"],
                  "strike": r["short_strike"], "mid": r["short_mid"],
                  "delta": r["short_delta"], "iv": r.get("short_iv"), "oi": r.get("short_oi")},
        "long": {"expiration": r["long_exp"], "dte": r["long_dte"],
                 "strike": r["long_strike"], "mid": r["long_mid"],
                 "delta": r["long_delta"], "iv": r.get("long_iv"), "oi": r.get("long_oi")},
        "net_debit": r["net_debit"],
        "net_debit_total": round(r["net_debit"] * 100, 2),
        "breakeven": r["breakeven"],
        "max_profit_approx": round(r["short_mid"] * 100, 2),
        "spot": spot,
    }


def explain_diagonal_miss(t: dict, cfg: dict) -> str:
    """W146 (s108): say WHICH gate emptied the search, with the count.

    "No diagonal combinations found matching risk criteria" was true of five
    different outcomes and distinguished none of them. Attributing ONE name
    (LIN, whose back-month strikes all failed open interest) took a
    purpose-built probe against the live chain. This turns that hour into a
    sentence.

    A caveat this message deliberately carries: open interest here comes from
    yfinance, the same feed that leaves the column NaN on quoted strikes
    (W145). A thin OI reading is a fact about the FEED until it is confirmed
    against IBKR -- so the wording says "reported", not "is".
    """
    if not t:
        return "No diagonal combinations found matching risk criteria."
    where = t.get("stopped_at")
    if where == "spot":
        return "No spot price available -- the chain was never read."
    if where == "expiries":
        return (
            "No expiry pair in range: %d short expiries at %d-%d DTE, %d long "
            "at %d-%d DTE. A diagonal needs one of each."
            % (t["short_exps"], t.get("short_dte_min_eff", cfg.get("short_dte_min", 0)),
               t.get("short_dte_max_eff", cfg.get("short_dte_max", 0)),
               t["long_exps"], t.get("long_dte_min_eff", cfg.get("long_dte_min", 0)),
               t.get("long_dte_max_eff", cfg.get("long_dte_max", 0))))
    if where == "short_leg":
        return (
            "No SHORT leg survived: %d quoted strikes -> %d reported OI 100 or "
            "better -> %d in delta %.2f-%.2f."
            % (t["short_rows"], t["short_pass_oi"], t["short_pass_delta"],
               cfg.get("short_delta_min", 0), cfg.get("short_delta_max", 1)))
    if where == "long_leg":
        return (
            "No LONG leg survived for any short candidate: %d quoted back-month "
            "strikes -> %d on the right side of the short strike -> %d reported "
            "OI 100 or better -> %d in delta %.2f-%.2f."
            % (t["long_rows"], t["long_pass_strike"], t["long_pass_oi"],
               t["long_pass_delta"], cfg.get("long_delta_min", 0),
               cfg.get("long_delta_max", 1)))
    if where == "debit_ratio":
        return (
            "%d pair(s) built, all rejected on cost: net debit exceeded %.0f%% "
            "of the strike width."
            % (t["dropped_debit_ratio"], 100 * cfg.get("max_debit_pct", 1.0)))
    if where == "net_debit":
        return ("%d pair(s) built, all rejected: the long leg priced at or below "
                "the short, which is not a diagonal."
                % t["dropped_net_debit"])
    return "No diagonal combinations found matching risk criteria."


def _chain_num(x):
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


def evaluate_diagonal(ticker: str, config: dict = None, allow_empty: bool = False) -> tuple:
    """
    Best CALL diagonal combinations for the live/manual path. Delegates
    selection to the validated evaluate_diagonals core (BS-delta from yfinance
    IV, two-expiry pairing, corrected gates) and reshapes its flat output into
    the nested shape display_diagonal / _confirm_diagonal consume.
    Returns (spot, diagonals_list).
    """
    import yfinance as yf
    from helm.cli.open_cmd import evaluate_diagonals, STRATEGY_CONFIG
    cfg = {**DIAGONAL_CONFIG, **(config or {})}
    strategy = cfg.get("strategy", "DIAGONAL")
    side = str(cfg.get("option_type", "CALL")).upper()
    core_cfg = STRATEGY_CONFIG.get(strategy, cfg)
    console.print(f"  [dim]Fetching options chain for {ticker}...[/dim]")
    tk = yf.Ticker(ticker)
    spot = getattr(tk.fast_info, "last_price", None)
    if not spot:
        h = tk.history(period="5d")
        spot = float(h["Close"].iloc[-1]) if not h.empty else None
    if not spot:
        raise RuntimeError(f"Could not determine spot price for {ticker}.")
    _trace = {}
    flat = evaluate_diagonals(ticker, strategy, core_cfg, side=side, trace=_trace)
    if not flat:
        if allow_empty:
            # W163 (s113): a pinned caller does not need the screen to have
            # found anything -- the pin names its own pair from the chain.
            return spot, []
        raise RuntimeError(explain_diagonal_miss(_trace, core_cfg))
    return spot, [_reshape_diagonal(r, spot) for r in flat]


def display_diagonal(ticker: str, spot: float, diagonals: list, args: list, label: str = "Diagonal Spread"):
    """Display diagonal candidates and handle confirm flow."""
    console.print()
    console.print(f"  [bold]{ticker}[/bold]  {label}  ·  spot [bold cyan]${spot:.2f}[/bold cyan]")
    console.print()

    tbl = Table(box=box.SIMPLE, show_header=True, header_style="bold dim")
    tbl.add_column("#", width=3)
    tbl.add_column("Short leg", style="bold")
    tbl.add_column("Δ", justify="right")
    tbl.add_column("Mid", justify="right")
    tbl.add_column("Long leg")
    tbl.add_column("Δ", justify="right")
    tbl.add_column("Mid", justify="right")
    tbl.add_column("Net debit", justify="right")
    tbl.add_column("Breakeven", justify="right")
    tbl.add_column("Est. credit", justify="right")

    for i, d in enumerate(diagonals, 1):
        s, l = d["short"], d["long"]
        tbl.add_row(
            str(i),
            f"${s['strike']:.0f} {s['expiration'][5:]} ({s['dte']}d)",
            f"{s['delta']:.2f}",
            f"${s['mid']:.2f}",
            f"${l['strike']:.0f} {l['expiration'][5:]} ({l['dte']}d)",
            f"{l['delta']:.2f}",
            f"${l['mid']:.2f}",
            f"[yellow]${d['net_debit']:.2f}[/yellow]",
            f"${d['breakeven']:.2f}",
            f"[green]${d['max_profit_approx']:.0f}[/green]",
        )

    if not diagonals:
        console.print("  [dim]No screened candidates today (pinned caller -- the pin names its own pair).[/dim]")
    console.print(tbl)
    console.print(f"  [dim]Net debit per share = long mid - short credit (x100 per contract)[/dim]")
    console.print(f"  [dim]Breakeven at short expiry = short strike + net debit[/dim]")
    console.print(f"  [dim]Est. credit = short premium collected at expiry (long leg retained)[/dim]")
    console.print()

    if "--confirm" not in args:
        console.print("[dim]Add [bold]--confirm[/bold] to open a position.[/dim]")
        return

    _confirm_diagonal(ticker, spot, diagonals, args)


def _confirm_diagonal(ticker: str, spot: float, diagonals: list, args: list = None):
    """Prompt, confirm fills, and log the two-leg position -- through the
    canonical multi-leg writer (W163). Books position + both legs + entry
    snapshot + OPENED lifecycle atomically, with net_premium, book, entry_dte,
    spread_width, max_loss and origin stamped; the old writer here left all of
    those blank and the position half-invisible (thesis card, audit, P&L).

    Non-interactive callers (PG's log_open) can pin the pair and assert a
    receipt:
      --strike K --expiry YYYY-MM-DD          the SHORT leg
      --long-strike K --long-expiry YYYY-MM-DD  the LONG leg
      --expect-contracts N --expect-net X      refuse on mismatch (W84)
    A pin that matches a screened candidate books it as SELL_SCREEN. A full
    four-flag pin that matches NO candidate is fetched straight from the chain
    (s113 -- W97's bypass for two legs) and booked as MANUAL_PIN with the
    out-of-band entry written into the position's notes; a contract that is
    not on the chain REFUSES rather than substituting (W7).
    """
    args = args or []

    def _argval(flag):
        try:
            k = args.index(flag)
            return args[k + 1]
        except (ValueError, IndexError):
            return None

    pin_ks, pin_es = _argval("--strike"), _argval("--expiry")
    pin_kl, pin_el = _argval("--long-strike"), _argval("--long-expiry")
    exp_c, exp_net = _argval("--expect-contracts"), _argval("--expect-net")

    d = None
    if pin_ks is not None and pin_es is not None:
        # Pin path: name the pair instead of trusting a rank against a chain
        # that has been re-pulled since the trader clicked (W7's reasoning).
        for cand in diagonals:
            s0, l0 = cand["short"], cand["long"]
            if (abs(float(pin_ks) - float(s0["strike"])) < 0.01
                    and str(s0["expiration"])[:10] == str(pin_es)[:10]
                    and (pin_kl is None or abs(float(pin_kl) - float(l0["strike"])) < 0.01)
                    and (pin_el is None or str(l0["expiration"])[:10] == str(pin_el)[:10])):
                d = cand
                break
        if d is None and (pin_kl is None or pin_el is None):
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

    if d is None:
        choice = Prompt.ask(
            "  Select diagonal",
            default="1",
            choices=[str(i + 1) for i in range(len(diagonals))] + ["n"],
            show_choices=False,
        )
        if choice.lower() == "n":
            console.print("[dim]  No position opened.[/dim]")
            return
        d = diagonals[int(choice) - 1]

    s, l = d["short"], d["long"]

    raw = Prompt.ask("  Contracts", default="1")
    try:
        contracts = max(1, int(raw))
    except ValueError:
        contracts = 1

    short_fill = float(Prompt.ask(f"  Short fill price (mid ${s['mid']:.2f})", default=str(s["mid"])))
    long_fill  = float(Prompt.ask(f"  Long fill price  (mid ${l['mid']:.2f})", default=str(l["mid"])))
    net_debit_actual = round((long_fill - short_fill) * contracts * 100, 2)

    # Receipt checks (W84 / HELM-152): refuse to write anything that does not
    # match what the calling form said it was logging. A blank line landing on
    # a prompt looks exactly like a deliberate default -- this is the guard.
    if exp_c is not None and int(exp_c) != contracts:
        console.print(f"[red]  Receipt check failed:[/red] form said {exp_c} "
                      f"contract(s), prompts produced {contracts}. Nothing was booked.")
        return
    if exp_net is not None and abs(float(exp_net) - (long_fill - short_fill)) > 0.005:
        console.print(f"[red]  Receipt check failed:[/red] form said net debit "
                      f"{float(exp_net):.2f}/share, prompts produced "
                      f"{long_fill - short_fill:.2f}. Nothing was booked.")
        return

    console.print()
    console.print(f"  SELL {contracts}x  ${s['strike']:.0f} CALL  {s['expiration']}  @ ${short_fill:.2f}")
    console.print(f"  BUY  {contracts}x  ${l['strike']:.0f} CALL  {l['expiration']}  @ ${long_fill:.2f}")
    console.print(f"  Net debit: [yellow]${net_debit_actual:.2f}[/yellow]")
    console.print()

    if not Confirm.ask("  Confirm and log?", default=True):
        console.print("[dim]  Cancelled.[/dim]")
        return

    from helm.cli.entry_snapshot import open_multileg_with_snapshot

    legs = [
        dict(direction="SHORT", opt_type="CALL", strike=float(s["strike"]),
             expiration=str(s["expiration"])[:10], fill_price=short_fill,
             delta=s.get("delta"), iv=s.get("iv"), dte=s.get("dte"), spot=spot),
        dict(direction="LONG", opt_type="CALL", strike=float(l["strike"]),
             expiration=str(l["expiration"])[:10], fill_price=long_fill,
             delta=l.get("delta"), iv=l.get("iv"), dte=l.get("dte"), spot=spot),
    ]
    pf = {
        "entry_dte": s.get("dte"),                     # the short-leg clock
        # W97's rule: a pinned-from-chain booking is not a screen
        # recommendation; W19 must not credit the screen with it.
        "origin_screen": "MANUAL_PIN" if d.get("pinned_out_of_band") else "SELL_SCREEN",
        "spread_width": round(abs(float(s["strike"]) - float(l["strike"])), 2),
        "max_loss": abs(net_debit_actual),             # a debit diagonal risks its debit
    }
    pos_id, leg_ids, _snaps = open_multileg_with_snapshot(
        ticker=ticker, strategy="DIAGONAL", legs=legs, contracts=contracts,
        spot=spot, book="REAL", position_fields=pf, pricing_source="manual-fill",
        notes=f"Diagonal: short ${s['strike']:.0f} {s['expiration']} / "
              f"long ${l['strike']:.0f} {l['expiration']} -- fills confirmed at entry"
              + (" -- PINNED OUT OF BAND (s113/W163): not a screened candidate; "
                 f"short delta {s.get('delta')} {s.get('dte')}d, long delta {l.get('delta')} "
                 f"{l.get('dte')}d at entry" if d.get("pinned_out_of_band") else ""))

    console.print()
    console.print(f"  [green]OK[/green]  {ticker} DIAGONAL logged — {pos_id}")
    console.print(f"  [dim]{len(leg_ids)} legs + entry snapshot written; net debit ${net_debit_actual:.2f} recorded as the position premium.[/dim]")
    console.print()


# -- Put Diagonal Config -------------------------------------------------
DIAGONAL_PUT_CONFIG = {
    "strategy":          "DIAGONAL_PUT",
    "option_type":       "PUT",
    "label":             "Diagonal Spread (Put)",
    "is_diagonal":       True,
    "short_dte_min":     21,
    "short_dte_max":     45,
    "short_dte_sweet":   30,
    "short_delta_min":   0.30,
    "short_delta_max":   0.55,
    "short_delta_sweet": (0.38, 0.45),
    "long_dte_min":      60,
    "long_dte_max":      120,
    "long_dte_sweet":    75,
    "long_delta_min":    0.55,
    "long_delta_max":    0.85,
    "long_delta_sweet":  (0.65, 0.75),
    # W147 (s108): third dead copy, same as DIAGONAL and PMCC above.
    # evaluate_diagonal_put also passes core_cfg, so STRATEGY_CONFIG's 1.0 ran
    # and this 0.75 never gated a thing. Found by a readback that expected zero
    # occurrences and got one -- two of these were visible, the third was not.
}


def _fetch_puts(tk, exp):
    try:
        chain = tk.option_chain(exp)
        df = chain.puts
        df = df[df['bid'] > 0].copy()
        df['mid'] = (df['bid'] + df['ask']) / 2
        df['spread_pct'] = (df['ask'] - df['bid']) / df['mid'].clip(lower=0.01)
        return df
    except Exception:
        return None


def evaluate_diagonal_put(ticker, config=None):
    """
    Best PUT diagonal combinations for the live/manual path. Delegates to
    evaluate_diagonals(side="PUT") and reshapes (see evaluate_diagonal).
    Returns (spot, diagonals_list).
    """
    import yfinance as yf
    from helm.cli.open_cmd import evaluate_diagonals, STRATEGY_CONFIG
    cfg = {**DIAGONAL_PUT_CONFIG, **(config or {})}
    strategy = cfg.get("strategy", "DIAGONAL_PUT")
    side = str(cfg.get("option_type", "PUT")).upper()
    core_cfg = STRATEGY_CONFIG.get(strategy, cfg)
    console.print(f"  [dim]Fetching put chain for {ticker}...[/dim]")
    tk = yf.Ticker(ticker)
    spot = getattr(tk.fast_info, "last_price", None)
    if not spot:
        h = tk.history(period="5d")
        spot = float(h["Close"].iloc[-1]) if not h.empty else None
    if not spot:
        raise RuntimeError(f"Could not determine spot price for {ticker}.")
    flat = evaluate_diagonals(ticker, strategy, core_cfg, side=side)
    if not flat:
        raise RuntimeError("No put diagonal combinations found matching risk criteria.")
    return spot, [_reshape_diagonal(r, spot) for r in flat]


def display_diagonal_put(ticker, spot, diagonals, args):
    """Display put diagonal candidates and handle confirm flow."""
    console.print()
    console.print(f'  [bold]{ticker}[/bold]  Put Diagonal  ·  spot [bold cyan]${spot:.2f}[/bold cyan]')
    console.print()
    tbl = Table(box=box.SIMPLE, show_header=True, header_style='bold dim')
    tbl.add_column('#', width=3)
    tbl.add_column('Short put', style='bold')
    tbl.add_column('Delta', justify='right')
    tbl.add_column('Mid', justify='right')
    tbl.add_column('Long put')
    tbl.add_column('Delta', justify='right')
    tbl.add_column('Mid', justify='right')
    tbl.add_column('Net debit', justify='right')
    tbl.add_column('Breakeven', justify='right')
    tbl.add_column('Est. credit', justify='right')
    for i, d in enumerate(diagonals, 1):
        s, l = d['short'], d['long']
        tbl.add_row(
            str(i),
            f"${s['strike']:.0f} {s['expiration'][5:]} ({s['dte']}d)",
            f"{s['delta']:.2f}",
            f"${s['mid']:.2f}",
            f"${l['strike']:.0f} {l['expiration'][5:]} ({l['dte']}d)",
            f"{l['delta']:.2f}",
            f"${l['mid']:.2f}",
            f'[yellow]${d["net_debit"]:.2f}[/yellow]',
            f'${d["breakeven"]:.2f}',
            f'[green]${d["max_profit_approx"]:.0f}[/green]',
        )
    console.print(tbl)
    console.print('  [dim]Net debit = long mid - short credit (x100 per contract)[/dim]')
    console.print('  [dim]Breakeven at short expiry = short strike - net debit[/dim]')
    console.print()
    if '--confirm' not in args:
        console.print('[dim]Add [bold]--confirm[/bold] to open a position.[/dim]')
        return
    _confirm_diagonal_put(ticker, spot, diagonals)


def _confirm_diagonal_put(ticker, spot, diagonals):
    """Prompt, confirm fills, and log the two-leg put diagonal position."""
    choice = Prompt.ask('  Select diagonal', default='1',
        choices=[str(i+1) for i in range(len(diagonals))] + ['n'], show_choices=False)
    if choice.lower() == 'n':
        console.print('[dim]  No position opened.[/dim]')
        return
    d = diagonals[int(choice) - 1]
    s, l = d['short'], d['long']
    raw = Prompt.ask('  Contracts', default='1')
    try: contracts = max(1, int(raw))
    except ValueError: contracts = 1
    short_fill = float(Prompt.ask(f"  Short fill price (mid ${s['mid']:.2f})", default=str(s['mid'])))
    long_fill  = float(Prompt.ask(f"  Long fill price  (mid ${l['mid']:.2f})", default=str(l['mid'])))
    net_debit_actual = round((long_fill - short_fill) * contracts * 100, 2)
    console.print()
    console.print(f"  SELL {contracts}x  ${s['strike']:.0f} PUT  {s['expiration']}  @ ${short_fill:.2f}")
    console.print(f"  BUY  {contracts}x  ${l['strike']:.0f} PUT  {l['expiration']}  @ ${long_fill:.2f}")
    console.print(f'  Net debit: [yellow]${net_debit_actual:.2f}[/yellow]')
    console.print()
    if not Confirm.ask('  Confirm and log?', default=True):
        console.print('[dim]  Cancelled.[/dim]')
        return
    from helm.models.position import Position
    from helm.models.leg import Leg
    from helm.config import get_active_account
    from helm.db import get_conn
    acct = get_active_account()
    conn = get_conn()
    row = conn.execute('SELECT id FROM accounts WHERE name = ?', (acct,)).fetchone()
    if not row: console.print('[red]No active account.[/red]'); return
    account_id = row[0]
    pos = Position.create(
        account_id=account_id, ticker=ticker, strategy='DIAGONAL_PUT',
        status='OPEN', total_contracts=contracts,
        notes=f"Put diagonal: short ${s['strike']:.0f} {s['expiration']} / long ${l['strike']:.0f} {l['expiration']}",
    )
    Leg.create(position_id=pos.id, leg_role='SHORT_PUT', direction='SHORT',
        open_price=short_fill, option_type='PUT',
        strike=s['strike'], expiration=s['expiration'], contracts=contracts, multiplier=100)
    Leg.create(position_id=pos.id, leg_role='LONG_PUT', direction='LONG',
        open_price=long_fill, option_type='PUT',
        strike=l['strike'], expiration=l['expiration'], contracts=contracts, multiplier=100)
    console.print()
    console.print(f'  [green]OK[/green]  {ticker} DIAGONAL_PUT logged — {pos.id}')
    console.print('  [dim]Execute in Fidelity, then run [bold]helm activity[/bold] to confirm.[/dim]')
    console.print()
