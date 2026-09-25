"""helm resell -- W180 step 6: sell the next short against a diagonal's long.

    helm resell TICKER --strike K --expiry YYYY-MM-DD --price P
                [--position-id ID] [--contracts N] [--yes]
    helm resell TICKER --strike K --expiry YYYY-MM-DD --dry-run
    helm resell TICKER --candidates [--top N] [--json] [--position-id ID]

A diagonal is a long call with a SEQUENCE of shorts sold against it (Russ,
2026-09-21). Step 3 closes a short and keeps the position (`helm close --leg`);
this is the other half: it ADDS a short leg to the same position, so the rent
history, the effective basis and the flags all stay on one record. Booking a
re-sell with `helm open` instead creates a SECOND position (W184) -- that is
the thing this command exists to prevent.

REAL BOOK ONLY. Russ sells at the broker; this records the fill he typed. It
decides nothing (HELM-193).

THE RE-SELL PANEL IS A DISPLAY, NOT A GATE (Russ, 2026-09-22, W180 §8.3).
Before confirming it shows the long's DTE against the 45-day runway line, the
candidate's DTE and delta against the diagonal screen's short-leg bands
(STRATEGY_CONFIG -- the single home, W147), whether the short expires before
the long reaches its own 21-DTE rule, the strike relation, IV now against at
entry, the rent so far, and every exit flag open on the position. Nothing on it
refuses. The refusals below are structural, not judgments:

  * the position is not a REAL diagonal-family position, or is not OPEN
  * a short is already on (one short against one long at a time -- harvest
    or close it first; posview.open_short_leg assumes one)
  * the short would expire on or after the long, or is already expired
  * more contracts than the long covers (that is a naked short)
  * the contract is not on the chain (chain_contract refuses; never substitutes)
  * a ticker with two open REAL diagonals and no --position-id (never guess)

WHAT IS WRITTEN, in one transaction: one `legs` row (SHORT_CALL / SHORT_PUT,
OPEN, open_price = the fill, entry_delta from the chain) and one
`lifecycle_events` row, ADJUSTED, narrative "LEG_ADDED | ..." carrying the fill
and the panel's readings -- ADJUSTED because event_type is CHECK-constrained
and widening it is a table rebuild (W155), the same reason step 3 writes
"LEG_CLOSED" under ADJUSTED. NO entry_snapshots row: that table is UNIQUE per
position (measured 2026-09-24; the spec assumed otherwise), so the re-sell's
entry context lives in the narrative and legs.entry_delta. positions is not
touched: net_premium stays the original structure's debit, for the record.

Then exit_flags.settle_legs() marks the position's open `bare` flag ACTED,
'inferred-resell'.
"""
import sys
from datetime import date, datetime, timedelta

from rich.console import Console
from rich.prompt import Confirm

console = Console()

DIAG_FAMILY = ("DIAGONAL", "DIAGONAL_PUT", "PMCC")
RUNWAY_DTE = 45          # W180 §4.6: a 21-45 DTE short expires before the long's 21-DTE rule
LONG_DTE_SOFT = 21


def _arg(args, name):
    if name in args:
        i = args.index(name)
        if i + 1 < len(args):
            return args[i + 1].strip()
    return None


def _d(s):
    return datetime.strptime(str(s)[:10], "%Y-%m-%d").date()


def _legdicts(legs):
    return [dict(vars(l)) for l in legs]


def resolve(ticker, position_id=None):
    """(position, None) or (None, refusal). Never prompts."""
    from helm.models.position import Position
    ps = [p for p in Position.by_ticker(ticker, status="OPEN")
          if getattr(p, "book", None) == "REAL"
          and (p.strategy or "").upper() in DIAG_FAMILY]
    if position_id:
        m = [p for p in ps if p.id == position_id]
        if not m:
            return None, f"No open REAL diagonal {position_id} for {ticker}."
        return m[0], None
    if not ps:
        return None, f"No open REAL diagonal for {ticker}."
    if len(ps) > 1:
        return None, (f"{ticker} has {len(ps)} open REAL diagonals -- name one with "
                      "--position-id: " + ", ".join(p.id for p in ps))
    return ps[0], None


