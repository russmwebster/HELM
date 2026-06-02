"""
helm guide — Strategy selection guide for HELM users.

Usage:
  helm guide           Quick reference — the strategy matrix on one screen
  helm guide --full    Full interactive walkthrough (5 steps)
"""

import sys
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box
from rich.prompt import Prompt

console = Console()


def c(text, color): return f"[{color}]{text}[/{color}]"
def bold(text): return f"[bold]{text}[/bold]"
def dim(text): return f"[dim]{text}[/dim]"
def _wait(): Prompt.ask(dim("\n  Press Enter to continue"))


# ── Quick Reference ────────────────────────────────────────────────────────────

def cmd_quick():
    console.print()
    console.print(Panel(
        bold("HELM Strategy Selection Guide") + "\n" +
        dim("How HELM chooses the right options strategy for each ticker"),
        border_style="cyan", expand=False
    ))
    console.print()

    console.print(bold("1. Directional Bias Score") + "  " + dim("(computed from RSI · trend · 52wk position)"))
    console.print()
    t = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", padding=(0,1))
    t.add_column("Score",  justify="center", width=9)
    t.add_column("Label",  width=16)
    t.add_column("Signals",  width=46)
    t.add_row(c("+2/+3","green"),  c("Bullish","green"),        "RSI oversold + uptrend — strong buy signal")
    t.add_row(c("+1","cyan"),      c("Mildly bullish","cyan"),  "Moderate upward technical signals")
    t.add_row(c("0","dim"),        c("Neutral","dim"),           "Mixed or no clear direction")
    t.add_row(c("-1","yellow"),    c("Mildly bearish","yellow"),"Moderate downward technical signals")
    t.add_row(c("-2/-3","red"),    c("Bearish","red"),           "RSI overbought + downtrend — strong sell signal")
    console.print(t)
    console.print()

    console.print(bold("2. IVR — Implied Volatility Rank") + "  " + dim("(0–100 relative to past 52 weeks)"))
    console.print()
    t2 = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", padding=(0,1))
    t2.add_column("IVR",        justify="center", width=10)
    t2.add_column("Meaning",    width=20)
    t2.add_column("Action",     width=42)
    t2.add_row(c("≥ 60","green"),  "Expensive options", "Sell premium — collect inflated credit")
    t2.add_row(c("35–60","cyan"),  "Moderate options",  "Context-dependent — direction matters more")
    t2.add_row(c("< 35","yellow"), "Cheap options",     "Buy premium — cheap to enter directional trades")
    console.print(t2)
    console.print()

    console.print(bold("3. The Strategy Matrix") + "  " + dim("(Bias × IVR = Strategy)"))
    console.print()
    m = Table(box=box.SIMPLE_HEAVY, show_header=True, header_style="bold", padding=(0,1))
    m.add_column("Bias",              style="bold", width=18)
    m.add_column("IVR < 35 (cheap)",  justify="center", width=20)
    m.add_column("IVR 35–60 (mod)",   justify="center", width=20)
    m.add_column("IVR ≥ 60 (rich)",   justify="center", width=20)
    m.add_row(c("Bullish +2/+3","green"),     c("LONG_CALL ✅","yellow"),        c("CSP","green"),              c("CSP ✅","green"))
    m.add_row(c("Mildly bull +1","cyan"),     c("DIAGONAL","cyan"),              c("BULL_PUT_SPREAD","cyan"),   c("CSP ✅","green"))
    m.add_row(c("Neutral 0","dim"),           c("LONG_STRADDLE","magenta"),      c("IRON_CONDOR","blue"),       c("IRON_CONDOR ✅","blue"))
    m.add_row(c("Mildly bear -1","yellow"),   c("BEAR_PUT_SPREAD","red"),        c("IRON_CONDOR","blue"),       c("BEAR_CALL_SPREAD ✅","red"))
    m.add_row(c("Bearish -2/-3","red"),       c("BEAR_PUT_SPREAD ✅","red"),     c("BEAR_CALL_SPREAD","red"),   c("BEAR_CALL_SPREAD ✅","red"))
    console.print(m)
    console.print(dim("  ✅ = clearest, highest-conviction setup for that cell"))
    console.print()

    console.print(bold("4. Conviction Level") + "  " + dim("(strength of the combined signal)"))
    console.print()
    t3 = Table(box=box.SIMPLE, show_header=True, header_style="bold dim", padding=(0,1))
    t3.add_column("Level",   width=12)
    t3.add_column("When",    width=52)
    t3.add_row(c("High","green"),    "Score ≥ 2 + IVR clearly actionable (>15 from neutral 50)")
    t3.add_row(c("Moderate","yellow"),"Score ≥ 1 + IVR has meaningful edge")
    t3.add_row(c("Low","dim"),       "Neutral score or both signals weak — size smaller")
    console.print(t3)
    console.print()

    console.print(Panel(
        bold("On Low Conviction:") + "\n\n"
        "Low does [bold]not[/bold] mean skip the trade. In a trending market, many excellent\n"
        "CSPs will show Low conviction because signals are moderate, not extreme.\n\n"
        "Use conviction to guide [bold]position sizing[/bold]:\n"
        f"  {c('High','green')} → full size   {c('Moderate','yellow')} → normal   {c('Low','dim')} → smaller",
        border_style="dim", title=dim("Conviction guidance"), expand=False
    ))
    console.print()
    console.print(dim("  Run ") + bold("helm guide --full") + dim(" for an interactive step-by-step walkthrough."))
    console.print()


