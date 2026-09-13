"""W172 (s120): `helm audit eod` verdict carries a MAGNITUDE -- PASS / DEGRADED /
LOST (INCONCLUSIVE unchanged) -- and notifies only on LOST. Also teaches the
slot check about W171's ivr retry row.

Dry-run by default; --apply writes with backup + readback + py_compile.
"""
import sys, shutil, py_compile, time
from pathlib import Path
ROOT = Path(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith('--') else '.')
APPLY = '--apply' in sys.argv
F = ROOT / 'helm' / 'cli' / 'audit_cmd.py'
src = F.read_text()

def rep(old, new, n=1):
    global src
    assert src.count(old) == n, (src.count(old), old[:60])
    src = src.replace(old, new)

# 1. header ------------------------------------------------------------------
rep("# Usage:  helm audit eod [--date YYYY-MM-DD] [--json] [--no-report]\n"
    "# Exit:   0 = no FAIL   1 = at least one FAIL\n",
    "# Usage:  helm audit eod [--date YYYY-MM-DD] [--json] [--no-report] [--no-notify]\n"
    "# Exit:   0 = no FAIL   1 = at least one FAIL\n"
    "# Verdict (W172, s120): PASS / DEGRADED / LOST / INCONCLUSIVE. The FAIL count\n"
    "#   is unchanged; the verdict says how MUCH was lost, which the word FAIL\n"
    "#   could not (W128: one paper reading and 147 read the same). Only LOST\n"
    "#   sends a notification, and only when auditing today.\n")

# 2. severity thresholds next to the other constants --------------------------
rep("# Legacy / meaningless exit reasons — a close carrying one is not labelled.\n",
    "# W172: a day is DEGRADED rather than LOST only if every lost reading belongs\n"
    "# to a PAPER position and no single slot lost more than this share of what\n"
    "# it attempted. 2026-09-08 lost 73 of 76 at 15:15 (LOST); 09-11 lost 13 of\n"
    "# 77 at 10:00, all paper (DEGRADED). A REAL position losing ANY reading is\n"
    "# LOST -- the real book is the one nobody can re-collect.\n"
    "DEGRADED_MAX_SLOT_LOSS = 0.25\n"
    "SEV_PASS, SEV_DEGRADED, SEV_LOST, SEV_INCONCLUSIVE = \"PASS\", \"DEGRADED\", \"LOST\", \"INCONCLUSIVE\"\n\n"
    "# Legacy / meaningless exit reasons — a close carrying one is not labelled.\n")

# 3. slot check: the ivr retry row is expected, not a stray --------------------
rep('''            # Runs that fired nowhere near a nominal time = launchd catch-up.
            stray = [
                r for r in got
                if not any(_within((r["started_at"] or "")[11:16], t) for t in times)
            ]
''', '''            # W171 (s120): `helm ivr refresh` may run a SECOND pass when the
            # 09:35 one came back short. Its ledger note starts "retry of HH:MM"
            # and it is expected, not a catch-up -- report it, never fail on it.
            retries = [r for r in got if str(r["notes"] or "").startswith("retry of ")]
            for r in retries:
                self.add(PASS, f"retry: {label}",
                         f"ran at {(r['started_at'] or '')[11:16]} — {r['notes']}")
            # Runs that fired nowhere near a nominal time = launchd catch-up.
            stray = [
                r for r in got
                if r not in retries
                and not any(_within((r["started_at"] or "")[11:16], t) for t in times)
            ]
''')

# 4. keep the unmarked list for the severity read -----------------------------
rep('''        if not unmarked:
            self.add(PASS, "book coverage", "every position open before the date was marked")
''', '''        self.unmarked = unmarked
        if not unmarked:
            self.add(PASS, "book coverage", "every position open before the date was marked")
''')