def panel(pos, legs, quote, long_quote, strike, expiry, today=None):
    """The §4.6 readings, as (label, value, note) rows plus the narrative
    fragment. Pure given its inputs. Refuses nothing."""
    from helm import posview as PV
    from helm.cli.open_cmd import STRATEGY_CONFIG
    today = today or date.today()
    ld = _legdicts(legs)
    lg = PV.open_long_leg(ld)
    cfg = STRATEGY_CONFIG.get((pos.strategy or "").upper(), STRATEGY_CONFIG["DIAGONAL"])
    long_dte = (_d(lg["expiration"]) - today).days
    short_dte = (_d(expiry) - today).days
    last_ok = _d(lg["expiration"]) - timedelta(days=LONG_DTE_SOFT)
    dmin, dmax = cfg["short_dte_min"], cfg["short_dte_max"]
    delta = quote.get("delta")
    kmin, kmax = cfg["short_delta_min"], cfg["short_delta_max"]
    call = (lg.get("option_type") or "CALL").upper() == "CALL"
    above = (float(strike) >= float(lg["strike"])) if call else (float(strike) <= float(lg["strike"]))
    rows = [
        ("long", f"${lg['strike']:g} {str(lg['expiration'])[:10]} · {long_dte} DTE",
         "at or above the 45-day runway line" if long_dte >= RUNWAY_DTE
         else f"BELOW the {RUNWAY_DTE}-day runway line"),
        ("short DTE", f"{short_dte}",
         f"inside the screen's {dmin}–{dmax}" if dmin <= short_dte <= dmax
         else f"outside the screen's {dmin}–{dmax}"),
        ("short delta", "—" if delta is None else f"{abs(delta):.2f}",
         "not computed (no IV on the chain)" if delta is None
         else (f"inside {kmin:.2f}–{kmax:.2f}" if kmin <= abs(delta) <= kmax
               else f"outside {kmin:.2f}–{kmax:.2f}")),
        ("expires vs long's 21-DTE", f"{str(expiry)[:10]} vs {last_ok.isoformat()}",
         "before the long reaches its 21-DTE rule" if _d(expiry) <= last_ok
         else "AFTER the long reaches its 21-DTE rule"),
        ("strike vs long", f"${float(strike):g} vs ${lg['strike']:g}",
         ("at or beyond the long's strike" if above
          else "INSIDE the long's strike -- assignment costs more than the long pays")),
        ("IV now", "—" if quote.get("iv") is None else f"{quote['iv']:.1f}%",
         "chain IV of this contract; momentum is not evaluated here"),
    ]
    if long_quote is not None and long_quote.get("mid") is not None and lg.get("open_price"):
        own = (long_quote["mid"] - lg["open_price"]) / lg["open_price"] * 100
        rows.append(("long's own move", f"{own:+.0f}% of its {lg['open_price']:.2f} debit",
                     "v3 measures this, never effective basis (Russ, 09-23)"))
    rows.append(("rent so far", f"${PV.rent_collected(ld):,.0f} over "
                 f"{PV.shorts_sold(ld)} short(s)",
                 f"effective basis ${PV.effective_basis(ld):,.0f}"
                 if PV.effective_basis(ld) is not None else ""))
    frag = (f"long_dte={long_dte} short_dte={short_dte} "
            f"delta={'NA' if delta is None else round(abs(delta), 3)} "
            f"iv={quote.get('iv')} before_long21={'Y' if _d(expiry) <= last_ok else 'N'} "
            f"beyond_long_k={'Y' if above else 'N'}")
    return rows, frag


def structural_refusal(pos, legs, strike, expiry, contracts, price, today=None):
    """None, or why the re-sell cannot be recorded. Structure only -- never a
    judgment about the trade (those are on the panel)."""
    from helm import posview as PV
    today = today or date.today()
    ld = _legdicts(legs)
    st = PV.diagonal_state(ld)
    if st == "SHORT ON":
        s = PV.open_short_leg(ld)
        return (f"a short is already on ({s['leg_role']} ${s['strike']:g} "
                f"{str(s['expiration'])[:10]}). Harvest or close it first: "
                f"helm close {pos.ticker} --leg {s['id']} --price P")
    if st == "CLOSED":
        return "no open long leg -- the position has nothing to sell against"
    lg = PV.open_long_leg(ld)
    try:
        e = _d(expiry)
    except (TypeError, ValueError):
        return f"--expiry {expiry!r} is not YYYY-MM-DD"
    if e < today:
        return f"{expiry} has already expired"
    if e >= _d(lg["expiration"]):
        return (f"the short would expire on or after the long ({str(lg['expiration'])[:10]}) "
                "-- that is not a diagonal")
    try:
        k = float(strike)
    except (TypeError, ValueError):
        return f"--strike {strike!r} is not a number"
    if k <= 0:
        return "--strike must be positive"
    if contracts is None or contracts < 1:
        return "--contracts must be at least 1"
    if contracts > int(lg["contracts"] or 0):
        return (f"{contracts} contracts against a {lg['contracts']}-lot long "
                "is a naked short")
    if price is not None and price < 0:
        return "--price must be >= 0"
    return None