# ── Full Walkthrough Steps ─────────────────────────────────────────────────────

def _step_bias():
    console.print()
    console.print(Panel(
        bold("Step 1 of 5 — Directional Bias Score") + "\n\n"
        "HELM scores every ticker for directional bias using three signals:\n\n"
        f"  {c('RSI (Relative Strength Index)','cyan')}\n"
        "    RSI < 30  = oversold, may bounce            (+2 points)\n"
        "    RSI 30–50 = below midpoint, mild bullish    (+1 point)\n"
        "    RSI 50–65 = above midpoint, mild bearish    (-1 point)\n"
        "    RSI > 65  = overbought, may pull back       (-2 points)\n\n"
        f"  {c('Trend — EMA20 vs SMA50','cyan')}\n"
        "    Price > EMA20 > SMA50 = uptrend             (+1 point)\n"
        "    Price < EMA20 < SMA50 = downtrend           (-1 point)\n\n"
        f"  {c('52-week Price Position','cyan')}\n"
        "    Near 52wk low = mean reversion potential    (+1 point)\n\n"
        "Scores sum to a value from -3 (strongly bearish) to +3 (strongly bullish).\n"
        "This is the [bold]directional axis[/bold] of the strategy matrix.",
        border_style="cyan", title="[bold cyan]Bias Score[/bold cyan]"
    ))
    _wait()


def _step_ivr():
    console.print()
    console.print(Panel(
        bold("Step 2 of 5 — IVR (Implied Volatility Rank)") + "\n\n"
        "IVR measures where [bold]current IV[/bold] sits relative to the past 52 weeks.\n\n"
        "  IVR = 0    → IV at 52-week LOW   (options are very cheap)\n"
        "  IVR = 50   → IV exactly in the middle of its 52-week range\n"
        "  IVR = 100  → IV at 52-week HIGH  (options are very expensive)\n\n"
        f"  {c('Why does this matter?','yellow')}\n"
        "  When IV is HIGH — sell options to collect inflated premium.\n"
        "  When IV is LOW  — buy options because they are cheap to enter.\n\n"
        "  Selling when IV is low = collecting less premium for the same risk.\n"
        "  Buying when IV is high = overpaying, and risking IV crush.\n\n"
        f"  {c('HELM uses IVR ≥ 35 as the threshold for selling premium','green')}\n"
        f"  {c('HELM uses IVR < 35 as the threshold for buying premium','yellow')}",
        border_style="cyan", title="[bold cyan]Implied Volatility Rank[/bold cyan]"
    ))
    _wait()


def _step_matrix():
    console.print()
    console.print(Panel(
        bold("Step 3 of 5 — The Strategy Matrix") + "\n\n"
        "Combining Bias Score and IVR gives us a strategy recommendation.\n"
        "Each cell answers: given this direction and IV environment,\n"
        "what is the best options structure?\n\n"
        f"  {c('Bullish + high IVR','green')} → {bold('CSP')}\n"
        "    Expect stock to stay flat or rise. Sell a put below market.\n\n"
        f"  {c('Bullish + low IVR','yellow')} → {bold('LONG_CALL')}\n"
        "    Expect stock to rise. Buy a cheap call to participate.\n\n"
        f"  {c('Neutral + high IVR','blue')} → {bold('IRON_CONDOR')}\n"
        "    No directional view. Sell put spread + call spread for income.\n\n"
        f"  {c('Neutral + low IVR','magenta')} → {bold('LONG_STRADDLE')}\n"
        "    No directional view. Buy call + put — profit from any big move.\n\n"
        f"  {c('Bearish + high IVR','red')} → {bold('BEAR_CALL_SPREAD')}\n"
        "    Expect stock to fall. Sell a call spread above market.\n\n"
        f"  {c('Bearish + low IVR','red')} → {bold('BEAR_PUT_SPREAD')}\n"
        "    Expect stock to fall. Buy a cheap put spread below market.",
        border_style="cyan", title="[bold cyan]Strategy Matrix — Cell by Cell[/bold cyan]"
    ))
    _wait()


