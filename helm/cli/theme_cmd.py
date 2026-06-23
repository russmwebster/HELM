import re

# helm/cli/theme_cmd.py
# helm theme -- Investment Themes management with Claude AI integration
#
# Usage:
#   helm theme setup              Interactive theme creation via Claude
#   helm theme list               Show all themes + ticker counts
#   helm theme show <name>        Full ticker list for a theme
#   helm theme ipo [name]         IPO & pre-IPO update (adds to watchlist)
#   helm theme ipo [name]         Claude surfaces pre-IPO companies
#   helm theme add <name> <tickers>  Manually add tickers to a theme
#   helm theme remove <name> <ticker> Remove a ticker from a theme

import sys
import os
import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

logging.getLogger("yfinance").setLevel(logging.CRITICAL)

from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich import box

from helm.models.theme import Theme, log_event, days_since_event

console = Console()

CATEGORY_COLORS = {
    'ESTABLISHED': 'green',
    'EMERGING': 'yellow',
    'PRE_IPO': 'cyan',
    'WATCH': 'dim',
}

CATEGORY_LABELS = {
    'ESTABLISHED': 'Established',
    'EMERGING': 'Emerging',
    'PRE_IPO': 'Pre-IPO',
    'WATCH': 'Watch',
}

# ── Claude API helper ─────────────────────────────────────────────────────────

def call_claude(prompt: str, system: str = None, max_tokens: int = 2000,
                web_search: bool = False) -> Optional[str]:
    """
    Call the Anthropic API and return the text response.
    Requires ANTHROPIC_API_KEY environment variable.
    Set web_search=True for commands that need current information.
    """
    import urllib.request
    import urllib.error

    from helm.secrets_loader import load_env
    load_env()
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        console.print("[red]Error:[/red] ANTHROPIC_API_KEY environment variable not set.")
        console.print("[dim]Add to your shell: export ANTHROPIC_API_KEY=your_key_here[/dim]")
        return None

    model = "claude-sonnet-4-20250514" if web_search else "claude-haiku-4-5-20251001"
    payload = {
        "model": model,
        "max_tokens": max_tokens,
        "messages": [{"role": "user", "content": prompt}],
    }
    if system:
        payload["system"] = system

    # Enable web search for commands that need current information
    if web_search:
        payload["tools"] = [
            {
                "type": "web_search_20250305",
                "name": "web_search",
            }
        ]

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages",
        data=data,
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        method="POST",
    )

    import time as _time
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                text_parts = []
                for block in result.get("content", []):
                    if block.get("type") == "text":
                        text_parts.append(block["text"])
                return "\n".join(text_parts) if text_parts else None
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8")
            if e.code == 429:
                wait = 30 * (attempt + 1)
                console.print(f"[yellow]Rate limited — waiting {wait}s before retry {attempt+1}/3...[/yellow]")
                _time.sleep(wait)
                continue
            console.print(f"[red]API error {e.code}:[/red] {body[:200]}")
            return None
        except Exception as e:
            console.print(f"[red]API call failed:[/red] {e}")
            return None
    console.print("[red]Max retries exceeded — skipping.[/red]")
    return None


def parse_claude_themes(response: str) -> list[dict]:
    """
    Parse Claude's JSON response for theme suggestions.
    Returns list of {name, description, tickers: {ESTABLISHED, EMERGING, PRE_IPO}}
    """
    # Find JSON block in response
    start = response.find("[")
    end = response.rfind("]") + 1
    if start == -1 or end == 0:
        # Try object
        start = response.find("{")
        end = response.rfind("}") + 1
        if start == -1:
            return []

    try:
        data = json.loads(response[start:end])
        if isinstance(data, dict):
            data = [data]
        return data
    except json.JSONDecodeError:
        return []


# ── Commands ──────────────────────────────────────────────────────────────────

