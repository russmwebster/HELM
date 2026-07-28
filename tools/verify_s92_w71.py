"""HELM-135 / W71 -- sell-side entry bands are the intersection. Checks."""
import os, sys, re
sys.path.insert(0, os.path.expanduser('~/Projects/helm'))
from helm import entry_bands as eb
from helm.cli.open_cmd import STRATEGY_CONFIG

FAIL = []
def ck(name, cond, detail=''):
    if callable(cond):
        try: cond = cond()
        except Exception as e:
            detail = type(e).__name__ + ': ' + str(e)[:80]; cond = False
    print(('  ok   ' if cond else '  FAIL ') + name + (('  -- ' + str(detail)) if detail and not cond else ''))
    if not cond: FAIL.append(name)

print('== the tighter-edge rule ==')
ck('floor takes the larger', eb.tighter(0.15, 0.20, 'lo') == 0.20)
ck('ceiling takes the smaller', eb.tighter(0.40, 0.30, 'hi') == 0.30)
ck('floor ignores a looser stored value', eb.tighter(0.15, 0.10, 'lo') == 0.15)
ck('ceiling ignores a looser stored value', eb.tighter(0.40, 0.50, 'hi') == 0.40)
ck('missing stored leaves code alone', eb.tighter(0.25, None, 'lo') == 0.25)
ck('missing code takes stored', eb.tighter(None, 0.25, 'lo') == 0.25)
ck('garbage stored leaves code alone', eb.tighter(0.25, 'x', 'lo') == 0.25)

print()
print('== NEVER LOOSEN -- the invariant that matters ==')
for s in eb.SELL_SIDE:
    cfg = STRATEGY_CONFIG.get(s)
    if not cfg: continue
    b, _ = eb.effective_bands(s, cfg)
    ck(s + ': delta floor never drops', b['delta_min'] >= cfg['delta_min'], (b['delta_min'], cfg['delta_min']))
    ck(s + ': delta ceiling never rises', b['delta_max'] <= cfg['delta_max'], (b['delta_max'], cfg['delta_max']))
    ck(s + ': dte floor never drops', b['dte_min'] >= cfg['dte_min'], (b['dte_min'], cfg['dte_min']))
    ck(s + ': dte ceiling never rises', b['dte_max'] <= cfg['dte_max'], (b['dte_max'], cfg['dte_max']))

print()
print('== sell side lands on the stored numbers ==')
b, notes = eb.effective_bands('CSP', STRATEGY_CONFIG['CSP'])
ck('CSP delta 0.20-0.30', (b['delta_min'], b['delta_max']) == (0.2, 0.3), b)
ck('CSP dte 30-45', (b['dte_min'], b['dte_max']) == (30, 45), b)
ck('CSP dte are ints', isinstance(b['dte_min'], int) and isinstance(b['dte_max'], int))
ck('CSP reports what moved', len(notes) == 4, notes)
b, _ = eb.effective_bands('BEAR_CALL_SPREAD', STRATEGY_CONFIG['BEAR_CALL_SPREAD'])
ck('bear call delta 0.15-0.25', (b['delta_min'], b['delta_max']) == (0.15, 0.25), b)
b, _ = eb.effective_bands('IRON_CONDOR', STRATEGY_CONFIG['IRON_CONDOR'])
ck('condor floor stays at the CODE 0.15, not the looser stored 0.10', b['delta_min'] == 0.15, b)
ck('condor ceiling takes the tighter stored 0.20', b['delta_max'] == 0.2, b)

print()
print('== buy side untouched ==')
for s in ('LONG_CALL', 'LONG_PUT', 'BEAR_PUT_SPREAD'):
    cfg = STRATEGY_CONFIG.get(s)
    if not cfg: continue
    b, notes = eb.effective_bands(s, cfg)
    ck(s + ': band identical to config',
       all(b[k] == cfg[k] for k in ('delta_min','delta_max','dte_min','dte_max')), (b, cfg))
    ck(s + ': no notes emitted', notes == [])

print()
print('== fails open ==')
b, notes = eb.effective_bands('CSP', STRATEGY_CONFIG['CSP'], stored={})
ck('empty settings leaves the code band', b['delta_max'] == STRATEGY_CONFIG['CSP']['delta_max'], b)
ck('empty settings emits no notes', notes == [])
def boom():
    raise RuntimeError('db down')
eb._SETTINGS_CACHE.pop('CSP', None)
ck('unreadable db returns {}', eb.stored_entry_bands('CSP', conn_factory=boom) == {})
eb._SETTINGS_CACHE.pop('CSP', None)

print()
print('== the deliberate exclusion ==')
src = open(os.path.expanduser('~/Projects/helm/helm/entry_bands.py'), encoding='utf-8').read()
ck('never reads entry_iv_rank_min', 'entry_iv_rank_min' not in src.split('THE RULE')[1].split('"""')[1] if 'THE RULE' in src else True)
ck('does not apply iv rank in code', 'entry_iv_rank' not in src.split('SELL_SIDE = (')[1])

print()
print('== wired into open ==')
oc = open(os.path.expanduser('~/Projects/helm/helm/cli/open_cmd.py'), encoding='utf-8').read()
ck('open imports effective_bands', 'from helm.entry_bands import effective_bands' in oc)
ck('delta_min comes from the resolved band', re.search(r'delta_min\s*=\s*_bands\[', oc) is not None)
ck('dte_max comes from the resolved band', re.search(r'dte_max\s*=\s*_bands\[', oc) is not None)
ck('no direct config delta-band reads remain anywhere',
   re.search(r'config\[' + chr(34) + '(delta_min|delta_max)' + chr(34) + r'\]', oc) is None)
# W51: every sibling path, not just the one that was patched first.
for fn in ('evaluate_contracts', 'evaluate_condors', 'evaluate_strangles', 'evaluate_spreads'):
    i = oc.find('def ' + fn)
    body = oc[i:i+9000] if i >= 0 else ''
    ck(fn + ' resolves its band', '_eff_bands(strategy, config)' in body, 'not found')
ck('the delta display uses the enforced band', '_disp_bands[' + chr(34) + 'delta_min' + chr(34) + ']' in oc)
ck('delta_sweet still comes from config', 'delta_sweet = config["delta_sweet"]' in oc)
ck('the runway floor still runs after', '_entry_dte_floor(strategy, dte_min)' in oc)

print()
print('ALL PASS' if not FAIL else 'FAILURES: ' + repr(FAIL))