def record(pos, legs, strike, expiry, contracts, price, quote, frag, spot=None):
    """Write the leg and the event, atomically. Returns the new leg id."""
    from helm import posview as PV
    from helm.db import transaction
    from helm.models.leg import Leg
    from helm.models.lifecycle import LifecycleEvent
    ld = _legdicts(legs)
    lg = PV.open_long_leg(ld)
    otype = (lg.get("option_type") or "CALL").upper()
    role = "SHORT_" + otype
    n = PV.shorts_sold(ld) + 1
    taken = {l["id"] for l in ld}
    lid = Leg.new_id(pos.id, role)
    while lid in taken:                      # Leg.save is INSERT OR REPLACE:
        lid = Leg.new_id(pos.id, role)       # a 4-hex collision would overwrite
    now = datetime.now().isoformat()
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM legs WHERE id=?", (lid,)).fetchone():
            raise RuntimeError(f"leg id {lid} already exists -- nothing written")
        Leg.create(position_id=pos.id, leg_role=role, direction="SHORT",
                   open_price=float(price), open_date=date.today().isoformat(),
                   id=lid, option_type=otype, strike=float(strike),
                   expiration=str(expiry)[:10], contracts=int(contracts),
                   entry_delta=quote.get("delta"),
                   notes=f"W180 step 6: short #{n} sold against {lg['id']}",
                   conn=conn)
        LifecycleEvent.record(
            pos.id, "ADJUSTED", occurred_at=now, leg_id=lid,
            option_price=float(price), contracts=int(contracts), spot_price=spot,
            narrative=(f"LEG_ADDED | {role} SHORT ${float(strike):g} {str(expiry)[:10]} | "
                       f"fill {float(price):.2f} x{int(contracts)} | short #{n} against "
                       f"{lg['id']} | chain mid {quote.get('mid')} ({quote.get('mid_source')}) | "
                       f"{frag} | W180"),
            conn=conn)
    return lid, n


# Liquidity marks -- LABELS, never a cut (2026-09-25, below). Both numbers are
# the ones HELM already uses elsewhere, named here so the table can say which.
SPREAD_CUT_PCT = 25.0     # open_cmd's IBKR fetcher: spread_threshold default 0.25
DIAG_SHORT_MIN_OI = 100   # open_cmd.evaluate_diagonals: a short under 100 OI is dropped


def spread_pct(bid, ask):
    """(ask - bid) / mid, in percent -- open_cmd's definition. None without a
    two-sided quote."""
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError):
        return None
    if b <= 0 or a <= 0:
        return None
    m = (a + b) / 2
    return round((a - b) / m * 100, 1) if m > 0 else None


