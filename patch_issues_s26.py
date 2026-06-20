#!/usr/bin/env python3
"""
ISSUES.md checkpoint patch — HELM session 26.   Commit: 469c3cc

Edits (all anchor-validated before any write):
  1. _Last updated_  s25 -> s26
  2. Status block      -> refreshed (phase / next-leverage / blocked / counts)
  3. HELM-005          -> removed from Active (resolved; moved to Resolved log)
  4. HELM-011          -> DEFERRED -> OPEN (now next-highest-leverage)
  5. Resolved log      -> 3 new s26 entries prepended (newest-first), commit-anchored
  6. Carried threads   -> s26 block appended

Discipline: dry-run default | timestamped backup | per-anchor count asserts |
single in-memory transform | readback. Run from the repo root.

    python patch_issues_s26.py            # dry-run — validates anchors, writes nothing
    python patch_issues_s26.py --apply    # backup ISSUES.md, then write
"""
import sys, os, shutil
from datetime import datetime

F = "ISSUES.md"
APPLY = "--apply" in sys.argv
HASH = "469c3cc"

NEW_BULLETS = (
    "- **Phase:** scaffolding complete (live book \u00b7 paper book \u00b7 edge instrument). "
    "Watchlist is now a deliberate 65-name `core_v1` universe; paper book clean-slated. "
    "Learning loop still the frontier \u2014 corpus now accumulating fresh on the clean universe.\n"
    "- **Next highest-leverage:** HELM-011 \u2014 light the neutral long-vol (straddle) cell so "
    "the cheap-IVR corner produces corpus.\n"
    "- **Blocked (market/RTH):** `core_v1` IVR backfill (Mon RTH \u2014 the 12 new names); "
    "HELM-019 Fidelity reconcile; HELM-018 RTH P&L sweep.\n"
    "- **Counts:** 11 active \u00b7 5 parked \u00b7 last shipped s26 "
    "(HELM-005 `core_v1` cull + HELM-024 `add`-bug fix)."
)

NEW_RESOLVED = (
    "- **2026-06-20 (s26)** \u2014 **HELM-005 RESOLVED (reframed) \u2014 `core_v1` cull.** "
    "The monoculture wasn't a narrow watchlist: bare `helm scan` runs the `active` set, which had "
    "silently grown to 60 uncurated names (75% of signals from 156 thematic non-core tickers \u2014 "
    "the \"benched\" themes were never benched). Data-only fix: re-culled `active` to a deliberate 65 "
    "(53 quality + 12 directional-diversity adds \u2014 DHI PNC DAL FDX DE GM SLB NEM NUE TGT DG O), "
    "tagged `core_v1`, benched the rest (preserved, dormant). `active` is now the single source of "
    "truth for the scan universe; `build` is a label only. Verified 65 active / 65 core_v1 / "
    f"41 REAL untouched / paper emptied. Patch `patch_core_v1.py` (guarded). Commit `{HASH}`.\n"
    "- **2026-06-20 (s26)** \u2014 **Paper clean slate.** Soft-voided the 14 open PAPER positions "
    "(\u2192 CLOSED) so the corpus restarts on the clean 65; the `core_v1` cull date is the "
    f"regime-break line for the learning layer. REAL book untouched. Commit `{HASH}`.\n"
    "- **2026-06-20 (s26)** \u2014 **HELM-024 found + fixed \u2014 `helm watchlist add` crash.** "
    "`WatchlistItem` dataclass field `active: int = 0` collided with the classmethod `active(cls)`; "
    "@dataclass captured the method as the field default, so fresh items got "
    "`self.active = <bound method>` and `save()` raised `type 'method' is not supported`. Latent "
    "since the `active()` fetcher landed (rows had arrived via screen/build/import). Fix: renamed "
    "classmethod `active` \u2192 `active_universe` (sole caller `scan_cmd.py`); mechanical rename, "
    f"no behavior change. Patch `fix_active_collision.py` (guarded). Commit `{HASH}`.\n"
)

S26_CARRIED = (
    "**s26:**\n"
    "- Monday RTH: `helm ivr refresh` to backfill IVR on the 12 `core_v1` adds "
    "(they scan via the `ivr_unknown` score-only path until then).\n"
    "- `helm ivr refresh` churns all 206 watchlist names, not just the active 65 \u2014 harmless, "
    "but scoping it to `active` is a small OPS nicety worth a future ticket.\n"
    "- Uncommitted after this checkpoint: `patch_issues_s26.py` + `ISSUES.md` \u2014 commit on the "
    "usual explicit-named-files step; push separate. (Cull/fix landed in commit "
    f"`{HASH}`.)\n"
)

STATUS_MARK = "read via `helm status`._\n\n"
SEP = "\n\n---"
RESOLVED_ANCHOR = "- **2026-06-20 (s25)** \u2014 **HELM-016"
H011_OLD = "**HELM-011 \u00b7 `DESIGN` \u00b7 `DEFERRED` \u00b7"
H011_NEW = "**HELM-011 \u00b7 `DESIGN` \u00b7 `OPEN` \u00b7"


def transform(text):
    def need(sub, n=1):
        c = text.count(sub)
        if c != n:
            sys.exit(f"ABORT: expected {n}x {sub!r}, found {c}.")

    need("(s25)._")
    need(STATUS_MARK)
    need("**HELM-005")
    need("**HELM-011")
    need(H011_OLD)
    need(RESOLVED_ANCHOR)

    t = text.replace("(s25)._", "(s26)._", 1)

    # refresh Status bullets (split-on-marker; no exact-middle reproduction)
    pre, rest = t.split(STATUS_MARK, 1)
    _bullets, post = rest.split(SEP, 1)
    t = pre + STATUS_MARK + NEW_BULLETS + SEP + post

    # remove HELM-005 from Active (drop from its header to HELM-011's)
    before, rest = t.split("**HELM-005", 1)
    _removed, after = rest.split("**HELM-011", 1)
    t = before + "**HELM-011" + after

    # promote HELM-011 DEFERRED -> OPEN
    t = t.replace(H011_OLD, H011_NEW, 1)

    # prepend s26 Resolved-log entries
    t = t.replace(RESOLVED_ANCHOR, NEW_RESOLVED + RESOLVED_ANCHOR, 1)

    # append s26 carried-threads block
    t = t.rstrip() + "\n\n" + S26_CARRIED

    # readback
    assert "(s26)._" in t and "(s25)._" not in t
    assert "**HELM-005 \u00b7" not in t          # Active entry (header w/ middot) gone
    assert "HELM-024" in t
    assert HASH in t
    assert H011_NEW in t and H011_OLD not in t
    assert t.count("## Resolved log") == 1
    return t


def main():
    if not os.path.exists(F):
        sys.exit(f"{F} not found — run from the repo root.")
    text = open(F, encoding="utf-8").read()
    new = transform(text)
    print(f"[anchors] OK. {len(text)} -> {len(new)} chars (+{len(new) - len(text)}). "
          f"Commit anchor: {HASH}")
    if not APPLY:
        print("DRY-RUN only. Re-run with --apply to write.")
        return
    bak = f"{F}.bak.s26_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    shutil.copy2(F, bak)
    open(F, "w", encoding="utf-8").write(new)
    print(f"[applied] {F}  (backup {bak})")


if __name__ == "__main__":
    main()
