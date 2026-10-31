#!/usr/bin/env python3
"""
log_helm019_deferral.py — append the deferred 'weakest-leg confidence' sub-item
to the HELM-019 entry in ISSUES.md, so the deferral isn't lost.

Self-guarding: idempotency sentinel, single-anchor assert, timestamped backup,
DRY-RUN BY DEFAULT (--apply to write). Run from the repo root.
    python log_helm019_deferral.py            # dry-run diff
    python log_helm019_deferral.py --apply    # writes, after backup
"""
import sys, os, time, difflib

PATH = "ISSUES.md"

ANCHOR = "WELL/MCD next RTH. (Sibling of HELM-006.)"

NOTE = (
    "\n_Deferred (weakest-leg) — `check_one`'s leg_marks loop (`check_cmd.py` ~L617–626)\n"
    "stores only each leg's mid and discards its source, so v1 confidence uses the primary\n"
    "leg's `opt_source` as a market-state proxy (live / frozen / stale). Stamp per-leg source\n"
    "there when that loop is reworked; pairs with the carried \"mid-only fast fetch for hedge\n"
    "legs\" (HELM-018 follow-up)._"
)

SENTINEL = "_Deferred (weakest-leg) —"


def main():
    apply = "--apply" in sys.argv[1:]
    if not os.path.exists(PATH):
        sys.exit(f"ABORT: {PATH} not found in CWD ({os.getcwd()}). Run from the repo root.")

    with open(PATH, encoding="utf-8") as f:
        cur = f.read()

    if SENTINEL in cur:
        sys.exit("ABORT (idempotent): deferral note already present. No change.")

    n = cur.count(ANCHOR)
    if n != 1:
        sys.exit(f"ABORT (anchor): expected the HELM-019 anchor exactly once, found {n}. "
                 "Re-send the HELM-019 entry so the patch can be re-based.")

    new = cur.replace(ANCHOR, ANCHOR + NOTE, 1)

    diff = "".join(difflib.unified_diff(
        cur.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile="ISSUES.md (current)", tofile="ISSUES.md (+ HELM-019 deferral)",
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
    assert SENTINEL in back, "post-write check failed: note missing"
    print(f"APPLIED. Backup: {bak}")


if __name__ == "__main__":
    main()
