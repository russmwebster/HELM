"""
helm workflow -- Display the HELM trading workflow and command reference
"""
import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.rule import Rule
from rich import box

console = Console()


def run():
    console.print()
    console.print(Panel.fit(
        "[bold cyan]HELM Trading Workflow[/bold cyan]\n"
        "[dim]Command reference for every stage of the trade lifecycle[/dim]",
        border_style="cyan"
    ))

    def section(title):
        console.print()
        console.print(f"[bold dim]{title}[/bold dim]")
        console.print("[dim]" + "─" * 60 + "[/dim]")

    def cmd(command, description, note=""):
        n = f"  [dim]{note}[/dim]" if note else ""
        console.print(f"  [bold cyan]{command:<42}[/bold cyan] [dim]{description}[/dim]{n}")

    def auto(time, schedule, command, description):
        console.print(
            f"  [green]{time:<8}[/green] [dim]{schedule:<12}[/dim] "
            f"[bold cyan]{command:<28}[/bold cyan] [dim]{description}[/dim]"
        )

    # ── Automated ────────────────────────────────────────────
    section("AUTOMATED  (no action needed)")
    auto("9:35am",  "Mon-Fri", "helm ivr refresh",     "IVR/IVP updated for all watchlist tickers")
    auto("10am/12pm/2pm/3:30pm", "Mon-Fri", "helm check --silent", "Positions checked 4x daily — P&L, flags, DTE")
    auto("10:00am", "Mon-Fri", "helm notify",          "Portfolio summary → HELM Reminders → iPhone")
    auto("10:15am", "Monday",  "helm screen",          "Optionability refresh — live OI data (market must be open)")

    # ── Morning routine ───────────────────────────────────────
    section("MORNING ROUTINE")
    cmd("helm check",                    "Review all open positions — health, P&L, DTE")
    cmd("helm check TICKER --deep",      "Deep dive on one position")
    cmd("helm check --deep",             "Deep dive on ALL open positions")
    cmd("helm ibkr status",              "Verify IBKR connection (needed for greeks/IVR)")

    # ── Find opportunities ────────────────────────────────────
    section("FIND OPPORTUNITIES")
    cmd("helm scan",                     "Scan watchlist for entry setups by strategy")
    cmd("helm screen",                   "Refresh optionability (manual run)")
    cmd("helm reconcile",                "Sync HELM positions with Fidelity CSV — shows available capital")
    cmd("helm theme",                    "Review investment themes and tickers")
    cmd("helm ivr list",                 "IV rank summary across full watchlist")
    cmd("helm ivr show TICKER",          "IV rank detail for one ticker")

    # ── Open a position ───────────────────────────────────────
    section("OPEN A POSITION")
    cmd("helm open TICKER CSP",          "Cash-secured put")
    cmd("helm open TICKER LONG_CALL",    "Long call")
    cmd("helm open TICKER COVERED_CALL", "Covered call")
    cmd("helm open TICKER DIAGONAL",     "Diagonal spread (long back-month, short front)")
    cmd("helm open TICKER PMCC",         "Poor man's covered call")

    # ── Manage positions ──────────────────────────────────────
    section("MANAGE POSITIONS")
    cmd("helm check",                    "Daily health check — runs automatically at 10am")
    cmd("helm check TICKER --deep",      "Strategy-aware deep analysis with guidance")
    cmd("helm roll TICKER",              "Roll position — close and reopen at new expiry")

    # ── Close a position ──────────────────────────────────────
    section("CLOSE A POSITION")
    cmd("helm close TICKER",             "Close position — captures P&L and close snapshot")

    # ── Analyze outcomes ──────────────────────────────────────
    section("ANALYZE OUTCOMES")
    cmd("helm analyze",                  "Win rates, P&L, ann. return, efficiency by strategy")
    console.print("  [dim]  Tip: Scan shows Conviction (High/Moderate/Low) — Low = valid trade, size smaller[/dim]")
    cmd("helm analyze trends",           "Trade-life trends — delta drift, IV movement")
    cmd("helm analyze TICKER",           "Full check history and trend summary for one position")

    # ── Research & maintenance ────────────────────────────────
    section("RESEARCH & MAINTENANCE")
    cmd("helm watchlist add TICKER",     "Add ticker to watchlist")
    cmd("helm watchlist list",           "Show full watchlist")
    cmd("helm screen TICKER",            "Check optionability for a specific ticker")

    console.print("\n  [bold dim]STRATEGY REFERENCE[/bold dim]")
    cmd("helm open TICKER CSP",           "Bullish + high IVR — sell cash-secured put")
    cmd("helm open TICKER LONG_CALL",     "Bullish + low IVR — buy cheap call")
    cmd("helm open TICKER IRON_CONDOR",   "Neutral + high IVR — sell both sides")
    cmd("helm open TICKER LONG_STRADDLE", "Neutral + low IVR — buy ATM call + put")
    cmd("helm open TICKER BEAR_CALL_SPREAD", "Bearish + high IVR — credit spread")
    cmd("helm open TICKER BEAR_PUT_SPREAD",  "Bearish + low IVR — debit put spread")
    cmd("helm open TICKER BULL_CALL_SPREAD", "Bullish + low IVR alt — debit call spread")
    cmd("helm status",                   "Portfolio dashboard — positions and P&L")
    cmd("helm notify",                   "Send portfolio summary to HELM Reminders now")
    cmd("helm notify test",              "Send test notification to verify setup")
    cmd("helm workflow",                 "Show this workflow guide")
    cmd("helm guide",                    "Strategy matrix quick reference — bias, IVR, conviction")
    cmd("helm guide --full",              "Interactive 5-step strategy walkthrough")

    console.print()
    console.print("[dim]  Tip: run [bold]helm check --deep[/bold] each morning for full position analysis.[/dim]")
    console.print()
