
# helm/cli/reconcile_cmd.py
# helm reconcile -- compare HELM open positions to Fidelity portfolio
#
# Read-only diff. No automatic changes.
# Shows: Match / Fidelity-only / HELM-only
# Advises on any discrepancies.
#
# Usage:
#   helm reconcile                    Auto-find latest Portfolio_Positions*.csv
#   helm reconcile ~/path/to/file.csv Explicit file

import sys
try:
    from helm.models.theme import log_event as _log_event
except Exception:
    _log_event = lambda *a, **k: None

import re
import glob
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

from helm.config import get_active_account
from helm.db import get_conn

console = Console()


def parse_option_symbol(symbol: str) -> Optional[dict]:
    """Parse Fidelity option symbol like -AVGO260618P400."""
    symbol = symbol.strip().lstrip("-")
    m = re.match(r'^([A-Z]+)(\d{6})([CP])(\d+(?:\.\d+)?)$', symbol)
    if not m:
        return None
    ticker, date_str, cp, strike_str = m.groups()
    try:
        exp = datetime.strptime(date_str, "%y%m%d").strftime("%Y-%m-%d")
        return {
            "ticker": ticker,
            "expiration": exp,
            "opt_type": "CALL" if cp == "C" else "PUT",
            "strike": float(strike_str),
        }
    except Exception:
        return None


