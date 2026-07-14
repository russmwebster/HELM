# helm/ownership.py
# HELM — Ownership Quality
# "If this CSP is assigned, do I want to wake up owning the underlying?"
#
# Grades a ticker A-F on business quality, SURVIVAL-WEIGHTED for the assign/bail
# fork at 21 DTE / underwater. cash_quality and balance_sheet_safety are hard
# gates: a failure caps the grade no matter how good everything else looks.
# Valuation is deliberately excluded — on assignment you get the shares at the
# strike regardless of "fair value". This scores the BUSINESS, not the price.
#
# Design:
#   - score(Fundamentals) -> dict is a PURE function (no I/O). Test in isolation.
#   - fetch_yf_fundamentals(ticker) is the default provider (yfinance, already a
#     HELM dependency via check_cmd.fetch_yf_data). Swap for IBKR/cache later.
#   - get_ownership_grade(ticker) is the read helper `helm check` (_render_csp)
#     will call in Phase 3. Phase 1: always recomputes live, records to cache.
#
# Standalone:  python3 -m helm.ownership IONQ RKLB MSFT KO [--json]
# Firewall:    read-only compute + an idempotent upsert into ownership_quality.
from __future__ import annotations

import sys
import json
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

# --------------------------------------------------------------------------- #
# CONFIG — tuning knobs (candidate to lift into strategy_settings/config later)
# --------------------------------------------------------------------------- #
CONFIG = {
    "weights": {
        "returns_on_capital":   0.25,
        "moat":                 0.15,
        "cash_quality":         0.20,
        "growth":               0.10,
        "balance_sheet_safety": 0.20,
        "capital_discipline":   0.10,
    },
    "roic_great": 15.0, "roic_ok": 10.0, "roic_weak": 5.0,
    "op_margin_great": 20.0, "op_margin_ok": 12.0, "op_margin_weak": 5.0,
    "fcf_conv_great": 0.90, "fcf_conv_ok": 0.60,
    "netdebt_ebitda_safe": 1.5, "netdebt_ebitda_stretch": 3.0, "netdebt_ebitda_danger": 4.5,
    "int_cov_safe": 8.0, "int_cov_ok": 3.0, "int_cov_danger": 1.5,
    "current_ratio_ok": 1.2,
    "cash_runway_years_min": 2.0,
    "growth_great": 12.0, "growth_ok": 5.0,
    "gate_fail_cap": 32.0,
    "gate_warn_cap": 50.0,
    "grade_cuts": [("A", 85), ("B", 70), ("C", 55), ("D", 40), ("F", 0)],
}

