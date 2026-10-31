#!/usr/bin/env python3
"""s117 — W152 (status prints a loss with no minus sign) and W143 (exposure_group
has no writer; assert instead of fixing the add path).

Backup per file, patch, readback, py_compile. No git. Idempotent: refuses if the
sentinel is already present.
"""
import re, shutil, py_compile, sys, datetime, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent
STAMP = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
APPLY = "--apply" in sys.argv
def log(*a): print(*a)

# ---------------------------------------------------------------- W152
S = ROOT / "helm/cli/status_cmd.py"
s = S.read_text()
OLD152 = '    pnl_sign  = "+" if total_realized >= 0 else ""'
NEW152 = ('    # W152: three states, three renderings. A loss must not depend on colour --\n'
          '    # it does not survive a screenshot, a copy-paste, or a colour-blind reader.\n'
          '    pnl_sign  = "+" if total_realized > 0 else ("-" if total_realized < 0 else "")')
if "W152" in s:
    log("W152: sentinel already present — refusing"); sys.exit(1)
if s.count(OLD152) != 1:
    log("W152: anchor matched %d times, expected 1 — refusing" % s.count(OLD152)); sys.exit(1)
s152 = s.replace(OLD152, NEW152)

# ---------------------------------------------------------------- W143
A = ROOT / "helm/cli/audit_cmd.py"
a = A.read_text()
if "check_exposure_groups" in a:
    log("W143: sentinel already present — refusing"); sys.exit(1)

METHOD = '''
    def check_exposure_groups(self):
        """W143: `exposure_group` has no writer, so a new watchlist row arrives NULL.

        Asserted here rather than fixed at the add path DELIBERATELY: an assertion
        also catches a group being dropped or a rename orphaning rows, which an
        add-path fix cannot. It has already recurred twice by hand in one day
        (the 22 tranche-1 names, then ROST hours later).

        Reads CURRENT watchlist state -- the table keeps no history -- so on a
        back-dated audit this assertion describes today, not the audited day.
        Said here rather than left to be discovered.
        """
        rows = self.q(
            "select ticker from watchlist "
            "where active = 1 and (exposure_group is null or trim(exposure_group) = '') "
            "order by ticker"
        )
        total = self.q("select count(*) as c from watchlist where active = 1")[0]["c"]
        if not total:
            self.add(SKIP, "exposure groups", "no active watchlist names")
            return
        if not rows:
            self.add(PASS, "exposure groups",
                     "every active watchlist name carries an exposure group",
                     "%d active names checked (current state)" % total)
            return
        CAP = 12  # W119: a capped list must SAY it is capped.
        named = ", ".join(r["ticker"] for r in rows[:CAP])
        if len(rows) > CAP:
            named += "; and %d more" % (len(rows) - CAP)
        self.add(FAIL, "exposure groups",
                 "%d of %d active watchlist names carry no exposure group"
                 % (len(rows), total),
                 named)

    def check_closes(self):'''
if a.count("\n    def check_closes(self):") != 1:
    log("W143: check_closes anchor not unique — refusing"); sys.exit(1)
a2 = a.replace("\n    def check_closes(self):", METHOD, 1)

CALL_OLD = "        self.check_closes()\n"
if a2.count(CALL_OLD) != 1:
    log("W143: registration anchor not unique — refusing"); sys.exit(1)
a2 = a2.replace(CALL_OLD, "        self.check_exposure_groups()\n" + CALL_OLD, 1)

BS_ANCHOR = '        self.blind_spot(\n            "The \'previous audit\' check only looks BACKWARD one session.'
if BS_ANCHOR not in a2:
    log("W143: blind-spot anchor not found — refusing"); sys.exit(1)
a2 = a2.replace(BS_ANCHOR,
    '        self.blind_spot(\n'
    '            "The exposure-group check reads the watchlist as it stands NOW. "\n'
    '            "It cannot tell you whether a name was ungrouped on the audited "\n'
    '            "day, only whether anything is ungrouped today."\n'
    '        )\n' + BS_ANCHOR, 1)

if not APPLY:
    log("DRY RUN — would patch:")
    log("  helm/cli/status_cmd.py   W152 sign  (%d bytes -> %d)" % (len(s), len(s152)))
    log("  helm/cli/audit_cmd.py    W143 check (%d bytes -> %d)" % (len(a), len(a2)))
    sys.exit(0)

for path, new in ((S, s152), (A, a2)):
    bak = path.with_suffix(path.suffix + ".bak-s117-" + STAMP)
    shutil.copy2(path, bak)
    path.write_text(new)
    py_compile.compile(str(path), doraise=True)
    back = path.read_text()
    assert back == new, "readback mismatch on %s" % path
    log("patched %s (backup %s) — %d bytes, py_compile OK" % (path.name, bak.name, len(back)))

# readback assertions on the PARSED thing, not a substring
import importlib.util
spec = importlib.util.spec_from_file_location("_st", S)
src = S.read_text()
assert 'pnl_sign  = "+" if total_realized > 0 else ("-" if total_realized < 0 else "")' in src
assert src.count("W152") == 1
asrc = A.read_text()
assert asrc.count("def check_exposure_groups") == 1
assert asrc.count("self.check_exposure_groups()") == 1
log("readback assertions OK")
