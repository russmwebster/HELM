#!/usr/bin/env python3
"""Verify W21 -- journal health, the run ledger, and the failure instrumentation.

The agent_runs half is behavioural: a constructed checks table with a KNOWN gap
must produce exactly that gap. The check_cmd half is structural (an AST walk),
and says so -- a behavioural test there would need a live broker quote.

    python3 tools/verify_s90c_journal_health.py
"""

import os
import re
import sys
import ast
import sqlite3
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

OK = 0
FAIL = 0


def chk(cond, label):
    global OK, FAIL
    if cond:
        OK += 1
        print('  ok   ' + label)
    else:
        FAIL += 1
        print('  FAIL ' + label)
    return bool(cond)


from helm import agent_runs as A

# A fixed Wednesday, so the test does not drift with the day it runs on.
WED = datetime(2026, 7, 22, 14, 0)   # Wed 14:00: 10:00 and 12:30 are due, 15:45 is not
SAT = datetime(2026, 7, 25, 12, 30)

print('agent_runs -- slot attribution')
chk(A.slot_for(WED.replace(hour=10, minute=0)) == '10:00', 'a run at 10:00 is the 10:00 slot')
chk(A.slot_for(WED.replace(hour=10, minute=44)) == '10:00',
    'a run 44 minutes late still counts (14 July ran 15:58)')
chk(A.slot_for(WED.replace(hour=10, minute=46)) is None,
    'a run 46 minutes late is ad-hoc, not the slot')
chk(A.slot_for(WED.replace(hour=9, minute=59)) is None, 'a run before the slot is ad-hoc')
chk(A.slot_for(WED.replace(hour=12, minute=30)) == '12:30', 'the 12:30 slot attributes')
chk(A.slot_for(SAT) is None, 'a weekend run is never a slot')

print('\nagent_runs -- expected slots')
due = A.expected_slots(WED.date(), now=WED)
chk(due == [(WED.date(), '10:00'), (WED.date(), '12:30')],
    'a slot that has not come round yet is not due (got ' + str(len(due)) + ')')
due2 = A.expected_slots(WED.date() - timedelta(days=4), now=WED)
chk(all(d.weekday() < 5 for d, _ in due2), 'weekends are never due')


def journal_db(times):
    c = sqlite3.connect(':memory:')
    c.execute('CREATE TABLE checks (position_id TEXT, checked_at TEXT)')
    c.executemany('INSERT INTO checks VALUES (?, ?)',
                  [('P1', t.isoformat()) for t in times])
    return c


print('\nagent_runs -- journal health finds the gap it should')
# Mon 20th ran clean. Tue 21st: 10:00 and 12:30 ran, 15:45 did not.
# Wed 22nd: 10:00 only, and 15:45 is not due yet at 14:00.
mon = datetime(2026, 7, 20, 0, 0)
tue = datetime(2026, 7, 21, 0, 0)
wed = datetime(2026, 7, 22, 0, 0)
c = journal_db([mon.replace(hour=10), mon.replace(hour=12, minute=30),
                mon.replace(hour=15, minute=45),
                tue.replace(hour=10), tue.replace(hour=12, minute=30),
                wed.replace(hour=10, minute=1)])
h = A.journal_health(c, days=3, now=WED)
chk(h['missed'] == [(wed.date(), '12:30'), (tue.date(), '15:45')],
    'exactly the two missed slots, newest first (got ' + str(h['missed']) + ')')
chk(h['slots_today'] == 1 and h['slots_due_today'] == 2,
    'today reads 1 of 2 (got ' + str(h['slots_today']) + ' of '
    + str(h['slots_due_today']) + ')')
chk(round(h['age_hours']) == 4, 'age is measured from the newest mark (got '
    + str(h['age_hours']) + ')')
chk(A.slot_for(wed.replace(hour=10, minute=1)) == '10:00',
    'a run one minute late still belongs to its slot')

# a clean board says nothing at all
c2 = journal_db([wed.replace(hour=10), wed.replace(hour=12, minute=30)])
h2 = A.journal_health(c2, days=0, now=WED)
chk(h2['missed'] == [] and A.health_line(h2) == '',
    'a healthy journal prints nothing -- silence is the healthy state')

print('\nagent_runs -- the line says the right thing')
# 52h with no mark: the streak cannot reach CONFIRM_DAYS, and the line says so
h3 = A.journal_health(journal_db([mon.replace(hour=10)]), days=5, now=WED)
chk('cannot advance' in A.health_line(h3),
    'a long gap names the consequence, not just the age')