def rank_candidates(cands, cfg, contracts):
    """Order and label chain_candidates' output. Pure. DISPLAY ONLY.

    Order: inside BOTH of the screen's short-leg bands (DTE and delta) first,
    then inside the delta band, then inside the DTE band, then the rest; within
    each, RENT AT THE BID per day (bid x 100 x contracts / DTE), highest first.

    WHY THE BID (Russ, 2026-09-25). Ranked on the mid, this was the one place in
    HELM where the spread did not count: on AA a 64%-wide candidate ranked
    seventh on mid and falls below third on bid. The bid is what a seller is
    sure of; the mid is what a patient limit order might get. Both are shown.

    WHY NO 25% CUT. Measured on every diagonal short HELM has journaled: median
    spread 7.8% above a $2 mid, 24% at $0.25-0.50, 50% under $0.25 -- the dollar
    width barely moves, the percentage balloons as the option gets cheap. A
    re-sell against an UNDERWATER long is cheap by construction (never inside the
    long's strike), so a 25% cut would empty the table on exactly the positions
    that need it. It is also not the diagonal screen's rule: evaluate_diagonals
    scores spread, it does not cut it. So: shown, coloured, labelled past 25%
    and under the screen's 100-OI floor -- and paid for in the rank, via the bid.
    Rent per day, not rent, so a longer short does not win by being longer."""
    dmin, dmax = cfg["short_dte_min"], cfg["short_dte_max"]
    kmin, kmax = cfg["short_delta_min"], cfg["short_delta_max"]
    out = []
    for c in cands:
        d = c.get("delta")
        in_d = d is not None and kmin <= abs(d) <= kmax
        in_t = dmin <= c["dte"] <= dmax
        rent = round(c["mid"] * 100 * contracts, 2)
        bid = float(c.get("bid") or 0)
        rent_bid = round(bid * 100 * contracts, 2)
        sp = spread_pct(c.get("bid"), c.get("ask"))
        oi = int(c.get("oi") or 0)
        out.append(dict(c, in_delta=in_d, in_dte=in_t, rent=rent,
                        rent_per_day=round(rent / c["dte"], 2) if c["dte"] else None,
                        rent_bid=rent_bid,
                        rent_bid_per_day=round(rent_bid / c["dte"], 2) if c["dte"] else None,
                        spread_pct=sp,
                        width=round(float(c.get("ask") or 0) - bid, 2),
                        wide=(sp is None or sp > SPREAD_CUT_PCT),
                        thin_oi=oi < DIAG_SHORT_MIN_OI))
    out.sort(key=lambda c: (not (c["in_delta"] and c["in_dte"]), not c["in_delta"],
                            not c["in_dte"], -(c["rent_bid_per_day"] or 0)))
    return out


