"""helm health - open the CSP health map in the browser.

Usage:
    helm health            portfolio health map (all open CSPs)
    helm health TICKER     single-position drill-down
"""
import os
import sys
import webbrowser

BASE_URL = "http://helm.local:8766/health"


def run():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    ticker = args[0].upper() if args else None
    url = BASE_URL + ("?ticker=" + ticker if ticker else "")
    try:
        from rich.console import Console
        c = Console()
        label = ("health map - " + ticker) if ticker else "portfolio health map"
        c.print(f"[bold green]Opening HELM {label}[/]  [dim]{url}[/]")
    except Exception:
        print("Opening " + url)
    opened = False
    try:
        opened = webbrowser.open(url)
    except Exception:
        opened = False
    if not opened:
        os.system('open "' + url + '"')
