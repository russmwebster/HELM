# s144 (2026-11-01) — the book's delta: study, market line, risk curve

**Study (read-only):**
- `delta_series.py DB spy_closes.csv out.json` rebuilds the book's beta-weighted delta at each day's last reading (15:59) from stored leg readings. It prints the positions and any that are incomplete.
- Checked against HELM's own figure: 10-30's five puts gave $140,304.
- Usable only from 10-21: before that, the s135 leg-readings loss left the diagonals incomplete.
- `spy_closes.csv` holds SPY daily closes from IBKR, 9-11 → 10-30.

**Harnesses:**
- `verify_board.py` starts the board as production does (`python3 app.py`, real engine, `HELM_DB` set to a fresh copy). It renders the pages, prints Today's market line and the curve table, and checks that every table is unchanged.
  - Needs `~/.helm_profile` holding the account id in the device VM.
  - Writes its copy to /tmp, because the VM's home disk is full.
- `advice_json_unchanged.py DIR` builds advice.json with the code in DIR. Run it once for the old code tree and once for the new, then compare; the two outputs were identical.

Engine self-test: `python3 -B -m helm.bookview --selftest` (32 checks). Four perturbations each failed: the curve without gamma, the typical day over √365, the break-even off by 100, stale never named.

`render-*.html` are rendered pages kept for viewing. They can go.