def cmd_setup(args):
    """Interactive theme creation via Claude API."""
    console.print()
    console.print(Panel.fit(
        "[bold cyan]Investment Themes Setup[/bold cyan]\n\n"
        "[dim]Tell me about the investment themes that interest you.\n"
        "Describe them in your own words — sectors, trends, technologies,\n"
        "or any conviction areas you want to track and trade around.[/dim]",
        border_style="cyan"
    ))
    console.print()

    user_input = Prompt.ask("  Your themes")
    if not user_input.strip():
        console.print("[dim]No input provided.[/dim]")
        return

    console.print()
    console.print("[dim]Thinking...[/dim]")

    system = """You are a financial research assistant helping an options trader 
organize their investment universe by theme. The trader uses options strategies 
(cash-secured puts, covered calls, long calls, spreads) and wants to track 
companies they believe in across three categories:
- ESTABLISHED: proven leaders, large caps, liquid options
- EMERGING: smaller, faster-growing companies with options
- PRE_IPO: private companies or those that recently filed S-1, no options yet

Return ONLY valid JSON — an array of theme objects. No markdown, no explanation.
Each theme object must have exactly these fields:
{
  "name": "short theme name (2-4 words)",
  "description": "one sentence describing this theme and why it matters",
  "tickers": {
    "ESTABLISHED": ["TICK1", "TICK2", ...],
    "EMERGING": ["TICK1", "TICK2", ...],
    "PRE_IPO": ["Company Name (private)", ...]
  }
}

For ESTABLISHED: 4-8 tickers with liquid options, well-known companies
For EMERGING: 3-6 tickers, smaller/newer companies with options
For PRE_IPO: 2-4 company names (not tickers) that are private or recently IPO'd

Focus on companies most relevant to options trading — avoid extremely illiquid names."""

    prompt = f"""The trader described their themes as:

"{user_input}"

Based on this, suggest 2-6 investment themes with appropriate company groupings.
Return only the JSON array."""

    response = call_claude(prompt, system=system, max_tokens=3000)
    if not response:
        return

    themes_data = parse_claude_themes(response)
    if not themes_data:
        console.print("[red]Could not parse Claude's response. Please try again.[/red]")
        console.print("[dim]Raw response:[/dim]")
        console.print(response[:500])
        return

    # Show suggested themes
    console.print()
    console.print("[bold]Here are the themes I'd suggest:[/bold]")
    console.print()

    for i, t in enumerate(themes_data, 1):
        name = t.get("name", f"Theme {i}")
        desc = t.get("description", "")
        tickers = t.get("tickers", {})

        est = tickers.get("ESTABLISHED", [])
        emg = tickers.get("EMERGING", [])
        pre = tickers.get("PRE_IPO", [])

        console.print(f"  [bold cyan]{name}[/bold cyan]")
        console.print(f"  [dim]{desc}[/dim]")
        if est: console.print(f"  [green]Established:[/green] {', '.join(est)}")
        if emg: console.print(f"  [yellow]Emerging:[/yellow]  {', '.join(emg)}")
        if pre: console.print(f"  [cyan]Pre-IPO:[/cyan]   {', '.join(pre)}")
        console.print()

    # Confirm and save
    if not Confirm.ask("  Save these themes to HELM?", default=True):
        console.print("[dim]Themes not saved.[/dim]")
        return

    console.print()
    saved = 0
    tickers_added = 0

    for t in themes_data:
        name = t.get("name", "").strip()
        if not name:
            continue

        # Check if theme already exists
        existing = Theme.get(name)
        if existing:
            console.print(f"  [dim]Theme already exists: {name} — skipping[/dim]")
            continue

        theme = Theme.create(
            name=name,
            description=t.get("description", "")
        )

        tickers = t.get("tickers", {})
        for category, items in tickers.items():
            if category not in ("ESTABLISHED", "EMERGING", "PRE_IPO", "WATCH"):
                continue
            for item in items:
                item = item.strip()
                if not item:
                    continue
                # PRE_IPO items may be company names not tickers
                if category == "PRE_IPO" or " " in item:
                    # Build a cleaner ticker from company name
                    words = item.replace(".", "").replace(",", "").split()
                    if len(words) == 1:
                        ticker_id = words[0][:10].upper()
                    elif len(words) == 2:
                        ticker_id = (words[0][:5] + words[1][:5]).upper()
                    else:
                        ticker_id = "".join(w[0] for w in words[:4]).upper() + words[0][1:4].upper()
                    ticker_id = ticker_id[:10]
                    theme.add_ticker(
                        ticker=ticker_id,
                        category="PRE_IPO",
                        company_name=item,
                        notes="Pre-IPO / private — no options available"
                    )
                else:
                    theme.add_ticker(ticker=item.upper(), category=category)
                    # Add to watchlist if optionable category
                    if category in ("ESTABLISHED", "EMERGING"):
                        try:
                            from helm.models.watchlist import WatchlistItem
                            if not WatchlistItem.get(item.upper()):
                                WatchlistItem.add(item.upper(), willing_to_own=1)
                        except Exception:
                            pass
                tickers_added += 1

        log_event("THEME_CREATED", entity_id=theme.id, entity_name=name)
        console.print(f"  [green]✓[/green] {name} — {len([x for v in tickers.values() for x in v])} companies")
        saved += 1

    console.print()
    console.print(Panel.fit(
        f"[bold green]{saved} theme(s) created[/bold green]\n"
        f"[dim]Run [bold]helm theme list[/bold] to see your themes.\n"
        f"Run [bold]helm theme refresh[/bold] anytime to get new suggestions.[/dim]",
        border_style="green"
    ))
    console.print()


