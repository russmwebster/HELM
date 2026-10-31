"""W163 (s113) -- Mac-side end-to-end rehearsal of the chain->CLI->DB path, run
through the helm.local:8766 bridge (spawned detached; results to /tmp/w163_rehearse*.json).
Variant A: AA 55/47 (today a screened candidate -> SELL_SCREEN). Variant B (long 45,
fills 2.33/10.20, expect-net 7.87): NOT a candidate -> chain bypass, MANUAL_PIN.
Live DB opened read-only; every write lands in a VACUUM INTO copy."""
import os, sys, subprocess, sqlite3, json, time
HOME = os.path.expanduser('~'); ROOT = HOME + '/Projects/helm'
COPY = '/tmp/w163_rehearse.db'; OUT = '/tmp/w163_rehearse.out'; RES = '/tmp/w163_rehearse.json'
LIVE = ROOT + '/data/helm.db'; CTRL = 'AA-DIAGONAL-20260901-219869'
for p in (COPY, OUT, RES):
    try: os.remove(p)
    except FileNotFoundError: pass
src = sqlite3.connect('file:' + LIVE + '?mode=ro', uri=True); src.execute('VACUUM INTO ?', (COPY,)); src.close()
def cnt(db):
    c = sqlite3.connect('file:' + db + '?mode=ro', uri=True)
    d = {t: c.execute('select count(*) from ' + t).fetchone()[0] for t in ('positions', 'legs', 'entry_snapshots', 'lifecycle_events')}
    c.close(); return d
before = cnt(COPY); live_before = cnt(LIVE)
env = dict(os.environ, HELM_DB=COPY, HELM_ROOT=ROOT, PYTHONDONTWRITEBYTECODE='1')
argv = [sys.executable, ROOT + '/helm.py', 'open', 'AA', 'DIAGONAL', '--confirm',
        '--strike', '55', '--expiry', '2026-10-16', '--long-strike', '47', '--long-expiry', '2026-12-18',
        '--expect-contracts', '5', '--expect-net', '6.65']
t0 = time.time()
try:
    r = subprocess.run(argv, input='5\n2.33\n8.98\ny\n', capture_output=True, text=True, env=env, cwd=ROOT, timeout=900)
    out = r.stdout + r.stderr; rc = r.returncode
except Exception as e:
    out = 'EXC ' + repr(e); rc = -1
open(OUT, 'w').write(out)
after = cnt(COPY)
c = sqlite3.connect('file:' + COPY + '?mode=ro', uri=True); c.row_factory = sqlite3.Row
def grab(pid):
    p = c.execute('select * from positions where id = ?', (pid,)).fetchone()
    legs = [dict(x) for x in c.execute('select * from legs where position_id = ? order by direction desc', (pid,))]
    snap = c.execute('select * from entry_snapshots where position_id = ?', (pid,)).fetchone()
    ev = [dict(x) for x in c.execute('select * from lifecycle_events where position_id = ?', (pid,))]
    return {'pos': dict(p) if p else None, 'legs': legs, 'snap': dict(snap) if snap else None, 'events': ev}
res = {'rc': rc, 'secs': round(time.time() - t0, 1), 'before': before, 'after': after,
       'live_before': live_before, 'live_after': cnt(LIVE), 'logged': 'DIAGONAL logged' in out, 'oob': 'OUT OF BAND' in out}
new = c.execute("select id from positions where ticker = 'AA' and strategy = 'DIAGONAL' and id <> ? order by created_at desc limit 1", (CTRL,)).fetchone()
res['new_id'] = new['id'] if new else None
if new:
    res['new'] = grab(new['id']); res['ctrl'] = grab(CTRL)
json.dump(res, open(RES, 'w'), default=str, indent=1)
