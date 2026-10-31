"""W172 verification: severity on days with KNOWN answers, from a fresh VACUUM
INTO copy per case; roster check stubbed (the VM has no launchctl). Then the
perturbations that matter: a REAL loss hidden in a paper day; an unnamed loss;
the ivr retry row vs a genuine stray."""
import os, sys, sqlite3, importlib
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
os.environ['HELM_ROOT'] = ROOT; sys.path.insert(0, ROOT)
LIVE = os.path.join(ROOT, 'data', 'helm.db')
SCRATCH = os.path.expanduser('~/s120'); os.makedirs(SCRATCH, exist_ok=True)
P = F = 0
def check(name, cond, info=''):
    global P, F; P += bool(cond); F += (not cond)
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(info)) if info else ''))

def fresh(tag):
    dst = os.path.join(SCRATCH, 'w172_%s.db' % tag)
    if os.path.exists(dst): os.remove(dst)
    c = sqlite3.connect('file:%s?mode=ro' % LIVE, uri=True); c.execute("VACUUM INTO '%s'" % dst); c.close()
    return dst

def audit(db, date):
    os.environ['HELM_DB'] = db
    for k in [k for k in sys.modules if k.startswith('helm')]: del sys.modules[k]
    import helm.cli.audit_cmd as A
    A.Audit.check_roster = lambda self, is_today: None   # launchctl is a Mac thing
    a = A.Audit(date); a.run(is_today=False)
    lvl, why = a.severity()
    text = A.render(a, 'test')
    vline = [l for l in text.splitlines() if l.startswith('VERDICT')][0]
    return a, lvl, why, vline, A

print('loaded-code probe:', importlib.import_module('helm.cli.audit_cmd').__file__)
# known days ---------------------------------------------------------------
for date, want in (('2026-09-08', 'LOST'), ('2026-09-09', 'DEGRADED'),
                   ('2026-09-10', 'DEGRADED'), ('2026-09-11', 'DEGRADED'), ('2026-08-11', 'PASS')):
    db = fresh(date)
    a, lvl, why, vline, A = audit(db, date)
    check(f'{date} -> {want}', lvl == want, f'{lvl}: {why[:110]}')
    check(f'{date} verdict line starts with the level', vline.startswith('VERDICT: ' + want), vline[:80])
    check(f'{date} FAIL count unchanged semantics (failed == any FAIL)',
          a.failed == any(r['status'] == 'FAIL' for r in a.results))
    if date == '2026-09-08':
        check('09-08 names the 15:15 slot', '15:15' in why, why[:120])
    if date == '2026-09-11':
        check('09-11 says all PAPER and real book complete', 'all PAPER' in why and 'real book is complete' in why, why)

# perturbation 1: a REAL id planted in 09-10's lost list -> LOST --------------
db = fresh('p1'); c = sqlite3.connect(db)
real_id = c.execute("select id from positions where book='REAL' and status='OPEN' limit 1").fetchone()[0]
c.execute("update agent_runs set attempted=75, journaled=73, notes=? where id=160",
          ('not-journaled (2): GE-DIAGONAL-20260813-FCDABC; ' + real_id,))
c.commit(); c.close()
a, lvl, why, vline, A = audit(db, '2026-09-10')
check('P1: one REAL id among the paper losses -> LOST', lvl == 'LOST' and 'REAL position' in why, f'{lvl}: {why[:100]}')

# perturbation 2: the note names fewer than it claims -> LOST ------------------
db = fresh('p2'); c = sqlite3.connect(db)
c.execute("update agent_runs set attempted=77, journaled=60, notes=? where id=165",
          ('not-journaled (17): GE-DIAGONAL-20260813-FCDABC; ABT-DIAGONAL-20260831-EFF093',))
c.commit(); c.close()
a, lvl, why, vline, A = audit(db, '2026-09-11')
check('P2: unnamed losses cannot be attributed -> LOST', lvl == 'LOST' and 'cannot be attributed' in why, f'{lvl}: {why[:100]}')

# perturbation 3: a slot losing 30% of paper -> LOST ---------------------------
db = fresh('p3'); c = sqlite3.connect(db)
paper = [r[0] for r in c.execute("select id from positions where book='PAPER' and status='OPEN' limit 24")]
c.execute("update agent_runs set attempted=77, journaled=53, notes=? where id=165",
          ('not-journaled (24): ' + '; '.join(paper),))
c.commit(); c.close()
a, lvl, why, vline, A = audit(db, '2026-09-11')
check('P3: a slot losing >25% (all paper) -> LOST', lvl == 'LOST' and 'more than 25%' in why, f'{lvl}: {why[:100]}')

# W171 retry row: expected, reported, not a stray -------------------------------
db = fresh('r1'); c = sqlite3.connect(db)
c.execute("insert into agent_runs (agent, started_at, finished_at, slot, attempted, journaled, failed, status, notes) "
          "values ('com.helm.ivr.refresh','2026-09-10T10:15:04','2026-09-10T10:16:00',NULL,3,3,0,'OK','retry of 09:35: recovered 3 of 3')")
c.commit(); c.close()
a, lvl, why, vline, A = audit(db, '2026-09-10')
check('R1: retry row produces a PASS line', any(r['name'] == 'retry: ivr refresh' and r['status'] == 'PASS' for r in a.results), [r['name'] for r in a.results if 'ivr' in r['name']])
check('R1: retry row is NOT a slot-timing stray', not any(r['name'] == 'slot timing: ivr refresh' for r in a.results))
check('R1: day stays DEGRADED', lvl == 'DEGRADED', lvl)

db = fresh('r2'); c = sqlite3.connect(db)
c.execute("insert into agent_runs (agent, started_at, finished_at, slot, attempted, journaled, failed, status, notes) "
          "values ('com.helm.ivr.refresh','2026-09-10T10:15:04','2026-09-10T10:16:00',NULL,3,3,0,'OK',NULL)")
c.commit(); c.close()
a, lvl, why, vline, A = audit(db, '2026-09-10')
check('R2: the same row WITHOUT the retry note is still a stray (FAIL) and the day is LOST',
      any(r['name'] == 'slot timing: ivr refresh' and r['status'] == 'FAIL' for r in a.results) and lvl == 'LOST', lvl)

# json carries severity; --no-notify flag exists ---------------------------------
src = open(os.path.join(ROOT, 'helm/cli/audit_cmd.py')).read()
check('JSON output carries severity', '"severity": a.severity()[0]' in src)
check('--no-notify flag exists and notify is gated on LOST + today', '--no-notify' in src and 'level == SEV_LOST and is_today and not args.no_notify' in src)
print('\nPASS %d  FAIL %d' % (P, F)); sys.exit(1 if F else 0)