def cmd_list(args):
    """Show all themes with ticker counts."""
    themes = Theme.all()

    if not themes:
        console.print()
        console.print("[yellow]No themes configured.[/yellow]")
        console.print("[dim]Run [bold]helm theme setup[/bold] to create your first theme.[/dim]")
        console.print()
        return

    console.print()
    t = Table(box=box.SIMPLE_HEAD, show_header=True, padding=(0,1))
    t.add_column("Theme",        style="bold cyan", width=25)
    t.add_column("Established",  justify="center", width=12)
    t.add_column("Emerging",     justify="center", width=10)
    t.add_column("Pre-IPO",      justify="center", width=9)
    t.add_column("Total",        justify="center", width=7)
    t.add_column("Description",  width=45)
    t.add_column("Last IPO Update", width=14)

    for theme in themes:
        tickers = theme.tickers()
        est = len([x for x in tickers if x["category"] == "ESTABLISHED"])
        emg = len([x for x in tickers if x["category"] == "EMERGING"])
        pre = len([x for x in tickers if x["category"] == "PRE_IPO"])
        total = len(tickers)

        days = days_since_event("THEME_IPO_UPDATED", entity_id=theme.id)
        if days is None:
            refresh_str = "[dim]never[/dim]"
        elif days == 0:
            refresh_str = "[green]today[/green]"
        elif days <= 7:
            refresh_str = f"[green]{days}d ago[/green]"
        elif days <= 30:
            refresh_str = f"[yellow]{days}d ago[/yellow]"
        else:
            refresh_str = f"[red]{days}d ago[/red]"

        t.add_row(
            theme.name,
            f"[green]{est}[/green]" if est else "[dim]0[/dim]",
            f"[yellow]{emg}[/yellow]" if emg else "[dim]0[/dim]",
            f"[cyan]{pre}[/cyan]" if pre else "[dim]0[/dim]",
            str(total),
            (theme.description or "")[:44],
            refresh_str,
        )

    console.print(f"[bold]Investment Themes ({len(themes)})[/bold]")
    console.print()
    console.print(t)
    console.print()


def cmd_show(args):
    """Show full ticker list for a theme."""
    if not args:
        console.print("[red]Specify a theme name.[/red]")
        return

    name = " ".join(args)
    theme = Theme.get(name)
    if not theme:
        console.print(f"[yellow]Theme not found:[/yellow] {name}")
        console.print("[dim]Run [bold]helm theme list[/bold] to see available themes.[/dim]")
        return

    tickers = theme.tickers()
    console.print()
    console.print(Panel.fit(
        f"[bold cyan]{theme.name}[/bold cyan]\n"
        f"[dim]{theme.description or ''}[/dim]",
        border_style="cyan"
    ))
    console.print()

    for category in ["ESTABLISHED", "EMERGING", "PRE_IPO", "WATCH"]:
        cat_tickers = [x for x in tickers if x["category"] == category]
        if not cat_tickers:
            continue

        color = CATEGORY_COLORS[category]
        label = CATEGORY_LABELS[category]
        console.print(f"  [{color}]{label} ({len(cat_tickers)})[/{color}]")

        for tk in cat_tickers:
            name_str = f"  [dim]{tk['company_name']}[/dim]" if tk.get("company_name") else ""
            note_str = f"  [dim italic]{tk['notes']}[/dim italic]" if tk.get("notes") else ""
            console.print(f"    [bold]{tk['ticker']}[/bold]{name_str}{note_str}")
        console.print()


