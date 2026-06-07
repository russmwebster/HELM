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
    # Risk filter
    "max_debit_pct":     0.75,
}

PMCC_CONFIG = {
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
    "max_debit_pct":     0.30,
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


def evaluate_diagonal(ticker: str, config: dict = None) -> tuple:
    """
    Fetch options chain and find best diagonal combinations.
    Returns (spot, diagonals_list).
    """
    import yfinance as yf

    cfg = {**DIAGONAL_CONFIG, **(config or {})}

    console.print(f"  [dim]Fetching options chain for {ticker}...[/dim]")
    tk = yf.Ticker(ticker)
    info = tk.fast_info
    spot = getattr(info, "last_price", None) or getattr(info, "previous_close", 0) or 0

    exps = tk.options
    today = date.today()

    short_exps, long_exps = [], []
    for exp in exps:
        try:
            dte = (datetime.strptime(exp, "%Y-%m-%d").date() - today).days
            if cfg["short_dte_min"] <= dte <= cfg["short_dte_max"]:
                short_exps.append((dte, exp))
            elif cfg["long_dte_min"] <= dte <= cfg["long_dte_max"]:
                long_exps.append((dte, exp))
        except Exception:
            continue

    if not short_exps:
        raise RuntimeError(f"No expirations in {cfg['short_dte_min']}-{cfg['short_dte_max']} DTE for short leg.")
    if not long_exps:
        raise RuntimeError(f"No expirations in {cfg['long_dte_min']}-{cfg['long_dte_max']} DTE for long leg.")

    # Score short leg candidates
    short_candidates = []
    for dte, exp in sorted(short_exps, key=lambda x: abs(x[0] - cfg["short_dte_sweet"])):
        df = _fetch_calls(tk, exp)
        if df is None:
            continue
        for _, row in df.iterrows():
            delta = abs(float(row.get("delta") or 0))
            if not (cfg["short_delta_min"] <= delta <= cfg["short_delta_max"]):
                continue
            sp = float(row["spread_pct"])
            if sp > 0.15:
                continue
            score = _score_delta(delta, cfg["short_delta_sweet"]) * 0.6 + (1 - min(sp, 0.15) / 0.15) * 0.4
            short_candidates.append({
                "expiration": exp, "dte": dte,
                "strike": float(row["strike"]),
                "mid": round(float(row["mid"]), 2),
                "delta": round(delta, 3),
                "iv": round(float(row.get("impliedVolatility") or 0) * 100, 1),
                "oi": int(row.get("openInterest") or 0),
                "spread_pct": round(sp * 100, 1),
                "score": score,
            })

    if not short_candidates:
        raise RuntimeError("No suitable short leg contracts found.")

    short_candidates.sort(key=lambda x: -x["score"])

    # For each top short, find best long leg
    diagonals = []
    for short in short_candidates[:5]:
        best_long, best_score = None, -1
        for dte, exp in sorted(long_exps, key=lambda x: abs(x[0] - cfg["long_dte_sweet"])):
            df = _fetch_calls(tk, exp)
            if df is None:
                continue
            for _, row in df.iterrows():
                delta = abs(float(row.get("delta") or 0))
                if not (cfg["long_delta_min"] <= delta <= cfg["long_delta_max"]):
                    continue
                if float(row["strike"]) > short["strike"]:
                    continue  # long strike must be <= short strike
                sp = float(row["spread_pct"])
                if sp > 0.12:
                    continue
                score = _score_delta(delta, cfg["long_delta_sweet"]) * 0.7 + (1 - min(sp, 0.12) / 0.12) * 0.3
                if score > best_score:
                    best_score = score
                    best_long = {
                        "expiration": exp, "dte": dte,
                        "strike": float(row["strike"]),
                        "mid": round(float(row["mid"]), 2),
                        "delta": round(delta, 3),
                        "iv": round(float(row.get("impliedVolatility") or 0) * 100, 1),
                        "oi": int(row.get("openInterest") or 0),
                        "spread_pct": round(sp * 100, 1),
                    }

        if not best_long:
            continue

        net_debit = round(best_long["mid"] - short["mid"], 2)
        if net_debit <= 0:
            continue

        spread_width = short["strike"] - best_long["strike"]
        if spread_width > 0 and (net_debit / spread_width) > cfg["max_debit_pct"]:
            continue

        breakeven = round(short["strike"] + net_debit, 2)
        max_profit_approx = round(short["mid"] * 100, 2)

        diagonals.append({
            "short": short, "long": best_long,
            "net_debit": net_debit,
            "net_debit_total": round(net_debit * 100, 2),
            "breakeven": breakeven,
            "max_profit_approx": max_profit_approx,
            "spot": spot,
        })

    if not diagonals:
        raise RuntimeError("No diagonal combinations found matching risk criteria.")

    return spot, diagonals


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

    console.print(tbl)
    console.print(f"  [dim]Net debit per share = long mid - short credit (x100 per contract)[/dim]")
    console.print(f"  [dim]Breakeven at short expiry = short strike + net debit[/dim]")
    console.print(f"  [dim]Est. credit = short premium collected at expiry (long leg retained)[/dim]")
    console.print()

    if "--confirm" not in args:
        console.print("[dim]Add [bold]--confirm[/bold] to open a position.[/dim]")
        return

    _confirm_diagonal(ticker, spot, diagonals)


def _confirm_diagonal(ticker: str, spot: float, diagonals: list):
    """Prompt, confirm fills, and log the two-leg position."""
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

    console.print()
    console.print(f"  SELL {contracts}x  ${s['strike']:.0f} CALL  {s['expiration']}  @ ${short_fill:.2f}")
    console.print(f"  BUY  {contracts}x  ${l['strike']:.0f} CALL  {l['expiration']}  @ ${long_fill:.2f}")
    console.print(f"  Net debit: [yellow]${net_debit_actual:.2f}[/yellow]")
    console.print()

    if not Confirm.ask("  Confirm and log?", default=True):
        console.print("[dim]  Cancelled.[/dim]")
        return

    from helm.models.position import Position
    from helm.models.leg import Leg
    from helm.config import get_active_account
    from helm.db import get_conn

    acct = get_active_account()
    conn = get_conn()
    row = conn.execute("SELECT id FROM accounts WHERE name = ?", (acct,)).fetchone()
    if not row:
        console.print("[red]No active account found.[/red]")
        return
    account_id = row[0]

    pos = Position.create(
        account_id=account_id,
        ticker=ticker,
        strategy="DIAGONAL",
        status="OPEN",
        total_contracts=contracts,
        notes=f"Diagonal: short ${s['strike']:.0f} {s['expiration']} / long ${l['strike']:.0f} {l['expiration']}",
    )

    Leg.create(
        position_id=pos.id, leg_role="SHORT_CALL", direction="SHORT",
        open_price=short_fill, option_type="CALL",
        strike=s["strike"], expiration=s["expiration"],
        contracts=contracts, multiplier=100,
    )

    Leg.create(
        position_id=pos.id, leg_role="LONG_CALL", direction="LONG",
        open_price=long_fill, option_type="CALL",
        strike=l["strike"], expiration=l["expiration"],
        contracts=contracts, multiplier=100,
    )

    console.print()
    console.print(f"  [green]OK[/green]  {ticker} DIAGONAL logged — {pos.id}")
    console.print(f"  [dim]Execute in Fidelity, then run [bold]helm activity[/bold] to confirm.[/dim]")
    console.print()


# -- Put Diagonal Config -------------------------------------------------
DIAGONAL_PUT_CONFIG = {
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
    "max_debit_pct":     0.75,
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
    Fetch options chain and find best put diagonal combinations.
    Returns (spot, diagonals_list).
    """
    import yfinance as yf
    cfg = {**DIAGONAL_PUT_CONFIG, **(config or {})}
    console.print(f'  [dim]Fetching put chain for {ticker}...[/dim]')
    tk = yf.Ticker(ticker)
    info = tk.fast_info
    spot = getattr(info, 'last_price', None) or getattr(info, 'previous_close', 0) or 0
    exps = tk.options
    today = date.today()
    short_exps, long_exps = [], []
    for exp in exps:
        try:
            dte = (datetime.strptime(exp, '%Y-%m-%d').date() - today).days
            if cfg['short_dte_min'] <= dte <= cfg['short_dte_max']:
                short_exps.append((dte, exp))
            elif cfg['long_dte_min'] <= dte <= cfg['long_dte_max']:
                long_exps.append((dte, exp))
        except Exception:
            continue
    if not short_exps:
        raise RuntimeError(f"No expirations in {cfg['short_dte_min']}-{cfg['short_dte_max']} DTE for short leg.")
    if not long_exps:
        raise RuntimeError(f"No expirations in {cfg['long_dte_min']}-{cfg['long_dte_max']} DTE for long leg.")
    short_candidates = []
    for dte, exp in sorted(short_exps, key=lambda x: abs(x[0] - cfg['short_dte_sweet'])):
        df = _fetch_puts(tk, exp)
        if df is None: continue
        for _, row in df.iterrows():
            delta = abs(float(row.get('delta') or 0))
            if not (cfg['short_delta_min'] <= delta <= cfg['short_delta_max']): continue
            sp = float(row['spread_pct'])
            if sp > 0.15: continue
            score = _score_delta(delta, cfg['short_delta_sweet']) * 0.6 + (1 - min(sp, 0.15) / 0.15) * 0.4
            short_candidates.append({
                'expiration': exp, 'dte': dte,
                'strike': float(row['strike']),
                'mid': round(float(row['mid']), 2),
                'delta': round(delta, 3),
                'iv': round(float(row.get('impliedVolatility') or 0) * 100, 1),
                'oi': int(row.get('openInterest') or 0),
                'spread_pct': round(sp * 100, 1),
                'score': score,
            })
    if not short_candidates:
        raise RuntimeError('No suitable short put leg contracts found.')
    short_candidates.sort(key=lambda x: -x['score'])
    diagonals = []
    for short in short_candidates[:5]:
        best_long, best_score = None, -1
        for dte, exp in sorted(long_exps, key=lambda x: abs(x[0] - cfg['long_dte_sweet'])):
            df = _fetch_puts(tk, exp)
            if df is None: continue
            for _, row in df.iterrows():
                delta = abs(float(row.get('delta') or 0))
                if not (cfg['long_delta_min'] <= delta <= cfg['long_delta_max']): continue
                # Long put strike must be >= short put strike (higher strike = more expensive put)
                if float(row['strike']) < short['strike']: continue
                sp = float(row['spread_pct'])
                if sp > 0.12: continue
                score = _score_delta(delta, cfg['long_delta_sweet']) * 0.7 + (1 - min(sp, 0.12) / 0.12) * 0.3
                if score > best_score:
                    best_score = score
                    best_long = {
                        'expiration': exp, 'dte': dte,
                        'strike': float(row['strike']),
                        'mid': round(float(row['mid']), 2),
                        'delta': round(delta, 3),
                        'iv': round(float(row.get('impliedVolatility') or 0) * 100, 1),
                        'oi': int(row.get('openInterest') or 0),
                        'spread_pct': round(sp * 100, 1),
                    }
        if not best_long: continue
        net_debit = round(best_long['mid'] - short['mid'], 2)
        if net_debit <= 0: continue
        spread_width = best_long['strike'] - short['strike']
        if spread_width > 0 and (net_debit / spread_width) > cfg['max_debit_pct']: continue
        breakeven = round(short['strike'] - net_debit, 2)
        max_profit_approx = round(short['mid'] * 100, 2)
        diagonals.append({
            'short': short, 'long': best_long,
            'net_debit': net_debit,
            'net_debit_total': round(net_debit * 100, 2),
            'breakeven': breakeven,
            'max_profit_approx': max_profit_approx,
            'spot': spot,
        })
    if not diagonals:
        raise RuntimeError('No put diagonal combinations found matching risk criteria.')
    return spot, diagonals


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
