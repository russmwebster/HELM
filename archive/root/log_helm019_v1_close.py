#!/usr/bin/env python3
"""
log_helm019_v1_close.py — close-out log for HELM-019 v1 + v1.1, plus the two
hygiene findings (HELM-020). Run from the repo root.

  - Resolved-log line: HELM-019 v1 + v1.1 shipped (2026-06-19, s24).
  - HELM-019 entry: annotate v1+v1.1 done; remaining = HELM-vs-Fidelity reconcile.
  - HELM-020 (new, Bugs): hardcoded "HON" label + dead divergent generate_guidance.
  - Stamp date -> 2026-06-19 (still s24).

Sentinel-guarded, anchor-asserted (each once), timestamped backup, DRY-RUN default.
    python log_helm019_v1_close.py
    python log_helm019_v1_close.py --apply
"""
import sys, os, time, difflib

PATH = "ISSUES.md"
SENTINEL = "HELM-020"

EDITS = [
    # stamp date (keep s24)
    (
        "_Last updated: 2026-06-18 (s24)._",
        "_Last updated: 2026-06-19 (s24)._",
    ),
    # new HELM-020 under ### Bugs (after HELM-012, before ### Tech debt)
    (
        "restructure decision._\n\n### Tech debt",
        "restructure decision._\n\n"
        "**HELM-020 · `BUG` · `OPEN` · `check_cmd.py` deep-view hygiene (hardcoded ticker + dead dup)**\n"
        "Two cleanups surfaced during HELM-019: (1) `cmd_check_deep_iron_condor` hardcodes the ticker\n"
        "label \"HON\" (`HON now: …`, `Alert if HON moves …`), so every non-HON condor deep view (WELL,\n"
        "MCD) prints the wrong ticker — use the position's ticker. (2) `generate_guidance` is defined\n"
        "twice at module scope (~L1032 and ~L1478); the first is dead (shadowed) and has *diverged*\n"
        "(stale RED-branch text), a footgun for anyone editing it — remove the L1032 copy.\n\n"
        "### Tech debt",
    ),
    # annotate HELM-019 (v1+v1.1 done) before the deferral note
    (
        "(Sibling of HELM-006.)\n_Deferred (weakest-leg) —",
        "(Sibling of HELM-006.)\n"
        "_v1+v1.1 shipped (2026-06-19, s24): `helm check` compact + condor deep views gate frozen/stale\n"
        "marks — no profit-target/stop close off non-live data; P&L shown + tagged, capped YELLOW,\n"
        "\"confirm at RTH\"; DTE + zone signals untouched. Remaining: the HELM-vs-Fidelity mark/P&L\n"
        "reconcile (oracle = Fidelity CSV value + gain/loss)._\n"
        "_Deferred (weakest-leg) —",
    ),
    # Resolved-log line at top
    (
        "## Resolved log\n\n- **2026-06-18 (s24)** — WELL iron condor **backfilled**",
        "## Resolved log\n\n"
        "- **2026-06-19 (s24)** — HELM-019 frozen-mark confidence shipped (v1 + v1.1). `helm check`\n"
        "  derives `mark_confidence` (live/frozen/stale) from the primary `opt_source`; non-live marks\n"
        "  can't drive a GREEN profit-target or RED stop (compact) or the condor deep-view \"close and\n"
        "  redeploy\" verdict — P&L shown + tagged, capped YELLOW, \"confirm at RTH\". DTE + zone signals\n"
        "  untouched. Patches `apply_helm019_v1.py`, `apply_helm019_v1_1.py`; live-validated on WELL/MCD.\n"
        "  Remaining under HELM-019: the HELM-vs-Fidelity mark/P&L reconcile.\n"
        "- **2026-06-18 (s24)** — WELL iron condor **backfilled**",
    ),
]


def main():
    apply = "--apply" in sys.argv[1:]
    if not os.path.exists(PATH):
        sys.exit(f"ABORT: {PATH} not found (CWD {os.getcwd()}).")
    with open(PATH, encoding="utf-8") as f:
        cur = f.read()

    if SENTINEL in cur:
        sys.exit("ABORT (idempotent): 'HELM-020' already present. No change.")

    for i, (old, _n) in enumerate(EDITS, 1):
        c = cur.count(old)
        if c != 1:
            sys.exit(f"ABORT (anchor {i}): expected 1 match, found {c}. Head: {old[:50]!r}")

    new = cur
    for old, repl in EDITS:
        new = new.replace(old, repl, 1)

    diff = "".join(difflib.unified_diff(
        cur.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile="ISSUES.md (current)", tofile="ISSUES.md (HELM-019 close-out)",
    ))
    if not apply:
        print(diff)
        print("\n--- DRY RUN. Nothing written. Re-run with --apply. ---")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = f"{PATH}.bak.{ts}"
    with open(bak, "w", encoding="utf-8") as f:
        f.write(cur)
    with open(PATH, "w", encoding="utf-8") as f:
        f.write(new)
    with open(PATH, encoding="utf-8") as f:
        back = f.read()
    assert "HELM-020" in back and "v1+v1.1 shipped" in back and "2026-06-19 (s24)" in back, "post-write check failed"
    print(f"APPLIED. Backup: {bak}")
    print("Post-write checks passed: HELM-020 added, HELM-019 annotated, Resolved line + stamp updated.")


if __name__ == "__main__":
    main()