def cmd_refresh(args, use_web=False):
    """Ask Claude to suggest new tickers and flag outdated ones."""
    theme_name = " ".join(args) if args else None

    themes = [Theme.get(theme_name)] if theme_name else Theme.all()
    themes = [t for t in themes if t is not None]

    if not themes:
        console.print(f"[yellow]Theme not found:[/yellow] {theme_name}")
        return

    if len(themes) > 1:
        console.print()
        console.print(f"[dim]Refreshing {len(themes)} themes with web search — this will take several minutes.[/dim]")
        console.print(f"[dim]Tip: refresh one theme at a time to avoid rate limits: helm theme refresh \"<name>\"[/dim]")
        console.print()

    for i, theme in enumerate(themes):
        # Rate limit protection — pause between API calls when refreshing multiple themes
        if i > 0:
            import time
            console.print("[dim]  Pausing 30s to respect API rate limits...[/dim]")
            time.sleep(30)
        console.print()
        console.print(f"[dim]Refreshing [bold]{theme.name}[/bold]...[/dim]")

        tickers = theme.tickers()
        current = {
            cat: [x["ticker"] for x in tickers if x["category"] == cat]
            for cat in ["ESTABLISHED", "EMERGING", "PRE_IPO"]
        }

        system = (
            "Financial research assistant for an options trader. "
            "Suggest additions/removals for an investment theme watchlist. "
            "Return ONLY valid JSON, no markdown, no explanation. "
            "Schema: {add:{ESTABLISHED:[str],EMERGING:[str],PRE_IPO:[str]},remove:[{ticker:str,reason:str}],commentary:str}. "
            "CRITICAL: ESTABLISHED and EMERGING values must be stock ticker symbols only (e.g. NVDA, MSFT) — never company names. "
            "PRE_IPO may use company names for private companies. "
            "Commentary max 2 sentences."
        )

        prompt = f"""Investment theme: {theme.name}
Description: {theme.description}

Current tickers:
- Established: {', '.join(current['ESTABLISHED']) or 'none'}
- Emerging: {', '.join(current['EMERGING']) or 'none'}
- Pre-IPO: {', '.join(current['PRE_IPO']) or 'none'}

Today's date: {datetime.now().strftime('%B %d, %Y')}

Suggest:
1. New companies to ADD that belong in this theme (not already listed)
2. Any listed companies to REMOVE (acquired, bankrupt, no longer relevant to theme)
3. Brief commentary on notable developments in this theme

Focus on companies with liquid options for ESTABLISHED and EMERGING categories."""

        response = call_claude(prompt, system=system, max_tokens=1500, web_search=use_web)
        if not response:
            continue

        # Parse response
        start = response.find("{")
        end = response.rfind("}") + 1
        if start == -1:
            console.print("[yellow]Could not parse response.[/yellow]")
            continue

        try:
            data = json.loads(response[start:end])
        except json.JSONDecodeError:
            console.print("[yellow]Could not parse JSON response.[/yellow]")
            continue

        add = data.get("add", {})
        # remove can be [{ticker, reason}] or [str] for backwards compat
        _remove_raw = data.get("remove", [])
        # Normalise to {ticker, reason} and dedup immediately
        _seen = set()
        remove = []
        for item in _remove_raw:
            if isinstance(item, dict):
                raw_tk = item.get("ticker", "")
                reason = item.get("reason", "")
            else:
                raw_tk = str(item)
                reason = ""
            # Clean ticker: strip spaces/parens, uppercase, alphanumeric only
            clean = re.sub(r'[^A-Z0-9]', '', re.split(r'[\s\(]', raw_tk.strip().upper())[0])
            if clean and clean not in _seen:
                _seen.add(clean)
                remove.append({"ticker": clean, "reason": reason})
        commentary = data.get("commentary", "")

        # Show suggestions with selective accept/reject
        console.print()
        if commentary:
            console.print(f"  [italic]{commentary}[/italic]")
            console.print()

        has_changes = False
        approved_adds = {}   # cat -> [tickers to add]
        approved_removes = []

        # Pre-fetch existing tickers once for fast lookup
        try:
            existing_set = {x["ticker"].upper() for x in theme.tickers()}
        except Exception:
            existing_set = set()

        # Process additions — ask per category
        for cat in ["ESTABLISHED", "EMERGING", "PRE_IPO"]:
            new_tickers = add.get(cat, [])
            if not new_tickers:
                continue
            has_changes = True
            color = CATEGORY_COLORS[cat]
            label = CATEGORY_LABELS[cat]
            approved_adds[cat] = []

            console.print(f"  [bold]Add to {label}:[/bold]")
            for tk in new_tickers:
                tk = tk.strip()
                if not tk:
                    continue
                # Skip if already in theme (check ticker and sanitized form)
                tk_up = tk.upper()
                tk_clean = re.sub(r'[^A-Z0-9]', '', tk_up)
                if tk_up in existing_set or tk_clean in existing_set:
                    console.print(f"    [dim]{tk} — already in theme, skipping[/dim]")
                    continue
                answer = Prompt.ask(
                    f"    [{color}]+[/{color}] {tk}  add?",
                    choices=["y", "n", "s"],
                    default="y",
                    show_choices=False,
                    show_default=True,
                )
                if answer == "s":
                    console.print("    [dim]Skipping remaining additions.[/dim]")
                    break
                if answer == "y":
                    approved_adds[cat].append(tk)

        # Process removals — ask per ticker
        # Filter out any ticker just added in this session
        just_added = {tk.upper() for cat_list in approved_adds.values() for tk in cat_list}
        # Dedup remove list by ticker, and filter out anything just added this session
        def _clean_ticker(raw):
            # Model sometimes returns "RIGETTI (RGTI)" — extract just the ticker symbol
            t = (raw.get("ticker","") if isinstance(raw, dict) else str(raw)).strip()
            # If it contains a space or paren, take the first clean word
            t = re.split(r'[\s\(]', t)[0]
            return re.sub(r'[^A-Z0-9]', '', t.upper())

        seen_removes = set()
        deduped_remove = []
        for item in remove:
            tk = _clean_ticker(item).upper()
            if tk and tk not in seen_removes and tk not in just_added:
                seen_removes.add(tk)
                # Normalize the ticker in the item
                if isinstance(item, dict):
                    item = dict(item, ticker=tk)
                else:
                    item = tk
                deduped_remove.append(item)
        remove = deduped_remove
        if remove:
            has_changes = True
            console.print(f"  [bold]Consider removing:[/bold]")
            for item in remove:
                answer = Prompt.ask(
                    f"    [dim]-[/dim] {tk}  remove?",
                    choices=["y", "n"],
                    default="n",
                    show_choices=False,
                    show_default=False,
                )
                if answer == "y":
                    approved_removes.append(tk if isinstance(item, dict) else item)

        if not has_changes:
            console.print("  [green]Theme looks current — no changes suggested.[/green]")
            log_event("THEME_REFRESHED", entity_id=theme.id, entity_name=theme.name)
            continue

        # Apply approved changes
        added_count = 0
        removed_count = 0

        for cat, tickers_to_add in approved_adds.items():
            for tk in tickers_to_add:
                if " " in tk or cat == "PRE_IPO":
                    theme.add_ticker(
                        ticker=tk[:10].upper().replace(" ", "_"),
                        category="PRE_IPO",
                        company_name=tk
                    )
                else:
                    theme.add_ticker(ticker=tk.upper(), category=cat)
                    if cat in ("ESTABLISHED", "EMERGING"):
                        try:
                            from helm.models.watchlist import WatchlistItem
                            if not WatchlistItem.get(tk.upper()):
                                WatchlistItem.add(tk.upper(), willing_to_own=1)
                        except Exception:
                            pass
                added_count += 1

        for tk in approved_removes:
            theme.remove_ticker(tk.upper())
            removed_count += 1

        total = added_count + removed_count
        if total > 0:
            console.print(f"  [green]✓ {theme.name} — {added_count} added, {removed_count} removed.[/green]")
        else:
            console.print(f"  [dim]No changes applied to {theme.name}.[/dim]")

        log_event("THEME_REFRESHED", entity_id=theme.id, entity_name=theme.name)
    console.print()


