#!/usr/bin/env python3
"""
apply_helm019_v1_1.py — HELM-019 v1.1: guard the IRON_CONDOR deep view.

`cmd_check_deep_iron_condor` recomputes its own profit_pct from the (possibly
frozen) pnl_mtm and prints "50% profit target reached — close and redeploy",
bypassing v1's flag gate. v1.1:
  (1) read mark_confidence off the assessment,
  (2) tag the P&L line when not live,
  (3) gate the profit-target verdict on live marks (DTE + zone verdicts untouched),
  (4) print a frozen advisory after the guidance ladder when not live.

Requires v1 (needs `mark_confidence` on the assessment). Idempotent, py_compile
gated, timestamped backup, DRY-RUN BY DEFAULT (--apply to write).

    python apply_helm019_v1_1.py
    python apply_helm019_v1_1.py --apply
    python apply_helm019_v1_1.py --path X --apply   # testing
"""
import sys, os, time, difflib, py_compile, shutil

DEFAULT_PATH = "helm/cli/check_cmd.py"
SENTINEL = "HELM-019 v1.1"

EDITS = [
    # (1) read mark_confidence
    (
        "    pnl_mtm = a.get('pnl_mtm') or 0\n"
        "    profit_pct = round(pnl_mtm / net_premium * 100, 1) if net_premium else 0",
        "    pnl_mtm = a.get('pnl_mtm') or 0\n"
        "    profit_pct = round(pnl_mtm / net_premium * 100, 1) if net_premium else 0\n"
        "    mark_confidence = a.get('mark_confidence', 'live')  # HELM-019 v1.1",
    ),
    # (2) tag the P&L line when not live
    (
        "    console.print(f'  Current P&L: [{pnl_color}]{pnl_mtm:+,.0f}  ({profit_pct:.1f}% of max profit)[/{pnl_color}]')",
        "    _fz = '' if mark_confidence == 'live' else f'  [yellow]({mark_confidence})[/yellow]'\n"
        "    console.print(f'  Current P&L: [{pnl_color}]{pnl_mtm:+,.0f}  ({profit_pct:.1f}% of max profit)[/{pnl_color}]{_fz}')",
    ),
    # (3) gate the profit-target close verdict on live marks
    (
        "    elif profit_pct >= 50:",
        "    elif profit_pct >= 50 and mark_confidence == \"live\":",
    ),
    # (4) frozen advisory after the guidance ladder
    (
        "    else:\n"
        "        console.print('  [red]⚠  Outside profit zone — evaluate adjustment or close[/red]')\n"
        "    console.print()",
        "    else:\n"
        "        console.print('  [red]⚠  Outside profit zone — evaluate adjustment or close[/red]')\n"
        "    if mark_confidence != \"live\":\n"
        "        console.print('  [yellow]⚠  P&L is frozen/stale — confirm at RTH before acting on profit/stop levels[/yellow]')\n"
        "    console.print()",
    ),
]


def main():
    apply = "--apply" in sys.argv[1:]
    path = DEFAULT_PATH
    if "--path" in sys.argv:
        path = sys.argv[sys.argv.index("--path") + 1]

    if not os.path.exists(path):
        sys.exit(f"ABORT: {path} not found (CWD {os.getcwd()}).")

    with open(path, encoding="utf-8") as f:
        cur = f.read()

    if SENTINEL in cur:
        sys.exit("ABORT (idempotent): v1.1 sentinel already present. No change.")
    if "mark_confidence" not in cur:
        sys.exit("ABORT: v1 not detected (no 'mark_confidence'). Apply apply_helm019_v1.py first.")

    for i, (old, _new) in enumerate(EDITS, 1):
        n = cur.count(old)
        if n != 1:
            sys.exit(f"ABORT (anchor {i}): expected 1 match, found {n}. Anchor head: {old[:60]!r}")

    new = cur
    for old, repl in EDITS:
        new = new.replace(old, repl, 1)

    diff = "".join(difflib.unified_diff(
        cur.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{path} (current)", tofile=f"{path} (HELM-019 v1.1)",
    ))

    if not apply:
        print(diff)
        print("\n--- DRY RUN. Nothing written. Re-run with --apply. ---")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = f"{path}.bak.{ts}"
    shutil.copyfile(path, bak)
    tmp = f"{path}.helm0191.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        os.remove(tmp)
        sys.exit(f"ABORT: patched file failed py_compile, original untouched.\n{e}")
    os.replace(tmp, path)

    with open(path, encoding="utf-8") as f:
        back = f.read()
    assert 'profit_pct >= 50 and mark_confidence == "live"' in back, "post-write: verdict gate missing"
    assert "confirm at RTH before acting on profit/stop" in back, "post-write: advisory missing"
    print(f"APPLIED. Backup: {bak}")
    print("Post-write checks passed: verdict gated + advisory present; file compiles.")


if __name__ == "__main__":
    main()
