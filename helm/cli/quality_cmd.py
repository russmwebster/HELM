"""
helm quality — Ownership Quality grade for the CSP assign/bail read.

  helm quality                      Grade every open REAL-book underlying (summary)
  helm quality TICKER [TICKER ...]  Grade specific tickers (detailed per-theme)
  helm quality --detail             Detailed per-theme view for the whole real book
  helm quality ... --json           Machine-readable output

Answers "if this cash-secured put is assigned, do I want to own the stock?"
Grade A-F, survival-weighted; cash_quality & balance_sheet_safety are gates that
cap the grade. Valuation is excluded by design (assignment is at the strike).
Data via yfinance; see helm/ownership.py. Recomputed live each call and recorded
to the ownership_quality cache table when present.
"""
import sys
import json

from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TaskProgressColumn
from rich import box

from helm import ownership

console = Console()

_GRADE_COLOR = {"A": "bold green", "B": "green", "C": "yellow", "D": "red", "F": "bold red"}


def _open_real_tickers() -> list:
    """Distinct underlyings across open/pending REAL-book positions."""
    from helm.db import get_conn
    conn = get_conn()
    try:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM positions "
            "WHERE status IN ('OPEN','PENDING') AND book='REAL' ORDER BY ticker"
        ).fetchall()
        return [r[0] for r in rows]
    finally:
        conn.close()


def _verdict(res: dict) -> str:
    """Short verdict tag from the lean sentence (OWNABLE / MARGINAL / BAIL / ...)."""
    lean = res.get("lean", "")
    for sep in (" —", " -"):
        if sep in lean:
            return lean.split(sep)[0].strip()
    return lean.strip()


def _grade_many(tickers: list) -> list:
    results = []
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  BarColumn(), TaskProgressColumn(),
                  console=console, transient=True) as prog:
        task = prog.add_task("Grading", total=len(tickers))
        for tk in tickers:
            prog.update(task, description=f"Grading {tk}")
            try:
                results.append(ownership.get_ownership_grade(tk))
            except Exception as e:
                results.append({"ticker": tk, "error": f"{type(e).__name__}: {e}"})
            prog.advance(task)
    return results


def _render_detail(res: dict) -> None:
    g = res["grade"]
    col = _GRADE_COLOR.get(g, "white")
    name = res.get("name") or ""
    console.print(f"\n[bold]{res['ticker']}[/bold] {name}  "
                  f"[{col}]{g}[/{col}] "
                  f"[dim]({res['composite']}/100 · conf {res['confidence']})[/dim]")
    console.print(f"  [{col}]{res['lean']}[/{col}]")
    if res["gates_failed"]:
        console.print(f"  [red]gate failed:[/red] {', '.join(res['gates_failed'])}")
    t = Table(box=box.SIMPLE, show_header=True, padding=(0, 2))
    t.add_column("theme")
    t.add_column("score", justify="right")
    t.add_column("detail")
    for theme, d in res["themes"].items():
        score = d["score"]
        sc = "green" if score >= 70 else "yellow" if score >= 45 else "red"
        gate = d.get("gate")
        gate_s = " [red]\\[fail][/red]" if gate == "fail" else (
            " [yellow]\\[warn][/yellow]" if gate == "warn" else "")
        extras = {k: v for k, v in d.items() if k not in ("score", "gate")}
        t.add_row(theme, f"[{sc}]{score:.0f}[/{sc}]{gate_s}", str(extras))
    console.print(t)


def _render_summary(results: list) -> None:
    results = sorted(results, key=lambda r: r["composite"])  # worst first
    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0, 2))
    t.add_column("Ticker", style="bold cyan", no_wrap=True)
    t.add_column("OQ", justify="center")
    t.add_column("Score", justify="right")
    t.add_column("Conf")
    t.add_column("Gate", no_wrap=True)
    t.add_column("Verdict")
    for r in results:
        g = r["grade"]
        col = _GRADE_COLOR.get(g, "white")
        gates = ", ".join(r["gates_failed"]) if r["gates_failed"] else "—"
        gcol = "red" if r["gates_failed"] else "dim"
        t.add_row(r["ticker"], f"[{col}]{g}[/{col}]", f"{r['composite']:.0f}",
                  r["confidence"], f"[{gcol}]{gates}[/{gcol}]",
                  f"[{col}]{_verdict(r)}[/{col}]")
    console.print(t)


def run() -> None:
    args = sys.argv[1:]
    if args and args[0] in ("-h", "--help"):
        console.print("\n[bold]Usage:[/bold]")
        console.print("  helm quality                     grade every open real-book underlying")
        console.print("  helm quality TICKER [TICKER ...] grade specific tickers (detailed)")
        console.print("  helm quality --detail            detailed view for the whole book")
        console.print("  [dim]--json for machine-readable output[/dim]\n")
        return

    as_json = "--json" in args
    detail = "--detail" in args
    tickers = [a.upper() for a in args if not a.startswith("-")]
    explicit = bool(tickers)

    if not tickers:
        tickers = _open_real_tickers()
        if not tickers:
            console.print("[yellow]No open REAL-book positions found.[/yellow]")
            return

    results = _grade_many(tickers)

    for e in [r for r in results if "error" in r]:
        console.print(f"[red]{e['ticker']}: {e['error']}[/red]")
    good = [r for r in results if "grade" in r]

    if as_json:
        print(json.dumps(results, indent=2, default=str))
        return

    if explicit or detail:
        for r in good:
            _render_detail(r)
    else:
        _render_summary(good)
        console.print(f"[dim]{len(good)} underlyings graded from the open real book "
                      f"— worst first.[/dim]")


if __name__ == "__main__":
    run()