def cmd_ipo(args, use_web=False):
    """Ask Claude for pre-IPO and recent IPO updates."""
    theme_name = " ".join(args) if args else None

    themes = [Theme.get(theme_name)] if theme_name else Theme.all()
    themes = [t for t in themes if t is not None]

    if not themes:
        console.print(f"[yellow]Theme not found:[/yellow] {theme_name}")
        return

    if len(themes) > 1:
        console.print()
        console.print(f"[dim]Refreshing {len(themes)} themes with web search — this will take several minutes.[/dim]")
        console.print(f"[dim]Tip: refresh one theme at a time to avoid rate limits: helm theme refresh \"<name>\"[/dim]")
        console.print()

    for i, theme in enumerate(themes):
        if i > 0:
            import time
            console.print("[dim]  Pausing 30s to respect API rate limits...[/dim]")
            time.sleep(30)
        console.print()
        console.print(f"[bold cyan]{theme.name}[/bold cyan] — IPO & Pre-IPO Update")
        console.print()

        system = """You are a financial research assistant tracking pre-IPO and 
recently-public companies. Return ONLY valid JSON — no markdown, no explanation.
Structure:
{
  "recent_ipos": [
    {
      "ticker": "TICK",
      "name": "Company Name", 
      "description": "2 sentence description of what they do and why they matter",
      "ipo_date": "Month Year",
      "status": "recently public"
    }
  ],
  "pre_ipo": [
    {
      "name": "Company Name",
      "description": "2 sentence description",
      "stage": "Series C / Filed S-1 / etc",
      "expected": "2025 / 2026 / unknown"
    }
  ],
  "commentary": "1-2 sentence overview of IPO activity in this theme"
}"""

        prompt = f"""Investment theme: {theme.name}
Description: {theme.description}
Today's date: {datetime.now().strftime('%B %d, %Y')}

For this theme, identify:
1. Companies that have had an IPO in the last 6-12 months and are now publicly traded
2. Notable private companies that may IPO in the next 1-2 years

Focus on companies relevant to options traders — companies that will likely 
have liquid options once public."""

        response = call_claude(prompt, system=system, max_tokens=2000, web_search=use_web)
        if not response:
            continue

        start = response.find("{")
        end = response.rfind("}") + 1
        if start == -1:
            console.print("[yellow]Could not parse response.[/yellow]")
            continue

        try:
            data = json.loads(response[start:end])
        except json.JSONDecodeError:
            console.print("[yellow]Could not parse JSON.[/yellow]")
            continue

        if data.get("commentary"):
            console.print(f"  [italic dim]{data['commentary']}[/italic dim]")
            console.print()

        recent = data.get("recent_ipos", [])
        if recent:
            console.print(f"  [bold green]Recent IPOs ({len(recent)})[/bold green]")
            for c in recent:
                console.print(f"    [bold]{c.get('ticker','?')}[/bold]  {c.get('name','')}  [dim]({c.get('ipo_date','')})[/dim]")
                console.print(f"    [dim]{c.get('description','')}[/dim]")
                console.print()

        pre = data.get("pre_ipo", [])
        if pre:
            console.print(f"  [bold cyan]Pre-IPO Watch ({len(pre)})[/bold cyan]")
            for c in pre:
                console.print(f"    [bold]{c.get('name','')}[/bold]  [dim]{c.get('stage','')} — expected {c.get('expected','')}[/dim]")
                console.print(f"    [dim]{c.get('description','')}[/dim]")
                console.print()

        # Offer to add recent IPO tickers to watchlist
        # Verify tickers exist in yfinance before offering to add
        def _ticker_exists(tk):
            try:
                import yfinance as yf
                info = yf.Ticker(tk).fast_info
                price = getattr(info, "last_price", None)
                return price is not None and float(price) > 0
            except Exception:
                return False
        console.print("  [dim]Verifying tickers...[/dim]")
        addable_ipos = []
        for c in recent:
            tk = c.get("ticker","").strip().upper()
            if not tk or tk == "?":
                continue
            if _ticker_exists(tk):
                addable_ipos.append(c)
            else:
                console.print(f"  [dim red]  {tk} — not found on exchange, skipping[/dim red]")

        if addable_ipos:
            console.print()
            console.print("  [dim]Add recent IPO tickers to watchlist as Emerging?[/dim]")
            ipo_added = 0
            for c in addable_ipos:
                tk = c.get("ticker","").strip().upper()
                if not tk:
                    continue
                if Confirm.ask(f"    [bold]{tk}[/bold] — {c.get('name','')}  add to watchlist?", default=True):
                    if theme.add_ticker(tk, category="EMERGING"):
                        console.print(f"    [green]+ {tk} added as Emerging[/green]")
                        ipo_added += 1
                    else:
                        console.print(f"    [dim]{tk} already in watchlist[/dim]")
            if ipo_added:
                console.print()

        # Offer to add pre-IPO companies to PRE_IPO watchlist
        if pre:
            console.print()
            console.print("  [dim]Add pre-IPO companies to watchlist?[/dim]")
            pre_added = 0
            for c in pre:
                name = c.get("name","").strip()
                if not name:
                    continue
                if Confirm.ask(f"    [bold]{name}[/bold]  add as Pre-IPO?", default=True):
                    if theme.add_ticker(name, category="PRE_IPO"):
                        console.print(f"    [green]+ {name} added as Pre-IPO[/green]")
                        pre_added += 1
                    else:
                        console.print(f"    [dim]{name} already in watchlist[/dim]")
            if pre_added:
                console.print()

        log_event("THEME_IPO_UPDATED", entity_id=theme.id, entity_name=theme.name)

    console.print()


