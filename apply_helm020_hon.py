#!/usr/bin/env python3
"""
apply_helm020_hon.py — HELM-020 (part 1): kill the hardcoded "HON" ticker label
in cmd_check_deep_iron_condor. Adds a `tkr = pos.get('ticker','')` local and uses
it in the two display strings ("HON now:" and "Alert if HON moves ...").

Idempotent, py_compile-gated, timestamped backup, DRY-RUN BY DEFAULT.
    python apply_helm020_hon.py
    python apply_helm020_hon.py --apply
    python apply_helm020_hon.py --path X --apply   # testing
"""
import sys, os, time, difflib, py_compile, shutil

DEFAULT_PATH = "helm/cli/check_cmd.py"
SENTINEL = "tkr = pos.get('ticker'"

EDITS = [
    # (1) add the ticker local at the top of the condor deep view
    (
        "    a = assessment\n    spot = a.get('underlying_price')",
        "    a = assessment\n    tkr = pos.get('ticker', '')  # HELM-020\n    spot = a.get('underlying_price')",
    ),
    # (2) "HON now:" -> "{tkr} now:"
    (
        "    console.print(f'  HON now:  ${spot:.2f}  —  {zone_str}')",
        "    console.print(f'  {tkr} now:  ${spot:.2f}  —  {zone_str}')",
    ),
    # (3) "Alert if HON moves" -> "Alert if {tkr} moves"
    (
        "console.print(f'  Alert if HON moves below",
        "console.print(f'  Alert if {tkr} moves below",
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
        sys.exit("ABORT (idempotent): ticker local already present. No change.")
    if "HON" not in cur:
        sys.exit("ABORT: no 'HON' literal found — nothing to fix (or already done).")

    for i, (old, _n) in enumerate(EDITS, 1):
        c = cur.count(old)
        if c != 1:
            sys.exit(f"ABORT (anchor {i}): expected 1 match, found {c}. Head: {old[:50]!r}")

    new = cur
    for old, repl in EDITS:
        new = new.replace(old, repl, 1)

    diff = "".join(difflib.unified_diff(
        cur.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{path} (current)", tofile=f"{path} (HELM-020 HON)",
    ))
    if not apply:
        print(diff)
        print("\n--- DRY RUN. Nothing written. Re-run with --apply. ---")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = f"{path}.bak.{ts}"
    shutil.copyfile(path, bak)
    tmp = f"{path}.helm020.tmp"
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
    assert "tkr = pos.get('ticker'" in back, "post-write: ticker local missing"
    assert "HON now" not in back and "Alert if HON moves" not in back, "post-write: HON literal remains"
    print(f"APPLIED. Backup: {bak}")
    print("Post-write checks passed: ticker local added, 'HON' labels replaced; file compiles.")


if __name__ == "__main__":
    main()
