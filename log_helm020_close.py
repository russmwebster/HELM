#!/usr/bin/env python3
"""
log_helm020_close.py — move HELM-020 from Active to Resolved (both parts shipped),
with corrected wording (the generate_guidance dup was an exact copy, not divergent).

Removes the HELM-020 block from ### Bugs (bracketed by the HELM-012 end and the
### Tech debt heading) and prepends a Resolved-log entry. Sentinel-guarded,
backup, DRY-RUN default. Run from repo root.
"""
import sys, os, time, difflib

PATH = "ISSUES.md"
SENTINEL = "**HELM-020** resolved"

A = "restructure decision._\n\n"        # end of HELM-012
B = "### Tech debt"                     # next section
RLOG = "## Resolved log\n\n"

RESOLVED = (
    "- **2026-06-19 (s24)** — **HELM-020** resolved. (1) `cmd_check_deep_iron_condor` now uses the\n"
    "  position's ticker, not a hardcoded \"HON\" label (it was printing the wrong ticker on every\n"
    "  non-HON condor deep view — WELL, MCD). (2) Removed the dead, shadowed `generate_guidance`\n"
    "  duplicate — an exact copy sitting before `cmd_check_deep_iron_condor`; only the\n"
    "  post-`cmd_check_deep` def ever ran (positional delete, one copy remains). Patches\n"
    "  `apply_helm020_hon.py`, `apply_helm020_deadgg.py`.\n"
)


def main():
    apply = "--apply" in sys.argv[1:]
    if not os.path.exists(PATH):
        sys.exit(f"ABORT: {PATH} not found (CWD {os.getcwd()}).")
    with open(PATH, encoding="utf-8") as f:
        cur = f.read()

    if SENTINEL in cur:
        sys.exit("ABORT (idempotent): HELM-020 already in Resolved log. No change.")

    # locate + remove the HELM-020 block (between HELM-012 end and Tech debt)
    if cur.count(A) != 1 or cur.count(B) != 1 or cur.count(RLOG) != 1:
        sys.exit("ABORT: expected unique anchors (HELM-012 end / Tech debt / Resolved log).")
    ia = cur.index(A); start = ia + len(A)
    ib = cur.index(B, start)
    block = cur[start:ib]
    if "HELM-020" not in block or "generate_guidance" not in block:
        sys.exit("ABORT: the block between HELM-012 and Tech debt isn't the HELM-020 entry.")
    if block.count("**HELM-") != 1:
        sys.exit("ABORT: block contains more than the HELM-020 entry — refusing.")

    removed = cur[:start] + cur[ib:]

    # prepend the Resolved entry
    new = removed.replace(RLOG, RLOG + RESOLVED, 1)

    diff = "".join(difflib.unified_diff(
        cur.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile="ISSUES.md (current)", tofile="ISSUES.md (HELM-020 resolved)",
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
    assert SENTINEL in back, "post-write: resolved entry missing"
    # HELM-020 should no longer appear as an Active bug header
    active = back.split("## Resolved log")[0]
    assert "**HELM-020" not in active, "post-write: HELM-020 still in Active"
    print(f"APPLIED. Backup: {bak}")
    print("Post-write checks passed: HELM-020 moved to Resolved, not in Active.")


if __name__ == "__main__":
    main()