def candidates(ticker, pid=None, top=8, as_json=False):
    """`helm resell TICKER --candidates` -- what could be sold against the
    long, never below its strike (Russ, 2026-09-24). Writes nothing."""
    import json as _json
    from helm.models.leg import Leg
    from helm import posview as PV
    from helm.cli.open_cmd import STRATEGY_CONFIG
    from helm.cli import diagonal as DG
    pos, err = resolve(ticker, pid)
    if err:
        print(_json.dumps({"ok": False, "error": err})) if as_json else \
            console.print(f"\n[red]{err}[/red]\n")
        return
    legs = Leg.for_position(pos.id)
    ld = _legdicts(legs)
    lg = PV.open_long_leg(ld)
    if lg is None:
        msg = "no open long leg"
        print(_json.dumps({"ok": False, "error": msg})) if as_json else console.print(f"[red]{msg}[/red]")
        return
    cfg = STRATEGY_CONFIG.get((pos.strategy or "").upper(), STRATEGY_CONFIG["DIAGONAL"])
    contracts = int(lg.get("contracts") or 1)
    side = (lg.get("option_type") or "CALL").upper()
    try:
        spot, cands, stats = DG.chain_candidates(ticker, lg["strike"], lg["expiration"],
                                                 option_type=side, long_dte_soft=LONG_DTE_SOFT)
    except Exception as e:
        msg = f"chain unavailable: {e}"
        print(_json.dumps({"ok": False, "error": msg})) if as_json else console.print(f"\n[red]{msg}[/red]\n")
        return
    ranked = rank_candidates(cands, cfg, contracts)
    state = PV.diagonal_state(ld)
    best_d = max((abs(c["delta"]) for c in ranked if c.get("delta") is not None), default=None)
    note = None
    if ranked and (best_d is None or best_d < cfg["short_delta_min"]):
        note = (f"No strike {'at or above' if side == 'CALL' else 'at or below'} the long's "
                f"${lg['strike']:g} reaches the screen's {cfg['short_delta_min']:.2f} delta -- "
                f"the highest here is {best_d:.2f}. That is the cost of never selling inside "
                "the long's strike on a long that is out of the money.") if best_d is not None else None
    if as_json:
        print(_json.dumps({"ok": True, "ticker": ticker, "position_id": pos.id, "state": state,
                           "spot": spot, "long": {"strike": lg["strike"], "expiration": str(lg["expiration"])[:10],
                                                  "contracts": contracts},
                           "bands": {"dte": [cfg["short_dte_min"], cfg["short_dte_max"]],
                                     "delta": [cfg["short_delta_min"], cfg["short_delta_max"]]},
                           "marks": {"spread_cut_pct": SPREAD_CUT_PCT,
                                     "min_oi": DIAG_SHORT_MIN_OI},
                           "stats": stats, "note": note, "candidates": ranked[:top]}, default=str))
        return
    console.print()
    console.print(f"[bold]{ticker}[/bold] re-sell candidates · {pos.id} · {state} · spot {spot:.2f}")
    console.print(f"  long ${lg['strike']:g} {str(lg['expiration'])[:10]} x{contracts} · "
                  f"strikes {'>=' if side == 'CALL' else '<='} ${lg['strike']:g} only · "
                  f"expiring by {stats['last_expiry_allowed']} (the long's 21-DTE date) · "
                  "[dim]shown, never enforced[/dim]")
    if state == "SHORT ON":
        console.print("  [yellow]A short is already on -- these are for AFTER it is bought back.[/yellow]")
    if not ranked:
        console.print(f"  [yellow]No candidates.[/yellow] [dim]{stats['expiries_in_window']} expiries in "
                      f"window; {stats['inside_long_strike']} strikes inside the long's; "
                      f"{stats['worthless']} at or under $0.05.[/dim]\n")
        return
    console.print(f"  {'#':>2}  {'expiry':<10} {'DTE':>4} {'strike':>7} {'bid':>5} {'ask':>5} "
                  f"{'spread':>7} {'delta':>5} {'IV':>6} {'OI':>5} {'@bid':>7} {'/day':>6} "
                  f"{'@mid':>7} {'/day':>6}  bands")
    for i, c in enumerate(ranked[:top], 1):
        bands = ("DTE+delta" if c["in_dte"] and c["in_delta"] else
                 "delta" if c["in_delta"] else "DTE" if c["in_dte"] else "outside")
        dl = "—" if c.get("delta") is None else f"{abs(c['delta']):.2f}"
        sp = c.get("spread_pct")
        spc = ("dim" if sp is None else "green" if sp <= 10 else "yellow" if sp <= 15 else "red")
        sps = "—" if sp is None else f"{sp:.0f}%"
        tags = " ".join(t for t, on in ((f">{SPREAD_CUT_PCT:.0f}%", c["wide"]),
                                        (f"OI<{DIAG_SHORT_MIN_OI}", c["thin_oi"])) if on)
        console.print(f"  {i:>2}  {c['expiration']:<10} {c['dte']:>4} {c['strike']:>7g} "
                      f"{c['bid']:>5.2f} {c['ask']:>5.2f} [{spc}]{sps:>7}[/{spc}] {dl:>5} "
                      f"{c['iv']:>5.1f}% {c.get('oi') or 0:>5} ${c['rent_bid']:>6,.0f} "
                      f"${(c['rent_bid_per_day'] or 0):>5,.2f} ${c['rent']:>6,.0f} "
                      f"${(c['rent_per_day'] or 0):>5,.2f}  [dim]{bands}[/dim]"
                      + (f" [yellow]{tags}[/yellow]" if tags else ""))
    console.print(f"  [dim]Ranked on rent at the BID (what a seller is sure of); mid shown for a "
                  f"patient limit. Spread coloured as HELM colours it everywhere (<=10 green, "
                  f"<=15 yellow); >{SPREAD_CUT_PCT:.0f}% = past the open side's cut, OI<"
                  f"{DIAG_SHORT_MIN_OI} = under the diagonal screen's floor. Marked, not "
                  f"hidden.[/dim]")
    if note:
        console.print(f"  [dim]{note}[/dim]")
    c0 = ranked[0]
    console.print(f"\n  [dim]Next: helm resell {ticker} --strike {c0['strike']:g} --expiry "
                  f"{c0['expiration']} --dry-run   (then --price <your fill>)[/dim]\n")


