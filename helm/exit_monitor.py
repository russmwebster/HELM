"""
HELM exit highlights  (HELM-079 seed) -- interactive `helm check` only.

Decision-support highlights for SHORT-PREMIUM positions. Nothing here is
mechanical: it surfaces data points that inform a human exit decision.

Signals (a position can carry several):
  STOP? / CLOSE : -100% stop line, one-session confirmation + dead-band.
  TAKE-PROFIT   : kept% >= the position's profit target (the +50% mirror).
  ASSIGN?       : CSP only. Past the short strike (ITM), extrinsic ~ 0, near expiry.
  PAST B/E      : spot beyond breakeven (be% < 0).
  (delta)       : change in kept% since the last session.

FIREWALL: invoked ONLY from check_cmd.cmd_check_all. Never runs during
`helm snapshot`; never writes checks/leg_checks. State lives only in the
check-owned tables check_exit_flags and check_kept_hist.
SCOPE: REAL book, premium-selling families only (CSP, CREDIT_SPREAD, IC).
"""

from __future__ import annotations
import datetime as _dt

from helm.db import get_conn

STOP_LINE = -100.0
DEAD_BAND = 5.0
ASSIGN_EXTR_MAX = 0.10
ASSIGN_DTE_MAX = 7
MOVER_MIN = 15.0
DEFAULT_TP = 50.0
PREMIUM_FAMILIES = ("CSP", "CREDIT_SPREAD", "IC")
REAL_BOOK = "REAL"

_DDL_FLAGS = """
CREATE TABLE IF NOT EXISTS check_exit_flags (
    position_id   TEXT PRIMARY KEY,
    flag_kept_pct REAL NOT NULL,
    flag_date     TEXT NOT NULL,
    created_at    TEXT NOT NULL
)
"""
_DDL_HIST = """
CREATE TABLE IF NOT EXISTS check_kept_hist (
    position_id  TEXT NOT NULL,
    session_date TEXT NOT NULL,
    kept_pct     REAL NOT NULL,
    PRIMARY KEY (position_id, session_date)
)
"""


def _ensure(conn):
    conn.execute(_DDL_FLAGS)
    conn.execute(_DDL_HIST)


def effective_session_date(market_open: bool, today: _dt.date | None = None) -> str:
    d = today or _dt.date.today()
    if not market_open:
        while d.weekday() >= 5:
            d -= _dt.timedelta(days=1)
    return d.isoformat()


def _safe(fn, *a, default=None):
    try:
        return fn(*a)
    except Exception:
        return default


def evaluate(rows, market_open: bool):
    from helm.cli import check_cmd as cc

    sess = effective_session_date(market_open)
    conn = get_conn()
    _ensure(conn)

    live = {}
    for r in rows:
        pos = r.get("pos") or {}
        if pos.get("book") != REAL_BOOK or r.get("family") not in PREMIUM_FAMILIES:
            continue
        a = r.get("a") or {}
        kept = a.get("pnl_pct")
        if kept is None:
            continue
        live[pos["id"]] = {
            "ticker": pos.get("ticker"), "kept": float(kept),
            "family": r.get("family"), "a": a, "pos": pos,
            "legs": r.get("legs") or [], "dte": r.get("_dte"),
        }

    flags = {row["position_id"]: dict(row)
             for row in conn.execute("SELECT * FROM check_exit_flags")}
    now_iso = _dt.datetime.now().isoformat(timespec="seconds")

    out = []
    for pid, info in live.items():
        kept, a, pos, legs = info["kept"], info["a"], info["pos"], info["legs"]
        fam, dte = info["family"], info["dte"]
        tags = []

        f = flags.get(pid)
        if f is None:
            if kept <= STOP_LINE:
                conn.execute(
                    "INSERT OR REPLACE INTO check_exit_flags "
                    "(position_id, flag_kept_pct, flag_date, created_at) "
                    "VALUES (?,?,?,?)", (pid, kept, sess, now_iso))
                tags.append(("STOP?", "yellow"))
        else:
            base = float(f["flag_kept_pct"])
            if f["flag_date"] == sess:
                tags.append(("STOP?", "yellow"))
            elif kept <= base - DEAD_BAND:
                tags.append(("CLOSE", "red"))
            elif kept > STOP_LINE:
                conn.execute("DELETE FROM check_exit_flags WHERE position_id=?", (pid,))
            else:
                tags.append(("STOP?", "yellow"))

        raw_tp = a.get("profit_target_pct")
        if raw_tp is None:
            target = DEFAULT_TP
        else:
            target = raw_tp * 100.0 if raw_tp <= 1.0 else raw_tp
        if kept >= target:
            tags.append(("TAKE-PROFIT", "green"))

        be_pct = None
        if fam in ("CSP", "CREDIT_SPREAD"):
            _sp, be_pct = _safe(cc._buffers_single_short, a, pos, legs,
                                default=(None, None))
        elif fam == "IC":
            ic = _safe(cc._ic_tested, a, pos, legs)
            be_pct = ic[2] if ic else None
        if be_pct is not None and be_pct < 0:
            tags.append(("PAST B/E", "red"))

        if fam == "CSP":
            sp, _be = _safe(cc._buffers_single_short, a, pos, legs,
                            default=(None, None))
            extr = _safe(cc._extrinsic, a)
            if (sp is not None and sp < 0 and extr is not None
                    and extr <= ASSIGN_EXTR_MAX
                    and dte is not None and dte <= ASSIGN_DTE_MAX):
                tags.append(("ASSIGN?", "magenta"))

        prior = conn.execute(
            "SELECT kept_pct FROM check_kept_hist "
            "WHERE position_id=? AND session_date<? "
            "ORDER BY session_date DESC LIMIT 1", (pid, sess)).fetchone()
        dstr = ""
        dmove = None
        if prior is not None:
            dmove = kept - float(prior["kept_pct"])
            if abs(dmove) >= 0.5:
                arrow, col = ("\u25b2", "green") if dmove > 0 else ("\u25bc", "red")
                dstr = f" [{col}]{arrow}{abs(dmove):.0f}[/{col}]"
        conn.execute(
            "INSERT OR REPLACE INTO check_kept_hist (position_id, session_date, kept_pct) "
            "VALUES (?,?,?)", (pid, sess, kept))

        if tags or (dmove is not None and abs(dmove) >= MOVER_MIN):
            out.append({"ticker": info["ticker"], "kept": kept,
                        "dstr": dstr, "tags": tags})

    for pid in [p for p in flags if p not in live]:
        conn.execute("DELETE FROM check_exit_flags WHERE position_id=?", (pid,))
    cutoff = (_dt.date.today() - _dt.timedelta(days=60)).isoformat()
    conn.execute("DELETE FROM check_kept_hist WHERE session_date < ?", (cutoff,))

    conn.commit()
    conn.close()
    return out


