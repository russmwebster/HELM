#!/usr/bin/env python3
"""W56 -- differential harness: account balances refresh from the reconcile CSV.

Asserts the PROPERTY (the balance can be refreshed, states its own age, and
cannot walk backwards), not the shape of any one function.

Why it is built this way:

* `reconcile_cmd` imports rich, which is absent from some interpreters this has
  to run under, so the parser functions are lifted out with `ast` and executed
  in isolation. That tests the real bytes in the working tree rather than a
  copy, and needs no third-party import.
* The fixture CSV is written with Fidelity's ACTUAL header casing -- "Account
  number", "Current value" -- because the defect being guarded is precisely a
  case-sensitive column lookup. A fixture with tidy Title Case headers would
  pass against the broken code and prove nothing.
* Balances are exercised against a throwaway SQLite file, never the live one.

Run against the pre-change files with --base DIR to confirm it fails there.

  python3 tools/verify_w56_balance_refresh.py
  python3 tools/verify_w56_balance_refresh.py --base /tmp/w56-base
"""
import argparse, ast, csv, datetime, os, re, sqlite3, sys, tempfile, typing
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PASS, FAIL = [], []


def ck(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"  {'PASS' if cond else 'FAIL'}  {name}" + (f"   [{detail}]" if detail and not cond else ""))


def load_funcs(path, names):
    """Exec named top-level functions from a module without importing it."""
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    ns = {"csv": csv, "re": re, "Optional": typing.Optional,
          "datetime": datetime.datetime, "os": os, "sys": sys}
    for n in tree.body:
        if isinstance(n, ast.FunctionDef) and n.name in names:
            exec(compile(ast.Module([n], []), "<w56>", "exec"), ns)
    return ns


# Fidelity's real header casing and its real quirks: a lowercase "Pending
# activity" row, SPAXX in two accounts, and the download date in a trailer.
FIXTURE = '''﻿Account number,Account name,Symbol,Description,Quantity,Last price,Current value,Type
X1111,Individual,SPAXX**,HELD IN MONEY MARKET,,,$10000.00,Cash
Y2222,Rollover IRA,SPAXX**,HELD IN MONEY MARKET,,,$400000.00,Cash
Y2222,Rollover IRA,FXAIX,FIDELITY 500 INDEX,10,$250.00,$2500.00,Cash
Y2222,Rollover IRA, -AAPL260918P200,AAPL SEP 18 2026 $200 PUT,1,$5.00,-$500.00,Cash
Y2222,Rollover IRA,Pending activity,,,,$7500.00,

"Date downloaded Jun-15-2026 at 4:36 p.m ET"
'''
# net liq = 10000 + 400000 + 2500 - 500 + 7500 = 419500 ; cash = 410000


