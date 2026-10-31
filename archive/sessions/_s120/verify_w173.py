"""W173 verification: consider() on synthetic assessments (units, gaps, nearest,
near-miss, family branches), then the agent end to end on a fresh VACUUM INTO
copy with check_one / _finalize_close / yfinance FAKED -- what is under test is
that every open paper position leaves exactly one row with the right outcome,
the ledger row still lands, and a DRY run writes nothing."""
import os, sys, sqlite3, types, importlib
ROOT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else '.')
os.environ['HELM_ROOT'] = ROOT; sys.path.insert(0, ROOT)
LIVE = os.path.join(ROOT, 'data', 'helm.db')
SCRATCH = os.path.expanduser('~/s120'); os.makedirs(SCRATCH, exist_ok=True)
P = F = 0
def check(name, cond, info=''):
    global P, F; P += bool(cond); F += (not cond)
    print(('PASS ' if cond else 'FAIL ') + name + (('  -- ' + str(info)) if info else ''))
def fresh(tag):
    dst = os.path.join(SCRATCH, 'w173_%s.db' % tag)
    if os.path.exists(dst): os.remove(dst)
    c = sqlite3.connect('file:%s?mode=ro' % LIVE, uri=True); c.execute("VACUUM INTO '%s'" % dst); c.close()
    os.environ['HELM_DB'] = dst
    for k in [k for k in sys.modules if k.startswith('helm') or k == 'paper_exit_agent']: del sys.modules[k]
    return dst

fresh('unit')
from helm import exit_considered as EC
print('loaded-code probe:', EC.__file__)
from datetime import date, timedelta
def exp(days): return (date.today() + timedelta(days=days)).isoformat()
RUN = '2026-09-14T15:35:00'

# credit CSP, 22% of a 25% target, 30 DTE vs 21 -> gap_target 3 (near), gap_calendar 9
r = EC.consider({'core_reason': None, 'pnl_pct': 22.0, 'legs': [{'expiration': exp(30)}]},
                {'id': 'X-CSP-1', 'ticker': 'X', 'strategy': 'CSP', 'book': 'PAPER'}, RUN, 'held', thresholds=(0.25, 21))
check('CSP: pnl stored as fraction', r['pnl_pct'] == 0.22, r['pnl_pct'])
check('CSP: gap_target 3 points', r['gap_target'] == 3.0, r['gap_target'])
check('CSP: gap_calendar 9 days', r['gap_calendar'] == 9, r['gap_calendar'])
check('CSP: nearest is PROFIT_TARGET and near_miss', r['nearest_rule'] == 'PROFIT_TARGET' and r['near_miss'] == 1, (r['nearest_rule'], r['nearest_gap']))
check('CSP: outcome held, acted 0', r['outcome'] == 'held' and r['acted'] == 0)

# same CSP at 10% and 23 DTE -> calendar is nearer (2 days), near_miss via days
r = EC.consider({'core_reason': None, 'pnl_pct': 10.0, 'legs': [{'expiration': exp(23)}]},
                {'id': 'X-CSP-2', 'ticker': 'X', 'strategy': 'CSP', 'book': 'PAPER'}, RUN, 'held', thresholds=(0.25, 21))
check('CSP: nearest is DTE_MANAGE at 2 days, near', r['nearest_rule'] == 'DTE_MANAGE' and r['nearest_gap'] == 2 and r['near_miss'] == 1, (r['nearest_rule'], r['nearest_gap']))

# fired: PROFIT_TARGET, closed
r = EC.consider({'core_reason': 'PROFIT_TARGET', 'pnl_pct': 31.0, 'legs': [{'expiration': exp(30)}]},
                {'id': 'X-CSP-3', 'ticker': 'X', 'strategy': 'CSP', 'book': 'PAPER'}, RUN, 'closed', thresholds=(0.25, 21))
check('CSP fired: verdict kept, acted 1, gap_target negative, NOT a near-miss', r['verdict'] == 'PROFIT_TARGET' and r['acted'] == 1 and r['gap_target'] == -6.0 and r['near_miss'] == 0, (r['gap_target'], r['near_miss']))

# long call with arms: pnl +14%, hwm +42%, floor +22%, stop -50%, 40 DTE -> trail gap is -8 (fired), stop gap 64, DTE_21 gap 19
arms = {'give_back': {'hwm': 0.42, 'floor': 0.22, 'band': 0.20}, 'v3': {'stop': -0.5, 'dte_soft': 21, 'dte_hard': 7}}
r = EC.consider({'core_reason': 'GIVE_BACK', 'pnl_pct': 14.0, 'arms': arms, 'legs': [{'expiration': exp(40)}]},
                {'id': 'Y-LC-1', 'ticker': 'Y', 'strategy': 'LONG_CALL', 'book': 'PAPER'}, RUN, 'closed', thresholds=(0.25, 21))