def run():
    args = sys.argv[1:]
    if not args or args[0] in ("-h", "--help"):
        console.print(__doc__)
        return
    ticker = args[0].upper() if not args[0].startswith("-") else None
    if not ticker:
        console.print("[red]Specify a ticker first: helm resell AA --strike 50 --expiry 2026-11-20 --price 0.85[/red]")
        return
    if "--candidates" in args:
        try:
            top = int(_arg(args, "--top") or 8)
        except ValueError:
            top = 8
        candidates(ticker, _arg(args, "--position-id"), top=top, as_json="--json" in args)
        return
    strike, expiry = _arg(args, "--strike"), _arg(args, "--expiry")
    pid = _arg(args, "--position-id")
    dry = "--dry-run" in args
    yes = "--yes" in args
    raw_p, raw_c = _arg(args, "--price"), _arg(args, "--contracts")
    if strike is None or expiry is None:
        console.print("[red]--strike and --expiry are both required.[/red]")
        return
    try:
        price = None if raw_p is None else float(raw_p.lstrip("$"))
    except ValueError:
        console.print(f"[red]--price must be a number, got {raw_p!r}.[/red]")
        return
    if price is None and not dry:
        console.print("[red]--price P (your actual fill) is required, or use --dry-run.[/red]")
        return

    from helm.models.leg import Leg
    pos, err = resolve(ticker, pid)
    if err:
        console.print(f"\n[red]Not recorded:[/red] {err}\n")
        return
    legs = Leg.for_position(pos.id)
    from helm import posview as PV
    lg = PV.open_long_leg(_legdicts(legs))
    try:
        contracts = int(raw_c) if raw_c is not None else int((lg or {}).get("contracts") or 0)
    except ValueError:
        console.print(f"[red]--contracts must be a whole number, got {raw_c!r}.[/red]")
        return
    why = structural_refusal(pos, legs, strike, expiry, contracts, price)
    if why:
        console.print(f"\n[red]Not recorded:[/red] {why}\n")
        return

    from helm.cli import diagonal as DG
    try:
        spot, q = DG.chain_contract(ticker, strike, expiry, "short",
                                    option_type=(lg.get("option_type") or "CALL"))
    except Exception as e:      # RuntimeError: not on the chain; anything else:
        console.print(f"\n[red]Not recorded: {e}[/red]\n")   # no chain at all
        return
    try:
        _s, lq = DG.chain_contract(ticker, lg["strike"], lg["expiration"], "long",
                                   spot=spot, option_type=(lg.get("option_type") or "CALL"))
    except Exception:
        lq = None                  # the long's quote only feeds the panel
    rows, frag = panel(pos, legs, q, lq, strike, expiry)

    console.print()
    console.print(f"[bold]{ticker}[/bold] {pos.strategy} · {pos.id} · re-sell panel "
                  "[dim](shown, never enforced -- W180 §4.6)[/dim]")
    for label, val, note in rows:
        console.print(f"  {label:<26} {val:<30} [dim]{note}[/dim]")
    try:
        import sqlite3
        from helm.config import DB_PATH
        c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
        fl = [r[0] for r in c.execute(
            "SELECT kind FROM exit_flags WHERE position_id=? AND disposition IS NULL",
            (pos.id,))]
        c.close()
    except Exception:
        fl = []
    console.print(f"  {'open exit flags':<26} {', '.join(fl) if fl else 'none'}")
    console.print(f"  {'chain mid':<26} {q['mid']:.2f} ({q['mid_source']})")
    console.print()
    if dry:
        console.print("[dim]  --dry-run: nothing written.[/dim]\n")
        return
    console.print(f"  SELL {contracts}x ${float(strike):g} {(lg.get('option_type') or 'CALL').upper()} "
                  f"{str(expiry)[:10]} @ {price:.2f}  -> ${price * contracts * 100:,.0f} collected")
    if not yes and not Confirm.ask("  Record this short against the long?", default=True):
        console.print("[dim]  Cancelled. Nothing written.[/dim]")
        return
    try:
        lid, n = record(pos, legs, strike, expiry, contracts, price, q, frag, spot=spot)
    except Exception as e:
        console.print(f"\n[red]Not recorded:[/red] {e}\n")
        return
    try:
        import sqlite3
        from helm.config import DB_PATH
        from helm import exit_flags as EF
        c = sqlite3.connect(str(DB_PATH))
        EF.settle_legs(c)
        c.close()
    except Exception:
        pass    # the leg is written; a flag settles at the next scan anyway
    console.print()
    console.print(f"  [green]OK[/green]  {ticker} short #{n} SOLD at {price:.2f} -- {lid} "
                  "-- position SHORT ON")
    console.print()