def cmd_add(args):
    """Manually add tickers to a theme."""
    if len(args) < 2:
        console.print("[red]Usage:[/red] helm theme add <theme-name> <TICK1,TICK2,...> [--category EMERGING]")
        return

    # Find category flag
    category = "ESTABLISHED"
    if "--category" in args:
        idx = args.index("--category")
        if idx + 1 < len(args):
            category = args[idx + 1].upper()
            args = [a for i, a in enumerate(args) if i not in (idx, idx+1)]

    # Last arg is tickers, everything before is theme name
    tickers_str = args[-1]
    theme_name = " ".join(args[:-1])

    theme = Theme.get(theme_name)
    if not theme:
        console.print(f"[yellow]Theme not found:[/yellow] {theme_name}")
        return

    tickers = [t.strip().upper() for t in tickers_str.replace(",", " ").split() if t.strip()]
    added = 0
    for ticker in tickers:
        if theme.add_ticker(ticker, category=category):
            console.print(f"  [green]✓[/green] {ticker} → {theme.name} ({category})")
            added += 1

    console.print(f"\n  {added} ticker(s) added.")
    console.print()


# ── Nudge checker ─────────────────────────────────────────────────────────────

def check_nudges():
    """
    Check if any theme-related nudges are due.
    Called silently from other commands.
    Returns list of nudge strings to display.
    """
    nudges = []
    themes = Theme.all()
    if not themes:
        return nudges

    for theme in themes:
        tname = theme.name
        ipo_days = days_since_event("THEME_IPO_UPDATED", entity_id=theme.id)
        if ipo_days is None or ipo_days >= 60:
            ipo_age = "never" if ipo_days is None else (str(ipo_days) + " days ago")
            nudges.append(
                "[dim]  [bold]" + tname + "[/bold] IPO watchlist last updated " + ipo_age + ". "
                "Run [bold]helm theme ipo[/bold].[/dim]"
            )

    return nudges[:2]  # Max 2 nudges at a time