check('LC: hwm/floor/stop carried as fractions', (r['hwm_pct'], r['trail_floor'], r['stop_pct']) == (0.42, 0.22, -0.5), (r['hwm_pct'], r['trail_floor'], r['stop_pct']))
check('LC: gap_trail -8 (under the line), gap_stop 64, gap_hard 33, gap_calendar 19', (r['gap_trail'], r['gap_stop'], r['gap_hard'], r['gap_calendar']) == (-8.0, 64.0, 33, 19), (r['gap_trail'], r['gap_stop'], r['gap_hard'], r['gap_calendar']))
check('LC: nearest GIVE_BACK', r['nearest_rule'] == 'GIVE_BACK', r['nearest_rule'])
check('LC: no target/dte_exit columns (not that family)', r['target_pct'] is None and r['dte_exit'] is None)

# long call holding 3 points above its trail -> near miss
arms2 = {'give_back': {'hwm': 0.30, 'floor': 0.10, 'band': 0.20}, 'v3': {'stop': -0.5, 'dte_soft': 21, 'dte_hard': 7}}
r = EC.consider({'core_reason': None, 'pnl_pct': 13.0, 'arms': arms2, 'legs': [{'expiration': exp(40)}]},
                {'id': 'Y-LC-2', 'ticker': 'Y', 'strategy': 'LONG_CALL', 'book': 'PAPER'}, RUN, 'held', thresholds=(0.25, 21))
check('LC holding 3 pts above the trail: near_miss 1, gap_trail 3', r['near_miss'] == 1 and r['gap_trail'] == 3.0, (r['gap_trail'], r['near_miss']))

# diagonal: no target branch; calendar off the BACK leg
r = EC.consider({'core_reason': None, 'pnl_pct': 40.0, 'legs': [{'expiration': exp(5)}, {'expiration': exp(70)}]},
                {'id': 'Z-DIAG-1', 'ticker': 'Z', 'strategy': 'DIAGONAL', 'book': 'PAPER'}, RUN, 'held', thresholds=(0.5, 21))
check('DIAGONAL: target_pct None and gap_target None (no branch, W159)', r['target_pct'] is None and r['gap_target'] is None)
check('DIAGONAL: dte_min 5, dte_cal 70, gap_calendar 49', (r['dte_min'], r['dte_cal'], r['gap_calendar']) == (5, 70, 49), (r['dte_min'], r['dte_cal'], r['gap_calendar']))
check('DIAGONAL: note names the missing branch', 'no profit-target branch' in (r['note'] or ''))

# failed check: empty assessment still yields a row
r = EC.consider({}, {'id': 'Q-IC-1', 'ticker': 'Q', 'strategy': 'IRON_CONDOR', 'book': 'PAPER'}, RUN, 'failed', thresholds=(0.25, 21))
check('failed check: row with outcome failed and no gaps', r['outcome'] == 'failed' and r['nearest_rule'] is None)

# live settings path (no thresholds arg) works against the copy
r = EC.consider({'core_reason': None, 'pnl_pct': 10.0, 'legs': [{'expiration': exp(30)}]},
                {'id': 'X-CSP-9', 'ticker': 'X', 'strategy': 'CSP', 'book': 'PAPER', 'account_id': None}, RUN, 'held')
check('settings lookup path returns a target and a dte_exit', r['target_pct'] is not None and r['dte_exit'] is not None, (r['target_pct'], r['dte_exit']))