# DDL for the Phase-1 cache table (mirrors iv_history conventions). Kept here so
# the migration script and schema.sql stay in sync with one source of truth.
SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS ownership_quality (
    ticker        TEXT NOT NULL,
    date          TEXT NOT NULL,                       -- as-of date (YYYY-MM-DD)
    grade         TEXT NOT NULL,                       -- A-F
    composite     REAL,
    lean          TEXT,
    gates_failed  TEXT,                                -- comma-separated theme names
    confidence    TEXT,                                -- high|medium|low
    themes_json   TEXT,                                -- full breakdown, for drill-down
    source        TEXT DEFAULT 'yfinance',
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (ticker, date)
);
"""


# --------------------------------------------------------------------------- #
# Data model — provider-agnostic. All series are annual, NEWEST FIRST.
# --------------------------------------------------------------------------- #
@dataclass
class Fundamentals:
    ticker: str
    name: str = ""
    sector: str = ""
    currency: str = "USD"
    revenue: list = field(default_factory=list)
    ebit: list = field(default_factory=list)                # operating income
    net_income: list = field(default_factory=list)
    gross_profit: list = field(default_factory=list)
    operating_cash_flow: list = field(default_factory=list)
    capex: list = field(default_factory=list)               # positive magnitude
    shares_diluted: list = field(default_factory=list)
    total_debt: Optional[float] = None
    cash: Optional[float] = None
    total_equity: Optional[float] = None
    ebitda: Optional[float] = None
    interest_expense: Optional[float] = None                # positive magnitude
    current_assets: Optional[float] = None
    current_liabilities: Optional[float] = None
    tax_rate: Optional[float] = None                        # effective, 0-1


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _f(x):
    try:
        if x is None:
            return None
        v = float(x)
        return None if v != v else v
    except (TypeError, ValueError):
        return None


def _first(series):
    for v in series or []:
        v = _f(v)
        if v is not None:
            return v
    return None


def _clean(series):
    return [v for v in (_f(x) for x in (series or [])) if v is not None]


def _lerp(x, lo, hi, out_lo=0.0, out_hi=100.0):
    if hi == lo:
        return out_hi if x >= hi else out_lo
    t = max(0.0, min(1.0, (x - lo) / (hi - lo)))
    return out_lo + t * (out_hi - out_lo)


def _cagr(series):
    s = _clean(series)
    if len(s) < 2:
        return None
    newest, oldest = s[0], s[-1]
    n = len(s) - 1
    if oldest is None or oldest <= 0 or newest <= 0:
        return None
    return ((newest / oldest) ** (1.0 / n) - 1.0) * 100.0


# --------------------------------------------------------------------------- #
# THEME SCORERS — each returns (subscore 0-100, detail dict, gate_status)
# gate_status: None | "pass" | "warn" | "fail"
# --------------------------------------------------------------------------- #
def score_returns_on_capital(f: Fundamentals):
    C = CONFIG
    tax = f.tax_rate if f.tax_rate is not None else 0.21
    equity = _f(f.total_equity) or 0.0
    debt = _f(f.total_debt) or 0.0
    cash = _f(f.cash) or 0.0
    invested = debt + equity - cash
    roics = []
    if invested and invested > 0:
        for e in _clean(f.ebit):
            roics.append(e * (1 - tax) / invested * 100.0)
    latest = roics[0] if roics else None
    avg = statistics.mean(roics) if roics else None
    detail = {"roic_latest_pct": round(latest, 1) if latest is not None else None,
              "roic_avg_pct": round(avg, 1) if avg is not None else None,
              "invested_capital": invested}
    if latest is None:
        return 40.0, detail, None
    base = _lerp(latest, C["roic_weak"], C["roic_great"], 20, 95)
    if len(roics) >= 3:
        cv = statistics.pstdev(roics) / (abs(avg) + 1e-9)
        base += _lerp(cv, 0.2, 0.8, 5, -10)
    return max(0.0, min(100.0, base)), detail, None


def score_moat(f: Fundamentals):
    C = CONFIG
    rev, op, gp = _clean(f.revenue), _clean(f.ebit), _clean(f.gross_profit)
    op_margins = [o / r * 100 for o, r in zip(op, rev) if r]
    gross_margins = [g / r * 100 for g, r in zip(gp, rev) if r]
    om = op_margins[0] if op_margins else None
    detail = {"op_margin_pct": round(om, 1) if om is not None else None,
              "gross_margin_pct": round(gross_margins[0], 1) if gross_margins else None}
    if om is None:
        return 40.0, detail, None
    base = _lerp(om, C["op_margin_weak"], C["op_margin_great"], 20, 90)
    if len(op_margins) >= 3:
        sd = statistics.pstdev(op_margins)
        detail["op_margin_stdev"] = round(sd, 1)
        base += _lerp(sd, 3.0, 12.0, 8, -12)
    return max(0.0, min(100.0, base)), detail, None


def score_cash_quality(f: Fundamentals):
    """GATE: are earnings real cash? persistent burn while unprofitable = fail."""
    C = CONFIG
    ocf, capex, ni = _clean(f.operating_cash_flow), _clean(f.capex), _clean(f.net_income)
    fcfs = [o - c for o, c in zip(ocf, capex)]
    fcf = fcfs[0] if fcfs else None
    conv = None
    if fcf is not None and ni and ni[0] and ni[0] > 0:
        conv = fcf / ni[0]
    neg_fcf_years = sum(1 for v in fcfs if v < 0)
    detail = {"fcf_latest": fcf, "fcf_conversion": round(conv, 2) if conv is not None else None,
              "negative_fcf_years": neg_fcf_years, "fcf_years_available": len(fcfs)}
    gate = "pass"
    if fcf is not None and fcf < 0 and (not ni or ni[0] < 0):
        gate = "fail"
    elif neg_fcf_years >= 2 and len(fcfs) >= 3:
        gate = "warn"
    if fcf is None:
        return 40.0, detail, "warn"
    if fcf < 0:
        base = _lerp(fcf, 0, -abs(fcf) - 1, 25, 5)
    elif conv is not None:
        base = _lerp(conv, C["fcf_conv_ok"], C["fcf_conv_great"], 50, 92)
    else:
        base = 60.0
    return max(0.0, min(100.0, base)), detail, gate


def score_growth(f: Fundamentals):
    """QMJ-style GROWTH-IN-PROFITABILITY (operating income, fallback net income),
    not revenue — so growth off a tiny/negative base can't flatter a money-loser.
    Sign-aware: deepening losses score low; turning profitable scores high."""
    C = CONFIG
    ebit = _clean(f.ebit)
    prof = ebit if len(ebit) >= 2 else _clean(f.net_income)
    basis = "operating_income" if len(ebit) >= 2 else "net_income"

    if len(prof) < 2:
        # last resort: revenue, only if no profit series at all
        g = _cagr(f.revenue)
        detail = {"basis": "revenue(fallback)", "growth_pct": round(g, 1) if g is not None else None}
        if g is None:
            return 50.0, detail, None
        base = _lerp(g, -15, 0, 10, 35) if g < 0 else _lerp(g, 0.0, C["growth_great"], 35, 90)
        return max(0.0, min(100.0, base)), detail, None

    latest, oldest, n = prof[0], prof[-1], len(prof) - 1
    detail = {"basis": basis, "profit_latest": latest, "profit_oldest": oldest}

    if latest <= 0:
        # not profitable now — small credit only if losses are narrowing
        if oldest < 0 and latest > oldest:
            detail["note"] = "losses narrowing"; base = 30.0
        elif oldest < 0 and latest <= oldest:
            detail["note"] = "losses deepening"; base = 12.0
        else:
            detail["note"] = "turned unprofitable"; base = 18.0
        return base, detail, None

    if oldest <= 0:
        # crossed into profitability over the window — genuinely strong
        detail["note"] = "turned profitable"
        return 85.0, detail, None

    cagr = ((latest / oldest) ** (1.0 / n) - 1.0) * 100.0
    detail["profit_cagr_pct"] = round(cagr, 1)
    base = _lerp(cagr, -10, 0, 15, 40) if cagr < 0 else _lerp(cagr, 0.0, C["growth_great"], 40, 92)
    return max(0.0, min(100.0, base)), detail, None


def score_balance_sheet_safety(f: Fundamentals):
    """GATE: can it survive being owned through a drawdown?"""
    C = CONFIG
    debt = _f(f.total_debt) or 0.0
    cash = _f(f.cash) or 0.0
    equity = _f(f.total_equity)
    ebitda = _f(f.ebitda)
    ebit = _first(f.ebit)
    interest = _f(f.interest_expense)
    ca, cl = _f(f.current_assets), _f(f.current_liabilities)
    net_debt = debt - cash
    nd_ebitda = (net_debt / ebitda) if (ebitda and ebitda > 0) else None
    int_cov = (ebit / interest) if (ebit is not None and interest and interest > 0) else None
    current_ratio = (ca / cl) if (ca and cl) else None
    ocf, capex = _clean(f.operating_cash_flow), _clean(f.capex)
    fcf = (ocf[0] - capex[0]) if (ocf and capex) else None
    runway = (cash / abs(fcf)) if (fcf is not None and fcf < 0 and cash) else None

    detail = {"net_debt_to_ebitda": round(nd_ebitda, 2) if nd_ebitda is not None else None,
              "interest_coverage": round(int_cov, 1) if int_cov is not None else None,
              "current_ratio": round(current_ratio, 2) if current_ratio is not None else None,
              "cash_runway_years": round(runway, 1) if runway is not None else None,
              "negative_equity": (equity is not None and equity < 0)}

    gate = "pass"
    # For a profitable, interest-bearing business, coverage is the survival tell.
    if int_cov is not None and ebit is not None and ebit > 0 and int_cov < C["int_cov_danger"]:
        gate = "fail"
    if nd_ebitda is not None and nd_ebitda > C["netdebt_ebitda_danger"]:
        gate = "fail" if gate == "fail" else "warn"
    if equity is not None and equity < 0:
        gate = "fail"
    if runway is not None and runway < C["cash_runway_years_min"]:
        gate = "fail"   # cash burner with < min years of runway

    parts = []
    if nd_ebitda is not None:
        parts.append(_lerp(nd_ebitda, C["netdebt_ebitda_danger"], C["netdebt_ebitda_safe"], 15, 92))
    elif net_debt <= 0:
        parts.append(90.0)
    if int_cov is not None and ebit is not None and ebit > 0:
        parts.append(_lerp(int_cov, C["int_cov_danger"], C["int_cov_safe"], 15, 92))
    if current_ratio is not None:
        parts.append(_lerp(current_ratio, 0.8, 2.0, 30, 85))
    if runway is not None:
        parts.append(_lerp(runway, 0.5, 4.0, 10, 70))
    base = statistics.mean(parts) if parts else 50.0
    return max(0.0, min(100.0, base)), detail, gate


def score_capital_discipline(f: Fundamentals):
    """dilution vs buyback: rising share count is a quiet tax on owners."""
    sh = _clean(f.shares_diluted)
    detail = {}
    if len(sh) >= 2 and sh[-1]:
        chg = (sh[0] / sh[-1] - 1.0) * 100.0 / (len(sh) - 1)
        detail["share_count_cagr_pct"] = round(chg, 1)
        base = _lerp(chg, 5.0, -3.0, 25, 85)   # buyback good, dilution bad
        return max(0.0, min(100.0, base)), detail, None
    detail["share_count_cagr_pct"] = None
    return 55.0, detail, None


THEME_FUNCS = {
    "returns_on_capital": score_returns_on_capital,
    "moat": score_moat,
    "cash_quality": score_cash_quality,
    "growth": score_growth,
    "balance_sheet_safety": score_balance_sheet_safety,
    "capital_discipline": score_capital_discipline,
}


# --------------------------------------------------------------------------- #
# aggregation (pure)
# --------------------------------------------------------------------------- #
def _grade(comp):
    for letter, cut in CONFIG["grade_cuts"]:
        if comp >= cut:
            return letter
    return "F"


def _lean(grade, gate_fail):
    if gate_fail:
        return "BAIL — fragile underlying; treat as premium-only, avoid assignment."
    if grade in ("A", "B"):
        return "OWNABLE — assignment acceptable; candidate to hold & wheel."
    if grade == "C":
        return "MARGINAL — assignment tolerable only with a defined exit plan."
    return "LEAN BAIL — low quality; prefer to close before owning it."


def score(f: Fundamentals) -> dict:
    W = CONFIG["weights"]
    themes, gate_fail, gate_warn = {}, [], []
    weighted, wsum = 0.0, 0.0
    for name, fn in THEME_FUNCS.items():
        sub, detail, gate = fn(f)
        themes[name] = {"score": round(sub, 1), "gate": gate, **detail}
        w = W[name]
        weighted += sub * w
        wsum += w
        if gate == "fail":
            gate_fail.append(name)
        elif gate == "warn":
            gate_warn.append(name)
    comp = weighted / wsum if wsum else 0.0
    if gate_fail:
        comp = min(comp, CONFIG["gate_fail_cap"])
    elif gate_warn:
        comp = min(comp, CONFIG["gate_warn_cap"])

    present = sum(1 for s in (f.revenue, f.ebit, f.net_income, f.operating_cash_flow) if _clean(s))
    present += sum(1 for v in (f.total_debt, f.total_equity, f.ebitda) if _f(v) is not None)
    confidence = "high" if present >= 6 else "medium" if present >= 4 else "low"

    grade = _grade(comp)
    return {
        "ticker": f.ticker,
        "name": f.name,
        "sector": f.sector,
        "grade": grade,
        "composite": round(comp, 1),
        "lean": _lean(grade, gate_fail),
        "gates_failed": gate_fail,
        "gates_warned": gate_warn,
        "confidence": confidence,
        "themes": themes,
    }


# --------------------------------------------------------------------------- #
# DATA PROVIDER — yfinance (default). HELM can swap in IBKR/cache later.
# --------------------------------------------------------------------------- #
def fetch_yf_fundamentals(ticker: str) -> Fundamentals:
    import yfinance as yf

    def row(df, *names):
        if df is None or getattr(df, "empty", True):
            return []
        for n in names:
            if n in df.index:
                return [None if (v is None or v != v) else float(v) for v in df.loc[n].values]
        return []

    def latest(df, *names):
        vals = row(df, *names)
        return vals[0] if vals else None

    t = yf.Ticker(ticker)
    inc, bs, cf = t.financials, t.balance_sheet, t.cashflow
    try:
        info = t.info or {}
    except Exception:
        info = {}

    capex_raw = row(cf, "Capital Expenditure", "Capital Expenditures")
    capex_raw = [abs(v) if v is not None else None for v in capex_raw]
    return Fundamentals(
        ticker=ticker.upper(),
        name=info.get("longName", ""),
        sector=info.get("sector", ""),
        currency=info.get("currency", "USD"),
        revenue=row(inc, "Total Revenue"),
        ebit=row(inc, "Operating Income", "EBIT"),
        net_income=row(inc, "Net Income", "Net Income Common Stockholders"),
        gross_profit=row(inc, "Gross Profit"),
        operating_cash_flow=row(cf, "Operating Cash Flow", "Total Cash From Operating Activities"),
        capex=capex_raw,
        shares_diluted=row(inc, "Diluted Average Shares", "Basic Average Shares"),
        total_debt=latest(bs, "Total Debt"),
        cash=latest(bs, "Cash And Cash Equivalents",
                    "Cash Cash Equivalents And Short Term Investments"),
        total_equity=latest(bs, "Stockholders Equity", "Total Stockholder Equity"),
        ebitda=latest(inc, "EBITDA", "Normalized EBITDA"),
        interest_expense=(abs(latest(inc, "Interest Expense") or 0) or None),
        current_assets=latest(bs, "Current Assets", "Total Current Assets"),
        current_liabilities=latest(bs, "Current Liabilities", "Total Current Liabilities"),
        tax_rate=info.get("effectiveTaxRate"),
    )


# --------------------------------------------------------------------------- #
# CACHE upsert (guarded: no-op if the table isn't there yet)
# --------------------------------------------------------------------------- #
def record_grade(result: dict, db_path=None) -> bool:
    """Idempotent upsert into ownership_quality. Returns True if written.
    Safe to call before the migration lands — silently skips if no table/db."""
    try:
        from helm.db import get_conn, table_exists
    except Exception:
        return False
    try:
        if not table_exists("ownership_quality", **({"db_path": db_path} if db_path else {})):
            return False
        conn = get_conn(db_path) if db_path else get_conn()
        with conn:
            conn.execute(
                """INSERT INTO ownership_quality
                   (ticker, date, grade, composite, lean, gates_failed,
                    confidence, themes_json, source, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?, datetime('now'))
                   ON CONFLICT(ticker, date) DO UPDATE SET
                     grade=excluded.grade, composite=excluded.composite,
                     lean=excluded.lean, gates_failed=excluded.gates_failed,
                     confidence=excluded.confidence, themes_json=excluded.themes_json,
                     source=excluded.source, updated_at=datetime('now')""",
                (result["ticker"], date.today().isoformat(), result["grade"],
                 result["composite"], result["lean"], ",".join(result["gates_failed"]),
                 result["confidence"], json.dumps(result["themes"]), "yfinance"),
            )
        return True
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# CACHE READER — fast lookup for `helm check` (_render_csp). No recompute.
# --------------------------------------------------------------------------- #
def read_cached_grade(ticker: str, db_path=None, max_age_days=None):
    """Return the latest cached ownership row for a ticker, or None.
    Pure read — never fetches or recomputes. Safe if the table is absent."""
    try:
        from helm.db import get_conn, table_exists
    except Exception:
        return None
    try:
        if db_path is not None:
            if not table_exists("ownership_quality", db_path=db_path):
                return None
            conn = get_conn(db_path)
        else:
            if not table_exists("ownership_quality"):
                return None
            conn = get_conn()
        try:
            row = conn.execute(
                "SELECT ticker, grade, composite, lean, gates_failed, confidence, "
                "date, updated_at FROM ownership_quality WHERE ticker=? "
                "ORDER BY date DESC, updated_at DESC LIMIT 1",
                (ticker.upper(),)).fetchone()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        if not row:
            return None
        d = dict(row)
        if max_age_days is not None and d.get("date"):
            try:
                asof = date.fromisoformat(str(d["date"])[:10])
                if (date.today() - asof).days > max_age_days:
                    d["stale"] = True
            except Exception:
                pass
        return d
    except Exception:
        return None


# --------------------------------------------------------------------------- #
# READ HELPER — recompute live + record to cache (used by `helm quality`).
# --------------------------------------------------------------------------- #
def get_ownership_grade(ticker: str, db_path=None, recompute: bool = True) -> dict:
    """Return the ownership-quality result dict for a ticker.
    Phase 1 policy = recompute live every call (max_age caching lands in Phase 3)."""
    f = fetch_yf_fundamentals(ticker)
    result = score(f)
    record_grade(result, db_path=db_path)
    return result


# --------------------------------------------------------------------------- #
# rendering (plain; the Rich cell for _render_csp comes in Phase 3)
# --------------------------------------------------------------------------- #
def render(res: dict) -> str:
    L = [f"{res['ticker']}  {res['name']}".strip(),
         f"  Ownership Quality: {res['grade']}  "
         f"(composite {res['composite']}/100, confidence {res['confidence']})",
         f"  -> {res['lean']}"]
    if res["gates_failed"]:
        L.append(f"  !! GATE FAILED: {', '.join(res['gates_failed'])}")
    L.append("  themes:")
    for name, d in res["themes"].items():
        gate = f" [{d['gate']}]" if d.get("gate") else ""
        extras = {k: v for k, v in d.items() if k not in ("score", "gate")}
        L.append(f"    {name:22s} {d['score']:5.1f}{gate}  {extras}")
    return "\n".join(L)


# --------------------------------------------------------------------------- #
# standalone entry — python3 -m helm.ownership TICKER [TICKER ...] [--json]
# --------------------------------------------------------------------------- #
def _main(argv):
    args = [a for a in argv[1:] if a != "--json"]
    as_json = "--json" in argv
    if not args:
        print("usage: python3 -m helm.ownership TICKER [TICKER ...] [--json]")
        return 0
    results = []
    for tk in args:
        try:
            results.append(score(fetch_yf_fundamentals(tk)))
        except Exception as e:
            results.append({"ticker": tk, "error": f"{type(e).__name__}: {e}"})
    if as_json:
        print(json.dumps(results, indent=2, default=str))
    else:
        for r in results:
            print(r["error"] if "error" in r else render(r) if "grade" in r else r)
            print()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
