
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
        })

    return positions


def get_helm_positions(account_id: str) -> list:
    """Get all open/pending HELM positions with their legs."""
    conn = get_conn()
    try:
        positions = conn.execute(
            "SELECT * FROM positions WHERE account_id=? AND status IN ('OPEN','PENDING') ORDER BY ticker",
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

        for leg in legs:
            if leg["option_type"] == "STOCK":
                # Stock leg -- check fid_stocks
                if ticker in fid_stocks:
                    found = True
            else:
                key = (ticker, leg["expiration"], leg["strike"], leg["option_type"])
                if key in fid_index:
                    found = True
                    matched_fid_keys.add(key)  # Mark ALL option legs as matched

        if found:
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
    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1), width=120)
    t.add_column("Status",   width=12, no_wrap=True)
    t.add_column("Ticker",   style="bold cyan", width=7, no_wrap=True)
    t.add_column("Strategy", width=14, no_wrap=True)
    t.add_column("Legs",     width=30, no_wrap=True)
    t.add_column("Note",     width=40, no_wrap=True)

    # Matched
    for hp in matched:
        pos = hp["position"]
        legs = hp["legs"]
        legs_str = "  ".join(
            f"{l['option_type'][0] if l['option_type'] else 'S'}"
            f"{l['strike']:.0f} " if l['strike'] else f"stock"
            for l in legs
        )
        status_str = "[green]✓ MATCH[/green]" if pos["status"] == "OPEN" else "[yellow]✓ PENDING[/yellow]"
        t.add_row(status_str, pos["ticker"], pos["strategy"], legs_str, "")

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
    console.print()

    try:
        _log_event("RECONCILE_RUN")
    except Exception:
        pass


if __name__ == "__main__":
    run()