# ---------------- the agent, end to end, on a copy ----------------
def run_agent(db, dry):
    import paper_exit_agent as PA
    import helm.market_calendar as MC
    import helm.cli.check_cmd as CC
    import helm.cli.close_cmd as CL
    MC.agent_should_run = lambda now=None: (True, 'open')
    sys.modules['yfinance'] = types.SimpleNamespace(Ticker=lambda t: None)
    c = sqlite3.connect(db)
    ids = [r[0] for r in c.execute("select id from positions where status='OPEN' and book='PAPER' order by ticker")]
    lcs = [r[0] for r in c.execute("select id from positions where status='OPEN' and book='PAPER' and strategy='LONG_CALL' order by ticker")]
    c.close()
    non_lc = [i for i in ids if i not in lcs]
    fire_pt = non_lc[0]              # one credit-ish position fires PROFIT_TARGET
    fire_defer = non_lc[1]           # one fires but a leg has no quote -> deferred
    fire_lc = lcs[0] if lcs else None   # one long call fires GIVE_BACK
    def fake_check(pos, legs, persist=False):
        base = {'legs': legs, 'pnl_pct': 12.0}
        if pos['id'] == fire_pt: return dict(base, core_reason='PROFIT_TARGET', pnl_pct=30.0)
        if pos['id'] == fire_defer: return dict(base, core_reason='PROFIT_TARGET', pnl_pct=30.0)
        if pos['id'] == fire_lc: return dict(base, core_reason='GIVE_BACK', pnl_pct=14.0,
                                            arms={'give_back': {'hwm': 0.42, 'floor': 0.22}, 'v3': {'stop': -0.5}})
        if pos['strategy'] == 'LONG_CALL': return dict(base, core_reason=None, arms={'give_back': {'hwm': 0.2, 'floor': 0.0}, 'v3': {'stop': -0.5}})
        return dict(base, core_reason=None)
    CC.check_one = fake_check
    PA.leg_mid = lambda tk, leg: (None if leg.position_id == fire_defer else 1.0)
    closed_ids = []
    def fake_close(pobj, lobjs, prices, reason):
        closed_ids.append(pobj.id); return {'ok': True, 'realized_pnl': 1.0}
    CL._finalize_close = fake_close
    PA.DRY = dry
    PA.sys.argv = ['x']
    PA.main()
    return ids, fire_pt, fire_defer, fire_lc, closed_ids

db = fresh('agent'); HW = sqlite3.connect(db).execute("select max(id) from agent_runs").fetchone()[0]
ids, fire_pt, fire_defer, fire_lc, closed_ids = run_agent(db, dry=False)
c = sqlite3.connect(db)
rows = c.execute("select position_id, outcome, acted, verdict, nearest_rule, near_miss from exit_considered").fetchall()
byid = {r[0]: r for r in rows}
check('AGENT: one row per open paper position', len(rows) == len(ids) and set(byid) == set(ids), (len(rows), len(ids)))
check('AGENT: the PROFIT_TARGET position is closed/acted', byid[fire_pt][1] == 'closed' and byid[fire_pt][2] == 1, byid[fire_pt])
check('AGENT: the no-quote position is deferred, not acted', byid[fire_defer][1] == 'deferred' and byid[fire_defer][2] == 0, byid[fire_defer])
if fire_lc:
    check('AGENT: the GIVE_BACK long call is closed with nearest GIVE_BACK', byid[fire_lc][1] == 'closed' and byid[fire_lc][4] == 'GIVE_BACK', byid[fire_lc])
held = [r for r in rows if r[1] == 'held']
check('AGENT: everything else held with acted 0', all(r[2] == 0 for r in held) and len(held) == len(ids) - (3 if fire_lc else 2), len(held))
check('AGENT: closes went through _finalize_close for exactly the fired ones', sorted(closed_ids) == sorted([fire_pt] + ([fire_lc] if fire_lc else [])), closed_ids)
led = c.execute("select attempted, journaled, failed, status, notes from agent_runs where id > ? and agent='com.helm.paper.exits'", (HW,)).fetchall()
check('AGENT: ledger row still written, closed count = 2, deferred counted as skipped', len(led) == 1 and led[0][1] == (2 if fire_lc else 1) and led[0][2] == 1, led)
n_open_after = c.execute("select count(*) from positions where status='OPEN' and book='PAPER'").fetchone()[0]
check('AGENT: positions table untouched by the fake close (copy only)', n_open_after == len(ids))
c.close()

# DRY: writes nothing
db = fresh('dry'); HW = sqlite3.connect(db).execute("select max(id) from agent_runs").fetchone()[0]
run_agent(db, dry=True)
c = sqlite3.connect(db)
has = c.execute("select count(*) from sqlite_master where name='exit_considered'").fetchone()[0]
n = c.execute("select count(*) from exit_considered").fetchone()[0] if has else 0
led = c.execute("select count(*) from agent_runs where id > ?", (HW,)).fetchone()[0]
check('DRY: no exit_considered rows and no ledger row', n == 0 and led == 0, (n, led))
c.close()

# unique index: re-running the same run_started_at replaces, never duplicates
db = fresh('dup'); c = sqlite3.connect(db)
from helm import exit_considered as EC2
r = EC2.consider({'core_reason': None, 'pnl_pct': 1.0, 'legs': [{'expiration': exp(30)}]}, {'id': 'A', 'ticker': 'A', 'strategy': 'CSP', 'book': 'PAPER'}, RUN, 'held', thresholds=(0.25, 21))
EC2.record(c, [r]); EC2.record(c, [r])
check('record: same run+position twice -> one row', c.execute("select count(*) from exit_considered").fetchone()[0] == 1)
c.close()
print('\nPASS %d  FAIL %d' % (P, F)); sys.exit(1 if F else 0)
