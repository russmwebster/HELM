
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
    Returns list of position dicts with ticker, strike, expiration, opt_type, contracts.
    """
    import pandas as pd
    import warnings
    warnings.filterwarnings("ignore")

    try:
        df = pd.read_csv(filepath, index_col=0, thousands=",")
    except Exception as e:
        raise ValueError(f"Cannot read file: {e}")

    positions = []
    for idx, row in df.iterrows():
        acct_name = str(row.get("Account Number", "")).strip()
        symbol = str(row.get("Account Name", "")).strip()

        if not symbol or symbol in ("nan", "Pending Activity", "Account Total"):
            continue
        if "SPAXX" in symbol or "FXAIX" in symbol or "GLD" in symbol:
            continue

        parsed = parse_option_symbol(symbol)
        if not parsed:
            # Stock position
            positions.append({
                "ticker": symbol.strip(),
                "type": "STOCK",
                "strike": None,
                "expiration": None,
                "opt_type": None,
                "contracts": None,
                "symbol": symbol,
                "value": _money(row.get("Last Price Change")),
                "total_gl": _money(row.get("Today's Gain/Loss Percent")),
            })
            continue

        # Get quantity
        qty_raw = row.get("Description", "")
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
            "value": _money(row.get("Last Price Change")),
            "total_gl": _money(row.get("Today's Gain/Loss Percent")),
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


def parse_fidelity_cash(filepath):
    cash = {}
    try:
        import csv
        with open(filepath, encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                if "SPAXX" not in row.get("Symbol",""):
                    continue
                an = row.get("Account Number","").strip()
                nm = row.get("Account Name","").strip()
                vs = row.get("Current Value","").replace("$","").replace(",","").strip()
                try: v = float(vs)
                except: continue
                if an not in cash: cash[an] = {"name":nm, "cash":0.0}
                cash[an]["cash"] += v
    except: pass
    return cash


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
    _cd = parse_fidelity_cash(str(filepath))
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

    try:
        _log_event("RECONCILE_RUN")
    except Exception:
        pass


if __name__ == "__main__":
    run()