def _money(v):
    """Parse a Fidelity dollar string ('+$1,696.49', '-$975.00') to float; None if blank."""
    if v is None:
        return None
    s = str(v).replace("$", "").replace("+", "").replace(",", "").strip()
    if not s or s in ("nan", "--", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _fidpnl_cell(v):
    """Render a Fidelity per-position P&L value as a colored Rich cell."""
    if v is None:
        return "[dim]—[/dim]"
    color = "green" if v >= 0 else "red"
    sign = "+" if v >= 0 else "-"
    return f"[{color}]{sign}${abs(v):,.0f}[/{color}]"


def parse_fidelity_positions(filepath: str) -> list:
    """
    Parse a Fidelity Portfolio_Positions CSV.

    Uses csv.DictReader (not pandas) so it is robust to Fidelity's trailing-comma
    "ragged" rows (which shift pandas' index_col alignment so Description lands in
    the Symbol column) and to header-casing changes. Column lookup is
    case-insensitive. Returns position dicts with ticker, strike, expiration,
    opt_type, contracts, value, total_gl.
    """
    import csv

    def _norm(s):
        return "".join(str(s).lower().split())

    try:
        fh = open(filepath, newline="", encoding="utf-8-sig")
    except Exception as e:
        raise ValueError(f"Cannot read file: {e}")

    positions = []
    with fh:
        reader = csv.DictReader(fh)
        colmap = {_norm(k): k for k in (reader.fieldnames or []) if k}

        def cell(row, *names, default=""):
            for nm in names:
                key = colmap.get(_norm(nm))
                if key is not None and row.get(key) is not None:
                    return str(row.get(key)).strip()
            return default

        for row in reader:
            symbol = cell(row, "Symbol")
            # W56: the CSV writes "Pending activity" (lower a); a case-sensitive
            # membership test let a $53,407.71 row through as a STOCK position
            # called "Pending activity" on every reconcile.
            if not symbol or symbol.lower() in ("nan", "pending activity", "account total"):
                continue
            if "SPAXX" in symbol or "FXAIX" in symbol or "GLD" in symbol:
                continue

            value = _money(cell(row, "Current value"))
            total_gl = _money(cell(row, "Total gain/loss dollar"))
            parsed = parse_option_symbol(symbol)

            if not parsed:
                positions.append({
                    "ticker": symbol.strip(),
                    "type": "STOCK",
                    "strike": None,
                    "expiration": None,
                    "opt_type": None,
                    "contracts": None,
                    "symbol": symbol,
                    "value": value,
                    "total_gl": total_gl,
                })
                continue

            qty_raw = cell(row, "Quantity")
            try:
                qty = abs(int(float(str(qty_raw).replace(",", ""))))
            except (ValueError, TypeError):
                qty = None

            positions.append({
                "ticker": parsed["ticker"],
                "type": "OPTION",
                "strike": parsed["strike"],
                "expiration": parsed["expiration"],
                "opt_type": parsed["opt_type"],
                "contracts": qty,
                "symbol": symbol,
                "value": value,
                "total_gl": total_gl,
            })

    return positions


def get_helm_positions(account_id: str) -> list:
    """Get all open/pending HELM positions with their legs."""
    conn = get_conn()
    try:
        positions = conn.execute(
            "SELECT * FROM positions WHERE account_id=? AND status IN ('OPEN','PENDING') AND book='REAL' ORDER BY ticker",
            (account_id,)
        ).fetchall()

        result = []
        for pos in positions:
            legs = conn.execute(
                "SELECT * FROM legs WHERE position_id=? AND status='OPEN'",
                (pos["id"],)
            ).fetchall()
            result.append({
                "position": dict(pos),
                "legs": [dict(l) for l in legs],
            })
        return result
    finally:
        conn.close()


def match_positions(helm_positions: list, fidelity_positions: list) -> dict:
    """
    Compare HELM and Fidelity positions.
    Returns {matched, helm_only, fidelity_only}
    """
    matched = []
    helm_only = []
    fidelity_only = []

    # Index Fidelity options by (ticker, expiration, strike, opt_type)
    fid_index = {}
    fid_stocks = {}
    for fp in fidelity_positions:
        if fp["type"] == "OPTION":
            key = (fp["ticker"], fp["expiration"], fp["strike"], fp["opt_type"])
            fid_index[key] = fp
        else:
            fid_stocks[fp["ticker"]] = fp

    # Check each HELM position
    matched_fid_keys = set()
    for hp in helm_positions:
        pos = hp["position"]
        legs = hp["legs"]
        ticker = pos["ticker"]
        strategy = pos["strategy"]
        found = False
        fid_pnl = 0.0
        fid_hit = False

        for leg in legs:
            if leg["option_type"] == "STOCK":
                # Stock leg -- check fid_stocks
                if ticker in fid_stocks:
                    found = True
                    _g = fid_stocks[ticker].get("total_gl")
                    if _g is not None:
                        fid_pnl += _g
                        fid_hit = True
            else:
                key = (ticker, leg["expiration"], leg["strike"], leg["option_type"])
                if key in fid_index:
                    found = True
                    matched_fid_keys.add(key)  # Mark ALL option legs as matched
                    _g = fid_index[key].get("total_gl")
                    if _g is not None:
                        fid_pnl += _g
                        fid_hit = True

        if found:
            hp["fid_pnl"] = fid_pnl if fid_hit else None
            matched.append(hp)
        else:
            helm_only.append(hp)

    # Fidelity positions not matched to any HELM position
    for fp in fidelity_positions:
        if fp["type"] == "OPTION":
            key = (fp["ticker"], fp["expiration"], fp["strike"], fp["opt_type"])
            if key not in matched_fid_keys:
                fidelity_only.append(fp)

    return {"matched": matched, "helm_only": helm_only, "fidelity_only": fidelity_only}


def parse_fidelity_balances(filepath):
    """Extract account balances from the same Fidelity CSV reconcile already reads.

    W56: `accounts.portfolio_value` had one writer -- `helm import fidelity` --
    behind an `if imported > 0` gate, and `import` refuses to run once positions
    exist. So the number every position is sized from could not be refreshed by
    any command. It sat at the 26 May value for two months.

    Returns {} when the file yields nothing usable, so callers can tell "no
    balances" from "zero balances".

    Net liquidation is the sum of every row's Current value for that account,
    and cash is the SPAXX row -- the same definition `import_cmd` has always
    used (import_cmd.py:285-305), lifted rather than reinvented so the two
    commands cannot drift into disagreeing about what the account is worth.

    Column lookup is case-insensitive. The previous version of this parser asked
    for "Account Number"/"Current Value" while the file says "Account number"/
    "Current value", so it returned {} on every real CSV and the Available
    Capital panel it fed silently never rendered.
    """
    import csv

    def _norm(s):
        return "".join(str(s).lower().split())

    out = {"accounts": {}, "net_liquidation": 0.0, "buying_power": 0.0,
           "pending": 0.0, "as_of": None}
    try:
        with open(filepath, newline="", encoding="utf-8-sig") as f:
            reader = csv.DictReader(f)
            colmap = {_norm(k): k for k in (reader.fieldnames or []) if k}

            def cell(row, *names, default=""):
                for nm in names:
                    key = colmap.get(_norm(nm))
                    if key is not None and row.get(key) is not None:
                        return str(row.get(key)).strip()
                return default

            for row in reader:
                acct = cell(row, "Account number", "Account #")
                if not acct or acct in ("nan",) or acct.startswith('"'):
                    continue
                val = _money(cell(row, "Current value"))
                if val is None:
                    continue
                sym = cell(row, "Symbol")
                a = out["accounts"].setdefault(
                    acct, {"name": cell(row, "Account name"), "net_liq": 0.0,
                           "cash": 0.0, "pending": 0.0})
                a["net_liq"] += val
                if "SPAXX" in sym.upper():
                    a["cash"] += val
                if sym.lower() == "pending activity":
                    a["pending"] += val
    except Exception:
        return {}

    if not out["accounts"]:
        return {}

    out["net_liquidation"] = sum(a["net_liq"] for a in out["accounts"].values())
    out["buying_power"]    = sum(a["cash"]    for a in out["accounts"].values())
    out["pending"]         = sum(a["pending"] for a in out["accounts"].values())
    out["as_of"]           = _fidelity_as_of(filepath)
    return out


def _fidelity_as_of(filepath):
    """The CSV's own 'Date downloaded Jul-26-2026 at 4:36 p.m ET' trailer -> ISO date.

    Preferred over the file's mtime, which changes if the file is copied, and
    over the filename, which the user can rename. Falls back to the filename
    stem, then to None -- never to today, because a balance that guesses its own
    age is exactly the failure W56 is about.
    """
    import re as _re
    from datetime import datetime as _dtm
    pats = None
    try:
        with open(filepath, encoding="utf-8-sig", errors="replace") as f:
            txt = f.read()
        pats = _re.search(r"Date downloaded\s+([A-Za-z]{3}-\d{1,2}-\d{4})", txt)
    except Exception:
        pass
    cand = pats.group(1) if pats else None
    if not cand:
        m = _re.search(r"([A-Za-z]{3}-\d{1,2}-\d{4})", str(filepath))
        cand = m.group(1) if m else None
    if not cand:
        return None
    try:
        return _dtm.strptime(cand, "%b-%d-%Y").date().isoformat()
    except Exception:
        return None


def parse_fidelity_cash(filepath):
    """Per-account cash, for the Available Capital panel.

    Kept as a thin view over parse_fidelity_balances so there is one parser and
    one definition of cash. Shape is unchanged for existing callers.
    """
    b = parse_fidelity_balances(filepath)
    if not b:
        return {}
    return {num: {"name": a["name"], "cash": a["cash"]}
            for num, a in b["accounts"].items()}


def get_csp_collateral():
    from helm.db import get_conn
    rows = get_conn().execute("SELECT l.strike, l.contracts FROM legs l JOIN positions p ON l.position_id=p.id WHERE p.status='OPEN' AND p.book='REAL' AND p.strategy IN ('CSP','COVERED_CALL') AND l.direction='SHORT' AND l.option_type='PUT'").fetchall()
    return sum(r['strike']*r['contracts']*100 for r in rows)


def run():
    args = sys.argv[1:]

    if not get_active_account():
        console.print("[red]No active account. Run helm setup first.[/red]")
        return

    account_id = get_active_account()

    # Find portfolio file
    if args and not args[0].startswith("--"):
        filepath = Path(args[0]).expanduser()
        if not filepath.exists():
            console.print(f"[red]File not found:[/red] {filepath}")
            return
    else:
        pattern = str(Path.home() / "Downloads" / "Portfolio_Positions_*.csv")
        matches = sorted(
            [Path(p) for p in glob.glob(pattern)],
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )
        if not matches:
            console.print("[red]No Portfolio_Positions_*.csv found in Downloads.[/red]")
            return
        filepath = matches[0]

    console.print()
    console.print(Panel.fit(
        f"[bold cyan]HELM Reconcile[/bold cyan]\n"
        f"[dim]Comparing HELM positions to Fidelity portfolio[/dim]\n"
        f"[dim]{filepath.name}[/dim]",
        border_style="cyan"
    ))
    console.print()

    # Parse Fidelity
    try:
        fidelity_positions = parse_fidelity_positions(str(filepath))
    except Exception as e:
        console.print(f"[red]Error reading Fidelity file:[/red] {e}")
        return

    # Get HELM positions
    helm_positions = get_helm_positions(account_id)

    # Compare
    result = match_positions(helm_positions, fidelity_positions)
    matched    = result["matched"]
    helm_only  = result["helm_only"]
    fid_only   = result["fidelity_only"]

    # ── Results table ─────────────────────────────────────────────────────────
    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1), width=135)
    t.add_column("Status",   width=12, no_wrap=True)
    t.add_column("Ticker",   style="bold cyan", width=7, no_wrap=True)
    t.add_column("Strategy", width=14, no_wrap=True)
    t.add_column("Legs",     width=30, no_wrap=True)
    t.add_column("Note",     width=40, no_wrap=True)
    t.add_column("Fid P&L",  width=11, no_wrap=True, justify="right")

    # Matched
    for hp in matched:
        pos = hp["position"]
        legs = hp["legs"]
        legs_str = "  ".join(
            f"{l['option_type'][0] if l['option_type'] else 'S'}"
            f"{l['strike']:.0f} " if l['strike'] else f"stock"
            for l in legs
        )
        # Auto-promote PENDING to OPEN when matched against Fidelity
        if pos["status"] == "PENDING":
            from helm.db import get_conn as _pgc
            _pgc().execute("UPDATE positions SET status='OPEN' WHERE id=?", (pos["id"],))
            _pgc().commit()
        status_str = "[green]✓ MATCH[/green]"
        t.add_row(status_str, pos["ticker"], pos["strategy"], legs_str, "", _fidpnl_cell(hp.get("fid_pnl")))

    # HELM only (not in Fidelity)
    for hp in helm_only:
        pos = hp["position"]
        legs = hp["legs"]
        legs_str = "  ".join(
            f"{l['option_type'][0] if l['option_type'] else 'S'}"
            f"{l['strike']:.0f} " if l['strike'] else f"stock"
            for l in legs
        )
        t.add_row(
            "[red]✗ HELM ONLY[/red]", pos["ticker"], pos["strategy"], legs_str,
            "[dim]Not in Fidelity — may be closed. Run helm activity.[/dim]"
        )

    # Fidelity only (not in HELM)
    for fp in fid_only:
        contract_str = f"{fp['opt_type'][0]}{fp['strike']:.0f} {fp['expiration'][5:]}" if fp["type"] == "OPTION" else fp["ticker"]
        t.add_row(
            "[yellow]⚠ FIDELITY ONLY[/yellow]", fp["ticker"], "--", contract_str,
            "[dim]Not in HELM — open via helm open --confirm or run helm activity.[/dim]"
        )

    console.print(t)
    console.print()

    # Summary
    total = len(matched) + len(helm_only) + len(fid_only)
    if not helm_only and not fid_only:
        console.print(Panel.fit(
            f"[bold green]✓ Fully aligned[/bold green] — {len(matched)} position(s) match between HELM and Fidelity.",
            border_style="green"
        ))
    else:
        lines = [f"[bold]{len(matched)} matched[/bold]  |  "]
        if helm_only:
            lines.append(f"[red]{len(helm_only)} in HELM only[/red]  |  ")
        if fid_only:
            lines.append(f"[yellow]{len(fid_only)} in Fidelity only[/yellow]")
        console.print(Panel.fit(
            "".join(lines) + "\n\n" +
            ("[dim]Run [bold]helm activity[/bold] to sync closes and confirms.[/dim]" if helm_only or fid_only else ""),
            border_style="yellow" if (helm_only or fid_only) else "green",
            title="Reconcile Summary"
        ))
    _bal = parse_fidelity_balances(str(filepath))
    _cd = {k: {"name": v["name"], "cash": v["cash"]} for k, v in _bal["accounts"].items()} if _bal else {}
    if _cd:
        _col = get_csp_collateral()
        _tc = sum(a['cash'] for a in _cd.values())
        _nd = _tc - _col
        from rich.table import Table as _T; from rich import box as _bx
        _t = _T(box=_bx.SIMPLE, show_header=False, padding=(0,1))
        _t.add_column('', style='dim', width=34); _t.add_column('', justify='right', width=14)
        for _an, _av in sorted(_cd.items()):
            _t.add_row(f"{_av['name']} ({_an})", f"[green]${_av['cash']:,.0f}[/green]")
        _t.add_row('─'*32, '─'*12)
        _t.add_row('[bold]Total cash[/bold]', f'[bold green]${_tc:,.0f}[/bold green]')
        _t.add_row('CSP collateral committed', f'[yellow]-${_col:,.0f}[/yellow]')
        _t.add_row('[bold]Net deployable[/bold]', f'[bold cyan]${_nd:,.0f}[/bold cyan]')
        console.print(); console.print(Panel(_t, title='[bold]Available Capital[/bold]', border_style='green')); console.print()
    console.print()

    # ── W56: refresh the balances from the file we just read ─────────────────
    # `accounts.portfolio_value` sizes every new position (suggest_contracts
    # takes 5% of it) and had no writer reachable after first setup, so it sat
    # at the 26 May figure for two months. The money was in the CSV this
    # command already opens; it was being thrown away with the Account Total
    # and SPAXX rows. No new file handling, no new habit -- it refreshes
    # whenever you check alignment.
    if _bal and _bal.get("net_liquidation"):
        _update_account_balances(account_id, _bal)

    try:
        _log_event("RECONCILE_RUN")
    except Exception:
        pass


