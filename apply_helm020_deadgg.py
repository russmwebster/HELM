#!/usr/bin/env python3
"""
apply_helm020_deadgg.py — HELM-020 (part 2): remove the dead, shadowed
`generate_guidance`. There are two identical module-level defs; only the second
(after cmd_check_deep) ever runs. The first sits between cmd_check_deep_csp and
cmd_check_deep_iron_condor and is dead. Because the bodies are identical, the
patch targets the dead copy *positionally* — the generate_guidance immediately
before `def cmd_check_deep_iron_condor` — and deletes it, leaving a clean
2-blank-line separation.

Guards: exactly two generate_guidance defs required, exactly one condor marker,
the deleted span must be a single function (no stray defs), and exactly one
generate_guidance must remain afterwards. py_compile-gated, backup, dry-run.

    python apply_helm020_deadgg.py
    python apply_helm020_deadgg.py --apply
    python apply_helm020_deadgg.py --path X --apply   # testing
"""
import sys, os, time, difflib, py_compile, shutil

DEFAULT_PATH = "helm/cli/check_cmd.py"
GG = "def generate_guidance("
MARKER = "def cmd_check_deep_iron_condor(pos, legs, assessment, snap):"


def main():
    apply = "--apply" in sys.argv[1:]
    path = DEFAULT_PATH
    if "--path" in sys.argv:
        path = sys.argv[sys.argv.index("--path") + 1]
    if not os.path.exists(path):
        sys.exit(f"ABORT: {path} not found (CWD {os.getcwd()}).")

    with open(path, encoding="utf-8") as f:
        cur = f.read()

    n_gg = cur.count(GG)
    if n_gg != 2:
        sys.exit(f"ABORT: expected exactly 2 generate_guidance defs, found {n_gg}. "
                 "Refusing (idempotent if already 1, anomalous if other).")
    if cur.count(MARKER) != 1:
        sys.exit(f"ABORT: expected exactly 1 cmd_check_deep_iron_condor marker, found {cur.count(MARKER)}.")

    idx_condor = cur.index(MARKER)
    idx_dead = cur.rindex(GG, 0, idx_condor)  # the generate_guidance just before the condor view

    dead_span = cur[idx_dead:idx_condor]
    if "return lines" not in dead_span:
        sys.exit("ABORT: dead span doesn't look like a full function (no 'return lines').")
    # the span must contain exactly the one generate_guidance def and nothing else top-level
    if dead_span.count("\ndef ") != 0:
        sys.exit("ABORT: dead span contains another top-level def — refusing to over-delete.")

    new = cur[:idx_dead] + cur[idx_condor:]

    # post-conditions
    if new.count(GG) != 1:
        sys.exit(f"ABORT: after delete, generate_guidance count is {new.count(GG)} (want 1).")
    if "Close this position TODAY to avoid assignment risk." not in new:
        sys.exit("ABORT: live copy's distinctive line vanished — refusing.")
    if MARKER not in new:
        sys.exit("ABORT: condor marker vanished — refusing.")

    diff = "".join(difflib.unified_diff(
        cur.splitlines(keepends=True), new.splitlines(keepends=True),
        fromfile=f"{path} (current)", tofile=f"{path} (HELM-020 dead-gg removed)",
    ))
    if not apply:
        # the diff is large (a whole function); show head + tail for review
        dl = diff.splitlines(keepends=True)
        if len(dl) > 60:
            print("".join(dl[:30]))
            print(f"... [{len(dl) - 50} diff lines elided — full function deletion] ...\n")
            print("".join(dl[-20:]))
        else:
            print(diff)
        print(f"\n--- DRY RUN. Removes the dead generate_guidance ({idx_condor - idx_dead} chars). "
              "Nothing written. Re-run with --apply. ---")
        return

    ts = time.strftime("%Y%m%d_%H%M%S")
    bak = f"{path}.bak.{ts}"
    shutil.copyfile(path, bak)
    tmp = f"{path}.helm020gg.tmp"
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
    assert back.count(GG) == 1, "post-write: not exactly one generate_guidance"
    print(f"APPLIED. Backup: {bak}")
    print("Post-write checks passed: one generate_guidance remains; file compiles.")


if __name__ == "__main__":
    main()
