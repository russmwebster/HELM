#!/usr/bin/env python3
"""
HELM core_v1 cull  —  HELM-005 watchlist re-cull + paper clean slate.

Sets the *active* scanning universe to a deliberate 65-name set, tags it
`core_v1` for provenance, benches everything else, and soft-voids the 14 open
PAPER positions so the corpus starts fresh. REAL (live) positions are never
touched -- the void predicate is `book='PAPER'` only.

Discipline: dry-run default | WAL-safe backup | /tmp validation | anchor
asserts | single transaction | readback.

Run from the repo root:
    python patch_core_v1.py            # dry-run: validates on a /tmp copy, writes nothing
    python patch_core_v1.py --apply    # backup -> validate -> commit live, one transaction

Precondition: the 12 new names must already be in the watchlist AND optionable
(i.e. `helm watchlist add ...` then `helm screen ...` run first). The dry-run
aborts loudly if they are not, telling you exactly which are missing.
"""
import sqlite3, sys, os, tempfile
from datetime import datetime, timezone

DB = "data/helm.db"
APPLY = "--apply" in sys.argv

# --- the deliberate active universe (53 existing + 12 net-new = 65) -----------
EXISTING_53 = [
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "AVGO", "ORCL", "TSLA",
    "AMD", "MU", "QCOM", "AMAT", "LRCX",
    "NOW", "CRM", "CSCO",
    "ABT", "JNJ", "ISRG", "MRK", "UNH", "LLY", "PFE",
    "GS", "MS", "JPM", "MA", "BX", "BAC", "V",
    "XOM", "CVX", "EQT",
    "HON", "GE", "CAT", "RTX", "BA",
    "PEP", "MCD", "KO", "PG", "COST",
    "HD", "NKE", "SBUX",
    "SO", "VST", "NEE",
    "FCX", "LIN", "WELL",
]
NEW_12 = ["DHI", "PNC", "DAL", "FDX", "DE", "GM", "SLB", "NEM", "NUE", "TGT", "DG", "O"]
TARGET = EXISTING_53 + NEW_12
assert len(TARGET) == 65, f"TARGET should be 65, is {len(TARGET)}"
assert len(set(TARGET)) == 65, "duplicate ticker in TARGET"

NOW = datetime.now(timezone.utc).isoformat()
PH = ",".join("?" * len(TARGET))


def run_ops(conn):
    """All writes in one place; caller controls commit/rollback. Returns readback."""
    cur = conn.cursor()

    # ---- anchor asserts (pre-write) ----
    present = {r[0] for r in cur.execute(
        f"SELECT ticker FROM watchlist WHERE ticker IN ({PH})", TARGET)}
    missing = [t for t in TARGET if t not in present]
    assert not missing, (
        "ABORT: not in watchlist -- run `helm watchlist add` + `helm screen` "
        f"on these first: {missing}")

    nonopt = [r[0] for r in cur.execute(
        f"SELECT ticker FROM watchlist WHERE ticker IN ({PH}) AND is_optionable < 1",
        TARGET)]
    assert not nonopt, f"ABORT: not optionable -- run `helm screen` on: {nonopt}"

    real_before = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE book='REAL'").fetchone()[0]
    real_open_before = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE book='REAL' AND status='OPEN'").fetchone()[0]
    paper_open = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE book='PAPER' AND status='OPEN'").fetchone()[0]
    assert paper_open == 14, f"ABORT: expected 14 open PAPER positions, found {paper_open}"

    # ---- writes ----
    cur.execute("UPDATE watchlist SET active=0")                                  # bench all
    cur.execute(f"UPDATE watchlist SET active=1 WHERE ticker IN ({PH})", TARGET)  # activate 65
    cur.execute(f"UPDATE watchlist SET build='core_v1' WHERE ticker IN ({PH})", TARGET)
    cur.execute(
        "UPDATE positions SET status='CLOSED', closed_at=?, exit_reason='MANUAL' "
        "WHERE book='PAPER' AND status='OPEN'", (NOW,))                            # void paper

    # ---- readback asserts (post-write, pre-commit) ----
    active = cur.execute("SELECT COUNT(*) FROM watchlist WHERE active=1").fetchone()[0]
    tagged = cur.execute("SELECT COUNT(*) FROM watchlist WHERE build='core_v1'").fetchone()[0]
    real_after = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE book='REAL'").fetchone()[0]
    real_open_after = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE book='REAL' AND status='OPEN'").fetchone()[0]
    paper_open_after = cur.execute(
        "SELECT COUNT(*) FROM positions WHERE book='PAPER' AND status='OPEN'").fetchone()[0]

    assert active == 65, f"active={active}, expected 65"
    assert tagged == 65, f"core_v1 tagged={tagged}, expected 65"
    assert real_after == real_before, f"REAL count changed {real_before}->{real_after}!"
    assert real_open_after == real_open_before, (
        f"REAL open changed {real_open_before}->{real_open_after}!")
    assert paper_open_after == 0, f"PAPER open={paper_open_after}, expected 0"

    return dict(active=active, core_v1=tagged, real=real_after, real_open=real_open_after,
                paper_open=paper_open_after, paper_voided=paper_open)


def snapshot(src_path, dst_path):
    """WAL-safe copy via the sqlite backup API."""
    s = sqlite3.connect(src_path)
    d = sqlite3.connect(dst_path)
    s.backup(d)
    s.close()
    d.close()


def main():
    if not os.path.exists(DB):
        sys.exit(f"DB not found at {DB} -- run from the repo root.")

    # 1) validate on a WAL-safe /tmp copy (always, even in dry-run)
    tmp = os.path.join(tempfile.gettempdir(), "helm_core_v1_validate.db")
    snapshot(DB, tmp)
    vconn = sqlite3.connect(tmp)
    try:
        res = run_ops(vconn)
        vconn.rollback()  # discard -- validation only
    finally:
        vconn.close()
        try:
            os.remove(tmp)
        except OSError:
            pass
    print("[validate on /tmp copy] PASS:", res)

    if not APPLY:
        print("\nDRY-RUN only -- no live changes. Re-run with --apply to commit.")
        return

    # 2) WAL-safe timestamped backup of the live DB
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    bak = f"{DB}.bak.core_v1_{stamp}"
    snapshot(DB, bak)
    print(f"[backup] {bak}")

    # 3) live write, single transaction (commits on success, rolls back on error)
    live = sqlite3.connect(DB)
    try:
        with live:
            res = run_ops(live)
    finally:
        live.close()
    print("[LIVE APPLIED] PASS:", res)
    print("\nNext: Monday (RTH) run `helm ivr refresh` to backfill IVR on the 12 new names.")


if __name__ == "__main__":
    main()
