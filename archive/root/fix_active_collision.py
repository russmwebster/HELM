#!/usr/bin/env python3
"""
Fix: WatchlistItem.active  ->  field / classmethod name collision.

The dataclass field `active: int = 0` and the classmethod `active(cls)` share the
name `active`. Because the method is defined in the class body, @dataclass captures
the *method* as the field's default, so a freshly-constructed item gets
`self.active = <bound method>` and `save()` fails to bind it
("type 'method' is not supported"). This breaks every `helm watchlist add`.
(Rows read from the DB are unaffected -- from_row() sets active explicitly.)

Fix: rename the classmethod `active` -> `active_universe`. Its only caller is
scan_cmd.py. The field default of 0 is restored and `add` works again.

Discipline: dry-run default | anchor-count asserts | per-file backup | py_compile.

Run from the repo root:
    python fix_active_collision.py            # dry-run -- checks anchors, writes nothing
    python fix_active_collision.py --apply    # backup each file, edit, py_compile
"""
import sys, os, shutil, py_compile
from datetime import datetime

APPLY = "--apply" in sys.argv

EDITS = [
    ("helm/models/watchlist.py",
     "def active(cls) -> list[WatchlistItem]:",
     "def active_universe(cls) -> list[WatchlistItem]:"),
    ("helm/cli/scan_cmd.py",
     "WatchlistItem.active()",
     "WatchlistItem.active_universe()"),
]


def main():
    # 1) validate every anchor first -- abort before any write if even one is off
    plans = []
    for path, old, new in EDITS:
        if not os.path.exists(path):
            sys.exit(f"ABORT: {path} not found -- run from the repo root.")
        text = open(path, encoding="utf-8").read()
        n = text.count(old)
        if n != 1:
            sys.exit(f"ABORT: expected exactly 1 occurrence of {old!r} in {path}, found {n}.")
        if new in text:
            sys.exit(f"ABORT: {new!r} already present in {path} -- looks already applied.")
        plans.append((path, old, new, text))
    print("[anchors] all OK -- exactly 1 occurrence each, none already applied.")

    if not APPLY:
        for path, old, new, _ in plans:
            print(f"  would edit {path}:\n      {old}\n   -> {new}")
        print("\nDRY-RUN only. Re-run with --apply to write.")
        return

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for path, old, new, text in plans:
        bak = f"{path}.bak.activefix_{stamp}"
        shutil.copy2(path, bak)
        open(path, "w", encoding="utf-8").write(text.replace(old, new, 1))
        py_compile.compile(path, doraise=True)  # raises if the edit broke syntax
        print(f"[edited] {path}  (backup {bak})  py_compile OK")

    print("\nDone. Next:")
    print("  helm watchlist add DHI PNC DAL FDX DE GM SLB NEM NUE TGT DG O")
    print("  helm screen DHI PNC DAL FDX DE GM SLB NEM NUE TGT DG O")
    print("  python patch_core_v1.py        # dry-run, should now PASS")


if __name__ == "__main__":
    main()