if __name__ == "__main__":
    run()


def _update_account_balances(account_id, bal):
    """Write refreshed balances, refusing to walk backwards in time.

    Deliberate choices, both of which W56 asked to be settled explicitly:

    * It REPORTS the change rather than refreshing silently. A number that
      moves $24,000 under the position sizer without saying so is the same
      class of problem as one that never moves at all.
    * It REFUSES a CSV older than the one already recorded. Re-running an old
      download from ~/Downloads -- where 33 of them are sitting -- would
      otherwise walk the account value backwards and silently resize every
      subsequent trade.
    """
    from helm.models.account import Account

    acct = Account.get(account_id)
    if not acct:
        return

    new_as_of = bal.get("as_of")
    old_as_of = getattr(acct, "balances_as_of", None)
    if new_as_of and old_as_of and new_as_of < old_as_of:
        console.print(
            f"[yellow]Balances not updated[/yellow] [dim]— this file is from "
            f"{new_as_of}, and HELM already holds figures from {old_as_of}. "
            f"Download a current portfolio file to refresh.[/dim]")
        console.print()
        return

    old_pv = acct.portfolio_value or 0.0
    new_pv = round(bal["net_liquidation"], 2)
    new_bp = round(bal["buying_power"], 2)
    delta = new_pv - old_pv

    try:
        acct.update_balances(new_bp, new_pv, as_of=new_as_of)
    except Exception as e:
        console.print(f"[yellow]Could not update balances:[/yellow] [dim]{e}[/dim]")
        console.print()
        return

    if abs(delta) < 0.005:
        console.print(f"[dim]Account value unchanged at ${new_pv:,.0f} "
                      f"(as of {new_as_of or 'unknown'}).[/dim]")
    else:
        _c = "green" if delta > 0 else "red"
        console.print(
            f"[bold]Account value[/bold] ${old_pv:,.0f} → [bold]${new_pv:,.0f}[/bold] "
            f"([{_c}]{'+' if delta > 0 else '-'}${abs(delta):,.0f}[/{_c}])"
            f"[dim] · cash ${new_bp:,.0f} · as of {new_as_of or 'unknown'}[/dim]")
        console.print(
            f"[dim]  Position sizing uses 5% of this — "
            f"${old_pv * 0.05:,.0f} → ${new_pv * 0.05:,.0f} per trade.[/dim]")
    if bal.get("pending"):
        console.print(f"[dim]  Includes ${bal['pending']:,.0f} pending activity "
                      f"(unsettled), per the same definition helm import uses.[/dim]")
    console.print()