def _step_conviction():
    console.print()
    console.print(Panel(
        bold("Step 4 of 5 — Conviction Levels") + "\n\n"
        f"Not all setups are equal. HELM rates each as {c('High','green')}, "
        f"{c('Moderate','yellow')}, or {c('Low','dim')}.\n\n"
        f"  {c('High conviction','green')}\n"
        "    Score ≥ 2 AND IVR is clearly actionable (distance ≥ 15 from 50).\n"
        "    Example: Bullish +3, IVR 82 → CSP, High.\n"
        "    Both signals are strong and aligned. Best setups in the scan.\n\n"
        f"  {c('Moderate conviction','yellow')}\n"
        "    Score ≥ 1 AND IVR has meaningful edge.\n"
        "    Example: Mildly bullish +1, IVR 70 → CSP, Moderate.\n"
        "    Good setups worth taking at normal position size.\n\n"
        f"  {c('Low conviction','dim')}\n"
        "    Weak score OR IVR near neutral (40–60 range).\n"
        "    Example: Mildly bullish +1, IVR 40 → CSP, Low.\n"
        "    Valid trade — but size smaller and be more selective.\n\n"
        f"  {bold('Important:')} In a trending bull market, many excellent CSPs will\n"
        "  show Low conviction. RSI 50–65 and IVR 35–50 are both moderate,\n"
        "  not extreme. Use conviction to size positions, not filter them.",
        border_style="cyan", title="[bold cyan]Conviction Levels[/bold cyan]"
    ))
    _wait()


def _step_workflow():
    console.print()
    console.print(Panel(
        bold("Step 5 of 5 — Your Daily Workflow") + "\n\n"
        f"  {c('1. Screen','cyan')}  →  helm screen\n"
        "    Monday mornings. Filters watchlist for options liquidity (OI ≥ 5,000).\n\n"
        f"  {c('2. Scan','cyan')}  →  helm scan\n"
        "    Applies the strategy matrix. Shows Strategy + Conviction for each ticker.\n\n"
        f"  {c('3. Open','cyan')}  →  helm open TICKER STRATEGY\n"
        "    Live IBKR chain. Recommends specific contract, strike, expiry, sizing.\n\n"
        f"  {c('4. Check','cyan')}  →  helm check\n"
        "    Daily P&L, delta drift, DTE countdown.\n"
        "    Flags 50% profit target and 21 DTE exit threshold.\n\n"
        f"  {c('5. Close','cyan')}  →  helm close TICKER\n"
        "    Records realized P&L. Feeds helm analyze.\n\n"
        f"  {c('6. Analyze','cyan')}  →  helm analyze\n"
        "    Win rates, annualized returns, efficiency by strategy.\n\n"
        f"  {dim('Run')} helm guide {dim('anytime for the quick matrix reference.')}",
        border_style="cyan", title="[bold cyan]Daily Workflow[/bold cyan]"
    ))
    console.print()
    console.print(Panel(
        f"  {c('Guide complete.','green')} You now understand how HELM selects strategies.\n\n"
        f"  {bold('helm guide')}       Quick matrix reference\n"
        f"  {bold('helm scan')}        See the matrix in action\n"
        f"  {bold('helm workflow')}    Step-by-step trading checklist",
        border_style="green", title="[bold green]All done[/bold green]", expand=False
    ))
    console.print()


# ── Full Walkthrough ───────────────────────────────────────────────────────────

STEPS = [
    ("Directional Bias",         _step_bias),
    ("IVR — Implied Volatility", _step_ivr),
    ("The Strategy Matrix",      _step_matrix),
    ("Conviction Levels",        _step_conviction),
    ("Your Daily Workflow",      _step_workflow),
]


def cmd_full():
    console.print()
    console.print(Panel(
        bold("HELM Strategy Guide — Full Walkthrough") + "\n" +
        dim("5 steps · press Enter to advance through each section"),
        border_style="cyan", expand=False
    ))
    for fn_title, fn in STEPS:
        fn()
    console.print(dim("  Here is the full matrix as a quick reminder:"))
    console.print()
    cmd_quick()


# ── Entry Point ────────────────────────────────────────────────────────────────

def run():
    args = sys.argv[1:]
    if '--full' in args or '-f' in args:
        cmd_full()
    elif '--help' in args or '-h' in args:
        console.print()
        console.print(f"  {bold('Usage:')}  helm guide [--full]")
        console.print()
        console.print("  helm guide          Quick reference — strategy matrix on one screen")
        console.print("  helm guide --full   Full interactive walkthrough (5 steps)")
        console.print()
    else:
        cmd_quick()