# ── Router ────────────────────────────────────────────────────────────────────

def run():
    args = sys.argv[1:]

    if not args or args[0] in ("--help", "-h"):
        console.print()
        console.print("[bold]Usage:[/bold]  helm theme <command>")
        console.print()
        console.print("  [cyan]setup[/cyan]                    Create themes via Claude AI")
        console.print("  [cyan]list[/cyan]                     Show all themes")
        console.print("  [cyan]show <name>[/cyan]              Full ticker list for a theme")
        console.print("  [cyan]ipo [name][/cyan]               Pre-IPO and recent IPO update")
        console.print("  [cyan]add <name> <tickers>[/cyan]     Manually add tickers")
        console.print()
        return

    cmd = args[0].lower()
    rest = args[1:]

    use_web = "--web" in rest
    rest = [a for a in rest if a != "--web"]

    if   cmd == "setup":   cmd_setup(rest)
    elif cmd == "list":    cmd_list(rest)
    elif cmd == "show":    cmd_show(rest)
    elif cmd == "ipo":     cmd_ipo(rest, use_web=use_web)
    elif cmd == "add":     cmd_add(rest)
    else:
        console.print(f"[red]Unknown theme command:[/red] {cmd}")
        console.print("[dim]Run [bold]helm theme --help[/bold] for usage.[/dim]")


if __name__ == "__main__":
    run()