def _rank(item):
    names = [t[0] for t in item["tags"]]
    return (0 if "CLOSE" in names else 1 if "STOP?" in names else
            2 if "ASSIGN?" in names else 3, item["ticker"] or "")


def run_exit_monitor(rows, console, market_open: bool):
    try:
        items = evaluate(rows, market_open)
    except Exception as e:
        console.print(f"[dim]exit highlights skipped: {e}[/dim]")
        return
    if not items:
        return

    # HELM-074 makes the shared `console` monochrome (no_color=True), which
    # strips all markup. Render THIS panel through a dedicated colour console so
    # the highlights box stands out against the monochrome data tables.
    from rich.console import Console as _Console
    from rich.panel import Panel
    console = _Console()
    urgent = any("CLOSE" in [t[0] for t in it["tags"]] for it in items)
    lines = []
    for it in sorted(items, key=_rank):
        chips = " ".join(f"[{col}]{name}[/{col}]" for name, col in it["tags"]) or "[dim]-[/dim]"
        tp = any(t[0] == "TAKE-PROFIT" for t in it["tags"])
        tcol = "green" if tp else "orange1"
        tick = f"[{tcol}]{(it['ticker'] or ''):<6}[/{tcol}]"
        lines.append(f" {tick} {it['kept']:+.0f}%{it['dstr']}   {chips}")
    console.print(Panel.fit(
        "\n".join(lines),
        title="[bold]exit highlights[/bold]  [dim]short premium - REAL - informational[/dim]",
        subtitle="[dim]STOP? · TAKE-PROFIT · ASSIGN? · PAST B/E · (delta) vs last session[/dim]",
        border_style="red" if urgent else "yellow"))
    console.print()


# --- row colouring for the main tables (appended) ---------------------------
_ANSI = {"orange1": "\x1b[38;5;214m", "green": "\x1b[32m"}
_RESET = "\x1b[0m"


def ticker_colors(items):
    out = {}
    for it in items:
        names = [t[0] for t in it["tags"]]
        col = "green" if "TAKE-PROFIT" in names else "orange1"
        if out.get(it["ticker"]) == "green":
            continue
        out[it["ticker"]] = col
    return out


def colorize_group(text, group_tickers, tcolors):
    lines, state = [], None
    for line in text.split("\n"):
        stripped = line.lstrip()
        first = stripped.split(" ", 1)[0] if stripped else ""
        if first in group_tickers:
            state = tcolors.get(first)
        elif stripped == "":
            state = None
        if state:
            lines.append(_ANSI[state] + line + _RESET)
        else:
            lines.append(line)
    return "\n".join(lines)


def render_panel(items):
    if not items:
        return
    from rich.console import Console as _Console
    from rich.panel import Panel
    console = _Console()
    urgent = any("CLOSE" in [t[0] for t in it["tags"]] for it in items)
    lines = []
    for it in sorted(items, key=_rank):
        chips = " ".join(f"[{col}]{name}[/{col}]" for name, col in it["tags"]) or "[dim]-[/dim]"
        tp = any(t[0] == "TAKE-PROFIT" for t in it["tags"])
        tcol = "green" if tp else "orange1"
        tick = f"[{tcol}]{(it['ticker'] or ''):<6}[/{tcol}]"
        lines.append(f" {tick} {it['kept']:+.0f}%{it['dstr']}   {chips}")
    console.print(Panel.fit(
        "\n".join(lines),
        title="[bold]exit highlights[/bold]  [dim]short premium - REAL - informational[/dim]",
        subtitle="[dim]STOP? - TAKE-PROFIT - ASSIGN? - PAST B/E - (delta) vs last session[/dim]",
        border_style="red" if urgent else "yellow"))
    console.print()


# --- 21-DTE yellow tier (appended) ------------------------------------------
_ANSI["yellow"] = "\x1b[38;5;226m"
DTE_MARK = 21


def row_colors(items, rows):
    """Box colours (green/orange) plus a yellow tier for premium REAL positions
    inside the 21-DTE gamma zone that carry no stronger signal."""
    out = ticker_colors(items)
    for r in rows:
        pos = r.get("pos") or {}
        if pos.get("book") != REAL_BOOK or r.get("family") not in PREMIUM_FAMILIES:
            continue
        tk = pos.get("ticker")
        if tk in out:
            continue
        dte = r.get("_dte")
        if dte is not None and dte <= DTE_MARK:
            out[tk] = "yellow"
    return out
