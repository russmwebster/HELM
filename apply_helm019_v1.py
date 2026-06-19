#!/usr/bin/env python3
"""
apply_helm019_v1.py — HELM-019 v1: frozen/stale mark-confidence on `helm check`.

Four anchored edits:
  (1) assess_position gains a `mark_confidence` param.
  (2) assess_position: when marks aren't live, P&L can't drive a GREEN profit-target
      or RED close — show the number, cap at YELLOW, say "confirm at RTH".
  (3) check_one derives mark_confidence from the primary opt_source and passes it in.
  (4) check_one exposes mark_confidence on the assessment dict.

Self-guarding: idempotency sentinel, per-anchor exactly-once assert, timestamped
backup, py_compile gate, DRY-RUN BY DEFAULT (--apply to write).

    python apply_helm019_v1.py                 # dry-run diff (default target)
    python apply_helm019_v1.py --apply         # write, after backup + compile gate
    python apply_helm019_v1.py --path X --apply # operate on a different file (testing)
"""
import sys, os, time, difflib, py_compile, shutil

DEFAULT_PATH = "helm/cli/check_cmd.py"

SENTINEL = "mark_confidence"

# (old, new) anchored edits. Each old must appear exactly once.
EDITS = [
    # (1) add the param
    (
        '                    leg_marks: Optional[dict] = None) -> dict:',
        '                    leg_marks: Optional[dict] = None,\n'
        '                    mark_confidence: str = "live") -> dict:',
    ),
    # (2) gate the P&L -> flag/reason escalation on mark freshness
    (
        '        if pnl_pct is not None:\n'
        '            if flag_direction == "SHORT" and pnl_pct >= profit_target:',
        '        if pnl_pct is not None and mark_confidence != "live":\n'
        '            # HELM-019: frozen/stale marks must not drive an actionable\n'
        '            # profit-target/stop signal. Show the number, cap at YELLOW,\n'
        '            # tell the trader to confirm at RTH.\n'
        '            flags.append("YELLOW")\n'
        '            reasons.append(f"Frozen/stale marks ({mark_confidence}) — P&L {pnl_pct:+.0f}% unverified, confirm at RTH")\n'
        '        elif pnl_pct is not None:\n'
        '            if flag_direction == "SHORT" and pnl_pct >= profit_target:',
    ),
    # (3) derive + pass mark_confidence
    (
        '    # Run assessment\n'
        '    assessment = assess_position(pos, legs, underlying_price, opt_data, strategy_settings, leg_marks=leg_marks)',
        '    # HELM-019: classify mark freshness from the primary leg\'s opt_source as a\n'
        '    # market-state proxy (per-leg weakest-link is a logged deferral).\n'
        '    if opt_source == "ibkr-live":\n'
        '        mark_confidence = "live"\n'
        '    elif opt_source == "ibkr-frozen":\n'
        '        mark_confidence = "frozen"\n'
        '    else:\n'
        '        mark_confidence = "stale"\n'
        '\n'
        '    # Run assessment\n'
        '    assessment = assess_position(pos, legs, underlying_price, opt_data, strategy_settings, leg_marks=leg_marks, mark_confidence=mark_confidence)',
    ),
    # (4) expose on the assessment dict
    (
        '        "opt_source": opt_source,',
        '        "opt_source": opt_source,\n'
        '        "mark_confidence": mark_confidence,',
    ),
]


def main():
    apply = "--apply" in sys.argv[1:]
    path = DEFAULT_PATH
    if "--path" in sys.argv:
        path = sys.argv[sys.argv.index("--path") + 1]

    if not os.path.exists(path):
        sys.exit(f"ABORT: {path} not found (CWD {os.getcwd()}). Run from the repo root or pass --path.")

    with open(path, encoding="utf-8") as f:
        cur = f.read()

    if SENTINEL in cur:
        sys.exit("ABORT (idempotent): 'mark_confidence' already present — looks already patched. No change.")

    # verify every anchor matches exactly once before touching anything
    for i, (old, _new) in enumerate(EDITS, 1):
        n = cur.count(old)
        if n != 1:
            sys.exit(f"ABORT (anchor {i}): expected exactly 1 match, found {n}.\n"
                     f"Re-send the current function so the patch can be re-based.\nAnchor head: {old[:60]!r}")

    new = cur
    for old, repl in EDITS:
        new = new.replace(old, repl, 1)

    diff = "".join(difflib.unified_diff(
        cur.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{path} (current)", tofile=f"{path} (HELM-019 v1)",
    ))

    if not apply:
        print(diff)
        print("\n--- DRY RUN. Nothing written. Re-run with --apply (backup + py_compile gate first). ---")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = f"{path}.bak.{ts}"
    shutil.copyfile(path, bak)

    # write to a temp, compile-gate, then swap
    tmp = f"{path}.helm019.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(new)
    try:
        py_compile.compile(tmp, doraise=True)
    except py_compile.PyCompileError as e:
        os.remove(tmp)
        sys.exit(f"ABORT: patched file failed py_compile, original untouched.\n{e}")
    os.replace(tmp, path)

    # readback
    with open(path, encoding="utf-8") as f:
        back = f.read()
    assert "mark_confidence: str =" in back, "post-write: param missing"
    assert 'mark_confidence != "live"' in back, "post-write: gate missing"
    assert '"mark_confidence": mark_confidence,' in back, "post-write: assessment key missing"
    print(f"APPLIED. Backup: {bak}")
    print("Post-write checks passed: param + gate + assessment key present; file compiles.")


if __name__ == "__main__":
    main()