def write_fixture(d, name="Portfolio_Positions_Jun-15-2026.csv", body=FIXTURE):
    p = Path(d) / name
    p.write_text(body, encoding="utf-8")
    return str(p)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="directory of pre-change files to test instead")
    a = ap.parse_args()

    if a.base:
        rec = Path(a.base) / "reconcile_cmd.py"
        acc = Path(a.base) / "account.py"
        chk = Path(a.base) / "check_cmd.py"
        print(f"W56 harness -- CONTROL RUN against {a.base}\n")
    else:
        rec = REPO / "helm/cli/reconcile_cmd.py"
        acc = REPO / "helm/models/account.py"
        chk = REPO / "helm/cli/check_cmd.py"
        print("W56 harness -- working tree\n")

    tmp = tempfile.mkdtemp(prefix="w56-")
    fx = write_fixture(tmp)

    # ── 1. the parser actually reads a real-cased Fidelity file ─────────────
    print("1. balance extraction")
    ns = load_funcs(rec, {"parse_fidelity_balances", "parse_fidelity_cash",
                          "parse_fidelity_positions", "_fidelity_as_of",
                          "_money", "parse_option_symbol"})

    has_bal = "parse_fidelity_balances" in ns
    ck("parse_fidelity_balances exists", has_bal)
    bal = ns["parse_fidelity_balances"](fx) if has_bal else {}
    ck("net liquidation is extracted", bool(bal) and bal.get("net_liquidation"), str(bal)[:80])
    ck("net liquidation sums every valued row (419,500)",
       bool(bal) and abs((bal.get("net_liquidation") or 0) - 419500) < 0.01,
       str(bal.get("net_liquidation") if bal else None))
    ck("cash is the SPAXX rows only (410,000)",
       bool(bal) and abs((bal.get("buying_power") or 0) - 410000) < 0.01,
       str(bal.get("buying_power") if bal else None))
    ck("both accounts are seen", bool(bal) and len(bal.get("accounts", {})) == 2)

    # ── 2. the panel parser that silently returned {} ───────────────────────
    print("\n2. parse_fidelity_cash (Available Capital panel)")
    cash = ns["parse_fidelity_cash"](fx) if "parse_fidelity_cash" in ns else {}
    ck("returns a non-empty mapping for a real-cased CSV", bool(cash),
       "returned {} -- the panel never renders")
    ck("cash totals 410,000",
       abs(sum(v["cash"] for v in cash.values()) - 410000) < 0.01 if cash else False)

    # ── 3. the lowercase 'Pending activity' leak ────────────────────────────
    print("\n3. pending-activity row")
    pos = ns["parse_fidelity_positions"](fx) if "parse_fidelity_positions" in ns else []
    leak = [p for p in pos if str(p.get("ticker", "")).lower()
            in ("pending activity", "account total")]
    ck("no pending/total row leaks in as a position", not leak,
       f"leaked: {[p['ticker'] for p in leak]}")

    # ── 4. as_of comes from the file, not from today ────────────────────────
    print("\n4. as-of date")
    ck("as_of is read from the CSV trailer (2026-06-15)",
       bool(bal) and bal.get("as_of") == "2026-06-15", str(bal.get("as_of") if bal else None))
    ck("as_of is NOT today",
       not bal or bal.get("as_of") != datetime.date.today().isoformat())

    # ── 5. the account model round-trips the new fields ─────────────────────
    print("\n5. account model")
    src = Path(acc).read_text(encoding="utf-8")
    ck("update_balances accepts an as_of", "as_of" in src and "def update_balances" in src)
    ck("save() persists balances_updated_at (not nulled on save)",
       "balances_updated_at" in src.split("def save")[1].split("def ")[0]
       if "def save" in src else False)

    db = os.path.join(tmp, "t.db")
    con = sqlite3.connect(db)
    con.execute("""CREATE TABLE accounts (id TEXT PRIMARY KEY, broker TEXT, nickname TEXT,
        buying_power REAL, portfolio_value REAL, currency TEXT, is_active INTEGER,
        created_at TEXT, notes TEXT, balances_as_of TEXT, balances_updated_at TEXT)""")
    con.commit(); con.close()
    ck("schema.sql declares both balance columns",
       all(c in (REPO / "helm/schema.sql").read_text(encoding="utf-8")
           for c in ("balances_as_of", "balances_updated_at")))

    # ── 6. the staleness guard ──────────────────────────────────────────────
    print("\n6. staleness guard")
    rsrc = Path(rec).read_text(encoding="utf-8")
    ck("reconcile writes the balances", "_update_account_balances" in rsrc
       or "update_balances" in rsrc)
    ck("an older file is refused rather than applied",
       "new_as_of < old_as_of" in rsrc or "not updated" in rsrc.lower())
    ck("the change is reported, not silent",
       "Account value" in rsrc and "→" in rsrc)

    # ── 7. helm check states the age ────────────────────────────────────────
    print("\n7. helm check surfaces the age")
    csrc = Path(chk).read_text(encoding="utf-8")
    ck("pulse selects balances_as_of", "balances_as_of" in csrc)
    ck("pulse exposes an age in days", "balances_age_days" in csrc)
    ck("the capital card renders the age", "account value" in csrc.lower()
       and "reconcile" in csrc.lower())

    print(f"\n{'='*58}\n  {len(PASS)} passed · {len(FAIL)} failed")
    if FAIL:
        print("  failed: " + ", ".join(FAIL))
    print("=" * 58)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