# 28h: under the hard threshold, but nothing has been marked today
h4 = A.journal_health(journal_db([tue.replace(hour=10)]), days=2, now=WED)
line4 = A.health_line(h4)
chk('yellow' in line4 and 'no marks today' in line4,
    'no marks at all today is yellow (got ' + line4[:60] + ')')
chk(A.health_line({'last_write': None}) ==
    '[yellow]journal: no marks ever recorded[/yellow]',
    'an empty journal says so rather than reading as healthy')

print('\nagent_runs -- the ledger')
c4 = sqlite3.connect(':memory:')
chk(A.ensure_table(c4), 'ledger table creates')
A.record_run(c4, A.AGENT_SNAPSHOT, WED.replace(hour=10).isoformat(),
             WED.replace(hour=10, minute=2).isoformat(), 90, 88, 2)
r = A.last_run(c4)
chk(r is not None and r['slot'] == '10:00' and r['journaled'] == 88,
    'a run round-trips with its slot')
chk(r['status'] == 'PARTIAL', 'a run that lost positions is PARTIAL, not OK')
A.record_run(c4, A.AGENT_SNAPSHOT, WED.replace(hour=12, minute=30).isoformat(),
             WED.replace(hour=12, minute=31).isoformat(), 90, 0, 0)
chk(A.last_run(c4)['status'] == 'EMPTY',
    'a run that journaled nothing is EMPTY however cleanly it exited')
A.record_run(c4, A.AGENT_SNAPSHOT, WED.replace(hour=15, minute=45).isoformat(),
             WED.replace(hour=15, minute=46).isoformat(), 90, 90, 0)
chk(A.last_run(c4)['status'] == 'OK', 'a clean run is OK')


print('\ncheck_cmd wiring  (structural -- an AST walk, not a live snapshot)')
src_path = os.path.join(ROOT, 'helm', 'cli', 'check_cmd.py')
src = open(src_path, encoding='utf-8').read()
tree = ast.parse(src)

top = {n.targets[0].id for n in tree.body
       if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)}
chk('VERDICT_FAILURES' in top, 'check_cmd collects verdict failures')

# the handler must keep the traceback and record identity
handler_ok = False
identity_ok = False
for node in ast.walk(tree):
    if isinstance(node, ast.ExceptHandler):
        body = ast.dump(node)
        if 'exception' in body and 'VERDICT_FAILURES' in body:
            handler_ok = True
            if all(k in body for k in ('position_id', 'ticker', 'strategy', 'book')):
                identity_ok = True
chk(handler_ok, 'the verdict handler logs a traceback and records the failure')
chk(identity_ok, 'and records which position it was, not just the ticker')
chk('core_verdict failed for' not in src,
    'the misattributing label is gone (it named the wrong call in the block)')

fn = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}


def mentions(node, needle):
    return needle in ast.dump(node)


chk('cmd_snapshot' in fn and mentions(fn['cmd_snapshot'], 'agent_runs'),
    'cmd_snapshot records the run in the ledger')
chk('cmd_snapshot' in fn and mentions(fn['cmd_snapshot'], 'journaled'),
    'cmd_snapshot counts rows that reached the journal, not attempts')
chk('cmd_snapshot' in fn and not mentions(fn['cmd_snapshot'], "'position(s) processed'")
    and 'position(s) processed' not in src,
    'the old "N position(s) processed" claim is gone')
chk('run_snapshot' in fn and mentions(fn['run_snapshot'], 'asctime'),
    'the agent log gets timestamps')

pulse = None
for name, node in fn.items():
    if mentions(node, 'health_line'):
        pulse = name
chk(pulse is not None, 'journal health is surfaced on the check screen (in ' + str(pulse) + ')')

schema = open(os.path.join(ROOT, 'helm', 'schema.sql'), encoding='utf-8').read()
chk('agent_runs' in schema, 'schema.sql declares agent_runs')

db = os.path.join(ROOT, 'data', 'helm.db')
live = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
chk(bool(live.execute("SELECT 1 FROM sqlite_master WHERE name = 'agent_runs'").fetchone()),
    'the live database has the agent_runs table')

# and the real journal must still answer -- this is the number the doctrine reads
h = A.journal_health(live, days=14)
chk(h['last_write'] is not None, 'journal health runs against the live database')
print('       live: last mark ' + str(h['age_hours']) + 'h ago · '
      + str(h['slots_today']) + ' of ' + str(h['slots_due_today'])
      + ' slots today · ' + str(len(h['missed'])) + ' missed in 14d')
live.close()

print('\n' + str(OK) + ' ok, ' + str(FAIL) + ' failed')
sys.exit(0 if FAIL == 0 else 1)