# 5. the severity method, after `failed` ---------------------------------------
rep('''    @property
    def failed(self):
        return any(r["status"] == FAIL for r in self.results)
''', '''    @property
    def failed(self):
        return any(r["status"] == FAIL for r in self.results)

    # W172 (s120): the verdict's magnitude. Pure over what run() already found.
    # DEGRADED is a NARROW gate -- any FAIL outside `reading coverage` /
    # `book coverage`, any REAL position among the losses, any slot losing more
    # than DEGRADED_MAX_SLOT_LOSS of what it attempted, or a loss the run note
    # cannot NAME (W119's cap) is LOST. Unknown is not "probably paper".
    SOFT_FAILS = ("reading coverage", "book coverage")

    def severity(self):
        """Return (level, why). Levels: PASS / DEGRADED / LOST / INCONCLUSIVE."""
        if self.machine_offline_day:
            return SEV_INCONCLUSIVE, "the machine was off or asleep for the session"
        fails = [r for r in self.results if r["status"] == FAIL]
        if not fails:
            return SEV_PASS, "the day collected as designed"
        hard = [r for r in fails if r["name"] not in self.SOFT_FAILS]
        if hard:
            return SEV_LOST, "; ".join(f"{r['name']}: {r['detail']}" for r in hard)[:300]
        lost_total = 0
        real_hits = []
        for r in self.runs:
            if "snapshot" not in (r["agent"] or "").lower():
                continue
            att, jn = (r["attempted"] or 0), (r["journaled"] or 0)
            if jn >= att:
                continue
            lost = att - jn
            lost_total += lost
            hhmm = (r["started_at"] or "")[11:16]
            if att and lost > DEGRADED_MAX_SLOT_LOSS * att:
                return SEV_LOST, (f"the {hhmm} slot lost {lost} of {att} readings "
                                  f"(more than {int(DEGRADED_MAX_SLOT_LOSS * 100)}%)")
            names, _kind = self._parse_notes(r["notes"])
            claimed = self._claimed_count(r["notes"])
            if (claimed or lost) and len(names) < (claimed or lost):
                return SEV_LOST, (f"the {hhmm} slot lost {lost} readings and the run note "
                                  f"names only {len(names)} — the rest cannot be attributed")
            if names:
                qs = ",".join("?" * len(names))
                books = self.q(f"select id, ticker, book from positions where id in ({qs})", names)
                real_hits += [f"{b['ticker']} at {hhmm}" for b in books if (b["book"] or "") == "REAL"]
                if len(books) < len(names):
                    return SEV_LOST, (f"the {hhmm} slot lost readings on ids not in `positions` — "
                                      f"cannot attribute them to a book")
        for u in (getattr(self, "unmarked", None) or []):
            if (u["book"] or "") == "REAL":
                real_hits.append(f"{u['ticker']} (no reading all day)")
        if real_hits:
            return SEV_LOST, "a REAL position lost a reading: " + ", ".join(real_hits[:8])
        unmarked_n = len(getattr(self, "unmarked", None) or [])
        return SEV_DEGRADED, (f"{lost_total} reading(s) lost, all PAPER, no slot missing"
                              + (f"; {unmarked_n} paper position(s) unmarked all day" if unmarked_n else "")
                              + " — the real book is complete and the two books remain comparable")
''')

# 6. render: verdict line carries the level -------------------------------------
rep('''    if a.machine_offline_day:
        verdict = ("INCONCLUSIVE — the machine was off or asleep for the session. "
                   "The data is missing, but nothing in HELM is broken.")
    elif a.failed:
        verdict = "FAIL — the day did not collect as designed"
    else:
        verdict = "PASS — the day collected as designed"
    L.append("VERDICT: " + verdict)
''', '''    level, why = a.severity()
    if level == SEV_INCONCLUSIVE:
        verdict = ("INCONCLUSIVE — the machine was off or asleep for the session. "
                   "The data is missing, but nothing in HELM is broken.")
    elif level == SEV_LOST:
        verdict = "LOST — " + why + ". The day did not collect as designed."
    elif level == SEV_DEGRADED:
        verdict = "DEGRADED — " + why + "."
    else:
        verdict = "PASS — the day collected as designed"
    L.append("VERDICT: " + verdict)
''')

# 7. run(): --no-notify, JSON severity, LOST notification ----------------------
rep('''    ap.add_argument("--no-report", action="store_true", help="do not write the dated report file")
''', '''    ap.add_argument("--no-report", action="store_true", help="do not write the dated report file")
    ap.add_argument("--no-notify", action="store_true",
                    help="never send the LOST notification (replays, tests)")
''')
rep('''            {"date": date_str, "run_at": clock, "failed": a.failed,
             "agents": a.agents,''', '''            {"date": date_str, "run_at": clock, "failed": a.failed,
             "severity": a.severity()[0], "severity_why": a.severity()[1],
             "agents": a.agents,''')
rep('''    sys.exit(1 if a.failed else 0)
''', '''    # W172: only LOST speaks, and only about TODAY -- a replayed --date is a
    # study, not an event. macOS notification, best effort, never raises.
    level, why = a.severity()
    if level == SEV_LOST and is_today and not args.no_notify:
        try:
            from helm.exit_alert import _notify
            _notify("HELM audit: LOST", f"{date_str} — {why}"[:230])
        except Exception:
            pass

    sys.exit(1 if a.failed else 0)
''')

print("would write %d bytes" % len(src))
if APPLY:
    bak = F.with_name(F.name + '.bak-s120-' + time.strftime('%Y%m%d-%H%M%S'))
    shutil.copy2(F, bak)
    F.write_text(src)
    py_compile.compile(str(F), doraise=True)
    rb = F.read_text()
    assert rb == src and rb.count('def severity') == 1 and 'DEGRADED_MAX_SLOT_LOSS = 0.25' in rb
    print("applied; backup", bak.name, "; py_compile ok; readback ok")
else:
    print("dry run -- pass --apply")
